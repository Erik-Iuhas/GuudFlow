"""Structured results an opencode agent returns at the end of each turn.

Replaces the free-text ``[COMMIT]/[VERIFY]/[ASK]`` protocol blocks. See
docs/OPENCODE_HARNESS_PLAN.md sections 4.2 and 4.3.
"""
import json
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

T = TypeVar("T", bound=BaseModel)


class BlockedOn(BaseModel):
    """An upstream role that must act before the blocked role can continue."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    need: str = Field(min_length=15)  # a concrete ask, at least one sentence


class Issue(BaseModel):
    """One specific problem a reviewer found."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    detail: str = Field(min_length=20)  # a FAIL must cite specifics


class AgentResult(BaseModel):
    """Result for builders, architects and testers."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["done", "blocked", "failed"]
    summary: str = Field(min_length=20)
    files_changed: list[str] = []
    blocked_on: BlockedOn | None = None
    notes_for: dict[str, str] = {}

    @model_validator(mode="after")
    def _check_blocked_on(self) -> "AgentResult":
        if self.status == "blocked" and self.blocked_on is None:
            raise ValueError("blocked_on: required when status is 'blocked'")
        if self.status != "blocked" and self.blocked_on is not None:
            raise ValueError(f"blocked_on: must be null when status is '{self.status}'")
        return self

    @model_validator(mode="after")
    def _check_notes(self) -> "AgentResult":
        for role, note in self.notes_for.items():
            if not role.strip():
                raise ValueError("notes_for: role keys must be non-empty")
            if not note.strip():
                raise ValueError(f"notes_for.{role}: note must be non-empty")
        return self


class ReviewResult(BaseModel):
    """Result for reviewers."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "fail"]
    issues: list[Issue] = []
    summary: str = ""

    @model_validator(mode="after")
    def _check_issues(self) -> "ReviewResult":
        if self.verdict == "fail" and not self.issues:
            raise ValueError("issues: at least one issue is required when verdict is 'fail'")
        return self


ResultModel = type[AgentResult] | type[ReviewResult]


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """Recursively resolve ``$ref``, drop ``title`` and close every object."""
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        extra = {k: v for k, v in node.items() if k != "$ref"}
        return _inline({**defs[name], **extra}, defs)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("$defs", "title"):
            continue
        if key == "properties":  # map of field name -> schema; keep field names
            out[key] = {name: _inline(sub, defs) for name, sub in value.items()}
        else:
            out[key] = _inline(value, defs)
    if out.get("type") == "object" or "properties" in out:
        # Close model objects, but keep map schemas (dict[str, X]) intact.
        out.setdefault("additionalProperties", False)
    return out


def result_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a fully inlined JSON Schema for opencode's ``format.schema``.

    No ``$ref``/``$defs``, ``additionalProperties: false`` on every object,
    no ``title`` keys. ``X | None`` is ``anyOf [X, {"type": "null"}]``.
    """
    raw = model.model_json_schema()
    return _inline(raw, raw.get("$defs", {}))


def _format_errors(exc: ValidationError) -> str:
    """Render pydantic errors as one short line per problem."""
    lines: list[str] = []
    for err in exc.errors():
        path = ".".join(str(p) for p in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] == "extra_forbidden":
            msg = "unexpected field"
        lines.append(f"{path}: {msg}" if path else msg)
    return "\n".join(lines)


def validate_result(model: type[T], raw: Any) -> tuple[T | None, str | None]:
    """Validate opencode's ``AssistantMessage.structured`` against ``model``.

    Returns ``(instance, None)`` on success or ``(None, error_text)`` where
    ``error_text`` has one line per problem, naming the field path.
    """
    if raw is None:
        return None, "no structured result was produced"
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            return None, f"result is not valid JSON: {exc}"
    if not isinstance(raw, dict):
        return None, f"result must be a JSON object, got {type(raw).__name__}"
    try:
        return model.model_validate(raw), None
    except ValidationError as exc:
        return None, _format_errors(exc)


def build_retry_prompt(model: type[BaseModel], error: str) -> str:
    """Build the single follow-up message sent when validation fails (plan 4.3)."""
    return (
        f"Your last {model.__name__} result was not accepted. Validation error:\n\n"
        f"{error}\n\n"
        "Call the StructuredOutput tool again with a corrected result that matches "
        "the same schema. Fix every problem listed above and do not change anything else."
    )
