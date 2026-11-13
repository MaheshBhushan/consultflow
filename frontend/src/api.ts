export type InputKind = "document" | "email";

export interface Client {
  id: number;
  name: string;
  contact_email: string | null;
  notes: string;
  created_at: string;
}

export interface Step {
  name: string;
  prompt: string;
}

export interface AgentConfig {
  name: string;
  provider: string;
  model: string;
  system_prompt: string;
  input_kind: InputKind;
  steps: Step[];
}

export interface Agent extends AgentConfig {
  id: number;
  client_id: number;
  created_at: string;
  updated_at: string;
}

export interface InputSource {
  id: number;
  client_id: number;
  kind: InputKind;
  origin: "upload" | "paste";
  filename: string | null;
  content_type: string | null;
  sha256: string;
  meta: Record<string, unknown>;
  text: string;
  created_at: string;
}

export interface RunStep {
  id: number;
  position: number;
  name: string;
  provider: string;
  model: string;
  sources: string[];
  prompt: string;
  output: string | null;
  status: string;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  latency_ms: number;
  started_at: string;
}

export interface RunSummary {
  id: number;
  agent_id: number;
  input_id: number;
  status: string;
  error: string | null;
  total_cost_usd: string;
  total_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  started_at: string;
  finished_at: string | null;
}

export interface Run extends RunSummary {
  agent_snapshot: AgentConfig;
  final_output: string | null;
  steps: RunStep[];
}

export type Providers = Record<string, { models: string[]; available: boolean }>;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d))).join("; ");
  return JSON.stringify(detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  const body = res.status === 204 ? null : await res.json();
  if (!res.ok) throw new ApiError(res.status, describe(body?.detail ?? res.statusText));
  return body as T;
}

const json = (method: string, data: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export const api = {
  providers: () => request<Providers>("/providers"),
  listClients: () => request<Client[]>("/clients"),
  createClient: (data: { name: string; contact_email?: string | null }) =>
    request<Client>("/clients", json("POST", data)),
  listAgents: (clientId: number) => request<Agent[]>(`/clients/${clientId}/agents`),
  createAgent: (clientId: number, cfg: AgentConfig) =>
    request<Agent>(`/clients/${clientId}/agents`, json("POST", cfg)),
  updateAgent: (agentId: number, cfg: AgentConfig) => request<Agent>(`/agents/${agentId}`, json("PUT", cfg)),
  listInputs: (clientId: number) => request<InputSource[]>(`/clients/${clientId}/inputs`),
  uploadInput: (clientId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<InputSource>(`/clients/${clientId}/inputs/upload`, { method: "POST", body: form });
  },
  pasteEmail: (clientId: number, raw: string) =>
    request<InputSource>(`/clients/${clientId}/inputs/email`, json("POST", { raw })),
  startRun: (agentId: number, inputId: number) =>
    request<Run>(`/agents/${agentId}/runs`, json("POST", { input_id: inputId })),
  listRuns: (agentId: number) => request<RunSummary[]>(`/agents/${agentId}/runs`),
  getRun: (runId: number) => request<Run>(`/runs/${runId}`),
};

export function formatCost(usd: string | number): string {
  return `$${Number(usd).toFixed(6)}`;
}

export function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

export function inputLabel(src: InputSource): string {
  const subject = typeof src.meta.subject === "string" && src.meta.subject ? src.meta.subject : null;
  return `#${src.id} ${src.kind}: ${src.filename ?? subject ?? "pasted email"}`;
}
