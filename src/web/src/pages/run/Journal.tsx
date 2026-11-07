import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useRef, useState } from "react";
import { CodeView } from "../../components/CodeView";
import { Badge, Button, Card, CardHeader, ErrorBox, Skeleton } from "../../components/ui";
import { api, type Journal } from "../../lib/api";
import { CHUNK_STATE, EVENT, KIND, dt, eventText, ms, n, plural } from "../../lib/format";

const STAGE: Record<string, string> = {
  parse: "Разбор ответов (формат → дерево)",
  normalize: "Приведение к плоской витрине",
  compare: "Сопоставление BEFORE / AFTER",
  segments: "Слой разрезов",
  write: "Запись порции (Parquet)",
  finalize: "Сборка витрины, фактов (counts) и индексов",
  checks: "Автопроверки",
  lineage: "Линия принятия решения (роли атрибутов для кластеров)",
  attribution: "Причины смены решения (контрфакты процедуры «что если»)",
  ai: "Второе мнение ИИ",
};

const stageTitle = (s: string) => STAGE[s] ?? (s.startsWith("check:") ? `Автопроверка ${s.slice(6)}` : s);

function CodeModal({ sha, onClose }: { sha: string; onClose: () => void }) {
  const code = useQuery({ queryKey: ["code", sha], queryFn: () => api.get<{ sha: string; kind: string; name: string; code: string; created_at: string }>(`/api/code/${sha}`) });
  return (
    <div role="dialog" aria-modal="true" aria-label="Код этапа" className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-lg border border-line bg-surface" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <div>
            <h2 className="text-[13px] font-semibold">{code.data?.name ?? "Код"}</h2>
            <p className="text-xs text-muted">версия {sha} · исполнялась именно она{code.data ? ` · ${KIND[code.data.kind] ?? code.data.kind}` : ""}</p>
          </div>
          <Button size="sm" onClick={onClose} autoFocus>Закрыть</Button>
        </header>
        <div className="min-h-0 flex-1 p-3">{code.data ? <CodeView value={code.data.code} readOnly className="h-[70vh] overflow-hidden rounded-md border border-line" /> : <Skeleton />}</div>
      </div>
    </div>
  );
}

export function JournalView({ runId, live }: { runId: string; live: unknown[] }) {
  const [tick, setTick] = useState(live.length);
  const latest = useRef(live.length);
  latest.current = live.length;
  const pending = tick !== live.length;
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(() => setTick(latest.current), 1000);
    return () => clearTimeout(t);
  }, [pending]);
  const j = useQuery({ queryKey: ["journal", runId, tick], queryFn: () => api.get<Journal>(`/api/runs/${runId}/journal`), placeholderData: (p) => p });
  const [sha, setSha] = useState<string | null>(null);
  if (j.isLoading) return <Skeleton rows={8} />;
  if (j.error || !j.data) return <ErrorBox error={j.error} />;
  const { stages, chunks, events } = j.data;
  const t0 = stages.length ? Math.min(...stages.map((s) => new Date(s.started_at).getTime())) : 0;
  const t1 = stages.length ? Math.max(...stages.map((s) => new Date(s.ended_at).getTime())) : 1;
  const span = Math.max(1, t1 - t0);
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader title="Этапы" hint="Время по всем порциям, строки на входе и выходе; «код» открывает именно ту версию, которая исполнялась" />
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b border-line">
                <th className="px-4 py-2 font-medium">Этап</th>
                <th className="px-2 py-2 font-medium">Выполнений</th>
                <th className="px-2 py-2 text-right font-medium">Строк вход → выход</th>
                <th className="px-2 py-2 text-right font-medium" title="Суммарное время по всем порциям">Время</th>
                <th className="w-1/3 px-2 py-2 font-medium">Шкала времени</th>
                <th className="px-4 py-2 font-medium">Код</th>
              </tr>
            </thead>
            <tbody>
              {stages.map((s) => {
                const a = new Date(s.started_at).getTime();
                const b = new Date(s.ended_at).getTime();
                return (
                  <tr key={s.stage + s.code_sha} className="border-b border-line last:border-0">
                    <td className="px-4 py-2">
                      <div className="font-medium">{stageTitle(s.stage)}</div>
                      {s.error && <ErrorBox inline error={s.error} debug={s.error_debug} />}
                    </td>
                    <td className="num px-2 py-2">
                      {n(s.executions)} {s.failed > 0 && <Badge tone="fail">{plural(s.failed, "ошибка", "ошибки", "ошибок")}</Badge>}
                    </td>
                    <td className="num px-2 py-2 text-right text-xs">{s.rows_in != null ? n(s.rows_in) : "—"} → {s.rows_out != null ? n(s.rows_out) : "—"}</td>
                    <td className="num px-2 py-2 text-right">{ms(s.cpu_ms)}</td>
                    <td className="px-2 py-2">
                      <div className="relative h-2 rounded-full bg-surface-2">
                        <div className="absolute h-full rounded-full bg-muted/60" style={{ left: `${((a - t0) / span) * 100}%`, width: `${Math.max(1, ((b - a) / span) * 100)}%` }} />
                      </div>
                    </td>
                    <td className="px-4 py-2">
                      {s.code_sha ? <button className="text-xs text-accent hover:underline" onClick={() => setSha(s.code_sha)}>код {s.code_sha.slice(0, 7)}</button> : <span className="text-xs text-muted">ядро</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title={`Порции · ${chunks.length}`} hint="Повторные попытки после сбоя видны в колонке попыток" />
          <div className="max-h-80 overflow-y-auto">
            <table className="w-full text-xs">
              <tbody>
                {chunks.map((c) => (
                  <Fragment key={c.chunk_no}>
                  <tr className="border-b border-line">
                    <td className="num px-4 py-1">№{c.chunk_no}</td>
                    <td className="px-2 py-1"><Badge tone={c.state === "done" ? "pass" : c.state === "failed" ? "fail" : "accent"}>{CHUNK_STATE[c.state] ?? c.state}</Badge></td>
                    <td className="num px-2 py-1">{plural(c.apps, "заявка", "заявки", "заявок")}</td>
                    <td className="num px-2 py-1">{c.attempts > 1 ? <Badge tone="warn">{plural(c.attempts, "попытка", "попытки", "попыток")}</Badge> : plural(c.attempts, "попытка", "попытки", "попыток")}</td>
                    <td className="px-2 py-1 text-muted">{c.worker}</td>
                    <td className="num px-4 py-1 text-right text-muted">{c.started_at && c.finished_at ? ms(new Date(c.finished_at).getTime() - new Date(c.started_at).getTime()) : "…"}</td>
                  </tr>
                  {c.error && <tr className="border-b border-line"><td colSpan={6} className="px-4 pb-2"><ErrorBox inline error={c.error} debug={c.error_debug} /></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card>
          <CardHeader title="События прогона" />
          <ul className="max-h-80 overflow-y-auto font-mono text-xs">
            {events.map((e) => (
              <li key={e.id} className="flex gap-2 border-b border-line px-4 py-1">
                <span className="shrink-0 whitespace-nowrap text-muted">{dt(e.ts)}</span>
                <span className="shrink-0 text-muted" title={e.type}>{EVENT[e.type] ?? e.type}</span>
                <span className="truncate text-muted" title={eventText(e.type, e.data)}>{eventText(e.type, e.data)}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {sha && <CodeModal sha={sha} onClose={() => setSha(null)} />}
    </div>
  );
}
