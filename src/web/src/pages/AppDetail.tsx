import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Badge, Card, CardHeader, ChangeGlyph, ErrorBox, Mono, Skeleton, cx, inputCls, titleCls } from "../components/ui";
import { api, type AppDetail, type Summary } from "../lib/api";
import { n, plural } from "../lib/format";

export function AppDetailPage() {
  const { runId, appId } = useParams({ strict: false }) as { runId: string; appId: string };
  const { path, scope } = useSearch({ strict: false }) as { path?: string; scope?: string };
  const app = useQuery({ queryKey: ["app", runId, appId], queryFn: () => api.get<AppDetail>(`/api/runs/${runId}/apps/${encodeURIComponent(appId)}`) });
  const summary = useQuery({ queryKey: ["summary", runId, ""], queryFn: () => api.get<Summary>(`/api/runs/${runId}/summary`) });
  const [showSame, setShowSame] = useState(false);
  const [q, setQ] = useState(path ?? "");
  const groups = useMemo(() => {
    const rows = (app.data?.rows ?? []).filter((r) => (showSame || r.CHANGE_TYPE !== "SAME" || r.PATH === path) && (!q || r.PATH.includes(q) || r.ARRAY_KEY.includes(q)));
    const m = new Map<string, typeof rows>();
    for (const r of rows.sort((a, b) => Number(a.CHANGE_TYPE === "SAME") - Number(b.CHANGE_TYPE === "SAME"))) {
      const k = r.CLASS ? `${r.PREFIX}.${r.CLASS}` : r.PREFIX;
      m.set(k, [...(m.get(k) ?? []), r]);
    }
    return [...m.entries()];
  }, [app.data, showSame, q, path]);
  if (app.isLoading) return <Skeleton rows={10} />;
  if (app.error || !app.data) return <ErrorBox error={app.error} />;
  const a = app.data;
  const field = summary.data?.decision_field ?? "DECISION";
  const db = a.segments[`${field}_BEFORE`] as string | undefined;
  const da = a.segments[`${field}_AFTER`] as string | undefined;
  const changed = a.rows.filter((r) => r.CHANGE_TYPE !== "SAME").length;
  const same = a.rows.length - changed;
  const segs = Object.entries(a.segments).filter(([k]) => k !== "APP_ID");
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Link to="/runs/$runId" params={{ runId }} search={{ tab: "apps", scope }} className="text-xs text-accent hover:underline">
          ← к списку заявок
        </Link>
        <h1 className={cx(titleCls, "mt-1 flex flex-wrap items-center gap-3")}>
          <span className="font-mono">{a.app_id}</span>
          {db !== da ? <Badge tone="fail">{db} → {da}</Badge> : <Badge>{db}</Badge>}
          {a.cluster && (
            <Link to="/runs/$runId" params={{ runId }} search={{ tab: "overview", scope: JSON.stringify({ cluster: a.cluster }) }} className="text-xs font-normal text-accent hover:underline">
              кластер №{a.cluster}
            </Link>
          )}
        </h1>
        <p className="num text-xs text-muted">{plural(a.rows.length, "атрибут", "атрибута", "атрибутов")} · изменилось {n(changed)} · без изменений {n(same)}</p>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card>
          <CardHeader
            title="BEFORE / AFTER по атрибутам"
            hint="Горизонтальное представление только для просмотра; хранение витрины вертикальное"
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <input className={inputCls + " h-7 w-56 text-xs"} placeholder="фильтр по пути или ключу" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Фильтр атрибутов" />
                <label className="flex items-center gap-1 text-xs text-muted">
                  <input type="checkbox" checked={showSame} onChange={(e) => setShowSame(e.target.checked)} /> показать {n(same)} без изменений
                </label>
              </div>
            }
          />
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-surface text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="w-8 px-2 py-2" />
                  <th className="px-2 py-2 font-medium">Атрибут</th>
                  <th className="px-2 py-2 font-medium">Ключ массива</th>
                  <th className="px-2 py-2 font-medium text-before">BEFORE (MASTER)</th>
                  <th className="px-2 py-2 font-medium text-after">AFTER (BETA)</th>
                </tr>
              </thead>
              {groups.map(([g, rows]) => (
                <tbody key={g}>
                  <tr>
                    <th colSpan={5} className="bg-surface-2 px-3 py-1 text-left font-mono text-xs font-medium text-muted">{g}</th>
                  </tr>
                  {rows.map((r) => {
                    const hit = path && r.PATH === path;
                    return (
                      <tr key={r.path_id + r.ARRAY_KEY} className={cx("border-b border-line align-top", hit && "bg-accent-bg", r.CHANGE_TYPE === "SAME" && "text-muted")}>
                        <td className="px-2 py-1"><ChangeGlyph type={r.CHANGE_TYPE} /></td>
                        <td className="px-2 py-1">
                          <Mono>{r.SUFFIX ? `${r.SUFFIX}.` : ""}{r.KEY || "(элемент)"}</Mono>
                          {r.input && <Badge tone="warn">вход</Badge>}
                          {r.TYPE_CHANGED && <Badge tone="warn">тип</Badge>}
                        </td>
                        <td className="max-w-48 truncate px-2 py-1 font-mono text-xs text-muted" title={r.ARRAY_KEY}>{r.ARRAY_KEY || "—"}</td>
                        <td className="max-w-72 break-all px-2 py-1 font-mono">{r.VALUE_BEFORE ?? <span className="text-muted">нет атрибута</span>}</td>
                        <td className={cx("max-w-72 break-all px-2 py-1 font-mono", r.CHANGE_TYPE !== "SAME" && "font-semibold")}>{r.VALUE_AFTER ?? <span className="font-normal text-muted">нет атрибута</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              ))}
            </table>
          </div>
        </Card>
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader title={`Проверки с этой заявкой · ${a.checks.length}`} />
            <ul className="divide-y divide-line">
              {a.checks.map((c, i) => (
                <li key={i} className="px-4 py-2 text-xs">
                  <div className="flex items-center gap-2">
                    <Badge tone={c.row.RESULT === "PASS" ? "pass" : c.row.CONFIDENCE === "LOW" ? "low" : "fail"}>{c.row.RESULT}</Badge>
                    <span className="font-medium">{c.label}</span>
                  </div>
                  <Mono className="text-muted">{c.row.ARGUMENT}</Mono>
                </li>
              ))}
              {!a.checks.length && <li className="px-4 py-3 text-xs text-muted">Заявка не попала ни в одну строку проверок</li>}
            </ul>
          </Card>
          <Card>
            <CardHeader title="Разрезы заявки" />
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 p-4 text-xs">
              {segs.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="font-mono text-muted">{k}</dt>
                  <dd className="truncate font-mono">{String(v ?? "—")}</dd>
                </div>
              ))}
            </dl>
          </Card>
        </div>
      </div>
    </div>
  );
}
