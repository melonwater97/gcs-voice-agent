#!/usr/bin/env python3
"""uav-mcp - a small, safety-gated MCP server for PX4 / ArduPilot over MAVLink.

Design rules
------------
1. Read-only by default.  Write tools (set_parameter, arm, disarm) are
   disabled unless UAV_MCP_ALLOW_WRITE=1 is set in the environment.
2. Every write is two-phase: the first call returns a short-lived
   confirmation token, the second call must echo it back.
3. No takeoff / mission-upload tools.  Those change the physical world in
   ways a chat model should not be one tool-call away from.

Run
---
    UAV_MAVLINK=udpin:0.0.0.0:14540 python3 server.py

Environment
-----------
    UAV_MAVLINK           connection string (default udpin:0.0.0.0:14540)
    UAV_MCP_ALLOW_WRITE   "1" to enable write tools
    UAV_MCP_FORCE_WRITE   "1" to also allow writes while armed
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    raise SystemExit("pip install mcp")

from pymavlink import mavutil

LINK = os.environ.get("UAV_MAVLINK", "udpin:0.0.0.0:14540")
# PX4's onboard link listens here (the port *we* must send to).  Sending to
# the bind address instead would loop our own packets back into our socket.
PX4_PORT = int(os.environ.get("UAV_PX4_PORT", "14580"))
GCS_SYSID = 255
ALLOW_WRITE = os.environ.get("UAV_MCP_ALLOW_WRITE", "0") == "1"
FORCE_WRITE = os.environ.get("UAV_MCP_FORCE_WRITE", "0") == "1"
TOKEN_TTL = 60.0

PX4_ARMED = 128
PX4_CUSTOM_MAIN_MODE = {
    1: "MANUAL", 2: "ALTCTL", 3: "POSCTL", 4: "AUTO", 5: "ACRO",
    6: "OFFBOARD", 7: "STABILIZED", 8: "RATTITUDE",
}
# sub-modes of AUTO; QGC renders these as "Hold", "Mission", "Return" ...
PX4_CUSTOM_SUB_MODE_AUTO = {
    1: "READY", 2: "TAKEOFF", 3: "HOLD", 4: "MISSION", 5: "RTL",
    6: "LAND", 7: "RTGS", 8: "FOLLOW_TARGET", 9: "PRECLAND",
}

_lock = threading.Lock()
_conn: mavutil.mavfile | None = None
_tokens: dict[str, float] = {}


# --------------------------------------------------------------------------- #
# link handling
# --------------------------------------------------------------------------- #
def _link() -> mavutil.mavfile:
    """Open (once) and return the MAVLink connection."""
    global _conn
    if _conn is not None:
        return _conn
    conn = mavutil.mavlink_connection(LINK, source_system=GCS_SYSID,
                                      source_component=190)
    if LINK.startswith("udpin"):
        # point outbound traffic at PX4 straight away; otherwise pymavlink
        # sends to the 0.0.0.0 bind address and we hear our own heartbeat
        conn.destination_addr = ("127.0.0.1", PX4_PORT)

    deadline = time.time() + 10.0
    msg = None
    while time.time() < deadline:
        m = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
        if m is not None and m.get_srcSystem() != GCS_SYSID:
            msg = m
            break
    if msg is None:
        conn.close()
        raise RuntimeError(
            f"no vehicle heartbeat on {LINK}. Is PX4 SITL running? "
            "(check: pgrep -x px4)"
        )
    conn.target_system = msg.get_srcSystem()
    conn.target_component = msg.get_srcComponent()
    _conn = conn
    return conn


def _recv_vehicle(msg_type: str, timeout: float = 3.0):
    """Receive a message that did not come from this GCS instance."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = _recv(msg_type, timeout=min(1.0, timeout))
        if msg is not None and msg.get_srcSystem() != GCS_SYSID:
            return msg
    return None


def _recv(msg_type: str, timeout: float = 3.0):
    conn = _link()
    return conn.recv_match(type=msg_type, blocking=True, timeout=timeout)


def _mode_name(hb) -> str:
    main = (hb.custom_mode >> 16) & 0xFF
    sub = (hb.custom_mode >> 24) & 0xFF
    name = PX4_CUSTOM_MAIN_MODE.get(main, f"MAIN_{main}")
    if main == 4 and sub:
        return f"{name}.{PX4_CUSTOM_SUB_MODE_AUTO.get(sub, sub)}"
    return name


def _is_armed(hb) -> bool:
    return bool(hb.base_mode & PX4_ARMED)


# --------------------------------------------------------------------------- #
# confirmation tokens
# --------------------------------------------------------------------------- #
def _fingerprint(action: str, payload: dict) -> str:
    raw = action + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _issue(action: str, payload: dict) -> dict:
    token = _fingerprint(action, payload)
    _tokens[token] = time.time() + TOKEN_TTL
    return {
        "status": "confirmation_required",
        "token": token,
        "expires_in": TOKEN_TTL,
        "action": action,
        "payload": payload,
        "hint": "call again with confirm_token=<token> within 60 s",
    }


def _consume(action: str, payload: dict, token: str | None) -> dict | None:
    if not token:
        return _issue(action, payload)
    expiry = _tokens.pop(token, None)
    if expiry is None:
        return {"status": "rejected", "reason": "unknown or already used token"}
    if time.time() > expiry:
        return {"status": "rejected", "reason": "token expired"}
    if token != _fingerprint(action, payload):
        return {"status": "rejected", "reason": "token does not match this request"}
    return None


def _write_guard() -> dict | None:
    if not ALLOW_WRITE:
        return {"status": "rejected",
                "reason": "write tools disabled (set UAV_MCP_ALLOW_WRITE=1)"}
    return None


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #
mcp = FastMCP("uav")


@mcp.tool()
def get_status() -> dict:
    """Vehicle heartbeat: armed state, flight mode, system status."""
    with _lock:
        hb = _recv_vehicle("HEARTBEAT", 3.0)
    if hb is None:
        return {"error": "no heartbeat"}
    return {
        "system_id": hb.get_srcSystem(),
        "armed": _is_armed(hb),
        "mode": _mode_name(hb),
        "custom_mode": hb.custom_mode,
        "system_status": hb.system_status,
        "autopilot": hb.autopilot,
        "mavlink_version": hb.mavlink_version,
    }


@mcp.tool()
def get_position() -> dict:
    """Global position / altitude and local NED position."""
    with _lock:
        gpi = _recv("GLOBAL_POSITION_INT")
        lpn = _recv("LOCAL_POSITION_NED")
    out: dict[str, Any] = {}
    if gpi:
        out.update({
            "lat_deg": gpi.lat / 1e7,
            "lon_deg": gpi.lon / 1e7,
            "alt_msl_m": gpi.alt / 1000.0,
            "alt_relative_m": gpi.relative_alt / 1000.0,
            "heading_deg": gpi.hdg / 100.0 if gpi.hdg != 65535 else None,
        })
    if lpn:
        out.update({
            "north_m": round(lpn.x, 3), "east_m": round(lpn.y, 3),
            "down_m": round(lpn.z, 3),
            "v_north": round(lpn.vx, 3), "v_east": round(lpn.vy, 3),
            "v_down": round(lpn.vz, 3),
        })
    return out or {"error": "no position message"}


@mcp.tool()
def get_attitude() -> dict:
    """Roll / pitch / yaw in degrees, plus body rates."""
    with _lock:
        att = _recv("ATTITUDE")
    if att is None:
        return {"error": "no attitude message"}
    import math
    return {
        "roll_deg": round(math.degrees(att.roll), 2),
        "pitch_deg": round(math.degrees(att.pitch), 2),
        "yaw_deg": round(math.degrees(att.yaw) % 360.0, 2),
        "roll_rate": round(att.rollspeed, 4),
        "pitch_rate": round(att.pitchspeed, 4),
        "yaw_rate": round(att.yawspeed, 4),
    }


@mcp.tool()
def get_battery() -> dict:
    """Battery voltage / current / remaining percentage."""
    with _lock:
        bat = _recv("BATTERY_STATUS")
    if bat is None:
        return {"error": "no battery message"}
    cells = [v for v in bat.voltages if v not in (0, 65535)]
    return {
        "remaining_pct": None if bat.battery_remaining == -1 else bat.battery_remaining,
        "voltage_v": round(sum(cells) / 1000.0, 2) if cells else None,
        "current_a": None if bat.current_battery == -1 else bat.current_battery / 100.0,
        "cell_count": len(cells),
    }


@mcp.tool()
def list_parameters(name_filter: str = "", limit: int = 50) -> dict:
    """List vehicle parameters (substring match, case insensitive)."""
    conn = _link()
    with _lock:
        conn.param_fetch_all()
        found: list[dict] = []
        deadline = time.time() + 6.0
        needle = name_filter.upper()
        while time.time() < deadline and len(found) < limit:
            msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=1.0)
            if msg is None:
                continue
            if needle and needle not in msg.param_id.upper():
                continue
            found.append({
                "name": msg.param_id,
                "value": round(msg.param_value, 6),
                "type": msg.param_type,
            })
    return {"count": len(found), "parameters": found, "filter": name_filter}


@mcp.tool()
def get_parameter(name: str) -> dict:
    """Read a single parameter by exact name."""
    conn = _link()
    with _lock:
        conn.mav.param_request_read_send(
            conn.target_system, conn.target_component, name.encode(), -1)
        deadline = time.time() + 4.0
        while time.time() < deadline:
            msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=1.0)
            if msg and msg.param_id == name:
                return {"name": msg.param_id, "value": round(msg.param_value, 6),
                        "type": msg.param_type}
    return {"error": f"parameter {name} not found"}


@mcp.tool()
def get_mission() -> dict:
    """Download the mission stored on the vehicle (waypoint list)."""
    conn = _link()
    with _lock:
        conn.mav.mission_request_list_send(conn.target_system, conn.target_component, 0)
        cnt = conn.recv_match(type="MISSION_COUNT", blocking=True, timeout=5.0)
        if cnt is None:
            return {"error": "no MISSION_COUNT (mission protocol unsupported?)"}
        items = []
        for seq in range(cnt.count):
            conn.mav.mission_request_int_send(
                conn.target_system, conn.target_component, seq, 0)
            item = conn.recv_match(type="MISSION_ITEM_INT", blocking=True, timeout=3.0)
            if item is None:
                break
            items.append({
                "seq": item.seq,
                "command": item.command,
                "frame": item.frame,
                "lat_deg": item.x / 1e7,
                "lon_deg": item.y / 1e7,
                "alt_m": item.z,
                "param1": item.param1,
            })
            conn.mav.mission_ack_send(
                conn.target_system, conn.target_component, 0)
    return {"count": len(items), "items": items}


@mcp.tool()
def set_parameter(name: str, value: float, confirm_token: str | None = None) -> dict:
    """Set a parameter. Two-phase: first call returns a token, second applies."""
    denied = _write_guard()
    if denied:
        return denied

    payload = {"name": name, "value": value}
    pending = _consume("set_parameter", payload, confirm_token)
    if pending:
        return pending

    conn = _link()
    with _lock:
        hb = _recv_vehicle("HEARTBEAT", 2.0)
        if hb and _is_armed(hb) and not FORCE_WRITE:
            return {"status": "rejected", "reason": "vehicle is armed"}

        current = None
        conn.mav.param_request_read_send(
            conn.target_system, conn.target_component, name.encode(), -1)
        msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=3.0)
        if msg and msg.param_id == name:
            current = msg.param_value

        conn.mav.param_set_send(
            conn.target_system, conn.target_component,
            name.encode(), float(value),
            mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

        deadline = time.time() + 5.0
        while time.time() < deadline:
            msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=1.0)
            if msg and msg.param_id == name:
                return {
                    "status": "applied",
                    "name": name,
                    "previous": current,
                    "value": round(msg.param_value, 6),
                }
    return {"status": "timeout", "name": name, "requested": value}


@mcp.tool()
def arm(confirm_token: str | None = None) -> dict:
    """Arm the vehicle (PROPS MAY SPIN). Two-phase confirmation required."""
    denied = _write_guard()
    if denied:
        return denied

    pending = _consume("arm", {}, confirm_token)
    if pending:
        return pending

    conn = _link()
    with _lock:
        conn.mav.command_long_send(
            conn.target_system, conn.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1, 0, 0, 0, 0, 0, 0)
        ack = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=5.0)
        hb = _recv_vehicle("HEARTBEAT", 2.0)
    return {
        "status": "sent",
        "ack_result": ack.result if ack else None,
        "armed": _is_armed(hb) if hb else None,
    }


@mcp.tool()
def disarm(confirm_token: str | None = None) -> dict:
    """Disarm the vehicle. Two-phase confirmation required."""
    denied = _write_guard()
    if denied:
        return denied

    pending = _consume("disarm", {}, confirm_token)
    if pending:
        return pending

    conn = _link()
    with _lock:
        conn.mav.command_long_send(
            conn.target_system, conn.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            0, 0, 0, 0, 0, 0, 0)
        ack = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=5.0)
        hb = _recv_vehicle("HEARTBEAT", 2.0)
    return {
        "status": "sent",
        "ack_result": ack.result if ack else None,
        "armed": _is_armed(hb) if hb else None,
    }


@mcp.tool()
def describe_environment() -> dict:
    """What this server is connected to and which capabilities are enabled."""
    return {
        "link": LINK,
        "write_enabled": ALLOW_WRITE,
        "force_write_while_armed": FORCE_WRITE,
        "tools": ["get_status", "get_position", "get_attitude", "get_battery",
                  "list_parameters", "get_parameter", "get_mission",
                  "set_parameter", "arm", "disarm"],
        "writes_need_token": True,
    }


def main() -> None:
    if os.environ.get("UAV_MCP_SELFTEST") == "1":
        # bypass the transport: useful for smoke tests without an MCP client
        print(json.dumps({
            "status": get_status(),
            "position": get_position(),
            "attitude": get_attitude(),
            "battery": get_battery(),
            "env": describe_environment(),
        }, indent=2, ensure_ascii=False))
        return
    mcp.run()


if __name__ == "__main__":
    main()
