---
name: REST API Design Patterns
triggers: [api, endpoint, rest, http, route, controller]
priority: 5
kind: reference
tags: [backend, api, rest]
---

# REST API Design Patterns

## Resource Naming
- Use plural nouns: `/users`, `/projects`, `/runs`
- Nest only one level deep: `/projects/{id}/runs`, not `/projects/{id}/runs/{rid}/events`
- Use kebab-case for multi-word: `/run-profiles`, `/persona-desks`

## HTTP Methods
- `GET` — idempotent read, no body
- `POST` — create or non-idempotent action
- `PUT` — full replace (requires all fields)
- `PATCH` — partial update (only changed fields)
- `DELETE` — idempotent remove, 204 on success

## Status Codes
- `200` — success with body
- `201` — created (include `Location` header)
- `202` — accepted, async work queued
- `204` — success, no body
- `400` — bad request / validation error (include field errors)
- `401` — unauthenticated
- `403` — authenticated but forbidden
- `404` — not found
- `409` — conflict (duplicate create)
- `422` — unprocessable (semantic validation failure)

## Error Response Shape
```json
{
  "error": "VALIDATION_FAILED",
  "message": "Human-readable summary",
  "fields": {"field_name": "what is wrong"}
}
```

## Pagination
Use cursor-based for large collections:
```json
{"items": [...], "next_cursor": "opaque_token", "total": 1234}
```
Limit default 50, max 200. Never return unbounded lists.

## Versioning
- URL versioning for breaking changes: `/v2/...`
- Header versioning for non-breaking additions: avoid
- Deprecate with `Sunset` header, minimum 90-day window
