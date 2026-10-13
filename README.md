# 本地中文语音识别

使用 FunASR 加载本地 Paraformer-large 模型。识别在本机 CPU 上运行；完成首次安装和模型下载后，运行时只读取本地文件。

## 安装

```sh
conda create -n speech-asr python=3.11 pip -y
conda activate speech-asr
python -m pip install -r requirements.txt
```

下载模型：

```sh
modelscope download iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch --local-dir models/paraformer-large
```
下载后需保留完整目录，包括配置文件和权重。模型文件不提交到版本库。
本次下载的 `model.pt` SHA-256 为
`5bba782a5e9196166233b9ab12ba04cadff9ef9212b4ff6153ed9290ff679025`。

## 使用

识别本地录音：

```sh
python asr.py --file /绝对路径/录音.wav
```

使用默认麦克风交互识别：

```sh
python asr.py --mic
```

模型加载完成后，按 `e` 开始录音，再按 `e` 结束录音并识别；按 `q` 直接退出。无需按回车，可以连续识别多条口令，模型只加载一次。首次麦克风录音可能需要在 macOS 中授权终端或 Codex 使用麦克风。`--mic` 需要交互式终端。

安装验收可使用模型自带音频：

```sh
python asr.py --file models/paraformer-large/example/asr_example.wav
```

本机测试输出：`欢迎大家来体验达摩院推出的语音识别模型`。在 M1 Max / 64 GB 上，这段约 5.6 秒的音频推理约 1 秒；包含启动与模型加载的单次命令约 8.5 秒，峰值内存约 3.2 GB。

`asr.py` 独立运行时只返回转写文字，也提供可复用的 `load_model`、`transcribe_file` 和 `iter_microphone_transcripts`。
Paraformer-large 是整句识别模型，麦克风录制结束后才开始识别；要边说边出字，应另用 `paraformer-zh-streaming` 模型及分块推理代码。

## 语音新增地面站任务

[地面站助手 Demo](demo_gcs_layers/README.md) 已接入文件和麦克风 ASR，完整链路为：

`语音 → ASR → 本地模型调用建议 → MCP → HTTP 地面站模拟程序 → 返回结果`。

在 `speech-asr` 环境下，先在一个终端启动模拟地面站：

```sh
python -m pip install -r demo_gcs_layers/requirements.txt
python demo_gcs_layers/gcs_client.py
```

单独启动 oMLX 后，在另一个交互式终端启动语音助手：

```sh
python demo_gcs_layers/demo.py --mic --local
```

使用录音文件则运行 `python demo_gcs_layers/demo.py --file /绝对路径/口令.wav --local`。
详细的 oMLX 启动、统一 JSON 接口和文本测试方法见 Demo README。
