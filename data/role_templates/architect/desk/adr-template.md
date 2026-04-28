---
name: Architecture Decision Record Template
triggers: [adr, architecture, decision, trade-off, design, pattern, approach]
priority: 6
kind: template
tags: [architect, documentation]
---

# ADR Template

Use this template when recording a non-trivial architectural decision. An ADR is warranted when:
- The decision is hard to reverse
- Multiple valid options exist
- Future readers would otherwise not understand *why* the code is structured this way

```markdown
# ADR-{N}: {Short Title}

**Date**: {YYYY-MM-DD}  
**Status**: Proposed | Accepted | Superseded by ADR-{M}

## Context
What is the situation that forces this decision? Include constraints (performance,
security, team size, timeline) and any background a new reader would need.

## Decision
What is the change we are making? State it clearly in one paragraph.

## Options Considered

### Option A: {Name}
{Brief description}
- Pro: ...
- Con: ...

### Option B: {Name}
{Brief description}
- Pro: ...
- Con: ...

## Rationale
Why we chose what we chose over the alternatives. Focus on the constraints that
made this the right call, not just "it felt cleaner."

## Consequences
- What becomes easier
- What becomes harder
- Any follow-up work this decision creates
```

## Where to Store ADRs
`docs/adr/` in the project workspace. Name files `{N:03d}-{slug}.md`.
Reference ADRs in commit messages when a commit implements a decision: `feat: ... (ADR-007)`.
