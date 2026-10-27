# 本地语音地面站助手

**2026-10-26 对接方案调整**：后续采用独立语音助手界面，助手自行控制录音和对话，助手侧 MCP 工具通过 HTTP 调用地面站业务服务。新的双方职责与接口草案见 [地面站对接接口 v2.0.0](docs/地面站对接接口.md)。本仓库提供独立的地面站 HTTP 模拟服务；语音助手到真实地面站的 HTTP 适配尚未实现。

独立桌面助手的产品范围与实施建议见 [PRD 0.1](docs/独立语音助手PRD.md)，内核比较见 [Pi 与 DSH 选型建议](docs/Agent内核选型-Pi与DSH.md)。

独立助手初版位于 [`desktop/`](desktop/README.md)，当前优先使用 **Web 模式**：在该目录执行 `npm run dev`，打开 [本地助手](http://127.0.0.1:5180/)。包含 Pi 1.4.0、工具轨迹、语音、知识库、MCP、Skills 和顺序工作流。

统一流程：`React 地面站 → Agent（ASR / LLM / 参数校验）→ MCP 本地任务模拟 → Agent 返回结果 → React 展示`。

React 界面就是地面站，直接请求 Agent，没有独立的地面站业务后端。Agent 负责 ASR、LLM 和 MCP，任务创建、保存与模拟执行在 Agent 侧完成，结果和参数状态返回界面。支持放飞前出任务和 C/U 型编队。LLM 后端可替换：默认指向本机 oMLX/Ornith，也可用 `--url` + `--model` 指向任何 OpenAI 兼容且支持工具调用的服务（本机 llama.cpp、Ollama、DeepSeek 等，见下文）。当前只做模拟，不连接真实飞控。

## 安装

使用 Python 3.11 或更高版本。在项目根目录执行：

```sh
conda create -n speech-asr python=3.11 pip -y
conda activate speech-asr
python -m pip install -r requirements.txt
```

文本模式只需以上核心依赖。使用录音或麦克风时额外安装：

```sh
python -m pip install -r requirements-speech.txt
modelscope download iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch --revision v2.0.4 --local-dir models/seaco-paraformer
```

保留完整模型目录，包括配置文件和权重；模型文件不提交到版本库。ASR 在 CPU 上运行，完成安装和模型下载后只读取本地模型。默认使用支持热词的 SeACo-Paraformer；原普通 Paraformer 保留在 `models/paraformer-large`。

**中文 Windows 说明**：`--mic` 依赖 Unix 终端 API，不可用（改用 `--file`，界面侧用服务端录音）；funasr 默认按系统区域编码（GBK）读取热词文件，本项目已在 `inputs/speech.py` 内把该读取的默认编码固定为 UTF-8，无需额外设置环境变量。

领域热词在 [`config/asr_hotwords.txt`](config/asr_hotwords.txt)，每行一个词，支持空行和 `#` 注释。当前包含 22 个领域词，覆盖任务类型、放飞模式、C/U 型编队、参数名称和坐标术语；不预设高度、坐标或任务名称。修改后重启 Agent 即生效，CLI 文件识别与麦克风输入也复用此配置。可通过 `--asr-hotwords-file /路径/词表.txt` 替换词表；需要关闭时指定空文件。普通 Paraformer 不支持热词，非空词表配合普通模型会明确报错，避免配置被静默忽略。接口依据见 [FunASR 官方文档](https://github.com/modelscope/FunASR/blob/main/docs/python_api.md)。

任务支持独立的 `task_name`，例如“新增名为晨曦的放飞前出任务，原地起飞，起飞高度50米”。名称长度为 1–64 字，显式空白名称会被拒绝；未指定时由服务生成“任务类型-编号前8位”。任务列表、参数明细、变化记录和上传文件都保存名称。可继续说“上传晨曦任务”“执行晨曦任务”；Agent 保留本次会话的名称/编号/状态目录，同名有多个时应先澄清，实际工具仍用唯一 `task_id`。

Web Agent 会把本会话已创建任务的名称加入每次录音的热词，辅助后续按名称上传和执行；首次创建时尚未知道名称，仍依赖模型本身识别。内部将热词转换为模型接受的分词文件，避免 `C型` 等中英文混合词被当作未知词；转写只清理汉字旁的 token 分隔空格，保留英文词间空格。热词不保证消除全部同音字错误，实测与限制见 [名称与热词联调记录](docs/task-names-hotwords-20261016.md)。

## 启动

以下命令均在项目根目录执行。文本 CLI 可独立运行，不需要启动地面站界面。**先准备一个 OpenAI 兼容、且支持工具调用（function calling）的模型服务**，三选一。

**① 本机 llama.cpp（Windows/Linux 推荐）**，另开一个终端：

```sh
# 模型已在本地时（推荐，不依赖网络）
llama-server -m /路径/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --jinja -c 8192 -np 1 --port 8080
# 或者让 llama-server 自己从 Hugging Face 拉取
llama-server -hf unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M --jinja -c 8192 -np 1 --port 8080
```

依赖与踩坑：用 `winget install llama.cpp` 装完后**必须新开一个终端**（PATH 只在进程启动时读取，旧窗口里会报 CommandNotFound）；`--jinja` 是工具调用的前提，不加会把工具调用当纯文本吐出、校验直接失败；`-c` 必须显式指定，否则会按模型的 262144 原生上下文分配 KV cache（还要乘槽位数），直接吃爆内存，`-np 1` 只保留一个槽位，单页面对话足够且更省内存。若工具调用失控（一次口令返回多个调用、`finish_reason=length`），说明这个版本太旧，换 GitHub 上的近期 nightly —— `winget` 装的是最新正式版，可能落后或带有已知的 Qwen3 工具调用解析缺陷。非默认后端统一用 `--url` + `--model` 接入，`--model` 只是标识，llama-server 不校验模型名：

```sh
python main.py assistant --url http://127.0.0.1:8080/v1/chat/completions --model qwen3-4b-instruct '新增放飞前出任务，模式为原地起飞'
```

**② macOS 本机 oMLX**（项目的默认后端，不加参数即指向它）：

```sh
/opt/homebrew/opt/omlx/libexec/bin/python scripts/serve_omlx_cached.py \
  --model-dir /Users/melonwater/project/OMLXProject/models --port 18000
```

当前 oMLX 0.6.3rc2 默认使用 2048-token 缓存块，无法命中本项目约 1600-token 的固定前缀。启动脚本在当前进程中适配为 512-token 分块，安装包保持原样；oMLX 升级后需重新验证。缓存配置与实测见 [缓存测试报告](docs/cache-performance.md)。

**③ DeepSeek 云端**（通过环境变量 `DEEPSEEK_API_KEY` 配置 Key）：

```sh
python main.py assistant --deepseek '新增C型编队飞行任务'
```

随后使用文本、录音或麦克风：

```sh
python main.py assistant '新增放飞前出任务，模式为原地起飞'        # 默认 oMLX；非默认后端加上 --url / --model
python main.py assistant --file /绝对路径/口令.wav
python main.py assistant --mic
```

麦克风模式按 `e` 开始录音，再按 `e` 结束并识别，按 `q` 退出，无需回车。每条口令都进入同一任务流程，ASR 模型和 MCP 连接在会话内复用。Paraformer 是整句识别模型，录音结束后才开始转写。麦克风录音需要交互式终端及系统权限；该模式依赖 Unix 终端 API，Windows 下不可用。

`main.py` 是项目统一入口。根目录的独立 `asr.py` 已移除，ASR 能力位于 `inputs/speech.py`。

## React 模拟地面站

真实地面站开发与联调请使用 [地面站对接接口文档](docs/地面站对接接口.md)，描述独立语音助手侧 MCP 工具调用地面站 HTTP 业务服务的新方案。以下 React 界面仍是当前本地模拟实现，可作为独立助手界面的改造基础；现有语音/文字输入、任务状态、健康诊断与内部工具契约见 [接口设计](docs/接口设计.md)。

### 地面站 HTTP 接口模拟服务

`simulator/http_server.py` 按接口文档 v2.0.0 提供 `POST /api/tasks`，用 `payload.type` 分别执行 `create_task`、`upload_task`、`execute_task`。它持久化任务状态和至少 24 小时的请求去重回执，可独立于 Agent 运行；默认监听 `127.0.0.1:18003`：

```sh
python -m simulator.http_server
```

使用 `GCS_SIM_HOST`、`GCS_SIM_PORT`、`GCS_SIM_STATE_PATH` 配置监听地址、端口和状态文件；使用 `GCS_AGENT_ID`、`GCS_STATION_ID` 配置请求/响应端点编号。默认状态文件为 `output/gcs_simulator_state.json`。当前助手 MCP 工具仍直接调用本地 `TaskStore`，不会自动请求这个 HTTP 模拟服务。

React 位于 `web/`，使用 React + Vite，需要 Node.js 22 或更高版本。先按上文启动模型服务，再在项目根目录启动 Agent（非默认后端同样用 `--url` + `--model`）：

```sh
python main.py web --url http://127.0.0.1:8080/v1/chat/completions --model qwen3-4b-instruct
```

Agent 监听 `127.0.0.1:18002`，启动时加载本地 ASR，并建立常驻 MCP 会话。在另一个终端启动界面：

```sh
cd web
npm install
npm run dev
```

打开 [本地地面站](http://127.0.0.1:5173/)。Vite 仅提供前端资源，将 `/api/*`、`/health` 请求直接代理到 Agent；不接收任务上传、保存任务或执行任务。更换 Agent 地址时使用 `AGENT_URL=http://目标地址:端口 npm run dev`。构建和本地预览可用 `npm run build`、`npm run preview`，预览使用相同代理规则；部署静态页面时也需将这些路径路由到 Agent。

React 界面仍不需要 `python main.py gcs` 或 `18001` 端口，且不会调用独立 HTTP 模拟服务。`simulator/backend.py` 是 MCP 使用的本地任务模拟模块：上传将任务写入 Agent 侧的 `output/TASK-*.json`，成功后更新为已上传；可用 `python main.py web --task-output-dir /绝对路径/任务目录` 修改保存位置，CLI 助手也支持该选项。执行只更新模拟状态。

接口统一使用 **`header + payload` JSON**，消息编号为 **`MSG-<UUID>`**，header 包含 msg_id、msg_type、version、src、dst。Agent 对地面站的所有响应统一使用 `msg_type: "AGENT_OUTPUT"`，响应种类由 `payload.type` 区分（`asr`、`task_result`、`clarify`、`error`、`state`、`health`、`record_start`、`record_status`）；错误信息在 `payload.message`。语音也使用 JSON，完整 WAV 文件经 Base64 编码放入 payload.audio_base64，payload.audio_format 为 wav；不再直接发送音频字节。文本放在 payload.text；请求与响应的对应关系由 HTTP 请求/响应本身确定，信封不再放关联编号，也不使用 X-Msg-Id。接口均为 POST：/api/voice、/api/text、/api/record、/api/record/status、/api/state、/health；录音控制通过 /api/record 的 `payload.type` 选择 `record_start` 或 `record_stop`，其余请求按接口定义携带 payload。成功和错误响应都使用同一信封。操作 payload 只含本次结果，完整任务列表和变化记录通过 /api/state 查询。内部 MCP 工具的业务参数和结果也采用该格式，MCP JSON-RPC 与模型提供方协议保持其标准格式。完整字段与示例见上述两份接口文档。

- 点击「点击说话」让 Agent 开始监听麦克风，再点击「结束并发送」结束录音并识别。录音在服务端完成，浏览器不需要麦克风权限；单次录音最多 90 秒，可用 `python main.py web --record-max-seconds` 修改，到时服务端自动结束。界面用 `/api/record/status` 轮询电平与倒计时。
- 语音接口使用 NDJSON 分阶段响应：ASR 完成后立即返回 `AGENT_OUTPUT`（`payload.type=asr`，`payload.text`）并显示到聊天框，Agent 内部继续 LLM 和工具处理，随后返回第二条 `AGENT_OUTPUT`（本次结果）。每条仍为 header + payload JSON，前端不回传识别文本。录音端点与 /api/voice 走同一套 Agent 流程，/api/voice 保留给自行编码 WAV 的外部地面站。
- 对话区显示澄清问题、工具名称、成功回执或错误；操作响应不暴露内部调用参数、不重复返回全量状态。「文字输入」是可选入口，与语音共用后续处理；澄清补参也可以继续说话。
- 任务面板展示完整参数、`草稿 → 已上传 → 执行中` 状态和变化记录。界面不周期轮询：在页面加载时和每次操作完成后查询一次 /api/state，刷新页面后恢复任务状态。
- 任务示例：「新增放飞前出任务，原地起飞，起飞高度50米」→「上传刚才创建的任务」→「执行刚才上传的任务」。三个步骤分别执行，创建不会自动上传或执行。

当前是单工作站共享会话：同一 Agent 的浏览器页面共用对话上下文与任务列表。聊天显示仅保存在当前页面；任务和最近 100 条变化保存在 Agent 内存，重启 Agent 会清空。当前 MCP 支持创建、上传和执行，尚无参数修改工具；界面展示已有参数及回执产生的变化，不模拟不存在的修改结果。执行仅更新 Agent 侧模拟状态。

前端与浏览器验证：

```sh
cd web
npm test
npm run build
npx playwright test
```

浏览器测试要求当前环境的 `python` 已安装项目核心依赖，且本机已安装 Google Chrome。测试使用独立的 `18012/5174` 端口和临时目录，界面直接请求 Agent，再调用真实 MCP 本地模拟；ASR/LLM/录音为确定性桩，不代表真实模型识别准确率，也不需要真实麦克风。

历史联调数据见 [ASR / oMLX 联调记录](docs/live-integration-20261016.md) 和 [名称与热词联调记录](docs/task-names-hotwords-20261016.md)；其中服务拓扑反映当时版本，当前地址与契约以上述两份接口文档为准。

## 框架与扩展

- `inputs/`：统一文本、文件、CLI 连续麦克风与服务端录音输入。
- `inference/`：模型接口、本地推理及任务提示词。
- `assistant/`：会话编排、工具白名单、参数与结果校验。
- `integrations/`：Agent 的 MCP 客户端与工具服务。
- `simulator/`：MCP 使用的本地任务模拟，以及独立的地面站 HTTP 协议模拟服务。
- `shared/`：共享业务契约、消息编号、运行配置与终端输出。
- `agent/`：浏览器调用的 ASR、Agent 和任务状态 HTTP 接口。
- `web/`：React 地面站界面、录音与任务监控，直接调用 Agent。
- `tests/`：真实 MCP/HTTP 链路及各层边界测试。

当前接口与参数规则见 [接口设计](docs/接口设计.md) 和 [地面站对接接口](docs/地面站对接接口.md)。历史框架和规划保留在 [框架说明](docs/architecture.md) 与 [整体规划](docs/project-status.md)。根目录 `接口.json` 是历史协议示例，当前运行不再读取该文件。

## 验证

```sh
python -m unittest discover -s tests -v
```

测试使用临时目录和临时 HTTP 服务，不需要真实模型或麦克风。ASR 测试注入转写结果，模型测试使用桩模型与 OpenAI 兼容桩服务，录音测试使用替身录音器（`tests/fakes.py`）；它们不能证明真实模型的语义准确率。
