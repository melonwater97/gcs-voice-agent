# 本地语音地面站助手

统一流程：`文本/录音/麦克风 → ASR（音频输入时）→ 模型调用建议 → 参数校验 → MCP → HTTP → 地面站结果`。

业务模块按层放在项目根目录。支持放飞前出任务和 C/U 型编队飞行任务。助手使用本机 oMLX/Ornith 模型。任务目前只写入独立的地面站模拟服务，不连接真实飞控，也不启动飞行。

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
modelscope download iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch --local-dir models/paraformer-large
```

保留完整模型目录，包括配置文件和权重；模型文件不提交到版本库。ASR 在 CPU 上运行，完成安装和模型下载后只读取本地模型。原下载的 `model.pt` SHA-256 为 `5bba782a5e9196166233b9ab12ba04cadff9ef9212b4ff6153ed9290ff679025`。

## 启动

以下命令均在项目根目录执行。终端 1 启动模拟地面站：

```sh
python main.py gcs
```

终端 2 验证默认文本任务（需先启动 oMLX）：

```sh
python main.py assistant '新增放飞前出任务，模式为原地起飞'
```

oMLX 在另一个终端启动：

```sh
/opt/homebrew/opt/omlx/libexec/bin/python scripts/serve_omlx_cached.py \
  --model-dir /Users/melonwater/project/OMLXProject/models --port 18000
```

当前 oMLX 0.6.3rc2 默认使用 2048-token 缓存块，无法命中本项目约 1600-token 的固定前缀。启动脚本在当前进程中适配为 512-token 分块，安装包保持原样；oMLX 升级后需重新验证。缓存配置与实测见 [缓存测试报告](docs/cache-performance.md)。

随后使用文本、录音或麦克风：

```sh
python main.py assistant '新增C型编队飞行任务'
python main.py assistant --file /绝对路径/口令.wav
python main.py assistant --mic
```

不想启动本地模型时，可用 DeepSeek 云端模型替代（Key 已配置在 `shared/config.py`，可用环境变量 `DEEPSEEK_API_KEY` 覆盖）：

```sh
python main.py assistant --deepseek '新增C型编队飞行任务'
```

麦克风模式按 `e` 开始录音，再按 `e` 结束并识别，按 `q` 退出，无需回车。每条口令都进入同一任务流程，ASR 模型和 MCP 连接在会话内复用。Paraformer 是整句识别模型，录音结束后才开始转写。麦克风录音需要交互式终端及系统权限。

`main.py` 是项目统一入口。根目录的独立 `asr.py` 已移除，ASR 能力位于 `inputs/speech.py`。

## 框架与扩展

- `inputs/`：统一文本、文件与连续麦克风输入。
- `inference/`：模型接口、本地推理及任务提示词。
- `assistant/`：会话编排、工具白名单、参数与结果校验。
- `integrations/`：MCP 客户端/服务端及地面站 HTTP 适配。
- `simulator/`：独立 HTTP 接收服务与任务存储。
- `shared/`：共享业务契约、运行配置与终端输出。
- `tests/`：真实 MCP/HTTP 链路及各层边界测试。

完整接口、五种任务样例、配置参数和扩展方法见 [框架说明](docs/architecture.md)。
现有框架、运行流程、工作站助手目标与后续能力建设见 [整体规划](docs/project-status.md)。

## 验证

```sh
python -m unittest discover -s tests -v
```

测试使用临时目录和临时 HTTP 服务，不需要真实模型或麦克风。ASR 测试注入转写结果，模型测试使用桩模型与 OpenAI 兼容桩服务；它们不能证明真实模型的语义准确率。
