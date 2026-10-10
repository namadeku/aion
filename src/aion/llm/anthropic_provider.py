"""Claude via the official Anthropic SDK (streaming, tool use, refusal fallback)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from aion.llm.base import (
    LlmError,
    LlmProvider,
    Message,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolDef,
    TurnEnd,
    api_error_detail,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LlmProvider):
    name = "anthropic"

    def __init__(
        self, model: str, api_key_env: str = "ANTHROPIC_API_KEY", effort: str = "low"
    ) -> None:
        try:
            import anthropic
        except ImportError as e:
            raise LlmError("Установите SDK: uv sync --extra cloud") from e
        api_key = os.environ.get(api_key_env) or None
        # Without an explicit key the SDK resolves credentials itself (env / `ant auth login`).
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=60)
        self._anthropic = anthropic
        self.model = model
        self.effort = effort

    async def aclose(self) -> None:
        await self._client.close()

    def _convert(self, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content,
                        "is_error": m.is_error,
                    }
                )
                continue
            if pending_results:  # all results of one assistant turn go in one user message
                out.append({"role": "user", "content": pending_results})
                pending_results = []
            if m.role == "assistant" and m.raw is not None and m.raw_provider == self.name:
                out.append({"role": "assistant", "content": m.raw})  # replay unchanged
            elif m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = (
                    [{"type": "text", "text": m.content}] if m.content else []
                )
                blocks += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in m.tool_calls
                ]
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": m.role, "content": m.content or "…"})
        if pending_results:
            out.append({"role": "user", "content": pending_results})
        return out

    async def stream(
        self,
        messages: list[Message],
        *,
        system: str,
        tools: list[ToolDef],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        api = self._anthropic
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max(max_tokens, 2048),  # thinking shares the budget with the answer
            "system": system,
            "messages": self._convert(messages),
            "output_config": {"effort": self.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        try:
            async with self._client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield TextDelta(event.text)
                final = await stream.get_final_message()
        except api.AuthenticationError as e:
            raise LlmError("Неверный или отсутствующий ключ Anthropic API") from e
        except api.RateLimitError as e:
            raise LlmError("Превышен лимит запросов к Claude, попробуйте позже") from e
        except api.APIStatusError as e:
            detail = api_error_detail(e)
            message = f"Claude API ответил ошибкой {e.status_code}"
            raise LlmError(f"{message}: {detail}" if detail else message) from e
        except api.APIConnectionError as e:
            raise LlmError("Нет связи с Claude API") from e

        if final.stop_reason == "refusal":
            yield TurnEnd(Message("assistant", "Простите, с этим я помочь не могу."), "refusal")
            return
        blocks = _after_fallback(list(final.content))
        text = "".join(b.text for b in blocks if b.type == "text")
        calls = [
            ToolCall(
                b.id, b.name, b.input if isinstance(b.input, dict) else json.loads(str(b.input))
            )
            for b in blocks
            if b.type == "tool_use"
        ]
        raw = [b.model_dump(mode="json", exclude_none=True) for b in blocks]
        message = Message("assistant", text, tool_calls=calls, raw=raw, raw_provider=self.name)
        yield TurnEnd(message, final.stop_reason or "")


_DROP_BEFORE_FALLBACK = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}


def _after_fallback(blocks: list[Any]) -> list[Any]:
    """After a mid-output model fallback, blocks of the declined attempt must not be echoed."""
    boundary = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    if boundary < 0:
        return blocks
    head = [b for b in blocks[:boundary] if b.type not in _DROP_BEFORE_FALLBACK]
    return head + blocks[boundary + 1 :]
