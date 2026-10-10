"""OpenAI-compatible chat completions (OpenAI, LM Studio, vLLM, OpenRouter, ...)."""

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


class OpenAICompatProvider(LlmProvider):
    name = "openai"

    def __init__(self, base_url: str, model: str, api_key_env: str = "OPENAI_API_KEY") -> None:
        try:
            import openai
        except ImportError as e:
            raise LlmError("Установите SDK: uv sync --extra cloud") from e
        self._openai = openai
        self._client = openai.AsyncOpenAI(
            base_url=base_url, api_key=os.environ.get(api_key_env) or "not-needed", timeout=60
        )
        self.model = model

    async def aclose(self) -> None:
        await self._client.close()

    @staticmethod
    def _convert(messages: list[Message], system: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            elif m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": json.dumps(c.arguments, ensure_ascii=False),
                                },
                            }
                            for c in m.tool_calls
                        ],
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert(messages, system),
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        text: list[str] = []
        partial: dict[int, dict[str, str]] = {}
        stop = ""
        try:
            response = await self._client.chat.completions.create(**kwargs)
            async for chunk in response:  # pyright: ignore[reportGeneralTypeIssues]
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text.append(delta.content)
                    yield TextDelta(delta.content)
                for tc in delta.tool_calls or []:
                    slot = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function:
                        slot["name"] += tc.function.name or ""
                        slot["args"] += tc.function.arguments or ""
                if choice.finish_reason:
                    stop = choice.finish_reason
        except self._openai.AuthenticationError as e:
            raise LlmError("Неверный ключ API") from e
        except self._openai.APIConnectionError as e:
            raise LlmError("Нет связи с OpenAI-совместимым сервером") from e
        except self._openai.APIStatusError as e:
            detail = api_error_detail(e)
            message = f"API ответил ошибкой {e.status_code}"
            raise LlmError(f"{message}: {detail}" if detail else message) from e
        calls: list[ToolCall] = []
        for i in sorted(partial):
            slot = partial[i]
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": slot["args"]}
            calls.append(ToolCall(slot["id"] or f"call_{i}", slot["name"], args))
        yield TurnEnd(Message("assistant", "".join(text), tool_calls=calls), stop)
