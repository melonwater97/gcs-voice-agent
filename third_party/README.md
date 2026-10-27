# MCP Python SDK

官方源码下载在 `mcp-python-sdk/`，该目录不提交到主项目 Git：

- 仓库：https://github.com/modelcontextprotocol/python-sdk
- 版本：`v2.3.0`
- 提交：`2118f14f8a19bc158d8a1cf90af58d85d187f849`
- 许可证：MIT，原文保留在源码的 `LICENSE` 中。

项目 `requirements.txt` 固定 `mcp==2.3.0`。Python MCP 服务直接使用 SDK 的
`mcp.server.MCPServer`；SDK 本身不是需要在 MCP 管理页另外注册的工具服务。

本机从这份源码构建的安装包位于
`desktop/.data/sdk-wheels/mcp-2.3.0-py3-none-any.whl`。构建使用 `uv build --wheel`，
然后在项目使用的 `speech-asr` 环境安装该 wheel。没有将源码目录加入 `PYTHONPATH`，
也没有使用可编辑安装，移动源码不会影响已安装的 SDK。

迁移到其他电脑时，先安装项目的 `requirements.txt`；如果需要使用这份本地构建包，
先准备运行依赖，再安装 wheel。SDK 版本和依赖声明可在已安装包的元数据中核对。
