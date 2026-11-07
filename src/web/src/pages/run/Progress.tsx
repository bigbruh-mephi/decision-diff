import { Card, CardHeader } from "../../components/ui";
import type { Run } from "../../lib/api";
import { EVENT, STATE, eventText, n, pct, plural } from "../../lib/format";

const STEPS: { state: Run["state"][]; label: string }[] = [
  { state: ["receiving"], label: "Приём и нарезка порций" },
  { state: ["sealed"], label: "Приведение и сравнение порций" },
  { state: ["finalizing"], label: "Сборка витрины, фактов (counts) и автопроверки" },
  { state: ["done"], label: "Отчёт готов" },
];

export function Progress({ run, live }: { run: Run; live: { type: string; data: Record<string, unknown> }[] }) {
  const chunks = run.chunks ?? [];
  const done = chunks.find((c) => c.state === "done");
  const total = chunks.reduce((s, c) => s + c.n, 0);
  const processed = done?.apps ?? 0;
  const changed = chunks.reduce((s, c) => s + (c.changed ?? 0), 0);
  const idx = STEPS.findIndex((s) => s.state.includes(run.state));
  const last = [...live].reverse().find((e) => e.type === "chunk.done");
  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card>
        <CardHeader title="Обработка идёт" hint="Отчёт откроется после полного завершения. Страницу можно закрыть: состояние сохранится." />
        <div className="space-y-4 p-4">
          <ol className="space-y-2">
            {STEPS.map((s, i) => (
              <li key={s.label} className="flex items-center gap-3">
                <span
                  aria-hidden
                  className={
                    "grid h-5 w-5 place-items-center rounded-full border text-[10px] " +
                    (i < idx || run.state === "done" ? "border-pass bg-pass text-white" : i === idx ? "border-accent text-accent" : "border-line-strong text-muted")
                  }
                >
                  {i < idx || run.state === "done" ? "✓" : i + 1}
                </span>
                <span className={i === idx ? "font-medium" : i < idx ? "text-muted" : "text-muted"}>{s.label}</span>
                {i === idx && run.state !== "done" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />}
              </li>
            ))}
          </ol>
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted">
              <span>Порции: {done?.n ?? 0} из {run.total_chunks ?? (total || "…")}</span>
              <span className="num">{n(processed)} из {plural(run.apps, "заявки", "заявок", "заявок")}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={processed} aria-valuemax={run.apps}>
              <div className="h-full bg-accent transition-all" style={{ width: `${run.apps ? (processed / run.apps) * 100 : 0}%` }} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div className="rounded-md bg-surface-2 px-3 py-2">
              <div className="text-xs text-muted">Обработано заявок</div>
              <div className="num text-lg font-semibold">{n(processed)}</div>
            </div>
            <div className="rounded-md bg-surface-2 px-3 py-2">
              <div className="text-xs text-muted">С различиями (пока)</div>
              <div className="num text-lg font-semibold">{n(changed)} <span className="text-xs font-normal text-muted">{pct(changed, processed)}</span></div>
            </div>
            <div className="rounded-md bg-surface-2 px-3 py-2">
              <div className="text-xs text-muted">Состояние</div>
              <div className="text-lg font-semibold">{STATE[run.state]}</div>
            </div>
          </div>
        </div>
      </Card>
      <Card>
        <CardHeader title="События" hint="Приходят в реальном времени и восстанавливаются после перезагрузки страницы" />
        <ul className="max-h-80 space-y-1 overflow-y-auto p-3 font-mono text-[11.5px]">
          {[...live].reverse().slice(0, 80).map((e, i) => (
            <li key={i} className="flex gap-2">
              <span className="shrink-0 text-accent" title={e.type}>{EVENT[e.type] ?? e.type}</span>
              <span className="truncate text-muted">{eventText(e.type, e.data)}</span>
            </li>
          ))}
          {!live.length && <li className="text-muted">ожидание событий…</li>}
        </ul>
        {last && <p className="border-t border-line px-3 py-2 text-xs text-muted">последняя порция: №{String(last.data.chunk_no)}</p>}
      </Card>
    </div>
  );
}
