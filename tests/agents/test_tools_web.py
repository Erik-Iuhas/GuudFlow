"""Tests for agents/tools/web.py."""

from __future__ import annotations

from agents.tools.web import WebFetch, WebSearch


# ── web:search ─────────────────────────────────────────────────────────────


async def test_web_search_no_handler_returns_no_handler_flag():
    res = await WebSearch().run({"query": "anything"})
    assert res.get("no_handler") is True
    assert res["results"] == []


async def test_web_search_with_handler():
    async def handler(query: str, limit: int) -> list[dict]:
        return [{"title": "result", "url": "https://x", "snippet": query}]
    res = await WebSearch(handler=handler).run({"query": "agentflow", "limit": 3})
    assert res["ok"] is True
    assert res["results"][0]["snippet"] == "agentflow"


async def test_web_search_missing_query():
    res = await WebSearch().run({})
    assert "error" in res


# ── web:fetch ──────────────────────────────────────────────────────────────


async def test_web_fetch_no_handler():
    res = await WebFetch().run({"url": "https://example.com"})
    assert res.get("no_handler") is True


async def test_web_fetch_with_handler():
    async def handler(url: str) -> dict:
        return {"status": 200, "text": "<html>...</html>"}
    res = await WebFetch(handler=handler).run({"url": "https://example.com"})
    assert res["ok"] is True
    assert res["status"] == 200
    assert "html" in res["text"]


async def test_web_fetch_rejects_non_http_url():
    res = await WebFetch().run({"url": "ftp://insecure"})
    assert "error" in res
