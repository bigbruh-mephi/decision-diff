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

export const tail = (path: string, k = 3) => {
  const parts = path.split(".");
  return parts.length > k ? "…" + parts.slice(-k).join(".") : path;
};
