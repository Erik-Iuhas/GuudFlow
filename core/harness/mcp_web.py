"""Host-side `web_search` and `web_fetch` MCP tools (plan §5.8).

Sandboxes never get internet access. A role granted these tools reaches the web
through the host, which enforces:

  - a domain policy (allow/deny lists, deny wins, an entry matches itself and
    its subdomains), applied to the requested URL and to every redirect hop;
  - an SSRF guard: private, loopback, link-local, multicast, reserved and
    unspecified addresses (including IPv4-mapped / 6to4 / NAT64 / Teredo forms
    of them) are always refused, whether the host is a literal IP or a name
    that resolves to one;
  - per-run and per-role request budgets, with a per-run cache whose hits are
    free;
  - an audit trail: every call, including hits and refusals, posts a
    `WEB_REQUEST` board signal;
  - untrusted-content delimiters around everything that came from the web.

Search-provider API keys come from `ctx.secret(...)` (the host's credential
store) and are only ever placed in the outbound provider request. They never
appear in tool results, signals or log lines.

Tools never raise: every failure is a `ToolResult(..., is_error=True)`.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from core.harness.brief import wrap_untrusted
from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.tool_context import RunToolContext
from core.signals import SignalType

log = logging.getLogger(__name__)

Resolver = Callable[[str], Awaitable[list[str]]]

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"
NOT_CONFIGURED = "web search is not configured (set BRAVE_API_KEY or TAVILY_API_KEY on the host)"

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
MAX_REDIRECTS = 5
MAX_RESULTS_CAP = 10
SNIPPET_CHARS = 300
USER_AGENT = "GuudFlow-web/1 (+host-side fetch)"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TEXT_TYPES = frozenset({"application/json", "application/xml", "application/xhtml+xml"})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_LOCAL_NAMES = ("localhost", ".localhost", ".local", ".internal")


class _Refused(Exception):
    """Internal: a request was refused for `reason` (never escapes this module)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Outcome:
    """What one tool call did, for the result and the audit signal."""

    result: ToolResult
    final_url: str | None = None
    status: int | None = None
    cache_hit: bool = False
    nbytes: int = 0
    refused: str | None = None


@dataclass(frozen=True)
class _Settings:
    """Parsed `web` project settings."""

    allow_domains: tuple[str, ...]
    deny_domains: tuple[str, ...]
    max_requests_per_run: int
    max_requests_per_role: int
    max_fetch_bytes: int
    max_text_chars: int
    search_provider: str | None
    timeout_s: float

    @classmethod
    def from_ctx(cls, ctx: RunToolContext) -> "_Settings":
        """Read `ctx.settings["web"]`, applying defaults."""
        raw = ctx.settings.get("web", {}) or {}

        def domains(key: str) -> tuple[str, ...]:
            return tuple(d.strip().lower().strip(".") for d in raw.get(key, []) or [] if str(d).strip())

        provider = raw.get("search_provider")
        return cls(
            allow_domains=domains("allow_domains"),
            deny_domains=domains("deny_domains"),
            max_requests_per_run=int(raw.get("max_requests_per_run", 40)),
            max_requests_per_role=int(raw.get("max_requests_per_role", 25)),
            max_fetch_bytes=int(raw.get("max_fetch_bytes", 1_000_000)),
            max_text_chars=int(raw.get("max_text_chars", 20_000)),
            search_provider=str(provider).lower() if provider else None,
            timeout_s=float(raw.get("timeout_s", 20)),
        )


# ── domain policy ───────────────────────────────────────────────────────────
def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _check_domain(host: str, cfg: _Settings) -> None:
    """Raise `_Refused` unless `host` passes the allow/deny lists (deny wins)."""
    if any(_matches(host, d) for d in cfg.deny_domains):
        raise _Refused(f"domain {host} is denied by project policy")
    if cfg.allow_domains and not any(_matches(host, d) for d in cfg.allow_domains):
        raise _Refused(f"domain {host} is not on the project allow-list")


# ── SSRF guard ──────────────────────────────────────────────────────────────
def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that must never be reached from the host."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded: list[ipaddress.IPv4Address] = []
        if ip.ipv4_mapped is not None:
            embedded.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        # NAT64 well-known prefix 64:ff9b::/96 carries an IPv4 address in its low 32 bits.
        if ip in ipaddress.IPv6Network("64:ff9b::/96"):
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(_ip_is_blocked(v4) for v4 in embedded):
            return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or not ip.is_global)


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


async def _default_resolver(host: str) -> list[str]:
    """Resolve `host` with the running loop's getaddrinfo."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def _check_host_not_internal(host: str, resolver: Resolver) -> None:
    """Raise `_Refused` if `host` is, or resolves to, a non-public address.

    Limitation (TOCTOU): the HTTP connection re-resolves the name, so a hostile
    DNS server could answer differently the second time (DNS rebinding). The
    sandbox has no network anyway; this guard protects the host, and closing the
    gap fully would mean pinning the connection to the vetted IP.
    """
    literal = _parse_ip(host)
    if literal is not None:
        if _ip_is_blocked(literal):
            raise _Refused(f"address {host} is not a public address")
        return
    if host == "localhost" or host.endswith(_LOCAL_NAMES):
        raise _Refused(f"host {host} is a local name")
    try:
        addresses = await resolver(host)
    except (OSError, asyncio.TimeoutError) as exc:
        raise _Refused(f"could not resolve {host} ({type(exc).__name__})") from exc
    if not addresses:
        raise _Refused(f"could not resolve {host}")
    for address in addresses:
        ip = _parse_ip(address)
        if ip is None or _ip_is_blocked(ip):
            raise _Refused(f"host {host} resolves to a non-public address")


# ── URL handling ────────────────────────────────────────────────────────────
def _normalise_url(url: str) -> str:
    """Canonical form for cache keys and hops: lowercase scheme/host, no fragment."""
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


async def _vet_url(url: str, cfg: _Settings, resolver: Resolver) -> str:
    """Validate one URL (the request or a redirect hop); return its host."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise _Refused(f"only http and https URLs are allowed (got {parts.scheme or 'none'!r})")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _Refused("URLs with embedded credentials are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise _Refused("invalid port in URL") from exc
    if port is not None and port not in ALLOWED_PORTS:
        raise _Refused(f"port {port} is not allowed (use the default port, 80, 443, 8080 or 8443)")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise _Refused("URL has no host")
    _check_domain(host, cfg)
    await _check_host_not_internal(host, resolver)
    return host


# ── HTML → text ─────────────────────────────────────────────────────────────
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe", "template"})
_BLOCK_TAGS = frozenset({"p", "div", "section", "article", "header", "footer", "main", "nav",
                         "aside", "ul", "ol", "table", "tr", "blockquote", "pre", "form",
                         "figure", "figcaption", "dl", "dt", "dd", "hr"})
_LINE_TAGS = frozenset({"br", "li"})
_HEADINGS = {f"h{n}": n for n in range(1, 7)}


class _TextExtractor(HTMLParser):
    """Collect readable text: headings and paragraph breaks kept, junk dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._parts: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "title":
            self._in_title = True
        elif tag in _HEADINGS:
            self._parts.append("\n\n" + "#" * _HEADINGS[tag] + " ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")
        elif tag in _LINE_TAGS:
            self._parts.append("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif self._skip:
            return
        elif tag == "title":
            self._in_title = False
        elif tag in _HEADINGS or tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        else:
            # Source newlines are layout, not structure: only tags create line breaks.
            self._parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        """The extracted text with whitespace collapsed."""
        body = _collapse("".join(self._parts))
        title = " ".join(self.title.split())
        return f"Title: {title}\n\n{body}".strip() if title else body


def _collapse(text: str) -> str:
    """Collapse spaces within lines and limit blank lines to one."""
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def html_to_text(markup: str) -> str:
    """Readable text of an HTML document (stdlib parser, no scripts or styles)."""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return parser.text()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n[truncated]"


def _plain(text: str, limit: int) -> str:
    """Strip tags/entities from a provider-supplied snippet and bound its length."""
    clean = " ".join(html.unescape(re.sub(r"<[^>]*>", "", text or "")).split())
    return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + "…"


def _charset(content_type: str) -> str:
    match = re.search(r"charset=\"?([\w.-]+)", content_type, re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _decode(raw: bytes, content_type: str) -> str:
    try:
        return raw.decode(_charset(content_type), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


# ── the tools ───────────────────────────────────────────────────────────────
@dataclass
class _WebTools:
    """Per-run state (cache, budgets) shared by the two tools."""

    ctx: RunToolContext
    cfg: _Settings
    transport: httpx.AsyncBaseTransport | None
    resolver: Resolver
    cache: dict[tuple[str, str], tuple[str, str, str | None]] = field(default_factory=dict)
    run_count: int = 0
    role_counts: dict[str, int] = field(default_factory=dict)

    # ── shared plumbing ─────────────────────────────────────────────────────
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=self.cfg.timeout_s,
                                 follow_redirects=False, headers={"User-Agent": USER_AGENT})

    def _spend(self, role: str) -> None:
        """Charge one cache miss to the run and role budgets, or raise `_Refused`."""
        if self.run_count >= self.cfg.max_requests_per_run:
            raise _Refused(f"web request budget for this run is used up "
                           f"({self.cfg.max_requests_per_run})")
        if self.role_counts.get(role, 0) >= self.cfg.max_requests_per_role:
            raise _Refused(f"web request budget for role {role!r} is used up "
                           f"({self.cfg.max_requests_per_role})")
        self.run_count += 1
        self.role_counts[role] = self.role_counts.get(role, 0) + 1

    def _audit(self, role: str, tool: str, subject: str, outcome: _Outcome) -> None:
        verb = "search" if tool == "web_search" else "fetch"
        note = " (cache hit)" if outcome.cache_hit else (
            f" (refused: {outcome.refused})" if outcome.refused else "")
        shown = subject if len(subject) <= 200 else subject[:200] + "…"
        detail: dict[str, Any] = {
            "tool": tool,
            "query" if tool == "web_search" else "url": subject,
            "final_url": outcome.final_url, "status": outcome.status,
            "cache_hit": outcome.cache_hit, "bytes": outcome.nbytes, "refused": outcome.refused,
        }
        try:
            self.ctx.post(SignalType.WEB_REQUEST, role, f"{role}: {verb} {shown}{note}", detail)
        except Exception:  # noqa: BLE001 - auditing must not turn a result into a crash
            log.exception("could not post WEB_REQUEST signal")

    async def _run(self, identity: McpIdentity, tool: str, subject: str,
                   work: Callable[[str], Awaitable[_Outcome]],
                   state: _Outcome | None = None) -> ToolResult:
        """Run `work` with the never-raise and always-audit guarantees.

        `state` carries hop details (final URL, status, bytes) gathered before a
        failure so the audit signal still reports them.
        """
        seen = state or _Outcome(ToolResult(""))
        try:
            outcome = await work(identity.role)
        except _Refused as exc:
            outcome = _Outcome(ToolResult(f"refused: {exc.reason}", is_error=True),
                               final_url=seen.final_url, status=seen.status,
                               nbytes=seen.nbytes, refused=exc.reason)
        except asyncio.TimeoutError:
            outcome = _Outcome(ToolResult("request timed out", is_error=True),
                               final_url=seen.final_url, status=seen.status)
        except Exception as exc:  # noqa: BLE001 - tools never raise
            log.exception("%s failed", tool)
            outcome = _Outcome(ToolResult(f"{tool} failed ({type(exc).__name__})", is_error=True),
                               final_url=seen.final_url, status=seen.status)
        self._audit(identity.role, tool, subject, outcome)
        return outcome.result

    # ── web_search ──────────────────────────────────────────────────────────
    async def search(self, identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        """`web_search` handler."""
        query = " ".join(str(args.get("query", "")).split())
        try:
            count = max(1, min(MAX_RESULTS_CAP, int(args.get("max_results", 5))))
        except (TypeError, ValueError):
            count = 5

        async def work(role: str) -> _Outcome:
            if not query:
                raise _Refused("query is empty")
            key = ("web_search", f"{query.lower()}|{count}")
            if key in self.cache:
                label, text, _ = self.cache[key]
                return _Outcome(ToolResult(wrap_untrusted(label, text)), cache_hit=True,
                                nbytes=len(text.encode()))
            provider = self._provider()
            self._spend(role)
            async with asyncio.timeout(self.cfg.timeout_s):
                async with self._client() as client:
                    if provider == "brave":
                        resp = await client.get(
                            BRAVE_URL, params={"q": query, "count": count},
                            headers={"X-Subscription-Token": self.ctx.secret("BRAVE_API_KEY") or "",
                                     "Accept": "application/json"})
                    else:
                        resp = await client.post(TAVILY_URL, json={
                            "api_key": self.ctx.secret("TAVILY_API_KEY"),
                            "query": query, "max_results": count})
            if resp.status_code != 200:
                return _Outcome(ToolResult(f"search provider {provider} returned HTTP "
                                           f"{resp.status_code}", is_error=True),
                                status=resp.status_code)
            try:
                payload = resp.json()
            except ValueError:
                return _Outcome(ToolResult(f"search provider {provider} returned invalid JSON",
                                           is_error=True), status=resp.status_code)
            raw = (payload.get("web", {}).get("results", []) if provider == "brave"
                   else payload.get("results", []))
            lines: list[str] = []
            for item in raw if isinstance(raw, list) else []:
                url = str(item.get("url", ""))
                if not self._result_url_ok(url):
                    continue
                snippet = item.get("description" if provider == "brave" else "content", "")
                lines.append(f"{len(lines) + 1}. {_plain(str(item.get('title', '')), 200)} — "
                             f"{url} — {_plain(str(snippet), SNIPPET_CHARS)}")
                if len(lines) >= count:
                    break
            text = _truncate("\n".join(lines) if lines else "(no results)", self.cfg.max_text_chars)
            label = "web search results"
            self.cache[key] = (label, text, None)
            return _Outcome(ToolResult(wrap_untrusted(label, text)), status=resp.status_code,
                            nbytes=len(text.encode()))

        return await self._run(identity, "web_search", query, work)

    def _provider(self) -> str:
        """Pick the search provider whose key exists, or raise `_Refused`."""
        has = {"brave": bool(self.ctx.secret("BRAVE_API_KEY")),
               "tavily": bool(self.ctx.secret("TAVILY_API_KEY"))}
        wanted = self.cfg.search_provider
        if wanted is not None:
            if wanted not in has:
                raise _Refused(f"unknown search provider {wanted!r} (use 'brave' or 'tavily')")
            if not has[wanted]:
                raise _Refused(NOT_CONFIGURED)
            return wanted
        for name in ("brave", "tavily"):
            if has[name]:
                return name
        raise _Refused(NOT_CONFIGURED)

    def _result_url_ok(self, url: str) -> bool:
        """Policy filter for search results (syntax + domain lists, no DNS)."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme.lower() not in ("http", "https") or not host:
            return False
        try:
            _check_domain(host, self.cfg)
        except _Refused:
            return False
        return True

    # ── web_fetch ───────────────────────────────────────────────────────────
    async def fetch(self, identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        """`web_fetch` handler."""
        url = str(args.get("url", "")).strip()
        state = _Outcome(ToolResult(""))

        async def work(role: str) -> _Outcome:
            if not url:
                raise _Refused("url is empty")
            norm = _normalise_url(url)
            key = ("web_fetch", norm)
            if key in self.cache:
                label, text, final = self.cache[key]
                return _Outcome(ToolResult(wrap_untrusted(label, text)), final_url=final,
                                cache_hit=True, nbytes=len(text.encode()))
            await _vet_url(norm, self.cfg, self.resolver)
            self._spend(role)
            return await self._download(norm, key, state)

        return await self._run(identity, "web_fetch", url, work, state)

    async def _download(self, start: str, key: tuple[str, str], state: _Outcome) -> _Outcome:
        """Follow redirects by hand (vetting each hop), stream and convert the body."""
        current = start
        async with asyncio.timeout(self.cfg.timeout_s):
            async with self._client() as client:
                for hop in range(MAX_REDIRECTS + 1):
                    state.final_url = current
                    async with client.stream("GET", current, headers={
                            "Accept": "text/html,text/plain,application/json,application/xml;q=0.9,*/*;q=0.1"}
                    ) as resp:
                        state.status = resp.status_code
                        location = resp.headers.get("location")
                        if resp.status_code in _REDIRECT_STATUSES and location:
                            if hop == MAX_REDIRECTS:
                                raise _Refused(f"too many redirects (more than {MAX_REDIRECTS})")
                            current = _normalise_url(urljoin(current, location))
                            await _vet_url(current, self.cfg, self.resolver)
                            continue
                        if resp.status_code >= 400 or resp.status_code < 200:
                            return _Outcome(ToolResult(f"HTTP {resp.status_code} from {current}",
                                                       is_error=True),
                                            final_url=current, status=resp.status_code)
                        ctype_header = resp.headers.get("content-type", "")
                        ctype = ctype_header.split(";", 1)[0].strip().lower()
                        if not (ctype.startswith("text/") or ctype in _TEXT_TYPES):
                            raise _Refused(f"content type {ctype or 'unknown'!r} is not supported "
                                           "(text, JSON and XML only)")
                        buf = bytearray()
                        async for chunk in resp.aiter_bytes():
                            buf += chunk
                            if len(buf) >= self.cfg.max_fetch_bytes:
                                break
                        raw = bytes(buf[:self.cfg.max_fetch_bytes])
                        state.nbytes = len(raw)
                        text = _decode(raw, ctype_header)
                        if ctype in _HTML_TYPES:
                            text = html_to_text(text)
                        else:
                            text = text.strip()
                        text = _truncate(text, self.cfg.max_text_chars)
                        label = f"web page {current}"
                        self.cache[key] = (label, text, current)
                        return _Outcome(ToolResult(wrap_untrusted(label, text)), final_url=current,
                                        status=resp.status_code, nbytes=len(raw))
        raise _Refused("too many redirects")  # pragma: no cover - loop always returns or raises


def tools(ctx: RunToolContext, *, transport: httpx.AsyncBaseTransport | None = None,
          resolver: Resolver | None = None) -> list[McpTool]:
    """Build `web_search` and `web_fetch` for one run.

    `transport` and `resolver` exist for tests (mock HTTP, fake DNS); production
    leaves both unset. Budgets and the cache live in the returned closures.
    """
    web = _WebTools(ctx=ctx, cfg=_Settings.from_ctx(ctx), transport=transport,
                    resolver=resolver or _default_resolver)
    return [
        McpTool(
            name="web_search",
            description=("Search the web. Returns numbered `title — url — snippet` lines, "
                         "wrapped as untrusted data. Counts against the run's web budget."),
            input_schema={"type": "object", "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS_CAP,
                                "default": 5}}, "required": ["query"]},
            handler=web.search),
        McpTool(
            name="web_fetch",
            description=("Fetch one http(s) page and return its readable text, wrapped as "
                         "untrusted data. Private network addresses are refused."),
            input_schema={"type": "object", "properties": {
                "url": {"type": "string", "description": "Absolute http or https URL."}},
                "required": ["url"]},
            handler=web.fetch),
    ]
