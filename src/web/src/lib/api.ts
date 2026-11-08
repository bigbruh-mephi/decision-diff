/** API error: ``message`` is the server's Russian ``detail``; ``debug`` keeps the technical text for "Подробности". */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public field?: string | null,
    public debug?: string | null,
  ) {
    super(message);
  }
}

const STATUS: Record<number, string> = {
  413: "Файл или запрос слишком большой для сервера",
  502: "Сервер приложения недоступен",
  503: "Сервис временно недоступен",
  504: "Сервер не ответил вовремя",
};

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, "Нет связи с сервером. Проверьте подключение к сети и повторите.", "network", null, String(e));
  }
  if (!res.ok) {
    let j: { detail?: unknown; code?: string; field?: string | null; debug?: string | null } = {};
    try {
      j = await res.json();
    } catch {
      /* not JSON: a proxy page */
    }
    const msg = typeof j.detail === "string" ? j.detail : `${STATUS[res.status] ?? "Ошибка сервера"} (HTTP ${res.status}). Повторите через минуту.`;
    throw new ApiError(res.status, msg, j.code, j.field, j.debug ?? (typeof j.detail === "string" ? null : `HTTP ${res.status} ${res.statusText}`));
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b ?? {}),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export type Scope = { seg?: Record<string, string[]>; cluster?: number; apps?: string[]; check?: { sha: string; row: number; cnt: number }; cause?: { key: string; label: string } };

export const scopeQuery = (s: Scope) =>
  Object.keys(s).length ? `?scope=${encodeURIComponent(JSON.stringify(s))}` : "";

export type Me = { user_id: string | null; login: string | null; run_id: string | null };
export type Dataset = { path: string; run_id: string; applications: number | null; before: number; after: number; excluded: string[]; slices?: string[] };
export type Procedure = { id: string; kind: string; name: string; origin: "file" | "workspace"; path: string | null; sha: string; updated_at: string; mine: boolean; cid?: string | null; description?: string; overridden?: boolean; code?: string; params?: Record<string, unknown> };
export type Selection = { normalize?: string | null; compare?: string | null; segments?: string | null; lineage?: string | null; whatif?: string | null; parser?: string | null; ai_prompt?: string | null; steps?: string[]; checks: string[] };
export type Config = { id: string; name: string; dataset: string | null; procedure_ids: Selection; params: Record<string, unknown>; updated_at: string };
export type RunStats = { apps?: number; changed_apps?: number; mart_rows?: number; comparison_rows?: number; facts?: number };
export type Run = {
  id: string; name: string; state: "receiving" | "sealed" | "finalizing" | "done" | "failed" | "cancelled";
  apps: number; total_chunks: number | null; done_chunks: number; failed_chunks: number; stats: RunStats;
  error: string | null; error_debug?: string | null; created_at: string; finished_at: string | null; sealed_at?: string | null; seal_requested?: boolean; config_id?: string | null;
  chunks?: { state: string; n: number; apps: number; changed: number }[];
  procedures?: Record<string, unknown>;
};
export type Swap = { before: string; after: string; apps: number };
export type Transition = { path?: string; before: string; after: string; apps: number };
export type Cluster = {
  cid: number | null; apps: number; decision_changed: number; origin: "input" | "logic" | "mixed";
  paths?: string[]; input_paths?: string[]; transitions?: Transition[]; swap?: Swap[]; clusters?: number;
  causes?: Cause[];
};
export type Cause = { key: string; label: string; hint?: string | null; apps: number };
export type Summary = {
  decision_field: string; apps: number; changed: number; decision_changed: number; changed_same_decision: number; unchanged: number;
  approval_before: number | null; approval_after: number | null; input_driven: number; swap: Swap[];
  run: { id: string; name: string; stats: RunStats }; clusters: Cluster[];
  issues?: { kind: string; apps: number; rows: number; examples: string[] }[]; labels?: Partial<import("./format").Labels>; causes_fidelity?: number | null;
};
export type Attribute = { path_id: number; PATH: string; PREFIX: string; KEY: string; apps: number; decision_changed: number; input: boolean; by_type: Record<string, number>; transitions: Transition[] };
export type SegmentField = { field: string; values: { value: string; apps: number; changed: number; decision_changed: number }[] };
export type CheckRow = { LABEL: string; SOURCE: string; ARGUMENT: string; CNT: number; TOTAL: number; METRIC: number | string; APP_IDS: string[]; RESULT: "PASS" | "FAIL"; CONFIDENCE: "HIGH" | "LOW" };
export type CheckResult = { check_id: string; check_sha: string; label: string; rows: CheckRow[]; duration_ms: number; error: string | null; error_debug?: string | null; created_at: string };
export type AppRow = { app_id: string; changed: boolean; decision_changed: boolean; cluster: number | null; decision_before: string | null; decision_after: string | null };
export type AppDetail = {
  app_id: string; cluster: number | null; segments: Record<string, unknown>;
  rows: { path_id: number; PATH: string; PREFIX: string; CLASS: string; SUFFIX: string; KEY: string; ARRAY_KEY: string; VALUE_BEFORE: string | null; VALUE_AFTER: string | null; ADJUSTED_BEFORE: string | null; ADJUSTED_AFTER: string | null; CHANGE_TYPE: string; TYPE_CHANGED: boolean; input: boolean }[];
  checks: { check_id: string; label: string; row: CheckRow }[];
};
export type Pivot = { dims: string[]; cells: (string | number)[][]; truncated: boolean; ms: number; row_totals?: (string | number)[][]; col_totals?: (string | number)[][]; total?: number };
export type Fields = { fact: Record<string, string[]>; segment: Record<string, string[]>; cluster: string[] };
export type PivotLayout = { rows: string[]; cols: string[]; filters: Record<string, string[]>; mode?: "count" | "row" | "col" | "total" };
export type PivotTemplate = { id: string; name: string; layout: PivotLayout; shared: boolean; mine: boolean; owner?: string };
export type Journal = {
  stages: { stage: string; code_sha: string | null; executions: number; failed: number; started_at: string; ended_at: string; cpu_ms: number; rows_in: number | null; rows_out: number | null; error: string | null; error_debug?: string | null }[];
  events: { id: number; ts: string; type: string; data: Record<string, unknown> }[];
  chunks: { chunk_no: number; state: string; apps: number; attempts: number; started_at: string | null; finished_at: string | null; worker: string | null; error: string | null; error_debug?: string | null }[];
};
export type CellResult = {
  id: string; state: "queued" | "running" | "done" | "failed";
  result: { stdout: string; error: string | null; error_debug?: string | null; traceback?: string[]; value: null | { type: string; repr?: string; rows?: Record<string, unknown>[]; more?: boolean }; check: null | { id: string; label: string; rows: CheckRow[] } } | null;
};
