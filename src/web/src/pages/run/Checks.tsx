import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Badge, Card, CardHeader, Empty, ErrorBox, Mono, Skeleton, cx } from "../../components/ui";
import { api, type CheckResult, type CheckRow } from "../../lib/api";
import { ms, n, plural } from "../../lib/format";
import { useScopeNav } from "../Run";

function verdict(r: CheckRow) {
  if (r.RESULT === "PASS") return { tone: "pass" as const, label: "PASS", row: "" };
  if (r.CONFIDENCE === "LOW") return { tone: "low" as const, label: "FAIL · низкая уверенность", row: "bg-fail-low-bg/60 text-fail-low" };
  return { tone: "fail" as const, label: "FAIL", row: "bg-fail-bg/50" };
}

export function ChecksView({ runId }: { runId: string }) {
  const go = useScopeNav(runId);
  const checks = useQuery({ queryKey: ["checks", runId], queryFn: () => api.get<CheckResult[]>(`/api/runs/${runId}/checks`) });
  const [filter, setFilter] = useState<"all" | "fail" | "pass">("all");
  const rows = useMemo(() => {
    const all = (checks.data ?? []).flatMap((c) => c.rows.map((r, row) => ({ ...r, check: c, row })));
    const rank = (r: CheckRow) => (r.RESULT === "FAIL" ? (r.CONFIDENCE === "HIGH" ? 0 : 1) : 2);
    return all.filter((r) => filter === "all" || (filter === "fail" ? r.RESULT === "FAIL" : r.RESULT === "PASS")).sort((a, b) => rank(a) - rank(b) || a.LABEL.localeCompare(b.LABEL));
  }, [checks.data, filter]);
  if (checks.isLoading) return <Skeleton rows={8} />;
  if (checks.error) return <ErrorBox error={checks.error} />;
  const errors = (checks.data ?? []).filter((c) => c.error);
  return (
    <div className="flex flex-col gap-4">
      {errors.map((c) => (
        <details key={c.check_id + c.check_sha} role="alert" className="rounded-md border border-fail/40 bg-fail-bg px-3 py-2 text-xs text-fail">
          <summary className="cursor-pointer">
            Проверка «{c.label}» не выполнилась: <Mono>{(c.error ?? "").split("\n")[0].slice(0, 200)}</Mono>
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{c.error}</pre>
        </details>
      ))}
      <Card>
        <CardHeader
          title={`Отчёт автопроверок · ${plural(rows.length, "строка", "строки", "строк")} из ${plural((checks.data ?? []).length, "проверки", "проверок", "проверок")}`}
          hint="Строки всех проверок в одной таблице. Бледные строки — мало срабатываний, уверенности в поломке нет"
          actions={
            <div className="flex gap-1">
              {(["all", "fail", "pass"] as const).map((f) => (
                <button key={f} onClick={() => setFilter(f)} className={cx("rounded px-2 py-1 text-xs", filter === f ? "bg-accent text-on-accent" : "text-muted hover:bg-surface-2")}>
                  {f === "all" ? "все" : f === "fail" ? "FAIL" : "PASS"}
                </button>
              ))}
            </div>
          }
        />
        {!rows.length ? (
          <Empty title="Нет строк" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead className="text-left text-xs text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-medium">Проверка</th>
                  <th className="px-2 py-2 font-medium">Срез</th>
                  <th className="px-2 py-2 font-medium">Аргумент</th>
                  <th className="px-2 py-2 text-right font-medium">Числа</th>
                  <th className="px-2 py-2 text-right font-medium">Метрика</th>
                  <th className="px-2 py-2 font-medium">Результат</th>
                  <th className="px-4 py-2 text-right font-medium">Заявки</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const v = verdict(r);
                  return (
                    <tr key={i} className={cx("border-b border-line last:border-0", v.row)}>
                      <td className="px-4 py-1.5">
                        {r.LABEL}
                        <div className="text-[11px] text-muted">{ms(r.check.duration_ms)} · код {r.check.check_sha.slice(0, 7)}</div>
                      </td>
                      <td className="px-2 py-1.5 text-xs">{r.SOURCE === "AFTER" ? "AFTER (BETA)" : "BEFORE (MASTER)"}</td>
                      <td className="max-w-80 truncate px-2 py-1.5" title={r.ARGUMENT}><Mono>{r.ARGUMENT || "—"}</Mono></td>
                      <td className="num px-2 py-1.5 text-right">{n(r.CNT)} / {n(r.TOTAL)}</td>
                      <td className="num px-2 py-1.5 text-right">{typeof r.METRIC === "number" ? Number(r.METRIC.toFixed(4)).toLocaleString("ru-RU") : r.METRIC}</td>
                      <td className="px-2 py-1.5"><Badge tone={v.tone}>{v.label}</Badge></td>
                      <td className="num px-4 py-1.5 text-right">
                        {r.APP_IDS.length ? (
                          <button className="text-accent hover:underline" onClick={() => go(r.CNT > r.APP_IDS.length ? { check: { sha: r.check.check_sha, row: r.row, cnt: r.CNT } } : { apps: r.APP_IDS }, "apps")}>
                            {n(Math.max(r.CNT, r.APP_IDS.length))} шт.
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
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
