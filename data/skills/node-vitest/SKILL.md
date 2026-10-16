---
name: node-vitest
description: Conventions for a Node or web project tested with Vitest. Use for Vitest projects.
---

# Node with Vitest

- Follow the project's language (JavaScript or TypeScript) and module style (ESM).
- Tests are `*.test.(js|ts)` next to the code or in `tests/`, using `describe`, `it`, `expect`.
- Run the check command in your rules (for example `npx vitest run`).
- Dependencies go in `package.json` with exact versions, then call `sync_dependencies` with
  manager `npm`.
