"""ContextManager — the single place all agent context decisions happen.

Agents call `get_context(task, role, max_tokens)` and receive an `AgentContext`
that fits in budget. They MUST NOT read files directly for codebase
understanding (DESIGN rule 55, §28).

Default behaviour:
- The repo map is always included. It is cheap (~3-5k tokens) and always
  useful (DESIGN rule 56).
- Full file contents are pull-not-push, ranked by task-keyword relevance.
- Embeddings are off by default. Symbol search is the fallback.

Track 3a: the iteration-2 wholesale-code-diff path was removed. Treating
source code as a document-style diff broke edit semantics — `fs-edit`
relies on byte-identical content for its exact-match `find` strings.
Agents that want to review their own commits use the `git-diff` tool
explicitly, scoped to a path. The `include_diff` parameter is retained
for backward compatibility but is now a no-op.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.repo_map import _approx_tokens, render_compact
from core.signals import Signal
from core.workspace import ContextSnapshot, RepoMap, Workspace

if TYPE_CHECKING:
    from core.file_workspace import FileWorkspace
    from core.git_workspace import GitWorkspace


# Per-tier defaults from DESIGN §28 / CLAUDE.md.
TIER_BUDGETS: dict[str, int] = {
    "heavy": 12000,
    "solid": 6000,
    "fast": 2000,
}


@dataclass
class SearchResult:
    path: str
    snippet: str
    relevance_score: float


@dataclass
class AgentContext:
    """What an agent receives from `get_context()`. The runner logs
    `token_count` per agent per iteration (DESIGN rule 59)."""

    repo_map: str
    relevant_files: dict[str, str] = field(default_factory=dict)
    git_diff: str | None = None
    recent_signals: list[Signal] = field(default_factory=list)
    token_count: int = 0


# ── Helpers ─────────────────────────────────────────────────────────────────


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def _split_camel_snake(name: str) -> list[str]:
    """`AuthManager` → ['auth', 'manager']; `auth_manager` → ['auth', 'manager']."""
    parts = re.split(r"[_\s\-]+", name)
    out: list[str] = []
    for p in parts:
        out.extend(re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)", p) or [p])
    return [p.lower() for p in out if p]


def _tokens(text: str) -> set[str]:
    """Identifier-ish tokens for keyword matching. Includes both the
    whole identifier and its camel/snake-split components so a query for
    `AuthManager` matches a symbol named `AuthManager` regardless of
    which side does the splitting."""
    out: set[str] = set()
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        out.add(word.lower())
        out.update(_split_camel_snake(word))
    return out


def _score_entry(entry, query_tokens: set[str]) -> float:
    """How relevant is a repo_map entry to the query? Combines path-token
    overlap, symbol-token overlap, and the entry's PageRank as a tiebreak."""
    if not query_tokens:
        return entry.rank

    path_tokens = _tokens(entry.path)
    symbol_tokens: set[str] = set()
    for s in entry.symbols:
        symbol_tokens.update(_split_camel_snake(s))

    path_hits = len(path_tokens & query_tokens)
    sym_hits = len(symbol_tokens & query_tokens)
    if path_hits == 0 and sym_hits == 0:
        return 0.0
    return 2.0 * sym_hits + 1.0 * path_hits + 0.1 * entry.rank


# ── ContextManager ──────────────────────────────────────────────────────────


class ContextManager:
    """Builds AgentContext objects. One instance per run.

    Parameters
    ----------
    workspace : Workspace
        The run's workspace. Must have a populated `repo_map` field for
        meaningful output; an empty map is handled gracefully.
    files : FileWorkspace | None
        Used to read full file contents for the relevant_files slice.
        If None, full contents are skipped (only the repo map is returned).
    git : GitWorkspace | None
        Used to compute git_diff for iteration ≥ 2. If None, no diff is
        produced even when `include_diff=True`.
    repo_map_budget_ratio : float
        Fraction of total token budget reserved for the repo map. Default
        0.4 — leaves majority for relevant file contents.
    """

    def __init__(
        self,
        workspace: Workspace,
        files: "FileWorkspace | None" = None,
        git: "GitWorkspace | None" = None,
        repo_map_budget_ratio: float = 0.4,
    ):
        self.workspace = workspace
        self.files = files
        self.git = git
        self.repo_map_budget_ratio = repo_map_budget_ratio

    async def get_context(
        self,
        task: str,
        agent_role: str,
        max_tokens: int | None = None,
        tier: str = "solid",
        include_diff: bool = True,
        recent_signals_limit: int = 5,
    ) -> AgentContext:
        if max_tokens is None:
            max_tokens = TIER_BUDGETS.get(tier, TIER_BUDGETS["solid"])

        repo_map: RepoMap = self.workspace.repo_map
        repo_map_budget = int(max_tokens * self.repo_map_budget_ratio)
        repo_map_text = render_compact(repo_map, max_tokens=repo_map_budget) if repo_map.entries else ""
        used = _approx_tokens(repo_map_text) if repo_map_text else 0

        ctx = AgentContext(repo_map=repo_map_text)

        # Track 3a: the iteration-2 wholesale-code-diff path was removed.
        # Wholesale diff treats source as a document — but source has
        # exact-match edit semantics via fs-edit. Compressing or summarizing
        # via diff would silently break subsequent edits. Agents that need
        # diffs request them explicitly via the git-diff tool, scoped to a
        # path. `include_diff` parameter retained for backward-compat
        # (now a no-op).
        _ = include_diff  # acknowledged deprecated

        # Fill remaining budget with full contents of top-ranked relevant files.
        # Track per-file token counts for the ContextSnapshot.
        _file_token_pairs: list[tuple[str, int]] = []
        if self.files is not None and repo_map.entries:
            query_tokens = _tokens(task)
            scored = [(_score_entry(e, query_tokens), e) for e in repo_map.entries]
            scored.sort(key=lambda pair: pair[0], reverse=True)

            for score, entry in scored:
                if score <= 0:
                    break
                try:
                    content = await self.files.read(entry.path)
                except Exception:
                    continue
                cost = _approx_tokens(content)
                if used + cost > max_tokens:
                    continue
                ctx.relevant_files[entry.path] = content
                _file_token_pairs.append((entry.path, cost))
                used += cost

        # Recent signals relevant to this role.
        ctx.recent_signals = self._recent_signals(agent_role, recent_signals_limit)
        used += sum(_approx_tokens(s.summary) for s in ctx.recent_signals)

        ctx.token_count = used

        # Build and record a ContextSnapshot for Track 1 analytics.
        _repo_map_tokens = _approx_tokens(repo_map_text) if repo_map_text else 0
        _diff_tokens = _approx_tokens(ctx.git_diff) if ctx.git_diff else 0
        snapshot = ContextSnapshot(
            run_id=self.workspace.run_id,
            role=agent_role,
            iteration=self.workspace.iteration,
            task=task[:500],
            tier=tier,
            max_tokens=max_tokens,
            used_tokens=used,
            repo_map_tokens=_repo_map_tokens,
            git_diff_tokens=_diff_tokens,
            relevant_files=_file_token_pairs,
            recent_signals_count=len(ctx.recent_signals),
            timestamp=time.time(),
        )
        self.workspace.record_context_snapshot(snapshot)

        return ctx

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """Symbol search across repo_map. Embeddings path is left as a
        future hook — when `workspace.embeddings_enabled` is true and an
        embedding store is wired in, this method should fan out to that
        store; for now it always uses symbol search."""
        repo_map = self.workspace.repo_map
        if not repo_map.entries:
            return []

        query_tokens = _tokens(query)
        scored = [(_score_entry(e, query_tokens), e) for e in repo_map.entries]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        results: list[SearchResult] = []
        for score, entry in scored[:limit]:
            if score <= 0:
                break
            snippet_parts = []
            if entry.symbols:
                snippet_parts.append(", ".join(entry.symbols[:6]))
            if entry.imports:
                snippet_parts.append("imports: " + ", ".join(entry.imports[:4]))
            results.append(SearchResult(
                path=entry.path,
                snippet=" | ".join(snippet_parts),
                relevance_score=score,
            ))
        return results

    # ── Internal ────────────────────────────────────────────────────────────

    def _recent_signals(self, role: str, limit: int) -> list[Signal]:
        relevant: list[Signal] = []
        for sig in reversed(self.workspace.board):
            if sig.from_agent == role or role in sig.seen_by:
                continue  # already absorbed or own output, skip
            relevant.append(sig)
            if len(relevant) >= limit:
                break
        relevant.reverse()
        return relevant
