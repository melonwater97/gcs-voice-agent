#!/usr/bin/env python3
"""Closed loop demo: upload mission -> arm -> fly waypoints -> RTL -> land.

PX4 (v1.17) rejects MAV_CMD_NAV_RETURN_TO_LAUNCH as a *mission item*, so the
return leg is triggered by switching to AUTO.RTL after the last waypoint is
reached - which is also how a real operator would do it.

Writes out/mission_demo.csv and out/mission_demo.png.
"""

from __future__ import annotations

import csv
import math
import os
import time
from dataclasses import dataclass, field

from pymavlink import mavutil

LINK = os.environ.get("UAV_MAVLINK", "udpin:0.0.0.0:14540")
PX4_PORT = int(os.environ.get("UAV_PX4_PORT", "14580"))
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

MODE_AUTO_MISSION = 0x04040000
MODE_AUTO_RTL = 0x05040000

HOME = (47.397971, 8.546164)
ALT_M = 12.0
# north-east offsets in degrees (~60 m / 100 m legs)
WP_OFFSETS = [(0.00060, 0.0), (0.00060, 0.00090), (0.0, 0.00090)]


@dataclass
class Sample:
    t: float
    lat: float
    lon: float
    alt_rel: float
    roll: float
    pitch: float
    yaw: float
    vn: float
    ve: float
    vd: float
    phase: str
    armed: bool
    wp: int


@dataclass
class State:
    att: object = None
    gpi: object = None
    ned: object = None
    hb: object = None
    phase: str = "pre"
    wp: int = -1
    reached_last: bool = field(default=False)


def connect():
    conn = mavutil.mavlink_connection(LINK, source_system=255, source_component=190)
    if LINK.startswith("udpin"):
        conn.destination_addr = ("127.0.0.1", PX4_PORT)
    deadline = time.time() + 10
    while time.time() < deadline:
        hb = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
        if hb and hb.get_srcSystem() != 255:
            conn.target_system = hb.get_srcSystem()
            conn.target_component = hb.get_srcComponent()
            return conn
    raise SystemExit("no vehicle heartbeat")


def upload_mission(conn, items) -> int:
    ts, tc = conn.target_system, conn.target_component
    conn.mav.mission_count_send(ts, tc, len(items), 0)
    while True:
        msg = conn.recv_match(type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"],
                              blocking=True, timeout=8.0)
        if msg is None:
            raise SystemExit("[mission] no response to MISSION_COUNT")
        if msg.get_type() == "MISSION_ACK":
            return msg.type
        it = items[msg.seq]
        conn.mav.mission_item_int_send(
            ts, tc, msg.seq, 6, it["cmd"], 0, 1, 0, 0, 0, 0,
            int(round(it["lat"] * 1e7)), int(round(it["lon"] * 1e7)), it["alt"], 0)


def set_mode(conn, custom_mode: int) -> None:
    conn.mav.set_mode_send(
        conn.target_system,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        custom_mode)


def arm(conn) -> bool:
    conn.mav.command_long_send(
        conn.target_system, conn.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0)
    deadline = time.time() + 10
    while time.time() < deadline:
        msg = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=1.0)
        if msg and msg.command == 400:
            return msg.result == 0
    return False


def build_mission():
    items = [dict(cmd=22, lat=HOME[0], lon=HOME[1], alt=ALT_M)]
    for dn, de in WP_OFFSETS:
        items.append(dict(cmd=16, lat=HOME[0] + dn, lon=HOME[1] + de, alt=ALT_M))
    return items


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = connect()
    items = build_mission()

    print(f"[mission] {len(items)} items, {ALT_M:.0f} m AGL")
    ack = upload_mission(conn, items)
    print(f"[mission] ack={ack} (0=accepted)")
    if ack != 0:
        return 1

    set_mode(conn, MODE_AUTO_MISSION)
    time.sleep(1.0)
    if not arm(conn):
        raise SystemExit("[arm] rejected")
    print("[arm] armed, mission starting")

    st = State()
    samples: list[Sample] = []
    t0 = time.time()
    last_wp = len(items) - 1
    rtl_at = None

    while time.time() - t0 < 240:
        while True:
            msg = conn.recv_match(blocking=False)
            if msg is None:
                break
            t = msg.get_type()
            if t == "ATTITUDE":
                st.att = msg
            elif t == "GLOBAL_POSITION_INT":
                st.gpi = msg
            elif t == "LOCAL_POSITION_NED":
                st.ned = msg
            elif t == "HEARTBEAT" and msg.get_srcSystem() == conn.target_system:
                st.hb = msg
            elif t == "MISSION_ITEM_REACHED":
                st.wp = msg.seq
                print(f"[fly] waypoint {msg.seq}/{last_wp} reached  "
                      f"alt={st.gpi.relative_alt/1000:.1f} m" if st.gpi else
                      f"[fly] waypoint {msg.seq} reached")
                if msg.seq == last_wp and rtl_at is None:
                    print("[fly] mission complete -> AUTO.RTL")
                    set_mode(conn, MODE_AUTO_RTL)
                    st.phase = "rtl"
                    rtl_at = time.time()
            elif t == "STATUSTEXT":
                text = msg.text.strip()
                if text and not text.startswith("["):
                    print(f"    [vehicle] {text}")

        if st.gpi and st.att and st.ned and st.hb:
            samples.append(Sample(
                t=time.time() - t0,
                lat=st.gpi.lat / 1e7, lon=st.gpi.lon / 1e7,
                alt_rel=st.gpi.relative_alt / 1000.0,
                roll=math.degrees(st.att.roll),
                pitch=math.degrees(st.att.pitch),
                yaw=math.degrees(st.att.yaw) % 360.0,
                vn=st.ned.vx, ve=st.ned.vy, vd=st.ned.vz,
                phase=st.phase, armed=bool(st.hb.base_mode & 128), wp=st.wp))

            if rtl_at and not (st.hb.base_mode & 128) and len(samples) > 100:
                print(f"[fly] landed and disarmed at t={samples[-1].t:.0f}s")
                break

        time.sleep(0.2)

    conn.close()

    csv_path = os.path.join(OUT_DIR, "mission_demo.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "lat", "lon", "alt_rel", "roll", "pitch", "yaw",
                    "vn", "ve", "vd", "phase", "armed", "wp"])
        for s in samples:
            w.writerow([round(s.t, 2), s.lat, s.lon, round(s.alt_rel, 2),
                        round(s.roll, 2), round(s.pitch, 2), round(s.yaw, 2),
                        round(s.vn, 2), round(s.ve, 2), round(s.vd, 2),
                        s.phase, s.armed, s.wp])

    alt_max = max(s.alt_rel for s in samples) if samples else 0
    print(f"[out] {csv_path}  samples={len(samples)}  "
          f"max_alt={alt_max:.1f} m  duration={samples[-1].t:.0f}s" if samples else "[out] no samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
