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
  model_tier?: ModelTier;
  agent_class_role_type?: string | null;
  is_reviewer?: boolean;
  max_tool_iterations?: number;
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
}

export interface RunMeta {
  run_id: string;
  project_id: string;
  goal?: string;
  status: string;
  iteration?: number;
  completed_agents?: string[];
  created_at?: string;
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
  n_episodes: number;
  reputation: {
    production?: { total_runs?: number; reviewer_first_pass_rate?: number };
    training?: { total_runs?: number; graduation_status?: string };
    total_episodes?: number;
    active_patterns?: number;
  };
}

export interface InvokeResponse {
  accepted: boolean;
  run_id: string;
  job_id: string;
  project_id: string;
  enqueued_at: string;
}

export interface MetaProposal {
  rationale: string;
  agents: AgentConfig[];
  rbac: Record<string, RoleRBAC>;
}
