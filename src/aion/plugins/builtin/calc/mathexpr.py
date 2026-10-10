"""Spoken arithmetic -> safe evaluation (AST whitelist, no ``eval``)."""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable
from typing import Any

from aion.text import normalize

_PHRASES = (
    (r"\bумножить на\b|\bумножь на\b|\bпомножить на\b|\bраз по\b", " * "),
    (r"\bразделить на\b|\bподелить на\b|\bделить на\b|\bподели на\b|\bраздели на\b", " / "),
    (r"\bв степени\b", " ** "),
    (r"\bв квадрате\b", " ** 2 "),
    (r"\bв кубе\b", " ** 3 "),
    (r"\bплюс\b", " + "),
    (r"\bминус\b", " - "),
    (r"(\d)\s*[xх×]\s*(\d)", r"\1 * \2"),
    (r"\bпроцентов от\b|\bпроцента от\b|\bпроцент от\b|% от", " / 100 * "),
    (r"\bкорень (?:квадратный )?из (\S+)", r" sqrt(\1) "),
    (r"\bмодуль (\S+)", r" abs(\1) "),
)
_FILLER = re.compile(r"\b(сколько|будет|равно|посчитай|вычисли|подсчитай|calculate|what is)\b")

_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS: dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": lambda x: math.sin(math.radians(x)),
    "cos": lambda x: math.cos(math.radians(x)),
}


class CalcError(ValueError):
    pass


def spoken_to_expression(text: str) -> str:
    expr = normalize(text)
    expr = _FILLER.sub(" ", expr)
    for pattern, repl in _PHRASES:
        expr = re.sub(pattern, repl, expr)
    expr = re.sub(r"(\d),(\d)", r"\1.\2", expr)
    return " ".join(expr.split())


def evaluate(expression: str) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise CalcError(expression) from e
    return float(_eval(tree.body))


def _eval(node: ast.AST) -> float:
    match node:
        case ast.Constant(value=int() | float() as value) if not isinstance(value, bool):
            return value
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BINOPS:
            a, b = _eval(left), _eval(right)
            if isinstance(op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
                raise CalcError("слишком большая степень")
            try:
                return _BINOPS[type(op)](a, b)
            except ZeroDivisionError as e:
                raise CalcError("деление на ноль") from e
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY:
            return _UNARY[type(op)](_eval(operand))
        case ast.Call(func=ast.Name(id=name), args=args) if name in _FUNCS and not node.keywords:  # pyright: ignore[reportAttributeAccessIssue]
            return _FUNCS[name](*(_eval(a) for a in args))
        case _:
            raise CalcError(f"недопустимое выражение: {ast.dump(node)[:60]}")


def format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.6g}"
    return text.replace(".", ",")
