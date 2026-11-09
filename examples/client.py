"""Minimal MCP client: start the server over stdio, list its tools, and call two of them.

Usage: uv run python examples/client.py "fed rate"
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result) -> object:
    if result.structured_content is not None:
        return result.structured_content
    return [json.loads(c.text) for c in result.content if getattr(c, "text", None)]


async def main(query: str) -> None:
    server = StdioServerParameters(command="uv", args=["run", "predmarket-mcp"])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))

        found = await session.call_tool("search_markets", {"query": query, "limit": 6})
        print(f"\nsearch_markets({query!r}):")
        print(json.dumps(payload(found), indent=2)[:2000])

        pairs = await session.call_tool("compare_venues", {"query": query, "limit": 5})
        print(f"\ncompare_venues({query!r}):")
        print(json.dumps(payload(pairs), indent=2)[:2000])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "recession"))
