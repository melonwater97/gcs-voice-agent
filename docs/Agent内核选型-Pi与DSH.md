# Agent 内核选型：Pi 与 DSH

日期：2026-10-26；状态：建议稿，待最小技术验证后确认。

## 1. 建议结论

对于“自己实现 React + Electron 产品、主窗口聚焦对话和工具轨迹、其他能力集中设置”的目标，建议以 Pi Coding Agent SDK 为内核，产品侧实现界面、资料管理和业务工作流，参考 dsh 的轨迹交互。

如果目标调整为“尽可能保留 dsh 的整套桌面界面、插件体系和设置，仅增加语音与地面站插件”，dsh 更值得优先验证。选择取决于希望复用多少成品功能，不能只根据内核体积或截图判断。

本次为代码与文档静态核对，未运行真实模型、MCP 端到端或 Electron 打包测试，不构成稳定性或性能结论。

## 2. 本次核对的本地版本

| 项目 | 本地位置 | 核对结果 |
| --- | --- | --- |
| Pi | `D:\miniconda3\node_modules\@earendil-works\pi-coding-agent` | package.json 为 `@earendil-works/pi-coding-agent` 1.4.0，包含 SDK、RPC、MCP 与扩展示例 |
| dsh | `D:\project\agent\deepseek-harness` | 根 package.json 为 0.2.0-rc.1；本地最新提交日期为 2026-09-28 |

本地 Pi 文档比部分旧教程的能力描述更完整。Pi 1.4.0 已依赖 `@earendil-works/pi-mcp`，提供 MCP 配置、连接与扩展入口，不能按“Pi 完全没有 MCP”评估接入成本。

本次读取 dsh 源码仅用于能力核对，不修改该仓库。线上主分支与本地版本可能不同，最终设计以锁定版本的文档、声明和验证结果为准。

## 3. 能力与成本比较

| 维度 | Pi 1.4.0 | dsh 本地版本 | 对本项目的影响 |
| --- | --- | --- | --- |
| 外部接入 | TypeScript SDK 可嵌入后台宿主，也有长期运行的 RPC 子进程模式 | SDK 客户端可启动运行时，通过 stdio JSON-RPC 驱动会话 | 两者都能外接 UI，Pi 不是唯一选择 |
| 现成桌面产品 | 本次核对的 coding-agent 包不提供本产品所需的现成桌面壳 | 已有 React Web 应用及 Electron 桌面壳 | 若整体保留 dsh UI，可以复用更多功能 |
| 工具轨迹 | 提供消息与工具生命周期事件，需要自行做持久化投影与 React 展示 | 已有轮次、步骤、时间概览、工具详情的轨迹 UI | 自绘几类轨迹不必绑定 dsh；完整复刻其行为成本更高 |
| MCP | 原生 MCP 支持，SDK 需显式装配相关扩展 | 有 MCP 客户端插件及配置机制 | 两者可复用协议层，均需本项目的地面站业务适配 |
| Skills | 有 Skills 资源发现、加载和扩展入口 | 有 Skills 提供方和 UI 相关模块 | 两者都能承载；导入、依赖状态和启停体验仍需产品设计 |
| 知识库 | 本次未核实有符合本 PRD 的成品知识库管理闭环 | 本次未核实有符合本 PRD 的成品知识库管理闭环 | 都需验证资料解析、索引、检索和引用，不能仅凭目录名认定已有 |
| 工作流 | 需实现本项目所定义的步骤模板和执行器，或评估合适扩展 | 已有脚本编排模块，可协调子 Agent | dsh 的模型脚本编排不等同于持久化业务流程编辑器 |
| 自建产品边界 | 可围绕 SDK 定义受控的领域工具与宿主接口 | 以 Cordis 插件、profile、bundle 和事件机制组织完整运行时 | 从零自建小产品时倾向 Pi；深度复用 dsh 时应接受其架构 |
| 稳定性与维护 | 固定版本，验证 SDK、事件和配置升级 | 官方声明开发者预览并可能发生不兼容变更；本地版本较线上滞后 | 不宣称任一方天然稳定，均要固定版本和回归关键链路 |
| 模型效果 | 取决于模型、提示词、工具定义和上下文 | 同样取决于这些因素 | 没有同任务实测，不能宣称某内核的中文或业务准确率更高 |

Pi 的 SDK、会话与自定义工具边界见 [官方 SDK 文档](https://github.com/earendil-works/pi/blob/v1.4.0/packages/coding-agent/docs/sdk.md)，MCP 能力见 [官方 MCP 文档](https://github.com/earendil-works/pi/blob/v1.4.0/packages/coding-agent/docs/mcp.md)。本地核对文件为上述安装目录下的 `docs/sdk.md`、`docs/mcp.md`、`docs/json.md` 和 `examples/sdk/14-codemode-mcp.ts`。

dsh 的桌面结构见 [官方桌面文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/desktop/README.md)，插件组合与运行时见 [架构文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)，现成轨迹能力见 [轨迹模块文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/ui-trajectory/README.md)。这些线上链接会随主分支变化，本次还核对了本地对应文件和 SDK、MCP、工作流模块说明。

## 4. 为什么本项目暂时倾向 Pi

用户准备自己实现主窗口及设置，主窗口也没有要求保留 dsh 的完整工作台布局。因此，主要复用对象是 Agent 运行能力，而不是已有应用的全部页面。Pi SDK 可以作为这一边界，UI 和业务配置由产品自己维护。

这个判断不意味着所有产品能力都应从零写：模型会话、MCP 协议连接、Skills 资源等复用 Pi；知识库导入与引用、可管理的设置、领域业务工作流和统一轨迹由应用实现。复用 dsh 的具体 UI 代码前，需要评估其 Session 投影、插件和组件依赖，不能假定轨迹模块是独立 React 控件。

如果评审要求首版完整拥有 dsh 同等级的轨迹时间轴、插件管理、复杂会话树、多 Agent、工作区与各种设置，则从 Pi 重建的总成本会增加，应重新比较以 dsh 为产品底座的路线。

## 5. Pi 接入时已经确认的细节

- 本地包提供 `createAgentSession()`、资源加载器、自定义工具和会话事件订阅。
- CLI 默认加载的 MCP 扩展不会自动出现在 SDK 会话中。本地 SDK 示例显式配置 `createMcpExtension()`，并调用 `session.bindExtensions()` 触发连接生命周期；codemode 和 tool_search 按所选暴露方式装配。
- 事件包含 `tool_execution_start`、`tool_execution_update` 和 `tool_execution_end`，以 `toolCallId` 关联；嵌套工具还可能带父调用编号。
- 单次低层 `agent_end` 不一定代表整个产品运行结束，仍可能发生自动恢复或排队工作；本地文档提供 `agent_settled` 表示自动工作已结束。
- Pi 的模型会话持久化不等于产品完整轨迹留存；ASR、检索、工作流和外部业务回执仍需应用记录。
- 进程分离用于生命周期与故障隔离，不自动限制文件、进程和网络权限；领域助手应显式控制工具和资源范围。

事件语义可对照 [官方事件流文档](https://github.com/earendil-works/pi/blob/v1.4.0/packages/coding-agent/docs/json.md)。开发以本地安装版本的类型声明与示例为准，不直接照搬其他版本教程。

## 6. 最小技术验证及改选条件

不先搭建两套完整产品。先使用 Pi 跑以下同一组验收任务，并把结果写入后续验证记录：

| 验证 | 通过条件 |
| --- | --- |
| 目标模型与领域工具 | 替换默认编码提示词和工具后，能完成缺参澄清、合法参数生成与结果归纳 |
| MCP 接入 | 连接现有业务 MCP 服务，发现三个工具并完成一次模拟业务调用 |
| 轨迹 | 能关联输入、模型消息、工具参数、结果、失败和实际可得的耗时 |
| 停止与恢复 | 停止后不继续调度；重开会话可恢复上下文及轨迹，不重发副作用请求 |
| Skills | 受控目录中的 Skill 可显式加载，且不依赖无关全局配置 |
| 桌面分发 | 后台宿主与 ASR 运行方式能在 Windows 包内启动，不要求用户自行安装全局 Pi 或进入开发环境 |

若出现无法解决的核心接入限制，或需求明确转向大量复用 dsh 成品模块，再用相同模型、工具和任务验证 dsh。比较自建代码量、关键能力覆盖、失败恢复与升级负担，不用一次演示的成功率代替系统判断。

本轮没有执行这些验证，也没有安装、升级或修改用户全局 Pi。

## 7. 先 PRD，再完成技术设计

建议顺序：PRD 0.1 明确用户路径和首版范围 → 主窗口与设置原型 → M0 内核验证 → 技术设计 → 分阶段实施。原型与 M0 可以在需求主体明确后并行推进，不需要等待一份面面俱到的长文档。

PRD 应定义知识库、MCP、Skills 和工作流分别让用户完成什么，尤其明确“工作流”是顺序模板、图形 DAG，还是模型自行规划。SDK/RPC、数据库和具体进程通信细节留在技术设计中，不让选型先锁死产品需求。

产品初稿见 [独立语音助手 PRD](独立语音助手PRD.md)。
