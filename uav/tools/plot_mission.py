"""Render out/mission_demo.csv into a 3-panel summary figure."""

import csv
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(HERE, "out", "mission_demo.csv")
PNG = os.path.join(HERE, "out", "mission_demo.png")

HOME = (47.397971, 8.546164)
WPS = [(HOME[0], HOME[1]),
       (HOME[0] + 0.00060, HOME[1]),
       (HOME[0] + 0.00060, HOME[1] + 0.00090),
       (HOME[0], HOME[1] + 0.00090)]


def main() -> None:
    rows = list(csv.DictReader(open(CSV)))
    t = [float(r["t"]) for r in rows]
    alt = [float(r["alt_rel"]) for r in rows]
    roll = [float(r["roll"]) for r in rows]
    pitch = [float(r["pitch"]) for r in rows]
    yaw = np.degrees(np.unwrap(np.radians([float(r["yaw"]) for r in rows])))
    phase = [r["phase"] for r in rows]

    lat0, lon0 = HOME
    east = [(float(r["lon"]) - lon0) * 111320 * math.cos(math.radians(lat0)) for r in rows]
    north = [(float(r["lat"]) - lat0) * 110540 for r in rows]

    rtl_start = next((t[i] for i, p in enumerate(phase) if p == "rtl"), None)

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16, 4.6))

    ax1.plot(t, alt, lw=1.8, color="#1f77b4")
    ax1.axhline(12, ls="--", lw=1, color="gray", label="mission alt 12 m")
    if rtl_start:
        ax1.axvline(rtl_start, ls=":", lw=1.5, color="#d62728")
        ax1.text(rtl_start + 2, max(alt) * 0.55, "RTL", color="#d62728", fontsize=10)
    ax1.set_xlabel("t [s]")
    ax1.set_ylabel("altitude AGL [m]")
    ax1.set_title("Altitude")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)

    ax2.plot(t, roll, lw=1.2, label="roll")
    ax2.plot(t, pitch, lw=1.2, label="pitch")
    ax2.plot(t, yaw, lw=1.0, alpha=0.6, label="yaw")
    if rtl_start:
        ax2.axvline(rtl_start, ls=":", lw=1.5, color="#d62728")
    ax2.set_xlabel("t [s]")
    ax2.set_ylabel("deg")
    ax2.set_title("Attitude")
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8, loc="upper right")

    i_rtl = next((i for i, p in enumerate(phase) if p == "rtl"), len(rows))
    ax3.plot(east[:i_rtl], north[:i_rtl], lw=1.6, color="#2ca02c", label="mission")
    if i_rtl < len(rows):
        ax3.plot(east[i_rtl - 1:], north[i_rtl - 1:], lw=1.8, color="#d62728", label="RTL")
    ax3.scatter([0], [0], marker="*", s=180, c="#d62728", zorder=5, label="home")
    for i, (la, lo) in enumerate(WPS[1:], start=1):
        e = (lo - lon0) * 111320 * math.cos(math.radians(lat0))
        n = (la - lat0) * 110540
        ax3.scatter([e], [n], marker="o", s=40, c="#1f77b4", zorder=5)
        ax3.annotate(f"WP{i}", (e, n), textcoords="offset points", xytext=(5, 5), fontsize=9)
    ax3.scatter([east[0]], [north[0]], marker="x", s=60, c="black", zorder=6, label="start")
    ax3.set_xlabel("east [m]")
    ax3.set_ylabel("north [m]")
    ax3.set_title("Ground track")
    ax3.axis("equal")
    ax3.grid(alpha=0.3)
    ax3.legend(fontsize=8, loc="lower right")

    fig.suptitle("PX4 SITL closed loop: mission -> RTL -> land", fontsize=13)
    fig.tight_layout()
    fig.savefig(PNG, dpi=130)
    print(f"[plot] {PNG}")
    print(f"[stats] samples={len(rows)} duration={t[-1]:.0f}s "
          f"max_alt={max(alt):.1f}m max|roll|={max(abs(r) for r in roll):.1f}deg "
          f"max|pitch|={max(abs(p) for p in pitch):.1f}deg")


if __name__ == "__main__":
    main()
