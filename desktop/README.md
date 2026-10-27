# 领航语音助手

支持本地 Web 和 Electron 桌面模式，React 界面使用 Pi 1.4.0 SDK 运行对话。主界面展示会话、工具轨迹与工具详情；模型、麦克风、知识库、MCP、Skills 和工作流在设置中管理。源码位于 `desktop/` 目录。

## 启动开发版

Node.js 22.19 或更新版本：

```powershell
cd desktop
npm install
npm run dev
```

`npm run dev` 默认启动 Web 界面及本机 Agent 服务。打开 [本地助手](http://127.0.0.1:5180/)。`npm run dev:web` 是同一 Web 启动方式的别名：

```powershell
npm run dev:web
```

Web 模式使用本地数据目录 `desktop/.data`。配置通过本机开发密钥进行 AES-GCM 加密，API 凭据在界面中以掩码显示。Web 模式当前仅供本机访问。

## 首次配置

在「设置 → 模型服务」中填写模型服务地址、模型名及可选 API Key。模型需要支持工具调用。服务不可访问时仍可打开对话界面，但不能与模型对话。

在「设置 → 语音输入」中配置可运行 `funasr`、`numpy`、`sounddevice` 和 `soundfile` 的 Python 环境。选择已下载的 SeACo Paraformer 模型目录；模型本身较大，需在首次识别前下载并配置。浏览器或桌面录音经过本机 Python 进程采集，识别完成后交由 Pi 进入同一会话。系统声音设置或麦克风设置可更换设备。

「加载并检查已保存 ASR 模型」会在常驻 Python 进程中加载模型与热词，后续录音复用该模型。加载检查不采集麦克风音频。

在「设置 → MCP 服务」中管理地面站工具。已有 `python_tasks` 会自动迁移为 Python MCP → 地面站 HTTP；新配置可点击「添加地面站服务」。点击服务行的「配置」齿轮，在 YAML 中填写 `ground_station.base_url`、超时和模拟标记，保存时由 Python 校验；运行中的服务会自动重启以加载配置，暂停的服务保持暂停。服务启停与其他 MCP 一样统一管理。连接测试分别显示 MCP 连接和 HTTP 可达性，不发送创建、上传或执行请求。

配置模板见 [`config/groundstation-mcp.yaml`](../config/groundstation-mcp.yaml)。Web 实际配置保存在 `desktop/.data/mcp/groundstation.yaml`，桌面版位于用户数据目录下的 `data/mcp/groundstation.yaml`，启动路径由系统自动定位。配置不含 API 令牌明文；有鉴权时，在服务高级配置的环境变量中设置 `LH_GCS_TOKEN`，并保持 YAML 中 `token_env: LH_GCS_TOKEN`。该环境变量随应用设置加密保存。

本地地面站模拟器独立启动：

```powershell
python -m simulator.http_server
```

默认基础地址为 `http://127.0.0.1:18003`，YAML 设置 `simulation: true`。它不是 MCP 服务，由 Python MCP 通过 HTTP 请求。切换真实地面站时修改地址，并将 `simulation` 改为 `false`，该标记不会自动选择地址。已有本地 TaskStore 的历史不会转成 HTTP 模拟器任务，旧任务需要在原后端核对，不能直接在新后端上传或执行。

也可独立启动自建 MCP（stdio）：

```powershell
python -m integrations.mcp_server --config config/groundstation-mcp.yaml
```

服务调用 `POST /api/tasks`，采用 `TASK_COMMAND` / `TASK_RESULT` 与协议版本 `2.0.0`。创建、上传、执行相互独立。HTTP 请求日志持久化，相同未确认操作重试时沿用原消息编号；超过 24 小时拒绝自动重试。模板配置相对目录下的 `requests/` 保存独立启动时的请求日志，Web/桌面通过启动参数将其放到用户数据目录。双方需按[接口草案](../docs/地面站对接接口.md)联调。

## 初版功能

- Pi SDK 提供模型会话、工具调用循环、会话内轨迹事件及自定义领域工具；MCP 连接由应用运行管理器持有，发现的工具以 Pi 自定义工具接入，同一连接供对话、工作流和管理界面共用，不再为每轮对话重复启动服务。普通模型会话默认不启用本机 shell、文件编辑等编码工具。
- 知识库接受 Markdown、TXT 和可提取文本的 PDF。使用本机关键词索引，不上传整个资料库；实际引用片段会进入模型请求并可从工具详情查看。扫描 PDF OCR、Word 文档、向量嵌入和重排暂未加入。
- MCP 管理使用可展开的服务列表，展示真实运行状态与工具数量；点击工具在详情面板查看说明、参数及 Schema。配置和高级连接信息从齿轮进入。统一启动/暂停入口直接生效并保存，下次应用启动恢复已启动的服务。
- 本地 stdio 的启动/暂停对应真实进程生命周期；远程 MCP 的连接/断开只管理客户端会话，不关闭远程服务。暂停时先阻止新调用，等待已有调用完成后释放连接。连接异常显示失败状态，可点击启动重试；每次任务结束只释放 Pi 会话，不关闭共享 MCP。应用正常退出会关闭 MCP 连接。
- 测试运行中的服务复用当前连接；测试暂停的服务临时连接、读取工具后关闭，不改变启停状态，也不执行业务操作。最近发现的工具列表会缓存，暂停后仍可查看并标为当前不可调用。地面站的 HTTP 可达性单独检测。
- `python_tasks` 直接启动 `integrations/mcp_server.py --config ...`，向 Pi 提供业务参数并根据 YAML 调用 HTTP。旧 JS 适配层已移除。不传 `--config` 时保留原 TaskStore 模拟和旧版信封接口；`--agent-tools` 可在此旧模拟模式下提供简化参数，兼容已有 Python CLI。
- Skills 读取具有 YAML `name` 和 `description` 的 `SKILL.md`，支持预览、启停和对话加载；它不自动运行 Skill 目录里的脚本。
- 工作流支持参数表单和顺序步骤，包括 MCP 工具、知识检索、Skill、受限的 Agent 子步骤及输出。失败后停止并留下轨迹；恢复会话不重放操作。拖拽编排、条件、循环、并行和持久断点续跑暂未加入。
- 轨迹视图提供输入/模型/工具三条时间轴、轮次与模型步骤、可折叠调用层级、全文搜索和类型/状态筛选。时间轴可滚轮缩放、拖动筛选、右键清除或拖动平移，点击记录与时间轴片段可联动查看详情。
- 详情包含概述、参数、结果、调用时 Schema、计时；模型消息还可查看实际采集的首字延迟、生成时间和用量。上下文记录可检查本轮实际使用的系统提示词和工具目录。未采集的历史计时显示未记录。
- 长会话列表使用固定行高虚拟滚动，搜索覆盖完整历史；流式更新跟随末尾，向上阅读后暂停跟随。会话和轨迹支持导出 JSON，重新打开历史不会重放工具调用。浏览器回归覆盖了 4,500 条轨迹记录的搜索、滚动与选择。

## Web 生产构建

```powershell
npm run build
npm start
```

`npm start` 在 [127.0.0.1:18301](http://127.0.0.1:18301/) 提供构建后的页面和 API，无需启动 Vite。语音识别使用后台所在电脑的麦克风、Python 环境和 ASR 模型。

Web 回归检查可运行 `npm run test:web`。该检查使用隔离的临时数据、本地模拟模型和真实 Pi SDK，覆盖设置页面、工具调用、轨迹详情和刷新恢复，不访问真实地面站或麦克风。

`npm test` 同时覆盖配置脱敏和工具结果解析。设置 `LH_TEST_PYTHON` 为项目 Python 环境的可执行文件后，还会验证原 Python MCP 的创建、上传、执行、跨进程状态恢复及独立 HTTP 模拟器联调；未设置时跳过这一环境依赖检查。

## Windows EXE

免安装单文件 EXE：

```powershell
npm run package:portable
```

产物为 `release/Linghang-Assistant-Portable-0.1.0-x64.exe`，双击启动，不需要执行安装向导。运行时会在系统临时目录展开应用，退出后清理；配置和历史仍保存至 `%APPDATA%/领航语音助手/data`，可接续已经迁移的桌面配置。Python 环境和 ASR 模型依旧使用本机配置的外部路径。

需要安装向导时：

```powershell
npm run package:win
```

安装程序输出到 `release/Linghang-Assistant-Setup-0.1.0-x64.exe`，可执行目录版位于 `release/win-unpacked/`。目录版首次在本项目位置运行时，会自动读取 `desktop/.data` 的 Web 配置和历史，将其重新加密保存至 `%APPDATA%/领航语音助手/data`；已存在的桌面配置不被覆盖。安装版随后使用同一桌面数据目录。密钥与会话不会打入公开安装包。

桌面版附带原 Python MCP 服务和 ASR 适配代码，自动将 MCP 路径切换到打包资源目录。Python 环境和大型 ASR 模型沿用本机已配置的外部路径；迁移到其他电脑时需另行准备这些依赖。

开发时 `npm run dev:electron` 启动桌面窗口；`npm run dev` 仍启动 Web。桌面产物检查可运行 `node scripts/check-desktop.mjs`，检查实际 EXE 启动、模型连接、Python MCP 发现、语音环境和轨迹界面。

协议接口、会话内容与知识库都以本机进程为边界。远程 MCP 地址与模型服务由用户在设置中提供；地面站 HTTP 地址保存在自建 MCP 的 YAML 中。
