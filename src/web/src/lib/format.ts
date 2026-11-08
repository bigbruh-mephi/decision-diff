const nf = new Intl.NumberFormat("ru-RU");

export const n = (x: number | null | undefined) => (x == null ? "—" : nf.format(x));

export const plural = (x: number, one: string, few: string, many: string) => {
  const m10 = x % 10;
  const m100 = x % 100;
  return `${n(x)} ${m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many}`;
};

export const pct = (part: number, whole: number, digits = 1) =>
  whole ? `${((part / whole) * 100).toFixed(digits).replace(".", ",")}%` : "—";

export const rate = (x: number | null | undefined) => (x == null ? "—" : `${(x * 100).toFixed(1).replace(".", ",")}%`);

export const ms = (x: number) => (x >= 1000 ? `${(x / 1000).toFixed(2).replace(".", ",")} с` : `${Math.round(x)} мс`);

export const dt = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";

export const CHANGE: Record<string, { glyph: string; label: string; cls: string }> = {
  VALUE_CHANGED: { glyph: "~", label: "значение изменилось", cls: "text-ct-changed" },
  ATTRIBUTE_NEW: { glyph: "+", label: "атрибут появился", cls: "text-ct-new" },
  ATTRIBUTE_LOST: { glyph: "−", label: "атрибут исчез", cls: "text-ct-lost" },
  SAME: { glyph: "=", label: "без изменений", cls: "text-ct-same" },
};

export const STATE: Record<string, string> = {
  receiving: "приём данных",
  sealed: "обработка",
  finalizing: "сборка отчёта",
  done: "готов",
  failed: "ошибка",
  cancelled: "отменён",
};

export const CHUNK_STATE: Record<string, string> = {
  ready: "ожидает",
  queued: "в очереди",
  running: "обрабатывается",
  done: "готова",
  failed: "ошибка",
  cancelled: "отменена",
};

export const KIND: Record<string, string> = {
  normalize: "нормализация",
  compare: "сравнение",
  segments: "разрезы",
  facts: "факты",
  check: "автопроверка",
  lineage: "цепочка решения",
  whatif: "что если",
  parser: "разбор формата",
  step: "шаг подготовки",
  ai_prompt: "запрос к ИИ",
  cell: "ячейка",
};

export const EVENT: Record<string, string> = {
  "run.created": "прогон создан",
  "ingest.started": "прием порции начат",
  "ingest.done": "порция принята",
  "run.sealed": "прием завершен",
  "chunk.done": "порция обработана",
  "chunk.failed": "ошибка порции",
  "finalize.started": "сборка отчета",
  "run.done": "прогон готов",
  "run.failed": "прогон завершился с ошибкой",
  "run.cancelled": "прогон отменен",
  "check.queued": "проверка в очереди",
  "check.done": "проверка выполнена",
  "ai.done": "мнение ИИ готово",
};

/** One readable line for a run event; the error (already in Russian from the API) wins over raw fields. */
export const eventText = (type: string, d: Record<string, unknown>) => {
  if (d.error) return `${d.check_id ? `${d.check_id}: ` : d.chunk_no != null ? `№${d.chunk_no}: ` : ""}${d.error}`;
  if (type === "chunk.done") return `№${d.chunk_no} · ${plural(Number(d.apps), "заявка", "заявки", "заявок")} · с различиями ${n(Number(d.changed_apps))}`;
  if (type === "ingest.done") return `${plural(Number(d.chunks), "порция", "порции", "порций")} · ${plural(Number(d.apps), "заявка", "заявки", "заявок")}`;
  if (type === "run.done") return `${plural(Number(d.apps), "заявка", "заявки", "заявок")} · с различиями ${n(Number(d.changed_apps))}`;
  if (type === "finalize.started") return plural(Number(d.chunks), "порция", "порции", "порций");
  if (type === "check.done") return `${d.check_id ?? ""}: без ошибок`;
  return "";
};

export const tail = (path: string, k = 3) => {
  const parts = path.split(".");
  return parts.length > k ? "…" + parts.slice(-k).join(".") : path;
};

export type Labels = { object: string[]; outcome: string; positive: string; positive_rate: string };
export const LABELS: Labels = { object: ["объект", "объекта", "объектов"], outcome: "исход", positive: "положительный исход", positive_rate: "доля положительного исхода" };
export const objects = (x: number, l: Labels) => plural(x, l.object[0], l.object[1], l.object[2]);
export const ofObjects = (x: number, l: Labels) => plural(x, l.object[1], l.object[2], l.object[2]);
