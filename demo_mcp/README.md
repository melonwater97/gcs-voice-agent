# MCP 创建任务 Demo

此目录演示 `转写文本 → 本地 Ornith 模型提出工具调用 → MCP 客户端执行 create_task → tasks.json`。
它独立于上层的 `asr.py`；输入是一句已经转写好的文字，任务只写入本目录的示例文件。

## 安装

使用项目已有的 `speech-asr` Conda 环境，在 `demo_mcp` 目录运行：

```sh
conda activate speech-asr
python -m pip install -r requirements.txt
```

## 启动本地模型

另开一个终端运行：

```sh
omlx serve --model-dir /Users/melonwater/project/OMLXProject/models --port 18000
```

等服务就绪后，在 `demo_mcp` 目录运行：

```sh
python client.py '明天下午三点提醒我交报告'
```

客户端会先通过 MCP stdio 发现 `create_task`，再把工具定义交给 oMLX。模型返回工具名及参数后，客户端经 MCP 调用任务服务。成功后查看 `tasks.json`。可用 `--url`、`--model` 指向其他兼容服务和模型；`--now 2026-10-13T12:00:00+08:00` 可固定相对日期的基准时间。

试试无关输入：

```sh
python client.py '今天天气怎么样？'
```

模型若不提出工具调用，任务文件不会变化。服务仅支持 `create_task`，并要求 `due_at` 是带 UTC 偏移的有效 ISO 8601 日期时间。MCP 服务通过客户端启动，无需手动在另一个终端运行。

## 验证 MCP 本身

`test_demo.py` 不需要启动大模型，会通过 stdio 连接服务，检查工具发现、有效任务、非法时间及未知工具：

```sh
python -m unittest -v test_demo.py
```
