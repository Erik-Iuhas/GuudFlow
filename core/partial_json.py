"""Read finished array elements out of a JSON document that is still being written.

Used to preview a model's tool call while it streams: the arguments arrive as text, and every
element of `"changes": [...]` whose closing brace has arrived can already be shown. Only whole
elements are returned, each parsed with `json.loads`; nothing is guessed.
"""

from __future__ import annotations

import json
import re
from typing import Any


def complete_items(text: str, key: str) -> list[dict[str, Any]]:  # Any: JSON objects
    """The finished objects of the array under `key` in partial JSON `text`."""
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*\[', text)
    if m is None:
        return []
    items: list[dict[str, Any]] = []  # Any: JSON objects
    depth = 0
    start = -1
    in_str = escaped = False
    for i in range(m.end(), len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            if depth == 0 and ch == "{":
                start = i
            depth += 1
        elif ch in "}]":
            if depth == 0:  # the array itself closed
                break
            depth -= 1
            if depth == 0 and ch == "}" and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except ValueError:
                    obj = None
                if isinstance(obj, dict):
                    items.append(obj)
                start = -1
    return items
