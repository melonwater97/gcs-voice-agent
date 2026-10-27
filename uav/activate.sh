#!/usr/bin/env bash
# =============================================================================
# UAV simulation environment
#   PX4 SITL v1.17  +  Gazebo Harmonic  +  ROS 2 Humble  +  uXRCE-DDS
#   +  QGC bridge  +  uav-mcp
#
#   usage:  source /opt/uav-sim/activate.sh
# =============================================================================

export UAV_SIM_ROOT=/opt/uav-sim
export PX4_DIR="${UAV_SIM_ROOT}/PX4-Autopilot"
export ROS_WS="${UAV_SIM_ROOT}/ros_ws"
export XRCE_AGENT="${UAV_SIM_ROOT}/Micro-XRCE-DDS-Agent/build/MicroXRCEAgent"
export UAV_MCP_DIR=/mnt/d/project/LK/llmspeech-recognition/uav

# --- ROS 2 -------------------------------------------------------------------
if [ -f /opt/ros/humble/setup.bash ]; then
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
fi

# --- px4_msgs overlay --------------------------------------------------------
if [ -f "${ROS_WS}/install/setup.bash" ]; then
    # shellcheck disable=SC1091
    source "${ROS_WS}/install/setup.bash"
fi

# --- simulation --------------------------------------------------------------
# uavsim start [model] | stop | attach | log | status
uavsim() {
    local action="${1:-status}" model="${2:-gz_x500}"
    case "${action}" in
        start)
            tmux kill-session -t uavsim 2>/dev/null
            tmux new-session -d -s uavsim -x 220 -y 50 \
                "cd '${PX4_DIR}' && HEADLESS=${HEADLESS:-1} make px4_sitl ${model} 2>&1 | tee /tmp/px4_sitl.log"
            echo "[uavsim] started ${model} (HEADLESS=${HEADLESS:-1}); attach with: uavsim attach"
            ;;
        stop)   tmux kill-session -t uavsim 2>/dev/null; echo "[uavsim] stopped" ;;
        attach) tmux attach -t uavsim ;;
        log)    tail -n 60 /tmp/px4_sitl.log ;;
        status) tmux has-session -t uavsim 2>/dev/null && echo "[uavsim] running" || echo "[uavsim] stopped" ;;
        *)      echo "usage: uavsim {start [model]|stop|attach|log|status}" ;;
    esac
}

# px4sim [model] - run in the foreground (Ctrl-C to quit)
px4sim() {
    local model="${1:-gz_x500}"
    ( cd "${PX4_DIR}" && HEADLESS="${HEADLESS:-1}" make px4_sitl "${model}" )
}

# --- ROS 2 bridge ------------------------------------------------------------
# xrce [transport] [port]   uXRCE-DDS agent (PX4 <-> ROS 2)
xrce() { "${XRCE_AGENT}" "${1:-udp4}" -p "${2:-8888}"; }

# fmu [n]   list PX4 -> ROS 2 topics
fmu() { ros2 topic list | grep 'fmu/out' | head -"${1:-15}"; }

# --- QGC bridge --------------------------------------------------------------
# qgc-bridge [-v]   relay MAVLink between PX4 (WSL) and QGC (Windows)
qgc-bridge() { python3 "${UAV_MCP_DIR}/qgc_bridge.py" "$@"; }

# qgc-ip   the address to put into QGC's UDP comm link
qgc-ip() {
    local ip
    ip=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
    echo "QGC Comm Link ->  UDP   Server: ${ip}   Port: 14550"
}

# --- MCP ---------------------------------------------------------------------
# uavmcp        read-only server
# uavmcp-rw     writes enabled (still two-phase confirmed)
uavmcp()    { python3 "${UAV_MCP_DIR}/server.py" "$@"; }
uavmcp-rw() { UAV_MCP_ALLOW_WRITE=1 python3 "${UAV_MCP_DIR}/server.py" "$@"; }
# uavmcp-check  run the built-in self test against the running SITL
uavmcp-check() { UAV_MCP_SELFTEST=1 python3 "${UAV_MCP_DIR}/server.py"; }

alias px4dir='cd "${PX4_DIR}"'
alias uavdir='cd "${UAV_MCP_DIR}"'
alias simlog='tail -n 40 /tmp/px4_sitl.log'
alias bridgelog='tail -n 20 /tmp/qgc_bridge.log'

echo "[uav-sim] ROS_DISTRO=${ROS_DISTRO}  PX4_DIR=${PX4_DIR}"
echo "[uav-sim] uavsim | px4sim | xrce | fmu | qgc-bridge | qgc-ip | uavmcp"
