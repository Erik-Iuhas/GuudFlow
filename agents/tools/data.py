"""Data tools — `data:json`, `data:csv`, `data:yaml`, `data:xml`.

Common structured-data formats. All read-only; all path-safe via
FileWorkspace. XML uses `defusedxml` to block XXE / billion-laughs.
"""

from __future__ import annotations

import csv as csv_lib
import io
import json as json_lib
from typing import Any

import yaml as yaml_lib
from defusedxml import ElementTree as DefusedET

from agents.tools.base import BaseTool
from core.file_workspace import FileWorkspace, PathSafetyError


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


async def _read_text(
    files: FileWorkspace,
    path: str,
    source: str,
) -> tuple[str | None, dict | None]:
    try:
        if source == "inputs":
            return await files.read_input(path), None
        return await files.read(path), None
    except PathSafetyError as e:
        return None, _err(f"path safety: {e}")
    except FileNotFoundError:
        return None, _err(f"not found: {path}")
    except UnicodeDecodeError:
        return None, _err("file is not UTF-8 text")
    except OSError as e:
        return None, _err(f"read failed: {e}")


# ── data:json ──────────────────────────────────────────────────────────────


def _navigate_json(value: Any, path: list) -> Any:
    """Walk nested JSON. Each path element is a string key or an int
    index. Raises KeyError / IndexError / TypeError on bad navigation —
    caller turns those into a tool error."""
    cur = value
    for step in path:
        if isinstance(step, int):
            if not isinstance(cur, list):
                raise TypeError(f"cannot index non-list with integer at {step}")
            cur = cur[step]
        elif isinstance(step, str):
            if not isinstance(cur, dict):
                raise TypeError(f"cannot key non-dict with {step!r}")
            cur = cur[step]
        else:
            raise TypeError(f"path element must be str or int, got {type(step).__name__}")
    return cur


class DataJson(BaseTool):
    """Read a JSON file. Optional `json_path` extracts a subtree.

    `json_path` is an array of keys/indices (strings for keys, integers
    for array indices). Strings are accepted unambiguously even when
    they contain dots or slashes (e.g. OpenAPI paths). For convenience
    a dot-separated string is also accepted but will not work for keys
    that themselves contain dots — use the array form for those.

    Examples:
      json_path: ["paths", "/users", "get", "parameters", 0]
      json_path: "data.results"
    """

    name = "data:json"
    description = "Parse a JSON file. Optional `json_path` (array of keys/indices, or dot-string) extracts a subtree."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "json_path": {
                "oneOf": [
                    {"type": "array", "items": {"type": ["string", "integer"]}},
                    {"type": "string"},
                ],
            },
            "from": {"type": "string", "enum": ["working", "inputs"]},
        },
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        source = arguments.get("from", "working")

        text, err = await _read_text(self.files, path, source)
        if err is not None:
            return err

        try:
            parsed = json_lib.loads(text)  # type: ignore[arg-type]
        except json_lib.JSONDecodeError as e:
            return _err(f"json decode failed: {e}")

        json_path = arguments.get("json_path")
        if json_path is None:
            return {"ok": True, "path": path, "value": parsed}

        if isinstance(json_path, str):
            path_list: list = json_path.split(".") if json_path else []
            # Convert numeric strings to ints so dot-path works for arrays.
            path_list = [int(p) if p.lstrip("-").isdigit() else p for p in path_list]
        elif isinstance(json_path, list):
            path_list = list(json_path)
        else:
            return _err("json_path must be array or dot-string")

        try:
            sub = _navigate_json(parsed, path_list)
        except (KeyError, IndexError, TypeError) as e:
            return _err(f"json_path navigation failed: {e}")

        return {"ok": True, "path": path, "json_path": path_list, "value": sub}


# ── data:csv ───────────────────────────────────────────────────────────────


class DataCsv(BaseTool):
    """Read a CSV file. By default the first row is treated as headers
    and rows are returned as dicts. Set `has_header=False` to get raw
    list-of-lists rows.

    `max_rows` caps the number of rows returned (default 1000) — total
    row count is reported separately so the agent can detect truncation.
    """

    name = "data:csv"
    description = "Parse a CSV file. Returns columns + rows-as-dicts (or list-of-lists if has_header=false)."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "from": {"type": "string", "enum": ["working", "inputs"]},
            "delimiter": {"type": "string", "description": "default ','"},
            "has_header": {"type": "boolean", "description": "default true"},
            "max_rows": {"type": "integer", "description": "default 1000"},
        },
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        source = arguments.get("from", "working")
        delimiter = arguments.get("delimiter", ",")
        has_header = bool(arguments.get("has_header", True))
        max_rows = int(arguments.get("max_rows", 1000))

        text, err = await _read_text(self.files, path, source)
        if err is not None:
            return err

        try:
            reader = csv_lib.reader(io.StringIO(text), delimiter=delimiter)  # type: ignore[arg-type]
            all_rows = list(reader)
        except csv_lib.Error as e:
            return _err(f"csv parse failed: {e}")

        if not all_rows:
            return {"ok": True, "path": path, "columns": [], "rows": [],
                    "total_rows": 0, "returned_rows": 0, "truncated": False}

        if has_header:
            columns = all_rows[0]
            data_rows = all_rows[1:]
        else:
            columns = [f"col_{i}" for i in range(len(all_rows[0]))]
            data_rows = all_rows

        truncated = len(data_rows) > max_rows
        returned = data_rows[:max_rows]

        if has_header:
            rows: list[Any] = [dict(zip(columns, r)) for r in returned]
        else:
            rows = returned

        return {
            "ok": True,
            "path": path,
            "columns": columns,
            "rows": rows,
            "total_rows": len(data_rows),
            "returned_rows": len(rows),
            "truncated": truncated,
        }


# ── data:yaml ──────────────────────────────────────────────────────────────


class DataYaml(BaseTool):
    """Parse a YAML file using `yaml.safe_load` (rejects arbitrary Python
    object construction)."""

    name = "data:yaml"
    description = "Parse a YAML file. Uses safe_load — rejects arbitrary object construction."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "from": {"type": "string", "enum": ["working", "inputs"]},
        },
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        source = arguments.get("from", "working")

        text, err = await _read_text(self.files, path, source)
        if err is not None:
            return err

        try:
            parsed = yaml_lib.safe_load(text)
        except yaml_lib.YAMLError as e:
            return _err(f"yaml parse failed: {e}")

        return {"ok": True, "path": path, "value": parsed}


# ── data:xml ───────────────────────────────────────────────────────────────


def _xml_elem_to_dict(elem, max_depth: int, depth: int = 0) -> dict[str, Any]:
    """Convert a defusedxml element to a dict tree, capped at max_depth.

    Each node: {tag, attrib, text, children}. Past max_depth, children
    are reported as truncated.
    """
    if depth >= max_depth:
        return {
            "tag": elem.tag,
            "attrib": dict(elem.attrib),
            "text": (elem.text or "").strip() or None,
            "children_truncated": True,
        }

    children = [_xml_elem_to_dict(c, max_depth, depth + 1) for c in elem]
    return {
        "tag": elem.tag,
        "attrib": dict(elem.attrib),
        "text": (elem.text or "").strip() or None,
        "children": children,
    }


class DataXml(BaseTool):
    """Parse an XML file safely via defusedxml (blocks XXE,
    billion-laughs, external DTDs).

    Returns a dict tree with `tag`, `attrib`, `text`, and `children` per
    node. Depth is capped at `max_depth` (default 8) — past that,
    `children_truncated: true` is returned for the node.
    """

    name = "data:xml"
    description = "Parse an XML file (safe via defusedxml). Returns a dict tree of {tag, attrib, text, children}."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "from": {"type": "string", "enum": ["working", "inputs"]},
            "max_depth": {"type": "integer", "description": "default 8"},
        },
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        source = arguments.get("from", "working")
        max_depth = int(arguments.get("max_depth", 8))

        text, err = await _read_text(self.files, path, source)
        if err is not None:
            return err

        try:
            root = DefusedET.fromstring(text)  # type: ignore[arg-type]
        except DefusedET.ParseError as e:
            return _err(f"xml parse failed: {e}")
        except Exception as e:
            # defusedxml raises distinct exceptions for entity attacks etc.
            return _err(f"xml rejected: {e}")

        tree = _xml_elem_to_dict(root, max_depth=max_depth)
        return {"ok": True, "path": path, "root": tree}
