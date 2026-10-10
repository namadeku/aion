from __future__ import annotations

import base64
import io
import json
import sys
from typing import Any

import httpx
import pytest
from PIL import Image

from aion.app import Aion
from tests.helpers import load_builtin_module, say

capture = load_builtin_module("screen", "capture")


def test_encode_scales_down_to_jpeg() -> None:
    data = capture.encode(Image.new("RGBA", (3840, 2160), "white"), 1600)
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG"
    assert image.size == (1600, 900)


@pytest.fixture
def ollama(app: Aion, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Fake Ollama /api/chat and a fake screenshot; returns the received requests."""
    requests: list[dict[str, Any]] = []
    plugin = app.plugins.instance("screen")
    assert plugin is not None
    module = sys.modules[type(plugin).__module__]
    monkeypatch.setattr(module, "screenshot", lambda _side: b"jpeg-bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["model"] == "missing":
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(200, json={"message": {"content": "На экране **редактор кода**."}})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),  # pyright: ignore[reportUnknownLambdaType]
    )
    return requests


async def test_describe_command_asks_vision_model(app: Aion, ollama: list[dict[str, Any]]) -> None:
    replies = await say(app, "что у меня на экране")
    assert replies == ["Секунду, смотрю.", "На экране редактор кода."]
    (request,) = ollama
    assert request["model"] == "qwen3-vl:4b-instruct"
    assert request["think"] is False
    assert request["keep_alive"] == 0  # frees the GPU for the main model
    assert request["options"]["num_predict"] == 300
    message = request["messages"][0]
    assert message["images"] == [base64.b64encode(b"jpeg-bytes").decode()]
    assert "какая программа открыта" in message["content"]


async def test_translate_command(app: Aion, ollama: list[dict[str, Any]]) -> None:
    await say(app, "переведи текст на экране")
    assert "переведи его на русский" in ollama[0]["messages"][0]["content"]


async def test_missing_model_is_explained(app: Aion, ollama: list[dict[str, Any]]) -> None:
    await app.plugins.update_settings("screen", {"model": "missing"})
    _, reply = await say(app, "что на экране")
    assert reply == "Нет модели missing. Скачайте её командой ollama pull missing."
    tool = next(t for t in app.plugins.tools() if t.name == "look_at_screen")
    assert (await tool.invoke({"question": "что это"})).startswith("Ошибка: Нет модели")
