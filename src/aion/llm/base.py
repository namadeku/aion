"""Provider-agnostic chat types and the LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # for role == "tool"
    is_error: bool = False
    #: Provider-native content of an assistant turn, replayed unchanged to the same provider
    #: (e.g. Claude thinking blocks must be passed back exactly as received).
    raw: Any = None
    raw_provider: str | None = None


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class TurnEnd:
    """End of one model response: the assistant message (with any tool calls)."""

    message: Message
    stop_reason: str = ""


StreamEvent = TextDelta | TurnEnd


class LlmError(RuntimeError):
    """User-presentable provider failure (not running, bad key, network...)."""


def api_error_detail(error: Any, limit: int = 200) -> str:
    """The server's own explanation from an SDK APIStatusError (OpenAI/Anthropic shape)."""
    body = getattr(error, "body", None)
    detail: Any = None
    if isinstance(body, dict):
        inner = body.get("error")
        detail = inner.get("message") if isinstance(inner, dict) else inner
        detail = detail or body.get("message")
    elif isinstance(body, str):
        detail = body
    text = " ".join(str(detail or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class LlmProvider(ABC):
    name: str = "llm"

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        system: str,
        tools: list[ToolDef],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        """Stream one assistant response; the last event is :class:`TurnEnd`."""

    async def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int | None = None
    ) -> str:
        parts: list[str] = []
        async for event in self.stream(
            [Message("user", prompt)],
            system=system or "",
            tools=[],
            temperature=0.3,
            max_tokens=max_tokens or 1024,
        ):
            if isinstance(event, TextDelta):
                parts.append(event.text)
        return "".join(parts).strip()

    async def aclose(self) -> None:  # noqa: B027 - optional
        pass
