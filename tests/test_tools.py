from __future__ import annotations

from typing import Annotated, Any, Literal

from aion.plugins.tools import build_tool
from aion.sdk.decorators import ToolMeta


async def sample(
    city: Annotated[str, "Город"],
    days: int = 1,
    unit: Literal["c", "f"] = "c",
    title: str | None = None,
) -> dict[str, Any]:
    return {"city": city, "days": days, "unit": unit, "title": title}


async def with_ctx(ctx: Any, text: str) -> str:
    return f"{ctx}:{text}"


def test_schema_from_signature() -> None:
    spec = build_tool("weather", sample, ToolMeta("Погода", None, False))
    assert spec.qualified_name == "weather__sample"
    schema = spec.parameters
    assert schema["required"] == ["city"]
    props = schema["properties"]
    assert props["city"] == {"type": "string", "description": "Город"}
    assert props["days"]["type"] == "integer"
    assert props["unit"]["enum"] == ["c", "f"]
    assert "title" in props  # a parameter named "title" must survive title stripping


async def test_invoke_validates_and_serializes() -> None:
    spec = build_tool("weather", sample, ToolMeta("Погода", None, False))
    result = await spec.invoke({"city": "Москва", "days": "2"})
    assert '"days": 2' in result
    assert "Москва" in result
    bad = await spec.invoke({"days": 1})
    assert bad.startswith("Ошибка аргументов")


async def test_ctx_is_injected_not_exposed() -> None:
    spec = build_tool("p", with_ctx, ToolMeta("x", "echo", False))
    assert spec.name == "echo"
    assert spec.wants_ctx
    assert "ctx" not in spec.parameters["properties"]
    assert await spec.invoke({"text": "hi"}, ctx="CTX") == "CTX:hi"
