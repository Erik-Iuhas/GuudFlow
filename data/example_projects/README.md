# Example projects

One-click project templates surfaced in the Studio's Projects view. Each
folder ships a `template.json` describing the team, RBAC, and a default
goal. Optional `seed_inputs/` content is copied into the new project's
`inputs/uploads/` directory at instantiation time.

The loader walks one level deep — any subfolder containing `template.json`
is discovered automatically. Folders prefixed with `_` are skipped, mirroring
the convention used by `data/training_camp/scenarios/`.

## Schema

```jsonc
{
  "id": "example-fullstack-web-app",         // stable id, also the URL slug
  "title": "Full-stack web app",
  "category": "Coding",                       // Coding | Documentation | Refactor | Research
  "icon": "🌐",                               // single emoji used in the card
  "summary": "One-line pitch shown on the card.",
  "details": "Longer markdown shown in the detail view.",
  "default_project_id": "fullstack-web-app", // suggested project id; collision-suffixed by the API
  "default_goal": "...",                      // pre-filled in the modal
  "requires": {
    "source_repo": false,                     // hint: user must attach a repo first
    "hints": ["sample-input.txt"]             // free-form hint strings for the UI
  },
  "project_template": {
    "max_iterations": 5,
    "approval_gates": { ... },
    "rbac": { ... },
    "agents": [ ... ]
  }
}
```

The `project_template` block is merged into the `ProjectConfig` posted to
`/api/projects` — its keys must match the `ProjectConfig` schema in
`core/runner.py`. Agent system prompts should describe *how* the role
works (inputs, outputs, hand-off pattern) without baking in *what* the
task is — the task comes from the project's `goal` field at instantiate
time.
