"""complete_items: finished array elements out of half-written JSON."""

from core.partial_json import complete_items


def test_only_finished_elements_are_returned() -> None:
    text = '{"groups": [{"name": "a"}], "changes": [{"op": "add_item", "title": "One"}, {"op": "add'
    assert complete_items(text, "changes") == [{"op": "add_item", "title": "One"}]
    assert complete_items(text, "groups") == [{"name": "a"}]


def test_braces_quotes_and_escapes_inside_strings() -> None:
    text = ('{"changes": [{"op": "x", "text": "a } b ] c { \\"quoted\\""}, '
            '{"op": "y", "checks": ["one", "two"], "params": {"k": 1}}, {"op"')
    assert complete_items(text, "changes") == [
        {"op": "x", "text": 'a } b ] c { "quoted"'},
        {"op": "y", "checks": ["one", "two"], "params": {"k": 1}}]


def test_nothing_before_the_key_or_after_the_array() -> None:
    assert complete_items('{"reply": "hi", "chan', "changes") == []
    assert complete_items('{"changes": [], "reply": "{\\"op\\": 1}"}', "changes") == []
