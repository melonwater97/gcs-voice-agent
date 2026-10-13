"""A tiny local task store exposed as a real MCP stdio tool."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


SERVER_DIR = Path(__file__).resolve().parent
TASKS_FILE = Path(os.environ.get("DEMO_TASKS_FILE", SERVER_DIR / "tasks.json"))
mcp = MCPServer("task-demo")


# @mcp.tool() 装饰器：把下面这个普通 Python 函数注册成 MCP 工具。
# 注册后，MCP 客户端（例如接了大模型的 Agent）就能通过 JSON-RPC 调用它。
# 函数的类型注解和 docstring 会被 MCP 框架自动读取，生成工具的“参数说明”，
# 供大模型判断何时、如何调用。
@mcp.tool()
def create_task(title: str, due_at: str) -> dict[str, str]:
    """Create a local reminder. due_at must be an ISO 8601 date-time with UTC offset."""
    # —— 参数校验阶段 ——
    # 1) title：去掉首尾空白后必须非空，否则拒绝。
    title = title.strip()
    if not title:
        raise ToolError("title must not be empty")
    # 2) due_at：必须是非空字符串。
    if not due_at or not isinstance(due_at, str):
        raise ToolError("due_at must be an ISO 8601 date-time with UTC offset")
    # 3) due_at 必须能被 ISO 8601 解析为 datetime，否则抛出 ToolError。
    #    ToolError 是 MCP 框架提供的“工具执行失败”异常，会被包装成
    #    is_error=True 的响应返回给客户端，而不是让进程崩溃。
    try:
        due = datetime.fromisoformat(due_at)
    except ValueError as exc:
        raise ToolError("due_at must be a valid ISO 8601 date-time") from exc
    # 4) 必须带时区信息（例如 +08:00），避免“本地时间 vs UTC”歧义。
    if due.tzinfo is None or due.utcoffset() is None:
        raise ToolError("due_at must include a UTC offset")

    # —— 读写任务存储文件（tasks.json）——
    # 若文件存在则读出已有的任务列表；不存在就用空列表。
    # 这里只用 JSON 文本文件做持久化，适合小型 demo，生产环境建议用数据库。
    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8")) if TASKS_FILE.exists() else []
    # 防御性校验：文件内容必须是一个列表，否则拒绝写入（防止数据损坏）。
    if not isinstance(tasks, list):
        raise ToolError("task store must contain a JSON list")

    # 构造新任务：用 uuid4 的前 8 位做短 id，due_at 存为规范化的 ISO 字符串。
    task = {"id": uuid4().hex[:8], "title": title, "due_at": due.isoformat()}
    tasks.append(task)

    # 确保 tasks.json 所在目录存在（首次运行时可能还没建）。
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # —— 原子写入：先写临时文件，再用 os.replace 原子替换目标文件 ——
    # 这样即使写入过程中程序崩溃，也不会把 tasks.json 写坏（要么是旧文件，要么是新文件）。
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=TASKS_FILE.parent, prefix=".tasks-", delete=False
    ) as temporary:
        # ensure_ascii=False 让中文直接以 UTF-8 写入，不转义成 \uXXXX。
        json.dump(tasks, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")  # 末尾补换行，符合文本文件惯例。
        temporary_path = Path(temporary.name)  # 拿到临时文件的路径
    # os.replace 是原子操作：把临时文件“替换”为正式的 tasks.json。
    os.replace(temporary_path, TASKS_FILE)

    # 返回新创建的任务对象，客户端可以拿到 id、title、due_at。
    return task


# 当直接运行本文件时（python task_server.py），启动 MCP stdio 服务器。
# stdio 模式下，服务器通过标准输入/输出与客户端交换 JSON-RPC 消息，
# 这是 MCP 最常见的本地进程间通信方式。
if __name__ == "__main__":
    mcp.run(transport="stdio")
