"""Scheduler — the single most load-bearing file in the project.

DESIGN §3.3 makes this the single point of dependency resolution and
concurrency. Every execution pattern (sequential, parallel fan-out, join,
working-group scoping, human pause, max-iteration cutoff) is implemented
entirely here. There is NO `asyncio.gather` outside this module — concurrency
happens here.

The class is reusable. PipelineScheduler and GroupScheduler are the same
class, instantiated at different scopes (different agent collection, different
workspace). The `scope` field is informational; the logic is identical.

Contract for `AgentRunner` callables (Stage 3 — pre-BaseAgent):
- A runner mutates the passed workspace.
- On normal completion: append the role to `workspace.completed_agents`,
  set `workspace.agent_states[role] = AgentState.COMPLETE`, and post any
  COMPLETE/APPROVED signal it wants on the board.
- On hard pause: set `workspace.pending_human = HumanRequest(...)`, set
  `workspace.agent_states[role] = AgentState.WAITING_FOR_HUMAN`, post a
  HUMAN_NEEDED signal, then return. The scheduler will respawn the runner
  after the pause is cleared. The runner sees the cleared `pending_human`
  on the next entry and continues from there.
- On error: raise. The scheduler marks the role failed and continues.

The scheduler never executes work itself — it owns the lifecycle and decides
which runners to invoke when. Runners do the work.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Literal

from core.workspace import AgentState, Workspace

log = logging.getLogger(__name__)


AgentRunner = Callable[[Workspace], Awaitable[None]]


# ── Interrupt / resume dataclasses ───────────────────────────────────────────

@dataclass
class WaitingForHuman:
    kind: Literal["human", "visual"] = "human"
    question: str = ""


@dataclass
class WaitingForPeer:
    target_role: str = ""
    ask_id: str = ""
    deadline_ts: float = 0.0
    reason_if_timeout: str = "PEER_UNRESPONSIVE"


@dataclass
class WaitingForSkipConfirm:
    confirmer_role: str = ""
    skip_id: str = ""
    deadline_ts: float = 0.0
    reason_if_timeout: str = "SKIP_NEEDS_HUMAN"


InterruptPayload = WaitingForHuman | WaitingForPeer | WaitingForSkipConfirm
InterruptKind = Literal["human", "peer", "skip"]


@dataclass
class InterruptToken:
    agent_role: str
    kind: InterruptKind
    id: str  # ask_id for peer; skip_id for skip; question text for human


@dataclass
class AgentSpec:
    """A single agent slot in this scheduler's scope.

    `runner` is the callable that does the work. In Stage 4+ this will be
    `BaseAgent.run`. For Stage 3 we contract directly on the callable so
    the scheduler can be tested without any agent class.
    """

    role: str
    runner: AgentRunner
    depends_on: list[str] = field(default_factory=list)
    # Review edge (plan §4.5): how many times this role may send work back to
    # its authors via `Scheduler.request_rework` before escalation.
    max_review_rounds: int = 2
    # Task board (plan §4.6): when set, the role is only spawned while this
    # returns True ("there is a card for you now"). Keeps the decision of WHEN
    # to spawn here, while the board decides WHAT the role works on.
    has_work: Callable[[], bool] | None = None


@dataclass(frozen=True)
class BlockOutcome:
    """What `Scheduler.block()` did."""

    action: Literal["rearmed", "waiting", "cycle", "unknown_role"]
    cycle: tuple[str, ...] = ()


class SchedulerStatus(str, Enum):
    COMPLETE = "complete"                       # every agent reached completed_agents
    MAX_ITERATIONS = "max_iterations"           # an agent was respawned past the cap
    STUCK = "stuck"                             # nothing spawnable, nothing running, not complete
    DEADLINE_EXCEEDED = "deadline_exceeded"     # workspace.deadline_at passed (hard mode)
    TIME_LIMIT = "time_limit"                   # soft deadline passed, running work finished
    STOPPED = "stopped"                         # force_cancel() — user pressed Stop


@dataclass
class SchedulerResult:
    status: SchedulerStatus
    completed: list[str]
    failed: list[str]
    spawn_counts: dict[str, int]
    sat_out: list[str] = field(default_factory=list)


class Scheduler:
    """Owns the lifecycle of a collection of agents.

    Single instance per scope. The pipeline scheduler operates on the
    pipeline-level agent set; group schedulers (one per group) operate on
    each group's internal agent set. Same class, different inputs.

    Concurrency rules (HARD):
    - This is the ONLY place in the codebase that may call
      `asyncio.create_task` for agent execution.
    - There is no `asyncio.gather` anywhere in this codebase outside
      this file.
    """

    def __init__(
        self,
        agents: list[AgentSpec],
        workspace: Workspace,
        scope: str = "pipeline",
        max_iterations: int | None = None,
        *,
        transition_rules: bool = True,
        max_parallel: int | None = None,
        watchdog: Callable[[], None] | None = None,
        watchdog_interval: float = 30.0,
    ):
        if len({a.role for a in agents}) != len(agents):
            raise ValueError("duplicate agent roles in scheduler scope")
        self.agents: dict[str, AgentSpec] = {a.role: a for a in agents}
        self.workspace = workspace
        self.scope = scope
        self.max_iterations = (
            max_iterations if max_iterations is not None else workspace.max_iterations
        )

        self._tasks: dict[str, asyncio.Task] = {}
        self._spawn_count: dict[str, int] = defaultdict(int)
        self._failed: set[str] = set()
        self._sat_out: set[str] = set()   # roles that got SAT_OUT — count as done
        self._wake = asyncio.Event()
        self._force_stopped: bool = False  # set by force_cancel(); main loop exits via STOPPED

        # interrupt/resume state
        self._interrupt_payloads: dict[str, InterruptPayload] = {}
        self._interrupt_tokens: dict[str, InterruptToken] = {}
        # queued values injected by resume() — runner drains at agent re-entry
        self._pending_input: dict[str, list[Any]] = defaultdict(list)

        # allowed-speaker-transition rules (AutoGen-style, M1 §10). The opencode
        # harness turns them off: it drives rework through request_rework().
        self._transition_rules: dict[tuple[str, str], frozenset[str]] = {}
        if transition_rules:
            self._build_transition_rules()

        # opencode harness primitives (plan §4.4–§4.6)
        self.max_parallel = max_parallel
        # Stall watchdog (plan §11.2): called every loop pass and at least every
        # `watchdog_interval` seconds while work is in flight. It may re-arm
        # roles (the overseer); it must not block.
        self.watchdog = watchdog
        self.watchdog_interval = watchdog_interval
        # Soft time limit (plan §11.6): once the deadline passes, only roles this allows
        # are started (the run allows reviews of cards already submitted).
        self.drain_filter: Callable[[str], bool] | None = None
        self._paused_since: float | None = None
        self._review_rounds: dict[tuple[str, str], int] = defaultdict(int)
        self._blocked_on: dict[str, str] = {}      # role → role it waits on
        self._parked: set[str] = set()

    # ── Public API ──────────────────────────────────────────────────────────

    def notify(self) -> None:
        """Wake the scheduler to re-evaluate state immediately.

        Call this from outside the scheduler whenever a relevant workspace
        change happens — most importantly, after posting `HUMAN_RESPONDED`
        and clearing `workspace.pending_human`. The Stage 5 runner wraps
        `post_signal` to call this; tests call it explicitly.
        """
        self._wake.set()

    def interrupt(
        self,
        agent_role: str,
        kind: InterruptKind,
        payload: InterruptPayload,
    ) -> InterruptToken:
        """Park an agent in a waiting state.  Returns a token for resume().

        - kind="human"  → WAITING_FOR_HUMAN; payload is WaitingForHuman
        - kind="peer"   → WAITING_FOR_PEER;  payload is WaitingForPeer
        - kind="skip"   → WAITING_FOR_SKIP_CONFIRM; payload is WaitingForSkipConfirm
        """
        if kind == "human":
            self.workspace.agent_states[agent_role] = AgentState.WAITING_FOR_HUMAN
            token_id = getattr(payload, "question", agent_role)
        elif kind == "peer":
            assert isinstance(payload, WaitingForPeer)
            self.workspace.agent_states[agent_role] = AgentState.WAITING_FOR_PEER
            token_id = payload.ask_id
        else:  # skip
            assert isinstance(payload, WaitingForSkipConfirm)
            self.workspace.agent_states[agent_role] = AgentState.WAITING_FOR_SKIP_CONFIRM
            token_id = payload.skip_id

        token = InterruptToken(agent_role=agent_role, kind=kind, id=token_id)
        self._interrupt_payloads[agent_role] = payload
        self._interrupt_tokens[agent_role] = token
        return token

    def resume(self, token: InterruptToken, value: Any = None) -> None:
        """Unpark an agent; `value` is appended to pending_input for next iteration.

        Sets agent state back to READY and wakes the scheduler loop.
        """
        role = token.agent_role
        self.workspace.agent_states[role] = AgentState.READY
        self._interrupt_payloads.pop(role, None)
        self._interrupt_tokens.pop(role, None)
        if value is not None:
            self._pending_input[role].append(value)
        self._wake.set()

    def drain_pending_input(self, role: str) -> list[Any]:
        """Return and clear any values queued by resume() for this role."""
        items = list(self._pending_input[role])
        self._pending_input[role].clear()
        return items

    def mark_sat_out(self, role: str) -> None:
        """Record that a role has SAT_OUT (confirmed skip).

        Counts as completion for downstream depends_on checks.
        """
        self._sat_out.add(role)
        self.workspace.agent_states[role] = AgentState.SAT_OUT
        self._wake.set()

    # ── opencode harness primitives (plan §4.4–§4.6) ───────────────────────

    def rearm(self, role: str, payload: Any = None) -> None:
        """Make a role runnable again, even if it already completed.

        `payload` is queued for the role's next spawn (read via
        `drain_pending_input`). Downstream roles that already completed are not
        touched; callers that need them to re-run re-arm them too.
        """
        if role not in self.agents:
            raise KeyError(role)
        if role in self.workspace.completed_agents:
            self.workspace.completed_agents.remove(role)
        self._failed.discard(role)
        self._parked.discard(role)
        self.workspace.agent_states[role] = AgentState.READY
        if payload is not None:
            self._pending_input[role].append(payload)
        self._wake.set()

    def review_rounds(self, reviewer: str, author: str) -> int:
        """How many times `reviewer` has sent work back to `author`."""
        return self._review_rounds[(reviewer, author)]

    def request_rework(self, reviewer: str, authors: list[str], payload: Any = None) -> bool:
        """Review edge: send work back to `authors`; the reviewer re-runs after them.

        Returns False (and changes nothing) once any author has used up the
        reviewer's `max_review_rounds` — the caller escalates instead. The
        reviewer's runner must return WITHOUT marking itself complete; because
        it depends on the authors it is spawned again once they finish.
        """
        limit = self.agents[reviewer].max_review_rounds
        if any(self._review_rounds[(reviewer, a)] >= limit for a in authors):
            return False
        for author in authors:
            self._review_rounds[(reviewer, author)] += 1
            self.rearm(author, payload)
        if reviewer in self.workspace.completed_agents:
            self.workspace.completed_agents.remove(reviewer)
        self.workspace.agent_states[reviewer] = AgentState.READY
        return True

    def block(self, role: str, on_role: str, need: Any = None) -> BlockOutcome:
        """`role` cannot continue until `on_role` acts (plan §4.4).

        - `on_role` unknown → nothing changes (caller fails the turn).
        - waiting on each other (directly or through a chain) → `pending_human`
          is set with the cycle: a human has to break it.
        - `on_role` running or not yet complete → `role` waits for it.
        - `on_role` already complete → it is re-armed with `need` as input.
        `role` becomes spawnable again once `on_role` completes.
        """
        if on_role not in self.agents or on_role == role:
            return BlockOutcome("unknown_role")
        chain = [role, on_role]
        nxt = self._blocked_on.get(on_role)
        while nxt is not None and nxt not in chain:
            chain.append(nxt)
            nxt = self._blocked_on.get(nxt)
        if nxt == role:
            from core.workspace import HumanRequest
            cycle = tuple(chain)
            self.workspace.pending_human = HumanRequest(
                from_agent=role,
                question=("These roles are blocked on each other: "
                          + " → ".join(cycle + (role,))
                          + ". Decide what should happen and answer, or stop the run."),
                iteration=self.workspace.iteration,
            )
            self.workspace.agent_states[role] = AgentState.BLOCKED
            self._blocked_on[role] = on_role
            return BlockOutcome("cycle", cycle)
        self._blocked_on[role] = on_role
        if role in self.workspace.completed_agents:
            self.workspace.completed_agents.remove(role)
        self.workspace.agent_states[role] = AgentState.BLOCKED
        if on_role in self.workspace.completed_agents and on_role not in self._tasks:
            self.rearm(on_role, need)
            return BlockOutcome("rearmed")
        if need is not None:
            self._pending_input[on_role].append(need)
        self._wake.set()
        return BlockOutcome("waiting")

    def blocked_on(self, role: str) -> str | None:
        """The role `role` is currently blocked on, if any."""
        return self._blocked_on.get(role)

    def is_running(self, role: str) -> bool:
        """True while a spawn of `role` is in flight."""
        return role in self._tasks

    def park(self, role: str) -> None:
        """Nothing is ready for `role` right now; don't respawn it until `wake_parked()`."""
        self._parked.add(role)
        self.workspace.agent_states[role] = AgentState.PARKED

    def wake_parked(self) -> None:
        """Something changed (e.g. a task finished): parked roles may look again."""
        for role in self._parked:
            self.workspace.agent_states[role] = AgentState.READY
        self._parked.clear()
        self._wake.set()

    def _release_blocked(self) -> None:
        for role, on_role in list(self._blocked_on.items()):
            if self._effective_complete(on_role):
                del self._blocked_on[role]
                if self.workspace.agent_states.get(role) is AgentState.BLOCKED:
                    self.workspace.agent_states[role] = AgentState.READY

    def _build_transition_rules(self) -> None:
        """Populate the default allowed-speaker-transition table (M1 §10).

        Rule format: (signal_type, from_role) → frozenset of allowed next roles.
        The special strings "<author_of_failed_work>" and "<target_role>" are
        resolved at enforcement time from the signal payload.

        Default = permissive (no rule → any ready role may spawn).
        """
        self._transition_rules = {
            # After a VERIFY_FAIL, only the author of the failed work may resume.
            ("VERIFY_FAIL", "*"): frozenset({"<author_of_failed_work>"}),
            # After an ESCALATION, only tech_lead may respond.
            ("ESCALATION", "*"): frozenset({"tech_lead"}),
            # After an ASK, only the target may respond.
            ("ASK", "*"): frozenset({"<target_role>"}),
        }

    async def run(self) -> SchedulerResult:
        """Drive the scheduler loop until everything is COMPLETE,
        MAX_ITERATIONS triggers, or the run is STUCK."""
        try:
            while True:
                # 0a. User pressed Stop → exit cleanly. Without this, a stop
                # that lands while pending_human is set would have the loop
                # park forever in _any_waiting() instead of returning.
                if self._force_stopped:
                    return self._result(SchedulerStatus.STOPPED)

                # 0. Wall-clock deadline check — runs before anything else. A hard
                #    deadline ends the run; a soft one starts draining (plan §11.6).
                self._account_pause()
                if self._deadline_exceeded():
                    if self.workspace.deadline_mode != "soft":
                        return self._result(SchedulerStatus.DEADLINE_EXCEEDED)
                    if not self.workspace.draining:
                        self._start_draining()

                # 0b. M1 deadline watcher — expire WAITING_FOR_PEER /
                #     WAITING_FOR_SKIP_CONFIRM interrupts past their deadline_ts.
                self._reap_expired_interrupts()

                # 0c. Stall watchdog (opencode harness): may re-arm the overseer,
                #     including right before the run would otherwise be STUCK.
                if self.watchdog is not None:
                    self.watchdog()

                # 1. Try to spawn anything ready. The `pending_human` flag is
                #    the global block for new spawns; per-agent WAITING_FOR_PEER
                #    and WAITING_FOR_SKIP_CONFIRM are filtered inside _ready_to_spawn.
                if not self.workspace.pending_human:
                    for spec in self._ready_to_spawn():
                        if self.workspace.draining and not (
                                self.drain_filter is not None and self.drain_filter(spec.role)):
                            continue
                        self._spawn(spec)

                # 2. Successful exit — every non-failed agent is complete or sat-out.
                if self._all_complete():
                    return self._result(SchedulerStatus.COMPLETE)

                # 3. Iteration cap.
                if self._iterations_exceeded():
                    return self._result(SchedulerStatus.MAX_ITERATIONS)

                # 4. Nothing in flight.
                if not self._tasks and self.workspace.draining:
                    return self._result(SchedulerStatus.TIME_LIMIT)
                if not self._tasks:
                    if self._any_waiting():
                        # Agents are parked waiting for external events; sleep.
                        # Bound the wait by the soonest interrupt deadline so
                        # we wake in time to expire stale ASKs / SKIPs.
                        await self._sleep_until_event_or_deadline()
                        continue
                    return self._result(SchedulerStatus.STUCK)

                # 5. Wait for a task to complete OR an external `notify()`.
                await self._wait_for_change()

                # 6. Reap finished tasks.
                self._reap_done()
        finally:
            await self._cancel_remaining()

    async def _sleep_until_event_or_deadline(self) -> None:
        """Block on `_wake` but cap the wait at the next interrupt deadline.

        Returns when `_wake.set()` fires OR the next deadline_ts elapses,
        whichever comes first. Caller re-enters the loop body to act.
        """
        next_deadline = self._next_interrupt_deadline()
        run_left = self._run_deadline_timeout()
        if next_deadline is None and self.watchdog is None:
            # Even while paused for a human the wall-clock deadline must still end the run.
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=run_left)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            return
        # With a watchdog, wake at least every interval so it (and the wall-clock
        # deadline check) keep running while the run waits for a human.
        timeout = min(max(0.0, next_deadline - time.monotonic())
                      if next_deadline is not None else float("inf"),
                      self.watchdog_interval if self.watchdog is not None else float("inf"))
        if run_left is not None:
            timeout = min(timeout, run_left)
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass  # deadline elapsed — top of loop will reap
        self._wake.clear()

    def _next_interrupt_deadline(self) -> float | None:
        """Soonest `deadline_ts` across active peer/skip interrupts."""
        deadlines: list[float] = []
        for payload in self._interrupt_payloads.values():
            ts = getattr(payload, "deadline_ts", 0.0)
            if ts > 0:
                deadlines.append(ts)
        return min(deadlines) if deadlines else None

    def _reap_expired_interrupts(self) -> None:
        """Convert past-deadline peer/skip interrupts into timeout signals.

        - WaitingForPeer past deadline → post PEER_TIMEOUT, mark agent FAILED
          for this iteration; the runner's escalation subscriber will route
          it to tech_lead.
        - WaitingForSkipConfirm past deadline → post SKIP_TIMEOUT and surface
          a HUMAN_NEEDED — never auto-grant (M1 §7a "default-deny").
        """
        from core.signals import Signal, SignalType
        from core.workspace import HumanRequest

        now = time.monotonic()
        for role, payload in list(self._interrupt_payloads.items()):
            deadline = getattr(payload, "deadline_ts", 0.0)
            if not deadline or now < deadline:
                continue

            if isinstance(payload, WaitingForPeer):
                self.workspace.post_signal(Signal(
                    type=SignalType.PEER_TIMEOUT,
                    summary=f"PEER_TIMEOUT {role} → {payload.target_role}",
                    from_agent=role,
                    detail=(
                        f"ask_id={payload.ask_id};reason={payload.reason_if_timeout}"
                    ),
                ))
                # Clear the interrupt so the role can be re-spawned. Runner's
                # escalation handler picks up PEER_TIMEOUT on the board.
                self._interrupt_payloads.pop(role, None)
                self._interrupt_tokens.pop(role, None)
                self.workspace.agent_states[role] = AgentState.READY

            elif isinstance(payload, WaitingForSkipConfirm):
                self.workspace.post_signal(Signal(
                    type=SignalType.SKIP_TIMEOUT,
                    summary=f"SKIP_TIMEOUT {role} (confirmer={payload.confirmer_role})",
                    from_agent=role,
                    detail=f"skip_id={payload.skip_id}",
                ))
                # Default-deny: do NOT auto-grant. Park as HUMAN_NEEDED so a
                # human (or external watcher) makes the call.
                self.workspace.pending_human = HumanRequest(
                    from_agent=role,
                    question=(
                        f"{role} requested to sit out (skip_id={payload.skip_id}); "
                        f"confirmer {payload.confirmer_role} did not respond in time."
                    ),
                    iteration=self.workspace.iteration,
                )
                self._interrupt_payloads.pop(role, None)
                self._interrupt_tokens.pop(role, None)
                self.workspace.agent_states[role] = AgentState.WAITING_FOR_HUMAN

    # ── Spawn / reap ───────────────────────────────────────────────────────

    # States that block re-spawning by `_ready_to_spawn`. WAITING_FOR_HUMAN is
    # NOT here — the `pending_human` workspace flag handles that globally at
    # the top of the run loop, and including it here would deadlock legacy
    # call sites that set the state without going through `interrupt()`.
    _WAITING_STATES: frozenset[AgentState] = frozenset({
        AgentState.WAITING_FOR_PEER,
        AgentState.WAITING_FOR_SKIP_CONFIRM,
    })

    # States surfaced by `_any_waiting()` to decide sleep-vs-STUCK. Includes
    # WAITING_FOR_HUMAN because a human pause means "park, don't return STUCK".
    _PARKED_STATES: frozenset[AgentState] = frozenset({
        AgentState.WAITING_FOR_HUMAN,
        AgentState.WAITING_FOR_PEER,
        AgentState.WAITING_FOR_SKIP_CONFIRM,
    })

    def _effective_complete(self, role: str) -> bool:
        """True if role counts as done for downstream depends_on resolution.

        Both COMPLETE (in completed_agents) and SAT_OUT satisfy this.
        """
        return role in self.workspace.completed_agents or role in self._sat_out

    def _ready_to_spawn(self) -> list[AgentSpec]:
        allowed_speaker = self._allowed_speaker_for_recent_signal()
        ready: list[AgentSpec] = []
        self._release_blocked()
        slots = (None if self.max_parallel is None
                 else max(0, self.max_parallel - len(self._tasks)))
        for role, spec in self.agents.items():
            if slots is not None and len(ready) >= slots:
                break
            if role in self._tasks:
                continue
            if self._effective_complete(role):
                continue
            if role in self._failed:
                continue
            if role in self._parked or role in self._blocked_on:
                continue
            state = self.workspace.agent_states.get(role)
            if state in self._WAITING_STATES:
                continue
            if not all(self._effective_complete(dep) for dep in spec.depends_on):
                continue
            if spec.has_work is not None and not spec.has_work():
                continue
            if self._spawn_count[role] >= self.max_iterations:
                continue
            if allowed_speaker is not None and role not in allowed_speaker:
                continue
            ready.append(spec)
        return ready

    def _allowed_speaker_for_recent_signal(self) -> frozenset[str] | None:
        """Resolve the allowed-speaker set from the most recent constraining
        signal on the board (M1 §10).

        Looks back through the board (newest first) until it finds a signal
        type with a transition rule. Resolves placeholders
        `<author_of_failed_work>` / `<target_role>` against the signal payload.
        Returns None if no rule applies (default-permissive).
        """
        if not self._transition_rules:
            return None
        # Build (signal_type_str → ruleset) lookup from `(type, "*")` keys.
        lookup: dict[str, frozenset[str]] = {}
        for (sig_type, _from), allowed in self._transition_rules.items():
            lookup[sig_type] = allowed

        # Walk the board newest-first; first matching constraint wins.
        for sig in reversed(self.workspace.board):
            rule_key = self._rule_key_for(sig)
            if rule_key is None or rule_key not in lookup:
                continue
            allowed = lookup[rule_key]
            return self._resolve_placeholders(allowed, sig)
        return None

    @staticmethod
    def _rule_key_for(sig: "Any") -> str | None:
        """Map a signal to a transition-rule key.

        - REJECTED → "VERIFY_FAIL" (same constraint family).
        - ESCALATION → "ESCALATION".
        - AGENT_MESSAGE with summary "ASK ..." → "ASK".
        - Anything else → None (no rule applies).
        """
        type_name = sig.type.value if hasattr(sig.type, "value") else str(sig.type)
        if type_name == "REJECTED":
            return "VERIFY_FAIL"
        if type_name == "ESCALATION":
            return "ESCALATION"
        if type_name == "AGENT_MESSAGE":
            summary = (sig.summary or "").strip()
            if summary.startswith("ASK "):
                return "ASK"
        return None

    def _resolve_placeholders(
        self, allowed: frozenset[str], sig: "Any",
    ) -> frozenset[str]:
        """Substitute `<author_of_failed_work>` and `<target_role>` against
        the signal payload. Concrete role names pass through unchanged."""
        resolved: set[str] = set()
        for entry in allowed:
            if entry == "<author_of_failed_work>":
                # The author is encoded in the rejected signal's depends_on
                # — for VERIFY_FAIL/REJECTED the from_agent is the reviewer;
                # we want the rejected upstream. Detail is "target_role=X"
                # for the existing escalation pipeline.
                target = self._extract_signal_field(sig, "target_role")
                if target:
                    resolved.add(target)
                else:
                    # Fall back to from_agent's depends_on lookup via agents dict.
                    reviewer_role = sig.from_agent or ""
                    spec = self.agents.get(reviewer_role)
                    if spec is not None:
                        resolved.update(spec.depends_on)
            elif entry == "<target_role>":
                target = self._extract_signal_field(sig, "to") or \
                         self._extract_signal_field(sig, "target_role")
                if target:
                    resolved.add(target)
            else:
                resolved.add(entry)
        return frozenset(resolved)

    @staticmethod
    def _extract_signal_field(sig: "Any", key: str) -> str | None:
        """Pull `key=value` (or JSON `key`) out of a Signal.detail string."""
        detail = getattr(sig, "detail", None)
        if not detail:
            return None
        # JSON shape
        try:
            import json as _json
            data = _json.loads(detail)
            if isinstance(data, dict) and key in data:
                return str(data[key])
        except (ValueError, TypeError):
            pass
        # `key=value;` shape
        for chunk in detail.split(";"):
            chunk = chunk.strip()
            if chunk.startswith(f"{key}="):
                return chunk[len(key) + 1 :].strip()
        return None

    def _spawn(self, spec: AgentSpec) -> None:
        self._spawn_count[spec.role] += 1
        self.workspace.agent_states[spec.role] = AgentState.RUNNING
        self._tasks[spec.role] = asyncio.create_task(
            self._wrap_runner(spec),
            name=f"{self.scope}:{spec.role}",
        )

    async def _wrap_runner(self, spec: AgentSpec) -> None:
        """Run the agent's runner. Always wake the loop on exit so the
        scheduler re-evaluates state regardless of how the runner exited."""
        try:
            await spec.runner(self.workspace)
        finally:
            self._wake.set()

    async def _wait_for_change(self) -> None:
        """Block until any in-flight task finishes OR `notify()` fires."""
        wake_task = asyncio.create_task(self._wake.wait(), name="scheduler:wake")
        waiters: list[asyncio.Task] = [wake_task, *self._tasks.values()]
        timeout = self.watchdog_interval if self.watchdog is not None else None
        run_left = self._run_deadline_timeout()
        if run_left is not None:
            timeout = run_left if timeout is None else min(timeout, run_left)
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
        finally:
            self._wake.clear()
            if not wake_task.done():
                wake_task.cancel()
                try:
                    await wake_task
                except asyncio.CancelledError:
                    pass

    def _reap_done(self) -> None:
        for role in list(self._tasks.keys()):
            task = self._tasks[role]
            if not task.done():
                continue
            del self._tasks[role]
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                from core.signals import Signal, SignalType
                self._failed.add(role)
                self.workspace.agent_states[role] = AgentState.FAILED
                # Never silent: a turn that crashed before it could post a result (its server
                # did not start, the gateway refused the model, ...) says why on the board.
                log.exception("%s: turn crashed", role)
                self.workspace.post_signal(Signal(
                    type=SignalType.FAILED, from_agent=role,
                    summary=f"{role}: turn crashed: {type(exc).__name__}: {exc}"[:300],
                    detail=str(exc)[:4000]))

    async def _cancel_remaining(self) -> None:
        """Cancel any in-flight tasks at scheduler shutdown."""
        if not self._tasks:
            return
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    async def force_cancel(self) -> None:
        """Externally-triggered hard stop. Cancels all in-flight tasks and
        wakes the main loop so it exits through the finally → _cancel_remaining
        path. Safe to call from any coroutine while the scheduler is running."""
        self._force_stopped = True
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._wake.set()

    # ── Predicates ──────────────────────────────────────────────────────────

    def _all_complete(self) -> bool:
        for role in self.agents:
            if role in self._failed:
                continue
            if not self._effective_complete(role):
                return False
        return True

    def _any_waiting(self) -> bool:
        """True if any agent is parked in a waiting state (human, peer, skip)."""
        if self.workspace.pending_human:
            return True
        for role in self.agents:
            state = self.workspace.agent_states.get(role)
            if state in self._PARKED_STATES:
                return True
        return False

    def _run_deadline_timeout(self) -> float | None:
        """Seconds until the run's wall-clock deadline (just past it), or None without one."""
        deadline = self.workspace.deadline_at
        if deadline is None:
            return None
        return max(0.0, deadline - time.time()) + 0.01

    def _deadline_exceeded(self) -> bool:
        deadline = self.workspace.deadline_at
        if deadline is None:
            return False
        if self.workspace.deadline_mode == "soft" and self.workspace.pending_human:
            return False        # paused for a human: the soft clock is stopped
        return time.time() > deadline

    def _account_pause(self) -> None:
        """Soft mode: time paused for a human moves the deadline out by as much."""
        ws = self.workspace
        if ws.deadline_mode != "soft" or ws.deadline_at is None:
            return
        if ws.pending_human and self._paused_since is None:
            self._paused_since = time.time()
        elif not ws.pending_human and self._paused_since is not None:
            ws.deadline_at += time.time() - self._paused_since
            self._paused_since = None

    def _start_draining(self) -> None:
        """The soft deadline passed: stop starting work and say so on the board."""
        import json

        from core.signals import Signal, SignalType
        self.workspace.draining = True
        running = sorted(self._tasks)
        self.workspace.post_signal(Signal(
            type=SignalType.TIME_LIMIT_REACHED, from_agent="system",
            summary=("time limit reached: no new work starts; "
                     + (f"finishing {', '.join(running)}" if running else "nothing running")),
            detail=json.dumps({"running": running, "deadline_at": self.workspace.deadline_at})))

    def _iterations_exceeded(self) -> bool:
        """True if any role has been spawned at the cap without completing.

        We stop the run as a whole when this happens — the alternative is
        leaving the scheduler stuck in STUCK state, which loses information
        for the caller."""
        for role, count in self._spawn_count.items():
            if count >= self.max_iterations:
                if role in self.workspace.completed_agents:
                    continue
                if role in self._failed:
                    continue
                return True
        return False

    def _result(self, status: SchedulerStatus) -> SchedulerResult:
        return SchedulerResult(
            status=status,
            completed=list(self.workspace.completed_agents),
            failed=sorted(self._failed),
            spawn_counts=dict(self._spawn_count),
            sat_out=sorted(self._sat_out),
        )
