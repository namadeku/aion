"""MCP bridge: tools of external MCP servers become tools of the assistant's LLM.

Each configured server (``mcp:`` in config.yaml) runs in its own task that keeps the
connection open — the MCP client must be entered and exited in the same task — and
reconnects if the server dies. Its tools are exposed as :class:`ToolSpec` named
``mcp_<server>__<tool>``, next to the plugin tools.

Every tool's schema is part of the prompt, and a local model has a small context, so the
``tools`` allow-list of a server is worth setting for servers with many tools.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, ConfigDict

from aion.plugins.tools import ToolSpec

if TYPE_CHECKING:
    from mcp import Client
    from mcp.types import CallToolResult, Tool

    from aion.app import Aion
    from aion.config.schema import McpServerConfig

RETRY_S = 30.0
CALL_TIMEOUT_S = 60.0
MAX_RESULT_CHARS = 4000  # a tool result goes back into the model's small context
_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


class _AnyArgs(BaseModel):
    """MCP servers validate their own arguments: pass the model's JSON through as is."""

    model_config = ConfigDict(extra="allow")


def is_dangerous(tool: Tool, confirm: str) -> bool:
    """Ask before calling it? "auto" trusts the server's hints: only read-only tools are safe."""
    if confirm != "auto":
        return confirm == "always"
    hints = tool.annotations
    return not (hints is not None and hints.read_only_hint)


def result_text(result: CallToolResult) -> str:
    from mcp.types import ImageContent, TextContent

    parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            parts.append("[изображение]")
        else:
            parts.append(f"[{block.type}]")
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    text = "\n".join(parts) or "Готово."
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "… (обрезано)"
    return f"Ошибка: {text}" if result.is_error else text


@dataclass
class Connection:
    name: str
    client: Client
    tools: list[ToolSpec] = field(default_factory=list)


@dataclass
class _Runner:
    """The background task that keeps one server connected."""

    config: McpServerConfig
    task: asyncio.Task[None]
    stopping: asyncio.Event


class McpHub:
    def __init__(self, app: Aion) -> None:
        self.app = app
        self.connections: dict[str, Connection] = {}
        self.errors: dict[str, str] = {}  # last connection error per server
        self._runners: dict[str, _Runner] = {}
        self._lock = asyncio.Lock()  # config changes in a row must not sync concurrently

    def tools(self) -> list[ToolSpec]:
        return [t for c in self.connections.values() for t in c.tools]

    def status(self) -> list[dict[str, Any]]:
        """Configured servers with their state, for the UI."""
        out: list[dict[str, Any]] = []
        for name, config in self.app.config.mcp.items():
            connection = self.connections.get(name)
            if not config.enabled:
                state = "off"
            elif connection is not None:
                state = "connected"
            elif name in self.errors:
                state = "error"
            else:
                state = "connecting"
            out.append(
                {
                    "name": name,
                    "config": config.model_dump(mode="json"),
                    "state": state,
                    "error": self.errors.get(name),
                    "tools": [t.name for t in connection.tools] if connection else [],
                }
            )
        return out

    async def sync(self) -> None:
        """Match the running servers to the config: only changed servers reconnect."""
        async with self._lock:
            await self._sync()

    async def _sync(self) -> None:
        wanted = {
            name: config
            for name, config in self.app.config.mcp.items()
            if config.enabled and (config.command or config.url)
        }
        for name, runner in list(self._runners.items()):
            if wanted.get(name) != runner.config:
                await self._stop_one(name)
        for name, config in wanted.items():
            if name not in self._runners:
                self.connect(name, _target(config), config)

    def connect(self, name: str, target: Any, config: McpServerConfig) -> None:
        """Serve one server in the background; ``target`` is anything ``mcp.Client`` takes."""
        stopping = asyncio.Event()
        task = asyncio.get_running_loop().create_task(
            self._serve(name, target, config, stopping), name=f"mcp:{name}"
        )
        self._runners[name] = _Runner(config, task, stopping)

    async def stop(self) -> None:
        async with self._lock:
            for name in list(self._runners):
                await self._stop_one(name)

    async def _stop_one(self, name: str) -> None:
        runner = self._runners.pop(name)
        runner.stopping.set()  # lets the task leave the client context in its own task
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(5):
                await asyncio.gather(runner.task, return_exceptions=True)
        runner.task.cancel()
        await asyncio.gather(runner.task, return_exceptions=True)
        self.connections.pop(name, None)
        self.errors.pop(name, None)

    async def wait_ready(self, timeout: float = 10.0) -> None:
        """Wait until every server connected or failed once — for tests and startup."""
        async with asyncio.timeout(timeout):
            while any(n not in self.connections and n not in self.errors for n in self._runners):
                await asyncio.sleep(0.05)

    async def _serve(
        self, name: str, target: Any, config: McpServerConfig, stopping: asyncio.Event
    ) -> None:
        from mcp import Client

        while not stopping.is_set():
            try:
                async with Client(target, read_timeout_seconds=CALL_TIMEOUT_S) as client:
                    connection = Connection(name, client)
                    connection.tools = await self._load_tools(connection, config)
                    self.connections[name] = connection
                    self.errors.pop(name, None)
                    logger.info(
                        "MCP {}: {} инструментов ({})",
                        name,
                        len(connection.tools),
                        ", ".join(t.name for t in connection.tools),
                    )
                    self.app.tools_changed()
                    await stopping.wait()
            except FileNotFoundError:
                error = f"Не найдена программа «{config.command}»: установите её или укажите путь"
                logger.warning("MCP-сервер {}: {}", name, error)
                self.errors[name] = error
            except Exception as e:
                logger.warning("MCP-сервер {} недоступен: {}", name, e)
                self.errors[name] = str(e) or type(e).__name__
            finally:
                if self.connections.pop(name, None) is not None:
                    self.app.tools_changed()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(RETRY_S):
                    await stopping.wait()

    async def _load_tools(self, connection: Connection, config: McpServerConfig) -> list[ToolSpec]:
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            page = await connection.client.list_tools(cursor=cursor)
            tools += page.tools
            cursor = page.next_cursor
            if not cursor:
                break
        wanted = set(config.tools)
        return [
            self._spec(connection, tool, config)
            for tool in tools
            if not wanted or tool.name in wanted
        ]

    def _spec(self, connection: Connection, tool: Tool, config: McpServerConfig) -> ToolSpec:
        async def call(**arguments: Any) -> str:
            result = await connection.client.call_tool(tool.name, arguments)
            return result_text(result)

        return ToolSpec(
            plugin=_UNSAFE.sub("_", f"mcp_{connection.name}"),
            name=_UNSAFE.sub("_", tool.name),  # the call below uses the original name
            description=(tool.description or tool.title or tool.name).strip()[:1000],
            parameters=tool.input_schema or {"type": "object", "properties": {}},
            func=call,
            args_model=_AnyArgs,
            wants_ctx=False,
            dangerous=is_dangerous(tool, config.confirm),
        )


def _target(config: McpServerConfig) -> Any:
    if config.url:
        return config.url
    from mcp import StdioServerParameters

    return StdioServerParameters(command=config.command, args=config.args, env=config.env or None)
