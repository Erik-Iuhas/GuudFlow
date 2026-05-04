"""Thin HTTP client around the GuudFlow API.

Wraps `httpx` (already in requirements.txt) with bearer-auth defaults
and friendly errors. The CLI never speaks WebSocket — `runs watch`
polls the board endpoint instead so we don't pull in another
dependency just for live tailing.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 30.0


class APIError(RuntimeError):
    """Raised for non-2xx HTTP responses. Carries status + body."""

    def __init__(self, status: int, detail: str, url: str):
        super().__init__(f"{status} {detail} ({url})")
        self.status = status
        self.detail = detail
        self.url = url


class Client:
    """Synchronous httpx wrapper. The CLI is short-lived, so async
    buys nothing here — sync keeps argparse handlers flat."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = (
            base_url
            or os.environ.get("GUUDFLOW_API_URL")
            or DEFAULT_API_URL
        ).rstrip("/")
        self.token = token if token is not None else os.environ.get("GUUDFLOW_API_TOKEN")
        self.timeout = timeout

    # ---- low-level ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
                params=params,
                timeout=self.timeout,
            )
        except httpx.RequestError as e:
            raise APIError(0, f"connection error: {e}", url) from e

        if resp.status_code == 204 or not resp.content:
            return None

        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text}

        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else str(data)
            raise APIError(resp.status_code, str(detail), url)
        return data

    # ---- generic verbs ------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("POST", path, json_body=json_body, params=params)

    def put(self, path: str, json_body: Any | None = None) -> Any:
        return self._request("PUT", path, json_body=json_body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # ---- typed shortcuts (mirror api/routes/*.py) ---------------------------

    def health(self) -> dict[str, Any]:
        return self.get("/health")

    def dashboard(self) -> dict[str, Any]:
        return self.get("/api/dashboard")

    def list_projects(self) -> list[dict[str, Any]]:
        return self.get("/api/projects") or []

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.get(f"/api/projects/{project_id}")

    def delete_project(self, project_id: str) -> None:
        self.delete(f"/api/projects/{project_id}")

    def list_runs(
        self, project_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        return self.get("/api/runs", params=params) or []

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.get(f"/api/runs/{run_id}")

    def get_run_board(self, run_id: str) -> list[dict[str, Any]]:
        return self.get(f"/api/runs/{run_id}/board") or []

    def post_run_comment(
        self, run_id: str, text: str, priority: str = "normal"
    ) -> dict[str, Any]:
        return self.post(
            f"/api/runs/{run_id}/comment",
            json_body={"text": text, "priority": priority},
        )

    def post_run_approval(
        self, run_id: str, approval_id: str, decision: str, note: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"decision": decision}
        if note:
            body["note"] = note
        return self.post(f"/api/runs/{run_id}/approval/{approval_id}", json_body=body)

    def stop_run(self, run_id: str) -> dict[str, Any]:
        return self.post(f"/api/runs/{run_id}/stop")

    def resume_run(self, run_id: str) -> dict[str, Any]:
        return self.post(f"/api/runs/{run_id}/resume")

    def list_personas(self) -> list[dict[str, Any]]:
        return self.get("/api/personas") or []

    def get_persona(self, persona_id: str) -> dict[str, Any]:
        return self.get(f"/api/personas/{persona_id}")

    def list_examples(self) -> list[dict[str, Any]]:
        return self.get("/api/example-projects") or []

    def instantiate_example(
        self,
        template_id: str,
        project_id: str | None = None,
        goal: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if project_id:
            body["project_id"] = project_id
        if goal:
            body["goal"] = goal
        return self.post(
            f"/api/example-projects/{template_id}/instantiate", json_body=body
        )

    def propose(
        self, goal: str, project_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"goal": goal}
        if project_id:
            body["project_id"] = project_id
        return self.post("/api/meta/propose", json_body=body)

    def invoke(
        self, project_id: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.post(f"/api/invoke/{project_id}", json_body=payload or {})
