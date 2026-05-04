"""GuudFlow CLI — a thin client over the FastAPI server.

The CLI gives terminal access to the same surfaces the studio exposes
(dashboard, projects, runs, personas, examples, propose, invoke), but
without trying to replicate every panel or chart. The aim is "GUI in a
shell" — fast lookups, list/show/watch loops, structured output — not
a full TUI.

Run with `python -m cli ...` or use the `scripts/guudflow` shim.

Note: the `main` entry point is intentionally NOT re-exported here.
Re-exporting it would shadow the `cli.main` submodule from the parent
package namespace, breaking `import cli.main`. Use `from cli.main
import main` directly.
"""
