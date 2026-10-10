"""Turn ``@tool`` methods into LLM function-calling tools (JSON schema + validated invocation)."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel, Field, ValidationError, create_model

from aion.sdk.decorators import ToolMeta

_SKIP = {"self", "ctx", "context"}


@dataclass(frozen=True, eq=False)
class ToolSpec:
    plugin: str
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Awaitable[Any] | Any]
    args_model: type[BaseModel]
    wants_ctx: bool
    dangerous: bool

    @property
    def qualified_name(self) -> str:
        """Unique, provider-safe name (``^[a-zA-Z0-9_-]{1,64}$``)."""
        return f"{self.plugin}__{self.name}"[:64]

    async def invoke(self, arguments: dict[str, Any], ctx: Any = None) -> str:
        try:
            parsed = self.args_model.model_validate(arguments)
        except ValidationError as e:
            return f"Ошибка аргументов: {e.errors(include_url=False)}"
        kwargs = parsed.model_dump()
        if self.wants_ctx:
            kwargs["ctx"] = ctx
        result = self.func(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return _stringify(result)


def _stringify(result: Any) -> str:
    if result is None:
        return "Готово."
    if isinstance(result, str):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except TypeError:
        return str(result)


def _field_type(annotation: Any) -> Any:
    """``Annotated[str, "описание"]`` -> ``Annotated[str, Field(description="описание")]``."""
    if get_origin(annotation) is Annotated:
        base, *extras = get_args(annotation)
        new_extras = [Field(description=e) if isinstance(e, str) else e for e in extras]
        return Annotated[base, *new_extras]  # pyright: ignore[reportInvalidTypeArguments]
    return annotation


def build_tool(plugin: str, func: Callable[..., Any], meta: ToolMeta) -> ToolSpec:
    sig = inspect.signature(func)
    hints = get_type_hints(func, include_extras=True)
    fields: dict[str, Any] = {}
    wants_ctx = False
    for pname, param in sig.parameters.items():
        if pname in _SKIP:
            wants_ctx = wants_ctx or pname in {"ctx", "context"}
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = _field_type(hints.get(pname, str))
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[pname] = (annotation, default)
    name = meta.name or func.__name__
    model = create_model(f"{plugin}_{name}_args", **fields)
    schema = model.model_json_schema()
    _strip_titles(schema)
    description = meta.description or (inspect.getdoc(func) or name).splitlines()[0]
    return ToolSpec(
        plugin=plugin,
        name=name,
        description=description,
        parameters=schema,
        func=func,
        args_model=model,
        wants_ctx=wants_ctx,
        dangerous=meta.dangerous,
    )


def _strip_titles(node: Any) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("title"), str):  # a *parameter* named "title" is a dict
            del node["title"]
        for value in node.values():  # pyright: ignore[reportUnknownVariableType]
            _strip_titles(value)
    elif isinstance(node, list):
        for value in node:  # pyright: ignore[reportUnknownVariableType]
            _strip_titles(value)
