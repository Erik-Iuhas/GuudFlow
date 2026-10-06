import type { TaskColumn, TaskThreadDetail } from "./lib/runEvents.gen";
// Shared types — mirror the backend's API shapes.

export type ModelTier = "heavy" | "solid" | "fast";

export interface AgentConfig {
  role: string;
  description?: string;
  depends_on?: string[];
  system_prompt?: string;
  declared_local_tools?: string[];
  declared_mcp_servers?: string[];
  persona_id?: string | null;
  /** The worker's name (core/crew.py); filled in when the project is saved. */
  name?: string | null;
  model_tier?: ModelTier;
  /** Optional explicit model id (e.g. "mistral-large-latest"). When set
   *  AND the model's tier matches `model_tier`, the runner uses this exact
   *  model. Empty / missing → first available model in tier. */
  model_id?: string;
  agent_class_role_type?: string | null;
  is_reviewer?: boolean;
  is_final_reviewer?: boolean;
  max_tool_iterations?: number;
  manages?: string[];
  escalates_to?: string[];
  /** Track 12 — per-agent override for sub-LLM dispatch model selection.
   *  Maps use_case → registered model id. Checked before the project's
   *  sub_llm_models. Empty/missing → inherit project default. */
  sub_llm_models_override?: Record<string, string>;
  /** Track 12 — explicit baseline opt-out. Tools listed here are dropped
   *  from the manifest even though they're in BASELINE_LOCAL_SCOPES.
   *  Studio's tool picker writes here when the user un-checks a baseline. */
  excluded_baseline_tools?: string[];
  /** opencode harness: for a reviewer, the roles whose work it reviews (a review edge). */
  review_of?: string[];
  /** opencode harness: rework rounds a review edge allows before escalating. */
  max_review_rounds?: number;
}

export interface RoleRBAC {
  allowed_scopes: string[];
}

export interface ProjectConfig {
  project_id: string;
  goal: string;
  agents?: AgentConfig[];
  rbac?: Record<string, RoleRBAC>;
  mcp_servers?: unknown[];
  max_iterations?: number;
  approval_gates?: Partial<ApprovalGates>;
  /** "legacy" only on request; a missing value means the opencode harness (plan O15). */
  harness?: "legacy" | "opencode";
  pr_mode?: "manual" | "auto";
  /** Track 12 — per-use-case sub-LLM model picks. Maps use_case name
   *  (e.g. "summarise-subtask", "protocol-cleanup", "llm-dispatch")
   *  to a registered model id from /api/models. Empty / missing
   *  entries fall back to the cheapest tool-capable model server-side. */
  sub_llm_models?: Record<string, string>;
  /** Project flow plan §2: who holds the Lead and Reviewer seats (Owner is always on). */
  seats?: ProjectSeats;
  start_mode?: StartMode;
}

export interface ProjectSeats {
  lead: boolean;
  reviewer: "off" | "all" | "must";
}

export type StartMode = "spec_first" | "prototype_first" | "test_first" | "research_first" | "maintain";

export interface PrReadyInfo {
  integration_branch: string;
  base_branch: string;
  head_sha?: string;
  leaf_branch?: string;
  gh_command: string;
  title_suggestion?: string;
}

export interface RunMeta {
  run_id: string;
  project_id: string;
  goal?: string;
  status: string;
  iteration?: number;
  completed_agents?: string[];
  created_at?: string;
  pr_mode?: "manual" | "auto";
  pr_ready?: PrReadyInfo;
  /** Spend cap in USD, when the run meta carries it (else the project's `approval_gates`). */
  token_budget_usd?: number | null;
  /** Review column WIP cap of the task board (server default). */
  review_column_cap?: number;
  /** Harness the run used, when the run meta carries it. */
  harness?: "legacy" | "opencode";
}

/** Signal types posted by the opencode harness. Their `detail` is a JSON
 *  string (THINKING's is free text). */
export const HARNESS_SIGNAL_TYPES = [
  "CAPABILITIES",
  "AGENT_RESULT",
  "TOOL_CALL",
  "AGENT_TODO",
  "THINKING",
  "BLOCKED",
] as const;

/** One card as returned by `GET /api/runs/{id}/cards` (`card_view` in the backend). */
export interface CardWire {
  id: string;
  title: string;
  owner: string;
  column: TaskColumn;
  kind?: string;
  size?: string;
  priority?: number;
  review_round?: number;
  acceptance?: string[];
  depends_on?: string[];
  column_since?: string | null;
  thread?: TaskThreadDetail[];
}

export interface Signal {
  id: string;
  type: string;
  summary: string;
  detail?: string | null;
  from_agent?: string | null;
  from_user: boolean;
  priority: "normal" | "high";
  iteration: number;
  seen_by: string[];
  timestamp: string;
}

export interface PersonaSummary {
  persona_id: string;
  name: string;
  specialty: string;
  character?: string;
  goal?: string;
  n_episodes: number;
  reputation: {
    production?: { total_runs?: number; reviewer_first_pass_rate?: number };
    training?: { total_runs?: number; graduation_status?: string };
    total_episodes?: number;
    active_patterns?: number;
  };
  style_tendencies?: Record<string, number>;
}

export interface InvokeResponse {
  accepted: boolean;
  run_id: string;
  job_id: string;
  project_id: string;
  enqueued_at: string;
}

export interface PersonaHire {
  role: string;
  action: "hire_existing" | "create_new";
  persona_id: string;
  display_name: string;
  rationale: string;
}

export interface RoleDelta {
  role: string;
  action: "keep" | "drop" | "add" | "swap_tier" | "replace_persona";
  rationale?: string;
  from_state?: Record<string, unknown> | null;
  to_state?: Record<string, unknown> | null;
}

export interface MetaProposal {
  rationale: string;
  agents: AgentConfig[];
  rbac: Record<string, RoleRBAC>;
  persona_hires?: PersonaHire[];
  warnings?: string[];
  deltas?: RoleDelta[];
  /** Ids of the active playbook entries that were shown to the meta-agent. */
  playbook_entries_used?: string[];
}

// ── Approval gates (Phase C.0.5) ───────────────────────────────────────────

export interface ApprovalGates {
  pre_run_cost_preview: boolean;
  heavy_tier_mid_run_dispatch: boolean;
  iteration_breach_at: number | null;
  per_iteration: boolean;
  token_budget_usd: number | null;
  /** Ongoing projects (plan §14): run on the run's code before review, e.g. "pytest -q". */
  check_command?: string;
  project_remote?: ProjectRemote | null;
  autopilot?: AutopilotSettings | null;
}

export interface ProjectRemote {
  url: string;
  branch?: string;
  publish?: "none" | "branch" | "pr" | "main";
  /** Name of a host environment variable (GUUDFLOW_GIT_*) holding the token; never the token. */
  token_env?: string | null;
}

export interface AutopilotSettings {
  enabled: boolean;
  every_hours: number;
  take: number;
  max_usd_per_run: number;
  max_usd_per_week: number;
  auto_accept: boolean;
}

export interface CostEstimate {
  total_usd: number;
  by_role: Record<string, number>;
  notes: string[];
}

export type InvokeOutcome =
  | {
      accepted: true;
      run_id: string;
      job_id: string;
      project_id: string;
      enqueued_at: string;
    }
  | {
      accepted: false;
      requires_approval: true;
      gate: string;
      estimated_cost: CostEstimate;
      project_id: string;
      message: string;
    };

// ── Project inputs (D-prime §33) ───────────────────────────────────────────

export interface InputUpload {
  filename: string;
  size: number;
}

export type SourceRepoMeta =
  | {
      kind: "git_url";
      git_url: string;
      branch: string;
      shallow: boolean;
      imported_at: string;
    }
  | {
      kind: "local_path";
      path: string;
      imported_at: string;
    }
  | {
      kind: "local_path_in_place";
      path: string;
      imported_at: string;
    };

export interface PriorRunAttachment {
  kind: "prior_run";
  run_id: string;
  project_id_of_origin?: string | null;
  attached_at: string;
  has_feedback: boolean;
}

export interface InputsState {
  uploads: InputUpload[];
  source_repo: SourceRepoMeta | null;
  prior_runs: PriorRunAttachment[];
}

export interface SecretFinding {
  path: string;
  reason: string;
}

// ── Example projects (one-click templates) ─────────────────────────────────

export type ExampleProjectCategory =
  | "Coding"
  | "Documentation"
  | "Refactor"
  | "Research";

export interface ExampleProjectRequires {
  source_repo: boolean;
  hints: string[];
}

export interface ExampleProjectSummary {
  id: string;
  title: string;
  category: ExampleProjectCategory;
  icon: string;
  summary: string;
  details: string;
  default_project_id: string;
  default_goal: string;
  requires: ExampleProjectRequires;
  has_seed_inputs: boolean;
}

export interface InstantiateExampleProjectResponse {
  project: ProjectConfig;
  from_template: string;
}

// ── Task desks (M3.5 §38.12) ───────────────────────────────────────────────

export interface TaskRef {
  kind: string;
  value: string;
  note: string | null;
}

export interface Task {
  task_id: string;
  story_id: string;
  parent_task_id: string | null;
  assigned_to: string;
  created_by: string;
  created_at_iter: number;
  goal: string;
  acceptance_criteria: string[];
  out_of_scope: string[];
  references: TaskRef[];
  status: "PENDING" | "IN_PROGRESS" | "COMPLETE" | "CANCELLED";
  started_at_iter: number | null;
  completed_at_iter: number | null;
  closed_by_commit: string | null;
}

export interface DesksData {
  desks: Record<string, Task[]>;
  tasksById: Record<string, Task>;
}

// ── One-shot launcher (POST /api/oneshot) ──────────────────────────────────

export interface OneShotRequestBody {
  goal: string;
  web: "researcher" | "off";
  /** USD; the backend accepts > 0 and <= 1000. */
  budget: number;
  demo: boolean;
}

export interface OneShotResponse {
  accepted: boolean;
  needs_approval: boolean;
  project_id: string;
  run_id?: string;
  job_id?: string;
  status_url?: string;
  confirm_url?: string;
  estimated_cost?: CostEstimate;
  message?: string;
}

// ── Project playbook (GET /api/projects/{id}/playbook) ─────────────────────

export type PlaybookSection =
  | "team_adjustments"
  | "brief_addenda"
  | "known_pitfalls"
  | "review_rounds"
  | "capability_hints"
  | "task_templates";

export interface PlaybookEntry {
  id: string;
  section: PlaybookSection;
  key: string;
  value: unknown;
  confidence: number;
  state: "emerging" | "active" | "declining" | "archived" | "superseded";
  evidence_runs: string[];
  needs_review?: boolean;
}

export interface PlaybookResponse {
  entries: PlaybookEntry[];
  metrics: { run_id: string; recorded_at: string; metrics: Record<string, number> }[];
}
