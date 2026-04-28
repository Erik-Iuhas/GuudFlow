---
name: System Design Checklist
triggers: [system, design, architecture, scale, integration, interface, contract]
priority: 5
kind: checklist
tags: [architect, design]
---

# System Design Checklist

Work through this when proposing or reviewing a new system, service, or major feature.

## Interfaces & Contracts
- [ ] Data shapes defined as dataclasses (internal) or Pydantic models (API boundary)
- [ ] All public methods have type hints; `Any` is justified with a comment
- [ ] Async where I/O can block; sync where computation-only
- [ ] Error types are specific domain exceptions, not bare `Exception`

## State & Persistence
- [ ] Single source of truth identified for every piece of shared state
- [ ] Concurrent writes protected: lock, CAS, or optimistic versioning chosen and documented
- [ ] Atomic writes for file persistence (temp file + rename pattern)
- [ ] Data migration path considered for schema changes

## Dependencies
- [ ] Dependency direction is correct: lower layers do not import higher layers
- [ ] Circular imports impossible by design (not just avoided)
- [ ] No global mutable state except explicitly initialized singletons

## Observability
- [ ] Key events logged with structured fields (run_id, agent_id, etc.)
- [ ] Error paths log at `error` level with enough context to reproduce
- [ ] Health/readiness check updated if new external dependency added

## Security
- [ ] External input validated at system boundary
- [ ] File paths go through `_safe_path()`
- [ ] Credentials through broker only — no direct key access
- [ ] LLM prompt injection mitigated (delimiters around external content)

## Hard Constraints Check (from CLAUDE.md)
- [ ] No `asyncio.gather` outside `core/scheduler.py`
- [ ] Board is append-only
- [ ] Agents never reference each other directly
- [ ] All file I/O through FileWorkspace
