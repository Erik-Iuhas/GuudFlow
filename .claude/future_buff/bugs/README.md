# bugs — captured failure modes pending a fix

Each file in this folder is one observed failure pattern from a real run. Capture format:

- **Symptom** — what the user / operator saw
- **Root cause** — where the bug actually is
- **Evidence** — run id, files, line numbers, board signals, LLM-call traces
- **Recommended fix** — the smallest change that addresses the cause
- **Severity** — `live` (corrupting active runs) / `cosmetic` (logged but no impact) / `latent` (only fires on rare path)

Pull from here when picking up bug work. Capture new failures here when diagnosing them — even if not fixing immediately — so they aren't lost between sessions.
