"""Render compiled capabilities into opencode's permission config (plan §5.1, §5.7).

opencode evaluates permission rules with LAST MATCH WINS, and for object-valued
keys insertion order matters: broad rules first, narrow rules last. Denies are
always rendered after allows so protected paths and denied commands win.
"""

from __future__ import annotations

from typing import Any

from core.harness.capabilities import CompiledCapabilities

# opencode names MCP tools "<server>_<tool>"; our MCP server is "guudflow".
# The wildcard key is verified in tests/harness/test_opencode_contract_o2.py.
MCP_PERMISSION_KEY = "guudflow_*"

_READ_TOOLS = ("read", "glob", "grep", "list", "lsp", "todowrite")
_ALWAYS_DENIED = ("external_directory", "webfetch", "websearch", "question", "doom_loop")


def _rules(default: str, allow: tuple[str, ...], deny: tuple[str, ...]) -> dict[str, str]:
    """Ordered rule object: default first, allows next, denies last (deny wins on overlap)."""
    rules: dict[str, str] = {"*": default}
    for pattern, action in [(p, "allow") for p in allow] + [(p, "deny") for p in deny]:
        if pattern != "*":
            rules.pop(pattern, None)  # re-insert so a later rule moves to the end
        rules[pattern] = action
    return rules


def build_permission(compiled: CompiledCapabilities) -> dict[str, Any]:
    """opencode `permission` config object for one role. Key order and pattern order are significant."""
    perm = compiled.permission
    config: dict[str, Any] = {"*": "deny"}
    for tool in _READ_TOOLS:
        config[tool] = "allow"
    config["StructuredOutput"] = "allow"  # gotcha #5: deny-all hides it otherwise
    config["edit"] = (_rules("deny", perm.edit_allow, perm.edit_deny)
                      if perm.edit_allow else "deny")
    config["bash"] = _rules(perm.bash_default, perm.bash_allow, perm.bash_deny)
    if perm.task == "explore":
        config["task"] = {"*": "deny", "explore": "allow"}
    else:
        config["task"] = perm.task  # "deny" | "allow"
    for tool in _ALWAYS_DENIED:
        config[tool] = "deny"
    # Per-tool RBAC is enforced server-side by GuudFlow's MCP server.
    config[MCP_PERMISSION_KEY] = "allow"
    return config


def to_ruleset(permission: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten a permission config into opencode's PermissionRuleset list, preserving order.

    A string value becomes pattern "*".
    """
    ruleset: list[dict[str, str]] = []
    for key, value in permission.items():
        patterns = {"*": value} if isinstance(value, str) else value
        for pattern, action in patterns.items():
            ruleset.append({"permission": key, "pattern": pattern, "action": action})
    return ruleset
