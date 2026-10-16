---
name: typescript-bun
description: Conventions for TypeScript run and tested with Bun. Use for Bun projects.
---

# TypeScript with Bun

- Write `.ts` files. Tests are `*.test.ts` using `bun:test` (`describe`, `it`, `expect`).
- Run `bun test` plain (no pipes or redirection); run one file with `bun test path`.
- Dependencies go in `package.json` with exact versions, then call `sync_dependencies` with
  manager `npm`.
