import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Badge, Card, CardHeader, Empty, ErrorBox, Skeleton } from "../components/ui";
import { api, type Run } from "../lib/api";
import { STATE, dt, n, pct } from "../lib/format";
import { useRunEvents } from "../lib/events";

export function StateBadge({ state }: { state: Run["state"] }) {
  const tone = state === "done" ? "pass" : state === "failed" ? "fail" : state === "cancelled" ? "neutral" : "accent";
  return <Badge tone={tone}>{state !== "done" && state !== "failed" && state !== "cancelled" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}{STATE[state]}</Badge>;
}

export function RunsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<Run[]>("/api/runs"), refetchInterval: 5000 });
  useRunEvents(null, () => runs.refetch());
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Прогоны</h1>
          <p className="text-xs text-muted">Сравнение срезов BEFORE (MASTER) и AFTER (BETA). Запуск не блокирует работу: результаты приходят по мере готовности.</p>
        </div>
        <Link to="/runs/new" className="inline-flex h-8 items-center rounded-md bg-accent px-3 text-[13px] font-medium text-white hover:opacity-90">
          Новый прогон
        </Link>
      </div>
      <Card>
        <CardHeader title="Мои прогоны" hint="Обновляются в реальном времени" />
        {runs.isLoading && <Skeleton rows={5} />}
        {runs.error && <ErrorBox error={runs.error} />}
        {runs.data && runs.data.length === 0 && <Empty title="Прогонов пока нет">Создайте первый прогон: выберите датасет и процедуры.</Empty>}
        {runs.data && runs.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-medium">Прогон</th>
                  <th className="px-2 py-2 font-medium">Статус</th>
                  <th className="px-2 py-2 text-right font-medium">Заявок</th>
                  <th className="px-2 py-2 text-right font-medium">С различиями</th>
                  <th className="px-2 py-2 font-medium">Порции</th>
                  <th className="px-2 py-2 font-medium">Создан</th>
                  <th className="px-4 py-2 text-right font-medium">Длительность</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((r) => {
                  const total = r.total_chunks ?? 0;
                  const sec = r.finished_at ? (new Date(r.finished_at).getTime() - new Date(r.created_at).getTime()) / 1000 : null;
                  return (
                    <tr key={r.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                      <td className="px-4 py-2">
                        <Link to="/runs/$runId" params={{ runId: r.id }} search={{}} className="font-medium text-accent hover:underline">
                          {r.name}
                        </Link>
                        {r.error && <div className="max-w-md truncate text-xs text-fail" title={r.error}>{r.error}</div>}
                      </td>
                      <td className="px-2 py-2"><StateBadge state={r.state} /></td>
                      <td className="num px-2 py-2 text-right">{n(r.apps)}</td>
                      <td className="num px-2 py-2 text-right">
                        {r.stats?.changed_apps != null ? <>{n(r.stats.changed_apps)} <span className="text-muted">({pct(r.stats.changed_apps, r.stats.apps ?? r.apps)})</span></> : "—"}
                      </td>
                      <td className="num px-2 py-2 text-muted">{total ? `${r.done_chunks}/${total}` : r.done_chunks ? `${r.done_chunks}/…` : "—"}{r.failed_chunks ? <span className="text-fail"> · ошибок {r.failed_chunks}</span> : null}</td>
                      <td className="px-2 py-2 text-muted">{dt(r.created_at)}</td>
                      <td className="num px-4 py-2 text-right text-muted">{sec != null ? `${sec.toFixed(1).replace(".", ",")} с` : "…"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
