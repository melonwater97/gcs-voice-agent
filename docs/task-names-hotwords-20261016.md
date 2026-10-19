# 任务名称与 FunASR 热词联调（2026-10-16）

本次新增 `task.task_name`，并将默认 ASR 从普通 Paraformer 切换为支持热词的 SeACo-Paraformer。任务接口详见 [接口设计](接口设计.md)，启动方式与词表配置见 [README](../README.md)。

## 任务名称

名称与类型、编号分别保存。用户指定的名称去除首尾空白后校验 1–64 字；未指定或为 null 时，由 MCP 服务生成 `放飞前出任务-{编号前8位}`，不让模型编造名称。名称进入创建回执、上传信封和任务文件，React 任务列表优先显示名称，参数与变化记录也展示名称。

Agent 维护本次会话的名称、编号、状态目录，不受最近四轮历史窗口影响。后续“上传晨曦任务”“执行晨曦任务”仍转换为使用 `task_id` 的工具调用。同名任务的命名操作在 L2 再次检查；模型擅自选择其中一个时也不调用工具，要求用户明确编号。显式提供编号后可以继续。

真实 oMLX + MCP + 隔离 HTTP 模拟站完成 9 轮验证：分别创建晨曦和晚霞、按名称上传两个任务、按名称执行两个任务、自动命名一个任务、再创建重名任务、按重名名称上传时澄清。实际结果保存于 `output/name-hotword-integration/names.json`。

## ASR 配置与适配

- 模型：`iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`，revision `v2.0.4`；本地目录 `models/seaco-paraformer`。
- 权重 SHA-256：`3d491689244ec5dfbf9170ef3827c358aa10f1f20e42a7c59e15e688647946d1`。
- FunASR `1.4.16`，CPU，本地权重，`trust_remote_code=False`。原普通模型目录保留。
- 基础词表：`config/asr_hotwords.txt`，共 22 个词，覆盖放飞前出、放飞模式、C/U 型编队、参数和坐标术语。
- 内部生成临时词表文件，将 `C型` 编码为同一行的 `C 型`，避免字符串入口将混合词整体编码为 `<unk>`。词表随模型或单次请求生命周期清理，不修改模型权重。
- Web Agent 每次识别前快照当前任务名称，和基础词表一起传入模型；新名称第一次出现时尚无这份上下文。CLI 文件与麦克风输入使用基础词表。
- SeACo 时间戳分支输出汉字间的 token 分隔空格；转写适配只删除汉字旁的分隔空格，保留英文词间空格，不替换同音字或业务参数。
- 普通 Paraformer 搭配非空热词表会明确报错；通过 `--asr-hotwords-file` 指定空文件可关闭热词。

实现依据：[FunASR Python API](https://github.com/modelscope/FunASR/blob/main/docs/python_api.md)、[官方模型卡](https://modelscope.cn/models/iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch)，以及本机安装版本的 `SeacoParaformer.generate_hotwords_list` 实现。

## 实际识别对比及限制

使用 macOS Tingting 合成的 7 段相同普通话音频，比较普通 Paraformer、SeACo 不带热词、SeACo 带基础热词；上传/执行口令额外验证加入已创建任务名称的效果。原始文本、完整结果与音频保存在 `output/name-hotword-integration/asr-comparison.json` 和 `audio/`。

“新增名为晨曦的放飞前出任务，原地起飞，起飞高度五十米”能够正确转写。初版 28 个词包含新增/上传/执行等通用口令，曾使“晨曦”输出成“承新”“执新”；精简为 22 个领域词后，两句名称口令在基础词表下也正确转写。加入本会话的名称热词后，“上传晨曦任务”“执行晨曦任务”同样正确。初版对比保留为 `asr-comparison-initial-28.json`，避免将调参前后结果混淆。

两段编队音频的高度、角度、半径、长度、宽度和经纬度数值保持正确；C/U 后的“型”在这组样本里仍输出“行”，加热词尚未消除该问题。该对比不能证明整体字错率已经下降，也不能代替真人、不同口音及有噪声条件下的评测。

## 浏览器完整链路

真实 Chrome 录音输入使用合成音频，经真实 SeACo、oMLX、MCP、HTTP 模拟站完成：

1. 语音创建“晨曦”，起飞高度 50 米；ASR HTTP 200，约 1.59 秒，创建完整响应约 5.90 秒。
2. 文字按名称上传，约 4.88 秒；上传文件包含相同名称与参数。
3. 文字按名称执行，约 5.36 秒；模拟站和界面状态均为 executing。
4. 核对三条状态变化、任务名称、参数、落盘文件；刷新恢复当前任务，并检查桌面与手机布局，页面脚本错误为零。
5. 对当前服务直接提交“上传晨曦任务”“执行晨曦任务”的录音，核实任务名称热词确实进入真实 ASR 调用，均正确转写。

浏览器结果、截图与直接语音请求分别位于 `output/name-hotword-integration/real-browser.json`、`real-browser-complete.png`、`real-browser-mobile.png`、`live-name-asr.json`。

自动化验证：50 项 Python 测试、3 项前端单元测试、3 项 Playwright 测试通过，前端生产构建通过。自动化浏览器套件使用 ASR/LLM 测试替身；上述实际浏览器联调使用真实模型。

Agent 和模拟站已重启以加载新契约与模型；旧会话的 5 个任务快照备份到 `output/name-hotword-integration/previous-session.json`。内存会话不会自动从旧文件恢复。
