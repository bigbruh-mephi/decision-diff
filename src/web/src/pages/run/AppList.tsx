import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";
import { Badge, Card, CardHeader, Empty, ErrorBox, Skeleton } from "../../components/ui";
import { api, type AppRow, type Scope } from "../../lib/api";
import { n } from "../../lib/format";
import type { Drill } from "../Run";

const PAGE = 500;

export function AppList({ runId, scope, drill }: { runId: string; scope: Scope; drill?: Drill }) {
  const [pages, setPages] = useState(1);
  useEffect(() => setPages(1), [scope, drill]);
  const q = useQuery({
    queryKey: ["apps", runId, scope, drill, pages],
    queryFn: () => api.post<{ total: number; apps: AppRow[] }>(`/api/runs/${runId}/apps`, { scope, cell: drill?.cell ?? {}, filters: drill?.filters ?? {}, transition: drill?.transition, offset: 0, limit: Math.min(1000, pages * PAGE) }),
    placeholderData: (p) => p,
  });
  const parent = useRef<HTMLDivElement>(null);
  const rows = q.data?.apps ?? [];
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => 34, overscan: 12 });
  const scopeStr = Object.keys(scope).length ? JSON.stringify(scope) : undefined;
  const path = drill?.cell?.PATH ?? drill?.path;
  return (
    <Card>
      <CardHeader
        title={q.data ? `Заявки: ${n(q.data.total)}` : "Заявки"}
        hint={drill?.title ? `Отбор: ${drill.title}` : "Все заявки текущего среза; клик открывает заявку с BEFORE/AFTER по каждому атрибуту"}
      />
      {q.isLoading && <Skeleton rows={8} />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && !rows.length && <Empty title="Нет заявок в этом отборе" />}
      {rows.length > 0 && (
        <>
          <div className="overflow-x-auto">
          <div className="min-w-[640px]">
          <div className="grid grid-cols-[1fr_7rem_7rem_8rem_6rem] border-b border-line px-4 py-2 text-xs font-medium text-muted">
            <span>Заявка</span>
            <span>BEFORE</span>
            <span>AFTER</span>
            <span>Изменения</span>
            <span className="text-right">Кластер</span>
          </div>
          <div ref={parent} className="max-h-[65vh] overflow-y-auto">
            <div style={{ height: v.getTotalSize(), position: "relative" }}>
              {v.getVirtualItems().map((it) => {
                const a = rows[it.index];
                return (
                  <Link
                    key={a.app_id}
                    to="/runs/$runId/app/$appId"
                    params={{ runId, appId: a.app_id }}
                    search={{ scope: scopeStr, path }}
                    className="absolute left-0 grid w-full grid-cols-[1fr_7rem_7rem_8rem_6rem] items-center border-b border-line px-4 text-[13px] hover:bg-surface-2"
                    style={{ top: it.start, height: it.size }}
                  >
                    <span className="font-mono text-[12.5px] text-accent">{a.app_id}</span>
                    <span className="text-xs">{a.decision_before ?? "—"}</span>
                    <span className={a.decision_changed ? "text-xs font-semibold text-fail" : "text-xs"}>{a.decision_after ?? "—"}</span>
                    <span>{a.decision_changed ? <Badge tone="fail">смена решения</Badge> : a.changed ? <Badge tone="accent">есть различия</Badge> : <Badge>без изменений</Badge>}</span>
                    <span className="num text-right text-xs text-muted">{a.cluster ? `№${a.cluster}` : "—"}</span>
                  </Link>
                );
              })}
            </div>
          </div>
          </div>
          </div>
          {q.data && q.data.total > rows.length && (
            <button className="w-full border-t border-line py-2 text-xs text-accent hover:bg-surface-2" onClick={() => setPages(pages + 1)} disabled={rows.length >= 1000}>
              {rows.length >= 1000 ? `показаны первые ${n(rows.length)} из ${n(q.data.total)} — сузьте срез` : `показать ещё (${n(rows.length)} из ${n(q.data.total)})`}
            </button>
          )}
        </>
      )}
    </Card>
  );
}
