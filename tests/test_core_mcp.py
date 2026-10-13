"""MCP hub: provider-safe unique tool names, plain-http warning."""

from __future__ import annotations

from loguru import logger
from mcp.server import MCPServer

from aion.app import Aion
from aion.config.schema import McpServerConfig
from aion.llm.mcp_hub import _target  # pyright: ignore[reportPrivateUsage]

LONG = "x" * 70


def long_names_server() -> MCPServer:
    server = MCPServer("Long")

    @server.tool(name=LONG + "_first")
    def first() -> str:
        """First."""
        return "one"

    @server.tool(name=LONG + "_second")
    def second() -> str:
        """Second."""
        return "two"

    return server


async def test_long_tool_names_stay_unique_and_callable(app: Aion) -> None:
    app.mcp.connect("long", long_names_server(), McpServerConfig(confirm="never"))
    await app.mcp.wait_ready()
    tools = app.mcp.tools()
    names = [t.qualified_name for t in tools]
    assert len(names) == 2
    assert len(set(names)) == 2, "truncation must not merge two tools into one"
    assert all(len(n) <= 64 for n in names)
    assert sorted([await t.invoke({}) for t in tools]) == ["one", "two"]


def test_plain_http_to_a_remote_server_is_warned_about() -> None:
    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        _target(McpServerConfig(url="http://example.com/mcp"))
        _target(McpServerConfig(url="http://127.0.0.1:9000/mcp"))
        _target(McpServerConfig(url="https://example.com/mcp"))
    finally:
        logger.remove(sink)
    assert len(seen) == 1
    assert "example.com" in seen[0]
