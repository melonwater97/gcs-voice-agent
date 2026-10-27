# uav-mcp

WSL2 里跑 PX4 SITL，Windows 上跑 QGC，再给 LLM 一个安全的 MAVLink 接口。

| 文件 | 作用 |
|---|---|
| `activate.sh` | 一键环境：ROS 2 Humble + px4_msgs + 仿真/桥接/MCP 命令 |
| `qgc_bridge.py` | WSL ↔ Windows 的 MAVLink UDP 桥，解决 WSL2 NAT |
| `server.py` | MCP server（读为主，写操作两段式确认） |
| `mission.py` | 闭环 demo：上传航线 → 解锁 → 起飞 → 沿航线飞 → RTL → 落地（**绕过 MCP**，见第 6 节） |
| `tools/mavlink_probe.py` | 不带依赖的 MAVLink 连通性探针 |
| `tools/mcp_smoke.py` | 走真实 stdio JSON-RPC 的 MCP 冒烟测试 |
| `tools/plot_mission.py` | 把 `out/mission_demo.csv` 画成 3 联图（QGC 已能看到轨迹，通常不需要） |

## 1. 环境

```bash
# 装一次
install -m 644 activate.sh /opt/uav-sim/activate.sh
grep -q uav-sim/activate.sh ~/.bashrc || \
  echo '[ -f /opt/uav-sim/activate.sh ] && source /opt/uav-sim/activate.sh' >> ~/.bashrc

# 之后每个新终端自动生效；手动加载：source /opt/uav-sim/activate.sh
```

自动加载后有：`uavsim`、`px4sim`、`xrce`、`fmu`、`qgc-bridge`、`qgc-ip`、`uavmcp`。

## 2. 起仿真

```bash
uavsim start              # 后台 tmux，无界面
HEADLESS=0 uavsim start   # 带 Gazebo 窗口（WSLg）
uavsim attach             # 进 PX4 shell
uavsim log                # 看日志
uavsim stop
```

## 3. 让 QGC 连上

WSL2 是 NAT，PX4 的 MAVLink 只在 localhost 且**发往 127.0.0.1:14550**，
Windows 侧收不到。所以用一个桥：

```bash
qgc-bridge          # 常驻；-v 打印流量统计
qgc-ip              # 打印该填进 QGC 的地址
```

然后在 QGC 里：

```
Application Settings -> Comm Links -> Add
  Name:   PX4-SITL
  Type:   UDP
  Server Address: 172.21.121.72      <- qgc-ip 输出的地址
  Port:   14550
  [x] Automatically Connect on Start
```

地址会随 WSL 重启变化，重连前先跑一次 `qgc-ip`。

> 为什么不是 14550/14540 直连：PX4 v1.17 的 Normal 链路是
> `local 18570 -> remote 14550`，而且默认 `MAV_0_BROADCAST=0`（只允许 localhost）。
> 桥正好坐在 127.0.0.1:14550 上接管这段流量。

## 4. MCP

```bash
uavmcp           # 只读
uavmcp-rw        # 允许写（仍需逐次确认 token）
uavmcp-check     # 不走协议，直接打一遍工具，用于排错
```

客户端配置（Claude Desktop / DSH 等，`command` 走 wsl）：

```json
{
  "mcpServers": {
    "uav": {
      "command": "wsl",
      "args": ["-e", "bash", "-lc",
               "cd /mnt/d/project/LK/llmspeech-recognition/uav && python3 server.py"]
    }
  }
}
```

### 工具

| 工具 | 类型 | 说明 |
|---|---|---|
| `get_status` | 读 | 解锁状态、飞行模式 |
| `get_position` | 读 | 经纬度、高度、NED 位置与速度 |
| `get_attitude` | 读 | 姿态角与角速度 |
| `get_battery` | 读 | 电压、电流、剩余电量 |
| `list_parameters` | 读 | 参数列表（可过滤） |
| `get_parameter` | 读 | 单个参数 |
| `get_mission` | 读 | 下载机载航线 |
| `set_parameter` | 写 | 两段式确认 |
| `arm` / `disarm` | 写 | 两段式确认，默认禁用 |
| `describe_environment` | 读 | 当前链路与能力开关 |

### 安全模型

1. **默认只读**：`UAV_MCP_ALLOW_WRITE=1` 才启用写工具。
2. **两段式**：第一次调用返回 8 位 token（60 秒有效），第二次带着 token 才执行。
3. **解锁写保护**：飞行器已解锁时拒绝改参数，除非 `UAV_MCP_FORCE_WRITE=1`。
4. **没有起飞/上传航线**：这两个动作离「一句话就飞走」太近，刻意不做。

## 5. 排错

| 现象 | 检查 |
|---|---|
| QGC 一直 `Disconnected` | `qgc-ip` 的地址变了没；`qgc-bridge` 在跑没；`ss -lunp \| grep 14550` |
| MCP 报 `no MAVLink heartbeat` | 仿真没起，或 14540 被别的进程占了（`ss -lunp \| grep 14540`） |
| 桥日志一直 "waiting for a QGC peer" | 旧版桥的毛病：QGC 在收到第一个心跳前不发包，双方死等。现在桥会自动往 WSL 网关（Windows 侧）发，无需人工干预；可用 `--host` 手动指定 |
| `uavsim start` 后没反应 | `uavsim log`，多半是上次的 px4 进程没退干净 |

## 6. 闭环 demo（mission.py）

在 SITL 里跑一遍完整闭环，遥测记 5 Hz 到 `out/mission_demo.csv`：

```bash
cd /mnt/d/project/LK/llmspeech-recognition/uav
python3 mission.py
```

航线写死为 4 个航点（TAKEOFF + 3×WAYPOINT），12 m 巡航，边长约 66 m，飞完切
`AUTO.RTL` 返航、落地自动上锁。实测全程约 126 秒。

**三个必须知道的限制：**

1. **它不经过 MCP**，是直连 `udpin:14540` 的脚本。它证明的是「飞机侧闭环能跑」，
   不是「AI 闭环」——决策是 `if seq == last_wp` 而不是模型判断。
2. **`MAV_CMD_NAV_RETURN_TO_LAUNCH` 不能作为任务项**（PX4 回 `ACK=3`），所以返航靠
   模式切换触发。这是试出来的结论，没有确认是帧类型/参数问题还是白名单限制。
3. **会覆盖飞控里已有的航线**（QGC Plan 视图会同步变成这 4 个点），演示前先在 QGC 里
   备份原航线。

> 真机上不要跑这个脚本。起飞/上传航线没有走人工确认闸门，见主仓
> `docs/` 下放飞前检查相关文档。

## 7. 替代方案：mirrored 网络

如果不想维护 IP，可把 WSL 改成镜像网络（需要 `wsl --shutdown` 重启 WSL）：

```ini
# C:\Users\<you>\.wslconfig
[wsl2]
networkingMode=mirrored
```

之后 WSL 与 Windows 共享 localhost，QGC 用默认的 127.0.0.1:14550 即可，桥可以不要。
代价：改动全局网络行为，VPN/代理环境下偶有冲突，改完要重启 WSL。
