// Typed client for campaign_agent/web/app.py. Shapes mirror campaign_agent/web/view.py.

export type StageKey = "understand" | "plan" | "diagnose" | "discuss" | "brief";
export type LrmMode = "live" | "simulated" | "off";

export interface Brand { brand_id: string; name: string; category: string }
export interface Psg { psg_id: string; name: string }
export interface ExcludedPsg extends Psg { reason: string; filters?: string[] }
export interface Stage { key: StageKey; label: string; status: "done" | "current" | "todo" }
export interface ChatMessage { role: "human" | "agent"; agent: string | null; text: string }
export interface Pending {
  stage: StageKey; awaiting: "checkpoint" | "clarification"; title: string;
  summary: string[]; key_numbers: [string, string][]; actions: string[];
}
export interface Cohort { cohort: string; label: string; size: number | null; include: boolean; reason: string }
export interface Finding { agent: string; findings: { text: string; blocks: number[] }[]; watch: string[] }
export interface Pack {
  pack_id: string; key_numbers: [string, string][]; notes: string[];
  reconciliation: { passed: boolean; checks: { check: string; passed: boolean; detail: string }[] };
  findings: Finding[]; audience: Cohort[]; audience_total: number;
  psgs: { include: Psg[]; hero: Psg[]; exclude: ExcludedPsg[] };
  extra_cuts: { tool?: string; params: Record<string, unknown>; output: unknown }[];
  errors: { block: string; tool: string; error: string }[];
}
export interface Proposal {
  label: string; brand: Brand | null; cohorts: string[]; excluded_psgs: number; hero: Psg[];
  claims: { topic: string | null; text: string; cites: number[] }[];
}
export interface Decision {
  key_numbers: [string, string][];
  brand_decision: { requested: string; selected: string; reason: string; alternatives_considered: string[];
                    selected_brand: Brand | null; requested_brand: Brand | null };
  audience: (Cohort & { cohort: string })[]; hero: Psg[]; include: Psg[]; exclude: ExcludedPsg[];
  dissent: { by: string; item: string; position: string; reason: string }[];
  notes_for_draft: string[]; rounds: number; agreement: number; converged: boolean;
  human_overrides: Record<string, unknown>[];
  transcript: { round: number; speaker: string; text: string; in_scope: boolean }[];
}
export interface HarnessRun {
  skill: string; source: "llm" | "fallback"; reason: string | null; iterations: number;
  tools: string[]; rejected: number; stage: string | null;
}
export interface RunView {
  run_id: string; lrm_mode: LrmMode; user: string; status: string | null; done: boolean;
  stage: StageKey | null; stages: Stage[]; pending: Pending | null; chat: ChatMessage[];
  request: { text: string | null; brand: Brand | null; requested_brand: Brand | null; objective: string | null;
             window: { start: string; weeks: number } | null };
  counters: { tool_calls: number; send_backs: number; brand_switches: number };
  plan: { signals: Record<string, unknown>;
          steps: { agent: string; tool: string; block: number | null; reason: string; added_by: string;
                   params: Record<string, unknown> }[] } | null;
  pack: Pack | null;
  discussion: { proposals: Proposal[]; top_alternative?: Brand | null; locked?: boolean;
                decision: Decision | null } | null;
  brief: { json: Record<string, unknown>; summary_md: string; handoff: { sink: string } | null } | null;
  harness_log: HarnessRun[];
  working_memory: { agent: string; stage: string | null; note: string }[];
  audit: { ts: string; stage: string; actor: string; event: string; text?: string; action?: string;
           target?: string; pack_id?: string }[];
  busy: boolean; error: string | null; last_event: number;
}
export interface RunSummary { run_id: string; request: string | null; brand: Brand | null; stage: string | null;
                              done: boolean; busy: boolean }
export interface AppConfig { lrm_mode: LrmMode; lrm_reason?: string; user: string; autonomy: string; examples: string[] }
export interface AgentEvent {
  seq: number; ts: string; type: string; skill?: string; agent?: string; name?: string; text?: string;
  reason?: string; errors?: string[]; ms?: number; ok?: boolean; mode?: string; stage?: string; done?: boolean;
}
export interface EpisodicMemory {
  runs: { run_id: string; category_id: string; brand_requested: string; brand_selected: string;
          objective: string; overrides: number }[];
  overrides: unknown[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error ?? `HTTP ${res.status}`);
  return data as T;
}

export const api = {
  config: () => request<AppConfig>("/api/config"),
  runs: () => request<RunSummary[]>("/api/runs"),
  run: (id: string) => request<RunView>(`/api/runs/${encodeURIComponent(id)}`),
  start: (message: string) => request<RunView>("/api/runs", { method: "POST", body: JSON.stringify({ message }) }),
  reply: (id: string, message: string) =>
    request<RunView>(`/api/runs/${encodeURIComponent(id)}/reply`, { method: "POST", body: JSON.stringify({ message }) }),
  memory: () => request<EpisodicMemory>("/api/memory"),
  briefUrl: (id: string) => `/api/runs/${encodeURIComponent(id)}/brief.json`,
  events: (id: string, after: number) => new EventSource(`/api/runs/${encodeURIComponent(id)}/events?after=${after}`),
};

export const AGENT_NAMES: Record<string, string> = {
  chat_gateway: "Chat Gateway", planner: "Campaign Planner", entity_resolution: "Entity Resolution",
  brand_diagnostics: "Brand Diagnostics", qualifying_psg: "Qualifying PSG", market_intelligence: "Market Intelligence",
  commercial: "Commercial", performance_memory: "Performance Memory", brand_scout: "Brand Scout",
  audience: "Audience", reach_advocate: "Reach advocate", margin_advocate: "Margin advocate",
  customer_advocate: "Customer advocate", critic_judge: "Critic / Judge", brief_writer: "Brief Writer",
  pack_analyst: "Pack Analyst",
};

export const agentName = (key?: string | null) => (key ? AGENT_NAMES[key] ?? key.replace(/_/g, " ") : "Agent");
export const fmt = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("en-GB"));
