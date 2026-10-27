#!/usr/bin/env python3
"""UDP bridge between PX4 SITL (inside WSL) and QGroundControl (on Windows).

Why this exists
---------------
PX4 SITL starts its "Normal" MAVLink link as::

    [mavlink] mode: Normal, ... on udp port 18570 remote port 14550
    [mavlink] MAVLink only on localhost (set MAV_{i}_BROADCAST = 1 ...)

i.e. PX4 *sends to* 127.0.0.1:14550 and only accepts localhost peers.
WSL2 runs in NAT mode, so a QGC instance on Windows cannot reach either
127.0.0.1:14550 (that is Windows' own loopback) or the WSL subnet.

This bridge sits at 127.0.0.1:14550 inside WSL, where PX4 already sends,
and relays both directions to/from QGC on the Windows side.

    PX4 127.0.0.1:18570  <--->  bridge :14550  <--->  QGC (Windows host)

Usage
-----
    python3 qgc_bridge.py            # listen on 0.0.0.0:14550
    python3 qgc_bridge.py -v         # verbose

Then in QGC:  Application Settings -> Comm Links -> Add
    Type: UDP
    Server Address: <WSL ip>        (ip -4 addr show eth0)
    Port: 14550
"""

import argparse
import select
import socket
import subprocess
import sys
import time

PX4_ADDR = ("127.0.0.1", 18570)   # PX4 "Normal" link local port
LISTEN = ("0.0.0.0", 14550)       # where PX4 sends its heartbeats
PEER_TTL = 120.0                  # forget a Windows peer after this long


def default_gateway() -> str | None:
    """The Windows host as seen from inside WSL (NAT gateway)."""
    try:
        out = subprocess.check_output(["ip", "route", "show", "default"],
                                      stderr=subprocess.DEVNULL).decode()
        for line in out.splitlines():
            parts = line.split()
            if "via" in parts:
                return parts[parts.index("via") + 1]
    except Exception:
        pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--listen-port", type=int, default=14550)
    ap.add_argument("--px4-port", type=int, default=18570)
    ap.add_argument("--host", default=None,
                    help="Windows host address (default: the WSL gateway)")
    ap.add_argument("--qgc-port", type=int, default=14550)
    args = ap.parse_args()

    px4 = ("127.0.0.1", args.px4_port)
    host = (args.host, args.qgc_port) if args.host else None
    if host is None:
        gw = default_gateway()
        host = (gw, args.qgc_port) if gw else None

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.listen_port))
    sock.setblocking(False)

    peer = None          # last Windows-side address
    peer_seen = 0.0
    n_px4 = n_ext = 0
    last_nag = 0.0

    print(f"[bridge] listening 0.0.0.0:{args.listen_port}, PX4 at {px4[0]}:{px4[1]}")
    print(f"[bridge] QGC target: {host[0]}:{host[1]}" if host else
          "[bridge] no QGC target: waiting for QGC to speak first")
    print("[bridge] Ctrl-C to stop")

    try:
        while True:
            r, _, _ = select.select([sock], [], [], 1.0)
            if not r:
                continue

            data, addr = sock.recvfrom(65535)

            if addr[0] in ("127.0.0.1", "::1"):
                # from PX4 -> QGC.
                # Send to the learned peer AND to the configured host, because
                # QGC waits for an incoming heartbeat before it sends anything
                # of its own. Without the second target both sides deadlock.
                n_px4 += 1
                targets = set()
                if peer and (time.time() - peer_seen) < PEER_TTL:
                    targets.add(peer)
                if host:
                    targets.add(host)
                for t in targets:
                    sock.sendto(data, t)
                if not targets and args.verbose and (time.time() - last_nag) > 5:
                    last_nag = time.time()
                    print("[bridge] PX4 packets arriving, no QGC target yet",
                          file=sys.stderr)
            else:
                # from QGC (Windows) -> forward to PX4
                n_ext += 1
                if peer != addr:
                    print(f"[bridge] QGC peer: {addr[0]}:{addr[1]}")
                peer, peer_seen = addr, time.time()
                sock.sendto(data, px4)

            if args.verbose and (n_px4 + n_ext) % 200 == 0:
                print(f"[bridge] px4->qgc={n_px4} qgc->px4={n_ext} peer={peer}")
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        print(f"\n[bridge] stopped: px4->qgc={n_px4} qgc->px4={n_ext}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
