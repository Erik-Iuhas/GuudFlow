"""The `submit_result` MCP tool and where accepted results wait for the runner.

Plan §4.3 and gotcha #12: agents finish a turn by calling
`guudflow_submit_result`. The payload is validated here, inside the tool call, so a
bad result is bounced straight back to the model in the same turn. The runner
then `take()`s the accepted result once the opencode session goes idle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.results import result_json_schema, validate_result

SUBMIT_TOOL = "submit_result"

_NOT_EXPECTED_SCHEMA: dict[str, Any] = {"type": "object", "properties": {},
                                        "additionalProperties": False}


# Extra checks beyond the schema (e.g. "the plan's owners exist", "every
# acceptance item is answered"): one problem per line, empty when acceptable.
ResultCheck = Callable[[BaseModel], list[str]]


@dataclass
class _Slot:
    model: type[BaseModel]
    check: ResultCheck | None = None
    accepted: BaseModel | None = None
    attempts: int = 0
    last_error: str | None = None


@dataclass
class ResultInbox:
    """Per (run, role) slot: which result schema is expected, and what was accepted."""

    _slots: dict[tuple[str, str], _Slot] = field(default_factory=dict)

    def expect(self, run_id: str, role: str, model: type[BaseModel],
               check: ResultCheck | None = None) -> None:
        """Open a slot for the role's next turn (clears any previous result).

        `check` runs after schema validation; its problems bounce the result back
        to the model in the same turn, exactly like schema errors.
        """
        self._slots[(run_id, role)] = _Slot(model=model, check=check)

    def take(self, run_id: str, role: str) -> BaseModel | None:
        """Return and clear the accepted result, if any."""
        slot = self._slots.get((run_id, role))
        if slot is None or slot.accepted is None:
            return None
        result, slot.accepted = slot.accepted, None
        return result

    def status(self, run_id: str, role: str) -> tuple[int, str | None]:
        """(attempts, last validation error) for the role's current slot."""
        slot = self._slots.get((run_id, role))
        return (slot.attempts, slot.last_error) if slot else (0, None)

    def close_run(self, run_id: str) -> None:
        """Drop every slot of a run."""
        for key in [k for k in self._slots if k[0] == run_id]:
            del self._slots[key]

    def _schema(self, identity: McpIdentity) -> dict[str, Any]:
        slot = self._slots.get((identity.run_id, identity.role))
        return result_json_schema(slot.model) if slot else _NOT_EXPECTED_SCHEMA

    async def _submit(self, identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        slot = self._slots.get((identity.run_id, identity.role))
        if slot is None:
            return ToolResult("No result is expected from you right now.", is_error=True)
        slot.attempts += 1
        result, error = validate_result(slot.model, args)
        if result is not None and slot.check is not None:
            problems = slot.check(result)
            if problems:
                result, error = None, "\n".join(problems)
        if result is None:
            slot.last_error = error
            return ToolResult(
                "Result rejected. Fix these problems and call submit_result again:\n"
                f"{error}", is_error=True)
        slot.accepted, slot.last_error = result, None
        return ToolResult("Result accepted. Your turn is complete; stop here.")

    def tool(self) -> McpTool:
        """The MCP tool (exposed to opencode as `guudflow_submit_result`)."""
        return McpTool(
            name=SUBMIT_TOOL,
            description=("Finish your turn by submitting your structured result. Call this "
                         "exactly once, as your last action. If it is rejected, fix the listed "
                         "problems and call it again."),
            input_schema=self._schema,
            handler=self._submit,
        )
