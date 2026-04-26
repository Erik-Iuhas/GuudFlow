"""Math tool — `math:eval`. Safe arithmetic via AST allowlist.

No `eval()`. No `exec()`. No name lookups, function calls, attribute
access, comprehensions, or side effects. Just numeric literals and a
small set of arithmetic operators on them.

Allowed:
  + - * / // % **    (binary)
  + -                (unary)
  ()                 (grouping, via the AST naturally)

Anything else raises `ValueError` and is reported as an error to the agent.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from agents.tools.base import BaseTool


_BIN_OPS = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def safe_eval(expression: str) -> float | int:
    """Evaluate a numeric expression. Raises ValueError on disallowed syntax."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e}") from e
    return _walk(tree.body)


def _walk(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            raise ValueError("booleans are not allowed")
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"non-numeric constant: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        op_cls = type(node.op)
        if op_cls not in _BIN_OPS:
            raise ValueError(f"operator not allowed: {op_cls.__name__}")
        try:
            return _BIN_OPS[op_cls](_walk(node.left), _walk(node.right))
        except (ZeroDivisionError, OverflowError, ValueError) as e:
            raise ValueError(str(e)) from e
    if isinstance(node, ast.UnaryOp):
        op_cls = type(node.op)
        if op_cls not in _UNARY_OPS:
            raise ValueError(f"operator not allowed: {op_cls.__name__}")
        return _UNARY_OPS[op_cls](_walk(node.operand))
    raise ValueError(f"node type not allowed: {type(node).__name__}")


class MathEval(BaseTool):
    name = "math:eval"
    description = "Safely evaluate a numeric expression: + - * / // % ** and parentheses. No names, no calls, no attributes."
    input_schema = {
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
        },
        "required": ["expression"],
    }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        expr = arguments.get("expression")
        if not isinstance(expr, str) or not expr.strip():
            return {"error": "missing required argument: expression"}
        try:
            value = safe_eval(expr)
        except ValueError as e:
            return {"error": f"could not evaluate: {e}"}
        return {"ok": True, "expression": expr, "value": value}
