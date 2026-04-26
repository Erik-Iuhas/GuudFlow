"""Tests for the general-purpose tools added post-Stage-4:
text:diff, text:estimate_tokens,
data:json, data:csv, data:yaml, data:xml,
time:now, time:diff,
math:eval.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.tools.data import DataCsv, DataJson, DataXml, DataYaml
from agents.tools.math_tool import MathEval, safe_eval
from agents.tools.text import TextDiff, TextEstimateTokens
from agents.tools.time_tool import TimeDiff, TimeNow
from core.file_workspace import FileWorkspace
from core.tool_manifest import BASELINE_LOCAL_SCOPES


@pytest.fixture
def fw(tmp_path: Path) -> FileWorkspace:
    return FileWorkspace(tmp_path / "ws")


# ── Baseline scope membership ──────────────────────────────────────────────


def test_general_tools_are_baseline():
    for scope in (
        "text:diff", "text:estimate_tokens",
        "data:json", "data:csv", "data:yaml", "data:xml",
        "time:now", "time:diff",
        "math:eval",
    ):
        assert scope in BASELINE_LOCAL_SCOPES, f"{scope} should be baseline"


# ── text:diff ──────────────────────────────────────────────────────────────


async def test_text_diff_string_inputs():
    res = await TextDiff().run({"a": "hello\nworld\n", "b": "hello\nworld!\n"})
    assert res["ok"] is True
    assert res["has_changes"] is True
    assert "world" in res["diff"]
    assert "world!" in res["diff"]


async def test_text_diff_no_changes():
    res = await TextDiff().run({"a": "same\n", "b": "same\n"})
    assert res["has_changes"] is False
    assert res["diff"] == ""


async def test_text_diff_file_inputs(fw: FileWorkspace):
    await fw.write("a.py", "v1\n")
    await fw.write("b.py", "v2\n")
    res = await TextDiff(fw).run({"a_path": "a.py", "b_path": "b.py"})
    assert res["ok"] is True
    assert "v1" in res["diff"]
    assert "v2" in res["diff"]


async def test_text_diff_mixed_string_and_file(fw: FileWorkspace):
    await fw.write("b.py", "current\n")
    res = await TextDiff(fw).run({"a": "old\n", "b_path": "b.py"})
    assert res["ok"] is True
    assert res["has_changes"] is True


async def test_text_diff_missing_file(fw: FileWorkspace):
    res = await TextDiff(fw).run({"a": "x\n", "b_path": "ghost.py"})
    assert "error" in res


async def test_text_diff_missing_args():
    res = await TextDiff().run({})
    assert "error" in res


# ── text:estimate_tokens ───────────────────────────────────────────────────


async def test_text_estimate_tokens_from_string():
    res = await TextEstimateTokens().run({"text": "x" * 100})
    assert res["ok"] is True
    assert res["chars"] == 100
    assert res["estimated_tokens"] == 25  # 100 / 4


async def test_text_estimate_tokens_from_file(fw: FileWorkspace):
    await fw.write("a.py", "x" * 200)
    res = await TextEstimateTokens(fw).run({"path": "a.py"})
    assert res["ok"] is True
    assert res["estimated_tokens"] == 50


async def test_text_estimate_tokens_empty_string():
    res = await TextEstimateTokens().run({"text": ""})
    assert res["estimated_tokens"] == 0


async def test_text_estimate_tokens_neither_arg():
    res = await TextEstimateTokens().run({})
    assert "error" in res


async def test_text_estimate_tokens_both_args():
    res = await TextEstimateTokens().run({"text": "x", "path": "p"})
    assert "error" in res


# ── data:json ──────────────────────────────────────────────────────────────


async def test_data_json_basic(fw: FileWorkspace):
    payload = {"users": [{"name": "Aria"}, {"name": "Kai"}]}
    await fw.write("data.json", json.dumps(payload))
    res = await DataJson(fw).run({"path": "data.json"})
    assert res["ok"] is True
    assert res["value"] == payload


async def test_data_json_array_path(fw: FileWorkspace):
    payload = {"paths": {"/users": {"get": {"summary": "list users"}}}}
    await fw.write("api.json", json.dumps(payload))
    res = await DataJson(fw).run({
        "path": "api.json",
        "json_path": ["paths", "/users", "get", "summary"],
    })
    assert res["ok"] is True
    assert res["value"] == "list users"


async def test_data_json_dot_path(fw: FileWorkspace):
    payload = {"data": {"results": [1, 2, 3]}}
    await fw.write("d.json", json.dumps(payload))
    res = await DataJson(fw).run({"path": "d.json", "json_path": "data.results"})
    assert res["value"] == [1, 2, 3]


async def test_data_json_dot_path_with_index(fw: FileWorkspace):
    payload = {"items": ["a", "b", "c"]}
    await fw.write("d.json", json.dumps(payload))
    res = await DataJson(fw).run({"path": "d.json", "json_path": "items.1"})
    assert res["value"] == "b"


async def test_data_json_invalid_path(fw: FileWorkspace):
    await fw.write("d.json", json.dumps({"a": 1}))
    res = await DataJson(fw).run({"path": "d.json", "json_path": ["b"]})
    assert "error" in res


async def test_data_json_malformed(fw: FileWorkspace):
    await fw.write("bad.json", "{not json")
    res = await DataJson(fw).run({"path": "bad.json"})
    assert "error" in res


async def test_data_json_from_inputs(fw: FileWorkspace):
    (fw.inputs / "spec.json").write_text(json.dumps({"v": 1}), encoding="utf-8")
    res = await DataJson(fw).run({"path": "spec.json", "from": "inputs"})
    assert res["ok"] is True
    assert res["value"] == {"v": 1}


# ── data:csv ───────────────────────────────────────────────────────────────


async def test_data_csv_with_header(fw: FileWorkspace):
    await fw.write("d.csv", "name,age\nAria,30\nKai,25\n")
    res = await DataCsv(fw).run({"path": "d.csv"})
    assert res["ok"] is True
    assert res["columns"] == ["name", "age"]
    assert res["rows"] == [{"name": "Aria", "age": "30"}, {"name": "Kai", "age": "25"}]
    assert res["total_rows"] == 2


async def test_data_csv_no_header(fw: FileWorkspace):
    await fw.write("d.csv", "a,b\nc,d\n")
    res = await DataCsv(fw).run({"path": "d.csv", "has_header": False})
    assert res["columns"] == ["col_0", "col_1"]
    assert res["rows"] == [["a", "b"], ["c", "d"]]


async def test_data_csv_max_rows(fw: FileWorkspace):
    rows = "x,y\n" + "\n".join(f"{i},{i*2}" for i in range(50))
    await fw.write("d.csv", rows + "\n")
    res = await DataCsv(fw).run({"path": "d.csv", "max_rows": 5})
    assert res["returned_rows"] == 5
    assert res["total_rows"] == 50
    assert res["truncated"] is True


async def test_data_csv_alt_delimiter(fw: FileWorkspace):
    await fw.write("d.tsv", "a\tb\nc\td\n")
    res = await DataCsv(fw).run({"path": "d.tsv", "delimiter": "\t"})
    assert res["columns"] == ["a", "b"]
    assert res["rows"][0] == {"a": "c", "b": "d"}


async def test_data_csv_empty_file(fw: FileWorkspace):
    await fw.write("d.csv", "")
    res = await DataCsv(fw).run({"path": "d.csv"})
    assert res["ok"] is True
    assert res["columns"] == []
    assert res["rows"] == []


# ── data:yaml ──────────────────────────────────────────────────────────────


async def test_data_yaml_basic(fw: FileWorkspace):
    await fw.write("c.yaml", "name: Aria\ntags:\n  - react\n  - frontend\n")
    res = await DataYaml(fw).run({"path": "c.yaml"})
    assert res["ok"] is True
    assert res["value"] == {"name": "Aria", "tags": ["react", "frontend"]}


async def test_data_yaml_safe_load_blocks_python_objects(fw: FileWorkspace):
    """yaml.safe_load rejects !!python/object — agents can't construct
    arbitrary classes via YAML."""
    dangerous = "!!python/object/apply:os.system\nargs: ['echo pwned']\n"
    await fw.write("evil.yaml", dangerous)
    res = await DataYaml(fw).run({"path": "evil.yaml"})
    assert "error" in res


async def test_data_yaml_malformed(fw: FileWorkspace):
    await fw.write("bad.yaml", "key: [unclosed")
    res = await DataYaml(fw).run({"path": "bad.yaml"})
    assert "error" in res


# ── data:xml ───────────────────────────────────────────────────────────────


async def test_data_xml_basic(fw: FileWorkspace):
    xml = "<root><item id='1'>hello</item><item id='2'>world</item></root>"
    await fw.write("d.xml", xml)
    res = await DataXml(fw).run({"path": "d.xml"})
    assert res["ok"] is True
    root = res["root"]
    assert root["tag"] == "root"
    assert len(root["children"]) == 2
    assert root["children"][0]["tag"] == "item"
    assert root["children"][0]["attrib"]["id"] == "1"
    assert root["children"][0]["text"] == "hello"


async def test_data_xml_depth_limit(fw: FileWorkspace):
    xml = "<a><b><c><d><e>deep</e></d></c></b></a>"
    await fw.write("d.xml", xml)
    res = await DataXml(fw).run({"path": "d.xml", "max_depth": 2})
    assert res["ok"] is True
    # Past depth 2, children_truncated.
    a = res["root"]
    b = a["children"][0]
    # b is at depth 1, its children fetched at depth 2 → truncated marker present
    found_truncated = False
    def walk(node):
        nonlocal found_truncated
        if node.get("children_truncated"):
            found_truncated = True
        for c in node.get("children", []):
            walk(c)
    walk(a)
    assert found_truncated


async def test_data_xml_blocks_external_entities(fw: FileWorkspace):
    """defusedxml rejects DOCTYPE-with-entity payloads (XXE)."""
    xxe = """<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<root>&xxe;</root>"""
    await fw.write("xxe.xml", xxe)
    res = await DataXml(fw).run({"path": "xxe.xml"})
    assert "error" in res


async def test_data_xml_malformed(fw: FileWorkspace):
    await fw.write("bad.xml", "<unclosed>")
    res = await DataXml(fw).run({"path": "bad.xml"})
    assert "error" in res


# ── time:now ───────────────────────────────────────────────────────────────


async def test_time_now_returns_iso_and_epoch():
    res = await TimeNow().run({})
    assert res["ok"] is True
    parsed = datetime.fromisoformat(res["iso"])
    assert parsed.tzinfo is not None
    assert isinstance(res["epoch_seconds"], int)
    assert res["epoch_ms"] == res["epoch_seconds"] * 1000 or abs(res["epoch_ms"] - res["epoch_seconds"] * 1000) < 1000


# ── time:diff ──────────────────────────────────────────────────────────────


async def test_time_diff_positive():
    a = datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc).isoformat()
    b = datetime(2026, 4, 25, 11, 30, tzinfo=timezone.utc).isoformat()
    res = await TimeDiff().run({"a": a, "b": b})
    assert res["ok"] is True
    assert res["total_seconds"] == 90 * 60
    assert res["hours"] == 1
    assert res["minutes"] == 30
    assert res["negative"] is False


async def test_time_diff_negative():
    a = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc).isoformat()
    b = datetime(2026, 4, 25, 11, 0, tzinfo=timezone.utc).isoformat()
    res = await TimeDiff().run({"a": a, "b": b})
    assert res["negative"] is True
    assert res["total_seconds"] == -3600


async def test_time_diff_handles_z_suffix():
    res = await TimeDiff().run({"a": "2026-04-25T10:00:00Z", "b": "2026-04-25T10:30:00Z"})
    assert res["ok"] is True
    assert res["total_seconds"] == 1800


async def test_time_diff_naive_treated_as_utc():
    res = await TimeDiff().run({"a": "2026-04-25T10:00:00", "b": "2026-04-25T10:00:30"})
    assert res["total_seconds"] == 30


async def test_time_diff_invalid_input():
    res = await TimeDiff().run({"a": "not a date", "b": "2026-04-25T10:00:00Z"})
    assert "error" in res


# ── math:eval ──────────────────────────────────────────────────────────────


def test_safe_eval_arithmetic():
    assert safe_eval("2 + 2") == 4
    assert safe_eval("(3 * 4) ** 2 / 8") == 18
    assert safe_eval("-5 + 3") == -2
    assert safe_eval("17 // 5") == 3
    assert safe_eval("17 % 5") == 2
    assert safe_eval("2 ** 10") == 1024


def test_safe_eval_floats():
    assert safe_eval("1.5 + 2.25") == 3.75


def test_safe_eval_rejects_names():
    with pytest.raises(ValueError):
        safe_eval("foo + 1")


def test_safe_eval_rejects_function_calls():
    with pytest.raises(ValueError):
        safe_eval("abs(-5)")


def test_safe_eval_rejects_attribute_access():
    with pytest.raises(ValueError):
        safe_eval("(1).bit_length()")


def test_safe_eval_rejects_import():
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('rm')")


def test_safe_eval_rejects_booleans():
    with pytest.raises(ValueError):
        safe_eval("True + 1")


def test_safe_eval_rejects_strings():
    with pytest.raises(ValueError):
        safe_eval("'a' + 'b'")


def test_safe_eval_division_by_zero():
    with pytest.raises(ValueError):
        safe_eval("1 / 0")


async def test_math_eval_tool_happy():
    res = await MathEval().run({"expression": "(2 + 3) * 4"})
    assert res["ok"] is True
    assert res["value"] == 20


async def test_math_eval_tool_invalid_expression():
    res = await MathEval().run({"expression": "import os"})
    assert "error" in res


async def test_math_eval_tool_missing_expression():
    res = await MathEval().run({})
    assert "error" in res
