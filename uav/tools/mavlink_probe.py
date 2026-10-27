"""Minimal MAVLink v2 probe: send a HEARTBEAT, report what comes back.

No dependencies (no pymavlink). Used to verify that a MAVLink endpoint is
reachable across the Windows <-> WSL boundary.

    python mavlink_probe.py 172.21.121.72 14550
"""

import socket
import struct
import sys
import time

HEARTBEAT_MSGID = 0
HEARTBEAT_CRC_EXTRA = 50


def x25_crc(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def build_heartbeat(seq: int, sysid: int = 255, compid: int = 190) -> bytes:
    # payload: custom_mode(u32) type(u8) autopilot(u8) base_mode(u8)
    #          system_status(u8) mavlink_version(u8)
    payload = struct.pack("<IBBBBB", 0, 2, 12, 0, 4, 3)
    header = struct.pack(
        "<BBBBBBBHB", 0xFD, len(payload), 0, 0, seq & 0xFF, sysid, compid,
        HEARTBEAT_MSGID & 0xFFFF, HEARTBEAT_MSGID >> 16,
    )
    crc = x25_crc(header[1:] + payload)          # skip magic
    crc = x25_crc(bytes([HEARTBEAT_CRC_EXTRA]), crc)
    return header + payload + struct.pack("<H", crc)


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 14550

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    sock.bind(("0.0.0.0", 0))
    local = sock.getsockname()
    print(f"[probe] local {local[0]}:{local[1]} -> {host}:{port}")

    end = time.time() + 6.0
    seq = 0
    received = 0
    senders = set()
    msgids = {}

    while time.time() < end:
        sock.sendto(build_heartbeat(seq), (host, port))
        seq += 1
        # drain for a fixed slice - a busy stream never hits the socket timeout
        slice_end = time.time() + 0.5
        while time.time() < slice_end:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                break
            received += 1
            senders.add(addr)
            if len(data) >= 10 and data[0] == 0xFD:
                msgid = data[7] | (data[8] << 8) | (data[9] << 16)
                msgids[msgid] = msgids.get(msgid, 0) + 1
            elif len(data) >= 6 and data[0] == 0xFE:
                msgids["v1"] = msgids.get("v1", 0) + 1

    print(f"[probe] packets received: {received}")
    print(f"[probe] senders: {sorted(senders)}")
    top = sorted(
        ((k, v) for k, v in msgids.items() if isinstance(k, int)),
        key=lambda kv: -kv[1],
    )[:10]
    print(f"[probe] top msgids: {top}")
    print("[probe] RESULT:", "OK" if received else "NO RESPONSE")
    return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(main())
