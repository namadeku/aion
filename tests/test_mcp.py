from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from aion.app import Aion
from aion.config.schema import McpServerConfig
from aion.core.events import ToolCalled
from aion.llm.base import Message, StreamEvent, ToolCall, ToolDef, TurnEnd
from tests.helpers import say
from tests.test_llm import text_reply


def demo_server() -> MCPServer:
    server = MCPServer("Demo")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def search_books(query: str) -> str:
        """Search the catalog by title."""
        return f"Найдено 2 книги по запросу «{query}»"

    @server.tool(name="order.book")
    def order_book(title: str, copies: int = 1) -> str:
        """Order a book."""
        return f"Заказано: {title} x{copies}"

    @server.tool()
    def broken() -> str:
        """Always fails."""
        raise ValueError("склад недоступен")

    return server


async def connect(app: Aion, **config: Any) -> None:
    app.mcp.connect("shop", demo_server(), McpServerConfig(**config))
    await app.mcp.wait_ready()


async def test_mcp_tools_become_llm_tools(app: Aion) -> None:
    await connect(app)
    tools = {t.qualified_name: t for t in app.mcp.tools()}
    assert set(tools) == {"mcp_shop__search_books", "mcp_shop__order_book", "mcp_shop__broken"}
    assert tools["mcp_shop__search_books"].description == "Search the catalog by title."
    assert tools["mcp_shop__search_books"].parameters["properties"]["query"]["type"] == "string"
    # only read-only tools are called without asking
    assert not tools["mcp_shop__search_books"].dangerous
    assert tools["mcp_shop__order_book"].dangerous

    result = await tools["mcp_shop__order_book"].invoke({"title": "Дюна", "copies": 2})
    assert result == "Заказано: Дюна x2"
    assert (await tools["mcp_shop__broken"].invoke({})).startswith("Ошибка:")


async def test_mcp_allow_list_and_confirm_policy(app: Aion) -> None:
    await connect(app, tools=["search_books", "order.book"], confirm="never")
    tools = app.mcp.tools()
    assert sorted(t.name for t in tools) == ["order_book", "search_books"]
    assert not any(t.dangerous for t in tools)


async def test_brain_calls_mcp_tool(app: Aion) -> None:
    from tests.test_llm import FakeProvider

    await connect(app)
    called: list[ToolCalled] = []
    app.bus.subscribe(ToolCalled, called.append)

    def script(messages: list[Message], tools: list[ToolDef]) -> list[StreamEvent]:
        assert "mcp_shop__search_books" in {t.name for t in tools}
        if messages[-1].role == "tool":
            return text_reply(f"{messages[-1].content}.")
        call = ToolCall("c1", "mcp_shop__search_books", {"query": "дюна"})
        return [TurnEnd(Message("assistant", "", tool_calls=[call]))]

    from aion.llm.brain import Brain

    provider = FakeProvider(script)
    app.llm = provider
    app.brain = Brain(app, provider)
    app.dialog.fallback = app.brain.respond
    assert await say(app, "есть ли в магазине дюна") == ["Найдено 2 книги по запросу «дюна»."]
    assert called[0].plugin == "mcp_shop"


async def test_mcp_stop_disconnects(app: Aion) -> None:
    await connect(app)
    assert app.mcp.tools()
    await app.mcp.stop()
    assert app.mcp.tools() == []


async def test_config_change_reconnects_only_changed_server(app: Aion) -> None:
    app.store.update(
        {"mcp": {"a": {"command": "aion-missing-a"}, "b": {"command": "aion-missing-b"}}}
    )
    await app.mcp.sync()
    await app.mcp.wait_ready()
    assert {s["name"]: s["state"] for s in app.mcp.status()} == {"a": "error", "b": "error"}
    task_a = app.mcp._runners["a"].task  # pyright: ignore[reportPrivateUsage]
    app.store.update({"mcp": {"b": {"command": "aion-missing-b2"}}})
    await app.mcp.sync()
    assert app.mcp._runners["a"].task is task_a  # pyright: ignore[reportPrivateUsage]
    assert app.mcp._runners["b"].config.command == "aion-missing-b2"  # pyright: ignore[reportPrivateUsage]
    app.store.update({"mcp": {"a": {"enabled": False}}})
    await app.mcp.sync()
    assert set(app.mcp._runners) == {"b"}  # pyright: ignore[reportPrivateUsage]
