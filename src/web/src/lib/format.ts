const nf = new Intl.NumberFormat("ru-RU");

export const n = (x: number | null | undefined) => (x == null ? "—" : nf.format(x));

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

export const tail = (path: string, k = 3) => {
  const parts = path.split(".");
  return parts.length > k ? "…" + parts.slice(-k).join(".") : path;
};

export const EVENT: Record<string, string> = {
  "run.created": "прогон создан",
  "run.sealed": "приём ответов завершён",
  "run.done": "прогон завершён",
  "run.failed": "прогон завершился ошибкой",
  "run.cancelled": "прогон отменён",
  "ingest.started": "приём ответов начат",
  "ingest.done": "ответы приняты",
  "chunk.done": "порция обработана",
  "chunk.failed": "ошибка порции",
  "finalize.started": "сборка витрины",
  "check.queued": "проверка в очереди",
  "check.done": "проверка выполнена",
  "ai.done": "мнение ИИ получено",
};
