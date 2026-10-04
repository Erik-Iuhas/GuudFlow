"""Tests for core.harness.capabilities (plan §5.7 compile table)."""

from __future__ import annotations

import pytest

from core.harness.capabilities import (
    DENIED_BASH,
    HOST_DEV_WARNING,
    LEVELS,
    PROTECTED_PATHS,
    ROLE_DEFAULTS,
    ROLE_TYPES,
    TEST_RUNNER_BASH,
    Capabilities,
    CapabilityError,
    compile_capabilities,
    defaults_for,
    gate_vision,
    model_supports_images,
)


def test_every_role_type_has_defaults_and_edit_scope() -> None:
    assert set(ROLE_DEFAULTS) == set(ROLE_TYPES)
    for role in ROLE_TYPES:
        compile_capabilities(defaults_for(role), role)


def test_invalid_value_and_unknown_key_rejected() -> None:
    with pytest.raises(CapabilityError):
        Capabilities(web="everything")  # type: ignore[arg-type]
    with pytest.raises(CapabilityError):
        Capabilities.from_dict({"teleport": "on"})


def test_from_dict_fills_from_base() -> None:
    caps = Capabilities.from_dict({"web": "search"}, base=defaults_for("builder"))
    assert caps.web == "search" and caps.shell == "project"
    assert Capabilities.from_dict(caps.to_dict()) == caps


@pytest.mark.parametrize("name", list(LEVELS))
def test_is_within_orders_each_dimension(name: str) -> None:
    low = Capabilities(**{name: LEVELS[name][0]})
    high = Capabilities(**{name: LEVELS[name][-1]})
    assert low.is_within(high)
    assert not high.is_within(low)


def test_host_dev_refused_in_production_and_warned_otherwise() -> None:
    caps = Capabilities(sandbox="host_dev", shell="project")
    with pytest.raises(CapabilityError):
        compile_capabilities(caps, "builder", production=True)
    compiled = compile_capabilities(caps, "builder")
    assert not compiled.container.use_container
    assert compiled.warnings == (HOST_DEV_WARNING,)


@pytest.mark.parametrize("sandbox,runners", [("strict", False), ("standard", True),
                                             ("host_dev", True)])
def test_test_runners_follow_sandbox(sandbox: str, runners: bool) -> None:
    compiled = compile_capabilities(Capabilities(sandbox=sandbox, shell="project"), "builder")  # type: ignore[arg-type]
    assert compiled.container.allow_test_runners is runners
    assert ("pytest*" in compiled.permission.bash_allow) is runners


@pytest.mark.parametrize("shell,default,commits", [("readonly", "deny", False),
                                                  ("project", "deny", True),
                                                  ("ask", "ask", True)])
def test_shell_levels(shell: str, default: str, commits: bool) -> None:
    compiled = compile_capabilities(Capabilities(sandbox="standard", shell=shell), "builder")  # type: ignore[arg-type]
    assert compiled.permission.bash_default == default
    assert ("git commit*" in compiled.permission.bash_allow) is commits
    assert "git status*" in compiled.permission.bash_allow


def test_reviewer_gets_test_runners_but_no_commits_or_edits() -> None:
    compiled = compile_capabilities(defaults_for("reviewer"), "reviewer")
    assert set(TEST_RUNNER_BASH) <= set(compiled.permission.bash_allow)
    assert "git commit*" not in compiled.permission.bash_allow
    assert compiled.permission.edit_allow == ()


def test_installers_denied_except_for_it_agent() -> None:
    builder = compile_capabilities(defaults_for("builder"), "builder").permission
    assert "pip *" in builder.bash_deny and "npm install*" in builder.bash_deny
    it = compile_capabilities(defaults_for("it_agent"), "it_agent")
    assert "pip install *" in it.permission.bash_allow
    assert "pip *" not in it.permission.bash_deny
    assert "curl *" in it.permission.bash_deny
    assert it.container.extra_egress == ("package-mirror",)


def test_push_and_network_tools_always_denied() -> None:
    for role in ROLE_TYPES:
        deny = compile_capabilities(defaults_for(role), role).permission.bash_deny
        for pattern in ("git push*", "curl *", "wget *", "sudo *"):
            assert pattern in deny, (role, pattern)
        assert set(deny) <= set(DENIED_BASH)


def test_protected_paths_denied_whenever_edits_allowed() -> None:
    for role in ROLE_TYPES:
        perm = compile_capabilities(defaults_for(role), role).permission
        if perm.edit_allow:
            assert perm.edit_deny == PROTECTED_PATHS
        else:
            assert perm.edit_deny == ()


@pytest.mark.parametrize("level,task,depth", [("off", "deny", None),
                                              ("explore_only", "explore", 1),
                                              ("full", "allow", 1)])
def test_subagents(level: str, task: str, depth: int | None) -> None:
    perm = compile_capabilities(Capabilities(subagents=level), "builder").permission  # type: ignore[arg-type]
    assert perm.task == task and perm.subagent_depth == depth


@pytest.mark.parametrize("web,tools", [("off", set()), ("search", {"web_search"}),
                                       ("search+fetch", {"web_search", "web_fetch"})])
def test_web_grants_mcp_tools_only(web: str, tools: set[str]) -> None:
    compiled = compile_capabilities(Capabilities(web=web), "researcher")  # type: ignore[arg-type]
    assert {"web_search", "web_fetch"} & compiled.mcp_tools == tools
    # Web never opens container egress.
    assert compiled.container.extra_egress == ()


def test_browser_vision_and_packages_grants() -> None:
    caps = Capabilities(sandbox="standard", browser="screenshot", vision="on",
                        packages="via_it_agent")
    compiled = compile_capabilities(caps, "frontend_builder")
    assert {"screenshot", "request_visual_feedback", "visual_diff",
            "request_package"} <= compiled.mcp_tools
    assert compiled.container.host_may_reach_dev_server
    assert compiled.permission.attach_images
    base = compile_capabilities(Capabilities(), "builder")
    assert base.mcp_tools == {"post_note", "read_board", "ask_human", "report_progress"}


def test_vision_gating() -> None:
    caps = Capabilities(vision="on")
    assert gate_vision(caps, model_supports_images=False).vision == "off"
    assert gate_vision(caps, model_supports_images=True).vision == "on"
    assert model_supports_images({"modalities": {"input": ["text", "image"]}})
    assert model_supports_images({"attachment": True})
    assert not model_supports_images({"modalities": {"input": ["text"]}})


def test_digest_changes_with_capabilities() -> None:
    a = compile_capabilities(defaults_for("builder"), "builder")
    b = compile_capabilities(defaults_for("builder"), "builder")
    c = compile_capabilities(Capabilities(sandbox="standard", shell="ask"), "builder")
    assert a.digest() == b.digest() != c.digest()


def test_unknown_role_type_rejected() -> None:
    with pytest.raises(CapabilityError):
        compile_capabilities(Capabilities(), "wizard")


@pytest.mark.parametrize("role,reviewer,declared,expected", [
    ("code_reviewer", False, "", "reviewer"),
    ("anything", True, "", "reviewer"),
    ("backend_dev", False, "", "builder"),
    ("frontend_dev", False, "", "frontend_builder"),
    ("test_writer", False, "", "tester"),
    ("researcher", False, "", "researcher"),
    ("planner", False, "", "planner"),
    ("x", False, "tester", "tester"),
    ("x", False, "wizard", "builder"),
])
def test_infer_role_type(role: str, reviewer: bool, declared: str, expected: str) -> None:
    from core.harness.capabilities import infer_role_type
    assert infer_role_type(role, is_reviewer=reviewer, declared=declared) == expected


def test_role_hints_match_whole_words_only() -> None:
    """O10 finding: "ui" inside "builder" made plain builders frontend builders."""
    from core.harness.capabilities import infer_role_type
    assert infer_role_type("builder") == "builder"
    assert infer_role_type("squad") == "builder"
    assert infer_role_type("guide") == "builder"
    assert infer_role_type("ui_dev") == "frontend_builder"
    assert infer_role_type("frontend") == "frontend_builder"
    assert infer_role_type("qa") == "tester"
    assert infer_role_type("code_reviewer") == "reviewer"
    assert infer_role_type("tech_lead") == "overseer"
