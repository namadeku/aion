"""``@command`` and ``@tool`` decorators: they only attach metadata, the manager does the rest."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

COMMAND_ATTR = "__aion_command__"
TOOL_ATTR = "__aion_tool__"


@dataclass(frozen=True)
class CommandMeta:
    patterns: tuple[str, ...]
    name: str | None
    dangerous: bool
    priority: int
    description: str


@dataclass(frozen=True)
class ToolMeta:
    description: str
    name: str | None
    dangerous: bool


def command[F: Callable[..., Any]](
    patterns: str | list[str] | tuple[str, ...],
    *,
    name: str | None = None,
    dangerous: bool = False,
    priority: int = 0,
    description: str = "",
) -> Callable[[F], F]:
    """Bind phrases to a handler ``async def handler(self, ctx, **slots)``.

    Pattern syntax: ``{slot}``, ``(alt1|alt2)``, ``[optional words]``. Slot values are
    converted by the handler's annotations (``int``/``float``/``str``).
    ``dangerous=True`` asks the user for a spoken confirmation first.
    A handler may ``return False`` to decline the phrase: it then goes to the LLM fallback.
    """
    items = (patterns,) if isinstance(patterns, str) else tuple(patterns)

    def wrap(fn: F) -> F:
        setattr(fn, COMMAND_ATTR, CommandMeta(items, name, dangerous, priority, description))
        return fn

    return wrap


def tool[F: Callable[..., Any]](
    description: str, *, name: str | None = None, dangerous: bool = False
) -> Callable[[F], F]:
    """Expose a method to the LLM as a function-calling tool.

    The JSON schema is built from type hints; use ``Annotated[str, "описание"]`` to describe
    parameters. Add a ``ctx`` parameter to receive the :class:`~aion.sdk.Context`.
    """

    def wrap(fn: F) -> F:
        setattr(fn, TOOL_ATTR, ToolMeta(description, name, dangerous))
        return fn

    return wrap
