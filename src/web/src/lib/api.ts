export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : j.error ?? "Проверьте введённые данные";
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b ?? {}),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export type Scope = { seg?: Record<string, string[]>; cluster?: number; apps?: string[] };

export const scopeQuery = (s: Scope) =>
  Object.keys(s).length ? `?scope=${encodeURIComponent(JSON.stringify(s))}` : "";

export type Me = { user_id: string | null; login: string | null; run_id: string | null };
export type Dataset = { path: string; run_id: string; applications: number | null; before: number; after: number; excluded: string[] };
export type Procedure = { id: string; kind: string; name: string; origin: "file" | "workspace"; path: string | null; sha: string; updated_at: string; mine: boolean; code?: string };
export type Selection = { normalize: string; compare: string; segments: string; lineage?: string | null; checks: string[] };
export type Config = { id: string; name: string; dataset: string | null; procedure_ids: Selection; params: Record<string, unknown>; updated_at: string };
export type RunStats = { apps?: number; changed_apps?: number; mart_rows?: number; comparison_rows?: number; facts?: number };
export type Run = {
  id: string; name: string; state: "receiving" | "sealed" | "finalizing" | "done" | "failed" | "cancelled";
  apps: number; total_chunks: number | null; done_chunks: number; failed_chunks: number; stats: RunStats;
  error: string | null; created_at: string; finished_at: string | null; sealed_at?: string | null;
  chunks?: { state: string; n: number; apps: number; changed: number }[];
  procedures?: Record<string, unknown>;
};
export type Swap = { before: string; after: string; apps: number };
export type Transition = { path?: string; before: string; after: string; apps: number };
export type Cluster = {
  cid: number | null; apps: number; decision_changed: number; origin: "input" | "logic" | "mixed";
  paths?: string[]; input_paths?: string[]; transitions?: Transition[]; swap?: Swap[]; clusters?: number;
};
export type Structure = { path_id: number; PATH: string; PREFIX: string; apps_new: number; apps_lost: number; apps_type_changed: number; apps_before: number; apps_after: number };
export type Summary = {
  decision_field: string; apps: number; changed: number; decision_changed: number; changed_same_decision: number; unchanged: number;
  approval_before: number | null; approval_after: number | null; input_driven: number; swap: Swap[];
  run: { id: string; name: string; stats: RunStats }; structure: Structure[]; clusters: Cluster[];
};
export type Attribute = { path_id: number; PATH: string; PREFIX: string; KEY: string; apps: number; decision_changed: number; input: boolean; by_type: Record<string, number>; transitions: Transition[] };
export type SegmentField = { field: string; values: { value: string; apps: number; changed: number; decision_changed: number }[] };
export type CheckRow = { LABEL: string; SOURCE: string; ARGUMENT: string; CNT: number; TOTAL: number; METRIC: number | string; APP_IDS: string[]; RESULT: "PASS" | "FAIL"; CONFIDENCE: "HIGH" | "LOW" };
export type CheckResult = { check_id: string; check_sha: string; label: string; rows: CheckRow[]; duration_ms: number; error: string | null; created_at: string };
export type AppRow = { app_id: string; changed: boolean; decision_changed: boolean; cluster: number | null; decision_before: string | null; decision_after: string | null };
export type AppDetail = {
  app_id: string; cluster: number | null; segments: Record<string, unknown>;
  rows: { path_id: number; PATH: string; PREFIX: string; CLASS: string; SUFFIX: string; KEY: string; ARRAY_KEY: string; VALUE_BEFORE: string | null; VALUE_AFTER: string | null; ADJUSTED_BEFORE: string | null; ADJUSTED_AFTER: string | null; CHANGE_TYPE: string; TYPE_CHANGED: boolean; input: boolean }[];
  checks: { check_id: string; label: string; row: CheckRow }[];
};
export type Pivot = { dims: string[]; cells: (string | number)[][]; truncated: boolean; ms: number };
export type Fields = { fact: Record<string, string[]>; segment: Record<string, string[]>; cluster: string[] };
export type Journal = {
  stages: { stage: string; code_sha: string | null; executions: number; failed: number; started_at: string; ended_at: string; cpu_ms: number; rows_in: number | null; rows_out: number | null; error: string | null }[];
  events: { id: number; ts: string; type: string; data: Record<string, unknown> }[];
  chunks: { chunk_no: number; state: string; apps: number; attempts: number; started_at: string | null; finished_at: string | null; worker: string | null; error: string | null }[];
};
export type CellResult = {
  id: string; state: "queued" | "running" | "done" | "failed";
  result: { stdout: string; error: string | null; traceback?: string[]; value: null | { type: string; repr?: string; rows?: Record<string, unknown>[]; more?: boolean }; check: null | { id: string; label: string; rows: CheckRow[] } } | null;
};
