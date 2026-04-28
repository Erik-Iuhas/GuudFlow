---
name: Authentication & Authorization Patterns
triggers: [auth, jwt, session, oauth, token, bearer, login, permission]
priority: 5
kind: reference
tags: [backend, auth, security]
---

# Auth Patterns

## JWT Best Practices
- Short-lived access tokens: 15-60 minutes
- Long-lived refresh tokens: 7-30 days, rotated on use
- Store refresh tokens server-side (allow revocation)
- Sign with RS256 (asymmetric) in production, HS256 only for dev
- Include: `sub` (user id), `exp`, `iat`, `jti` (unique id for revocation)
- Never store sensitive data in payload — it is base64, not encrypted

## Token Storage
- Access token: memory only (JS variable), never localStorage
- Refresh token: httpOnly, Secure, SameSite=Strict cookie
- Never return refresh token in JSON body

## RBAC Pattern
```python
# Default-deny: explicit allow required
SCOPES = {
    "fs-read": "Read files in workspace",
    "fs-write": "Write files in workspace",
    "git-commit": "Commit to agent branch",
}
# Check: user must have ALL required scopes
def require_scopes(*scopes): ...
```

## OAuth2 (external providers)
- Use PKCE for all flows (even server-side)
- State parameter prevents CSRF — always validate
- Nonce prevents replay — always validate on OIDC
- Never log access tokens or authorization codes

## Common Vulnerabilities to Avoid
- Timing attacks: use `hmac.compare_digest()`, not `==`
- Mass assignment: whitelist accepted fields, never `**request.json()`
- Privilege escalation: always re-check ownership on nested resources
