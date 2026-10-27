"""Smoke test: drive uav-mcp over real stdio JSON-RPC, like a client would."""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = "/mnt/d/project/LK/llmspeech-recognition/uav/server.py"


async def main() -> None:
    env = dict(os.environ)
    env.setdefault("UAV_MAVLINK", "udpin:0.0.0.0:14540")
    params = StdioServerParameters(
        command=sys.executable, args=[SERVER], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools:", ", ".join(t.name for t in tools.tools))

            for name, args in [
                ("get_status", {}),
                ("get_battery", {}),
                ("list_parameters", {"name_filter": "COM_DISARM", "limit": 3}),
                ("set_parameter", {"name": "COM_DISARM_PRFLT", "value": 10.0}),
                ("arm", {}),
            ]:
                res = await session.call_tool(name, args)
                text = res.content[0].text if res.content else ""
                print(f"\n[{name}] {json.dumps(json.loads(text), ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
