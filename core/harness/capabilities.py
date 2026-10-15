"""Per-role capabilities and their compilation (plan §5.7).

A role's `Capabilities` block is the one place a human decides what an agent
may do. `compile_capabilities()` turns it into:

  1. `PermissionInputs`  — what opencode may allow (rendered into an opencode
                           ruleset by core.harness.permissions),
  2. `ContainerProfile`  — how the sandbox is built (worker/sandbox_manager),
  3. `mcp_tools`         — which GuudFlow MCP tools the role is granted.

This module decides *what* is allowed. It is security-relevant: every shell
pattern, edit scope and MCP grant an agent can get is defined here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Literal

SandboxLevel = Literal["strict", "standard", "host_dev"]
WebLevel = Literal["off", "search", "search+fetch"]
ShellLevel = Literal["readonly", "project", "ask"]
PackagesLevel = Literal["off", "via_it_agent"]
SubagentsLevel = Literal["off", "explore_only", "full"]
BrowserLevel = Literal["off", "screenshot"]
VisionLevel = Literal["off", "on"]

# Each dimension ordered from most to least restrictive. Used to check that a
# runtime change only narrows (plan §5.7 "narrowing only, at runtime").
LEVELS: dict[str, tuple[str, ...]] = {
    "sandbox": ("strict", "standard", "host_dev"),
    "web": ("off", "search", "search+fetch"),
    "shell": ("readonly", "project", "ask"),
    "packages": ("off", "via_it_agent"),
    "subagents": ("off", "explore_only", "full"),
    "browser": ("off", "screenshot"),
    "vision": ("off", "on"),
}

ROLE_TYPES = ("researcher", "planner", "architect", "builder", "frontend_builder",
              "tester", "reviewer", "it_agent", "overseer")

# Paths no agent may edit, whatever its role (plan §5.5). Rendered last so
# they win over any allow (opencode: last matching rule wins).
PROTECTED_PATHS = ("inputs/**", ".github/**", "opencode.json", ".opencode/**", ".guudflow/**",
                   ".git")

READONLY_BASH = ("git status*", "git diff*", "git log*", "git show*", "git branch*",
                 "ls*", "cat *", "head *", "tail *", "wc *", "pwd", "grep *", "cd *",
                 # Looking around: each refusal costs a model call (run findings F8).
                 "find *", "which *", "diff *", "sort *", "uniq *", "tree*", "file *", "stat *")
# Roles that edit also move, copy and delete files (opencode has no delete or rename tool).
COMMIT_BASH = ("git add*", "git commit*", "git checkout -b *", "git switch *", "git restore *",
               "mkdir *", "rm *", "mv *", "cp *", "touch *", "echo *")
TEST_RUNNER_BASH = ("pytest*", "python -m pytest*", "python3 -m pytest*", "npm test*",
                    "npm run *", "node *", "python *", "python3 *", "npx vitest*", "npx tsc*",
                    "bun test*", "bun run *")
# Always denied, rendered after the allows. Installs go through the IT agent.
_OUTSIDE = tuple(f"{cmd} {pat}" for cmd in ("rm", "mv", "cp")
                 for pat in ("/*", "* /*", "*~*", "*..*", "*$*"))
DENIED_BASH = ("find *-exec*", "find *-execdir*", "find *-delete*", "find *-ok*",
               "find *-fprint*", *_OUTSIDE, "git push*", "git remote*", "git config*", "pip *", "pip3 *", "python -m pip*",
               "python3 -m pip*", "npm install*", "npm i *", "npm ci*", "yarn *", "pnpm *",
               "uv *", "poetry *", "apt*", "curl *", "wget *", "ssh *", "scp *", "sudo *",
               "docker *", "nc *", "bun install*", "bun add*", "bun i *", "bun x *", "bunx *",
               "bun upgrade*", "bun pm *", "bun create*", "bun link*")
IT_AGENT_BASH = ("pip install *", "pip3 install *", "npm install *")

ALWAYS_MCP = frozenset({"post_note", "read_board", "ask_human", "report_progress"})

# Third-party MCP tools (docs/MCP_PLAN.md) are named `{server}__{tool}`; no GuudFlow tool
# contains the separator, so a third-party name can never shadow a built-in one.
MCP_TOOL_SEP = "__"


def resolve_mcp_grants(requested: Iterable[str] | None, role_type: str,
                       offers: Mapping[str, Iterable[str]]) -> frozenset[str]:
    """Third-party MCP tools a role gets: its project config, narrowed by what the registry offers.

    `offers` maps each namespaced tool the registry offers (enabled servers, cached tools) to
    the role types that get it by default. `requested` is the agent's `mcp_tools` list:
    None means "the registry's defaults for this role type"; a list is explicit (an empty list
    grants nothing). Names the registry does not offer are dropped, so a stale or invented
    name never becomes a grant. Default-deny: no entry in `offers` means no access.
    """
    if requested is None:
        return frozenset(name for name, roles in offers.items() if role_type in set(roles))
    return frozenset(name for name in requested
                     if MCP_TOOL_SEP in name and name in offers)


class CapabilityError(ValueError):
    """Invalid capability block, or one not allowed in this environment."""


@dataclass(frozen=True)
class Capabilities:
    """The seven per-role toggles (plan §5.7)."""

    sandbox: SandboxLevel = "strict"
    web: WebLevel = "off"
    shell: ShellLevel = "readonly"
    packages: PackagesLevel = "off"
    subagents: SubagentsLevel = "off"
    browser: BrowserLevel = "off"
    vision: VisionLevel = "off"

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if value not in LEVELS[f.name]:
                raise CapabilityError(
                    f"{f.name}: {value!r} is not one of {', '.join(LEVELS[f.name])}")

    @classmethod
    def from_dict(cls, data: dict[str, Any], base: "Capabilities | None" = None) -> "Capabilities":
        """Build from a JSON block; missing keys come from `base` (or the strict default)."""
        unknown = set(data) - set(LEVELS)
        if unknown:
            raise CapabilityError(f"unknown capability: {', '.join(sorted(unknown))}")
        return replace(base or cls(), **data)

    def to_dict(self) -> dict[str, str]:
        """JSON-serialisable form."""
        return dict(asdict(self))

    def is_within(self, other: "Capabilities") -> bool:
        """True if every toggle is at most as permissive as `other`'s."""
        return all(LEVELS[name].index(getattr(self, name)) <= LEVELS[name].index(getattr(other, name))
                   for name in LEVELS)


ROLE_DEFAULTS: dict[str, Capabilities] = {
    "researcher": Capabilities(web="search+fetch"),
    "planner": Capabilities(subagents="explore_only", vision="on"),
    "architect": Capabilities(subagents="explore_only", vision="on"),
    "builder": Capabilities(sandbox="standard", shell="project", packages="via_it_agent",
                            subagents="explore_only"),
    "frontend_builder": Capabilities(sandbox="standard", shell="project",
                                     packages="via_it_agent", subagents="explore_only",
                                     browser="screenshot", vision="on"),
    "tester": Capabilities(sandbox="standard", shell="project", packages="via_it_agent",
                           subagents="explore_only"),
    # Reviewers run tests, so they need the `standard` sandbox; their shell stays
    # readonly and gains only the test runners (see _bash_allow).
    "reviewer": Capabilities(sandbox="standard", subagents="explore_only",
                             browser="screenshot", vision="on"),
    "it_agent": Capabilities(sandbox="standard"),
    "overseer": Capabilities(subagents="explore_only"),
}

# Edit scope per role type (plan §5.1). Empty means no edits at all.
EDIT_SCOPES: dict[str, tuple[str, ...]] = {
    "researcher": ("research/**",),
    "planner": ("docs/**", "*.md", "**/*.md"),
    "architect": ("docs/**", "*.md", "**/*.md"),
    "builder": ("*", "**"),
    "frontend_builder": ("*", "**"),
    "tester": ("tests/**", "**/tests/**", "**/*.test.*", "**/*.spec.*", "**/test_*.py",
               "**/*_test.py", "test_*.py"),
    "reviewer": (),
    "it_agent": ("requirements*.txt", "package.json", "pyproject.toml"),
    "overseer": (),
}

READS_TESTS = frozenset({"reviewer"})


_ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("review", "reviewer"), ("research", "researcher"), ("overseer", "overseer"),
    ("tech_lead", "overseer"), ("it_agent", "it_agent"), ("plan", "planner"),
    ("architect", "architect"), ("test", "tester"), ("qa", "tester"),
    ("front", "frontend_builder"), ("ui", "frontend_builder"),
)


def infer_role_type(role: str, *, is_reviewer: bool = False, declared: str = "") -> str:
    """Role type from an explicit declaration, the reviewer flag, or the role name."""
    if declared in ROLE_TYPES:
        return declared
    if is_reviewer:
        return "reviewer"
    name = role.lower()
    if name in ROLE_TYPES:
        return name
    words = [w for w in re.split(r"[^a-z0-9]+", name) if w]
    return next((rt for hint, rt in _ROLE_HINTS if _hint_matches(hint, name, words)), "builder")


def _hint_matches(hint: str, name: str, words: list[str]) -> bool:
    """Match a hint against whole words: "ui" must not match "builder" (O10 finding)."""
    if "_" in hint:
        return hint in name
    if len(hint) <= 2:
        return hint in words
    return any(w.startswith(hint) for w in words)


def defaults_for(role_type: str) -> Capabilities:
    """Default capabilities for a role type (unknown types get builder defaults)."""
    return ROLE_DEFAULTS.get(role_type, ROLE_DEFAULTS["builder"])


def gate_vision(caps: Capabilities, model_supports_images: bool) -> Capabilities:
    """Turn vision off when the role's model cannot take image input."""
    return caps if model_supports_images or caps.vision == "off" else replace(caps, vision="off")


def model_supports_images(model_config: dict[str, Any]) -> bool:
    """Read an opencode model entry: `modalities.input` has image, or `attachment` is true."""
    inputs = (model_config.get("modalities") or {}).get("input") or []
    return "image" in inputs or bool(model_config.get("attachment"))


@dataclass(frozen=True)
class PermissionInputs:
    """What opencode may allow. Rendered by core.harness.permissions."""

    edit_allow: tuple[str, ...]
    edit_deny: tuple[str, ...]
    bash_default: Literal["deny", "ask"]
    bash_allow: tuple[str, ...]
    bash_deny: tuple[str, ...]
    task: Literal["deny", "explore", "allow"]
    subagent_depth: int | None
    attach_images: bool


@dataclass(frozen=True)
class ContainerProfile:
    """How the role's sandbox is built (plan §5.3, §5.4)."""

    use_container: bool
    read_only_root: bool
    allow_test_runners: bool
    host_may_reach_dev_server: bool
    extra_egress: tuple[str, ...] = ()
    memory: str = "1g"
    pids_limit: int = 256


@dataclass(frozen=True)
class CompiledCapabilities:
    """Everything derived from one role's capabilities."""

    role_type: str
    capabilities: Capabilities
    permission: PermissionInputs
    container: ContainerProfile
    mcp_tools: frozenset[str]
    warnings: tuple[str, ...] = field(default=())

    def digest(self) -> str:
        """Stable hash recorded in the CAPABILITIES board signal."""
        payload = {
            "role_type": self.role_type,
            "capabilities": self.capabilities.to_dict(),
            "permission": asdict(self.permission),
            "container": asdict(self.container),
            "mcp_tools": sorted(self.mcp_tools),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


HOST_DEV_WARNING = ("⚠️  DEV MODE: opencode agent running on host. "
                    "Do not use with untrusted input.")


def missing_runners(perm: PermissionInputs, installed: Callable[[str], bool]) -> list[str]:
    """Test-runner programs the role may run that `installed` says are absent."""
    runners = {p.split()[0].rstrip("*") for p in TEST_RUNNER_BASH}
    allowed = {p.split()[0].rstrip("*") for p in perm.bash_allow if p.strip("* ")}
    return sorted(prog for prog in runners & allowed if not installed(prog))


# Runner commands a spec rule may name ("Use bun test"): the allowed runner commands without
# their wildcard. `node *` / `python *` are left out: in a rule's prose those are ordinary words.
_RULE_RUNNER = re.compile(r"\b(" + "|".join(sorted(
    (re.escape(p.rstrip("* ")) for p in TEST_RUNNER_BASH if not p.endswith(" *")
     or len(p.split()) > 2), key=len, reverse=True)) + r")\b", re.IGNORECASE)
_STEP_SPLIT = re.compile(r"&&|\|\||[;|]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def needed_runners(check_command: str, rules: Iterable[tuple[str, str]]) -> dict[str, str]:
    """The programs a project's tests need, each with why: the program of every step of
    `check_command`, and the test runners its spec `rules` ((id, text) pairs) name."""
    needed: dict[str, str] = {}
    command = check_command.strip()
    for step in _STEP_SPLIT.split(command):
        words = [w for w in step.split() if not _ASSIGNMENT.match(w)]
        if words and words[0] != "cd":
            needed.setdefault(words[0], f"the check command `{command}`")
    for rule_id, text in rules:
        for found in _RULE_RUNNER.finditer(text):
            needed.setdefault(found.group(1).split()[0].lower(), f"spec rule {rule_id}: {text}")
    return needed


def shell_summary(perm: PermissionInputs,
                  installed: Callable[[str], bool] | None = None) -> str | None:
    """The shell commands a role may run, for its brief (flow hardening §6). None: no shell
    beyond the default ask/deny, nothing worth saying.

    `installed` (host mode) says whether a program exists where the role runs; allowed
    runners that do not are named, so the role does not spend turns looking for them.
    """
    if not perm.bash_allow:
        return None
    allowed = sorted({p.rstrip("*").strip() for p in perm.bash_allow if p.strip("* ")})
    default = ("asks a person first" if perm.bash_default == "ask" else "is refused")
    text = ("You may run: " + ", ".join(f"`{a}`" for a in allowed) + f". Anything else {default}, "
            "and every part of a pipe or `;` chain is checked on its own: `bun test | grep x` "
            "is fine, but `sed`, `awk`, `echo`, `>` redirection and package installs are not. "
            "If a command is refused, run its allowed part alone and read the output with the "
            "read and grep tools; a refusal is never a reason to block a card or ask a person.")
    missing = missing_runners(perm, installed) if installed is not None else []
    if missing:
        text += (" Not installed here: " + ", ".join(f"`{m}`" for m in missing) + ". "
                 "Do not look for them or ask to install them; write the code and tests anyway "
                 "and mark the acceptance items you could not run as unmet, saying why.")
    return text


def _bash_allow(caps: Capabilities, role_type: str, runners: bool) -> tuple[str, ...]:
    allow: list[str] = list(READONLY_BASH)
    if caps.shell in ("project", "ask"):
        allow += COMMIT_BASH
    if runners and (caps.shell in ("project", "ask") or role_type in READS_TESTS):
        allow += TEST_RUNNER_BASH
    if role_type == "it_agent":
        allow += IT_AGENT_BASH
    return tuple(allow)


def compile_capabilities(caps: Capabilities, role_type: str, *,
                         production: bool = False,
                         extra_mcp_tools: Iterable[str] = ()) -> CompiledCapabilities:
    """Compile a capability block for `role_type` (plan §5.7 compile table).

    `extra_mcp_tools` are third-party grants already resolved by `resolve_mcp_grants`.
    """
    if role_type not in ROLE_TYPES:
        raise CapabilityError(f"unknown role type {role_type!r}")
    if caps.sandbox == "host_dev" and production:
        raise CapabilityError("sandbox 'host_dev' is refused in production")

    runners = caps.sandbox in ("standard", "host_dev")
    bash_deny = tuple(p for p in DENIED_BASH
                      if not (role_type == "it_agent" and p in ("pip *", "pip3 *", "npm install*")))
    edit_allow = EDIT_SCOPES[role_type]
    permission = PermissionInputs(
        edit_allow=edit_allow,
        edit_deny=PROTECTED_PATHS if edit_allow else (),
        bash_default="ask" if caps.shell == "ask" else "deny",
        bash_allow=_bash_allow(caps, role_type, runners),
        bash_deny=bash_deny,
        task={"off": "deny", "explore_only": "explore", "full": "allow"}[caps.subagents],
        subagent_depth=None if caps.subagents == "off" else 1,
        attach_images=caps.vision == "on",
    )
    container = ContainerProfile(
        use_container=caps.sandbox != "host_dev",
        read_only_root=caps.sandbox != "host_dev",
        allow_test_runners=runners,
        host_may_reach_dev_server=caps.browser == "screenshot",
        extra_egress=("package-mirror",) if role_type == "it_agent" else (),
    )
    tools = set(ALWAYS_MCP)
    if caps.web in ("search", "search+fetch"):
        tools.add("web_search")
    if caps.web == "search+fetch":
        tools.add("web_fetch")
    if caps.packages == "via_it_agent":
        tools.add("request_package")
        tools.add("sync_dependencies")
    if caps.browser == "screenshot":
        tools.update({"screenshot", "request_visual_feedback"})
    if caps.vision == "on":
        tools.add("visual_diff")
    tools.update(t for t in extra_mcp_tools if MCP_TOOL_SEP in t)
    warnings = (HOST_DEV_WARNING,) if caps.sandbox == "host_dev" else ()
    return CompiledCapabilities(role_type=role_type, capabilities=caps, permission=permission,
                                container=container, mcp_tools=frozenset(tools),
                                warnings=warnings)
