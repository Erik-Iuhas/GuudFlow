---
name: Data Validation & Schema Patterns
triggers: [validate, schema, sanitize, input, pydantic, type, coerce]
priority: 4
kind: reference
tags: [backend, validation, security]
---

# Data Validation Patterns

## Validation Layers
1. **Transport layer** — Pydantic models at API boundary, reject malformed JSON early
2. **Business layer** — domain invariants (e.g., run can only start if project exists)
3. **Storage layer** — DB constraints as final safety net, not primary validation

## Pydantic Patterns
```python
from pydantic import BaseModel, Field, field_validator

class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def name_no_slash(cls, v: str) -> str:
        if "/" in v:
            raise ValueError("name cannot contain /")
        return v.strip()
```

## Common Mistakes
- **Over-trust**: never assume data is valid after the boundary — re-validate on deserialization from DB/cache
- **Silent coercion**: Pydantic v2 coerces `"123"` to `int` by default — use `strict=True` for IDs
- **Missing strip()**: leading/trailing whitespace in names causes duplicate detection to fail

## File Path Validation
Always validate agent-supplied paths through `FileWorkspace._safe_path()`:
```python
safe = workspace._safe_path(user_supplied)  # raises PathEscapeError if outside root
```
Never use raw `Path(user_input)` in agent code.

## String Sanitization
- Wrap external file content in delimiters before LLM injection — never f-string
- Strip null bytes from text inputs: `s.replace("\x00", "")`
- Normalize unicode: `unicodedata.normalize("NFC", s)` before storage
