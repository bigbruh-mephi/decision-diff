import { useQuery } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";
import { Badge, Card, CardHeader, Empty, ErrorBox, Filter, Mono, Skeleton, cx, linkCls } from "../../components/ui";
import { api, type CheckResult, type CheckRow } from "../../lib/api";
import { cap, manyName, ms, n, plural } from "../../lib/format";
import { useLabels, useScopeNav } from "../Run";

function verdict(r: CheckRow) {
  if (r.RESULT === "PASS") return { tone: "pass" as const, label: "PASS", row: "" };
  if (r.CONFIDENCE === "LOW") return { tone: "low" as const, label: "FAIL · низкая уверенность", row: "text-fail-low" };
  return { tone: "fail" as const, label: "FAIL", row: "" };
}

export function ChecksView({ runId }: { runId: string }) {
  const go = useScopeNav(runId);
  const l = useLabels(runId);
  const checks = useQuery({ queryKey: ["checks", runId], queryFn: () => api.get<CheckResult[]>(`/api/runs/${runId}/checks`) });
  const [filter, setFilter] = useState<"all" | "fail" | "pass">("all");
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (i: number) => setOpen((o) => { const x = new Set(o); if (x.has(i)) x.delete(i); else x.add(i); return x; });
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
        <ErrorBox key={c.check_id + c.check_sha} inline title={`Проверка "${c.label}" не выполнилась.`} error={c.error} debug={c.error_debug} />
      ))}
      <Card>
        <CardHeader
          title={`Отчет автопроверок · ${plural(rows.length, "строка", "строки", "строк")} из ${plural((checks.data ?? []).length, "проверки", "проверок", "проверок")}`}
          hint="Строки всех проверок в одной таблице. Бледные строки - мало срабатываний, уверенности в поломке нет"
          actions={
            <Filter value={filter} onChange={setFilter} items={[{ id: "all", label: "все" }, { id: "fail", label: "FAIL" }, { id: "pass", label: "PASS" }]} />
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
                  <th className="px-4 py-2 text-right font-medium">{cap(manyName(l))}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const v = verdict(r);
                  const arg = r.ARGUMENT || "";
                  const cut = arg.indexOf(": ");
                  const head = cut > 0 ? arg.slice(0, cut) : arg.length > 60 ? "" : arg;
                  const detail = cut > 0 ? arg.slice(cut + 2) : arg.length > 60 ? arg : "";
                  return (
                    <Fragment key={i}>
                    <tr className={cx("border-b border-line last:border-0", v.row)}>
                      <td className="px-4 py-1.5" title={`${ms(r.check.duration_ms)} · код ${r.check.check_sha.slice(0, 7)}`}>{r.LABEL}</td>
                      <td className="px-2 py-1.5 text-xs">{r.SOURCE === "AFTER" ? "AFTER (BETA)" : "BEFORE (MASTER)"}</td>
                      <td className="max-w-80 px-2 py-1.5">
                        <span className="flex items-center gap-2">
                          {head && <Mono className="truncate">{head}</Mono>}
                          {detail ? (
                            <button className={cx(linkCls, "shrink-0 text-xs")} aria-expanded={open.has(i)} onClick={() => toggle(i)}>изменение {open.has(i) ? "▾" : "▸"}</button>
                          ) : !head && "—"}
                        </span>
                      </td>
                      <td className="num px-2 py-1.5 text-right">{n(r.CNT)} / {n(r.TOTAL)}</td>
                      <td className="num px-2 py-1.5 text-right">{typeof r.METRIC === "number" ? Number(r.METRIC.toFixed(4)).toLocaleString("ru-RU") : r.METRIC}</td>
                      <td className="px-2 py-1.5"><Badge tone={v.tone}>{v.label}</Badge></td>
                      <td className="num px-4 py-1.5 text-right">
                        {r.APP_IDS.length ? (
                          <button className={linkCls} onClick={() => go(r.CNT > r.APP_IDS.length ? { check: { sha: r.check.check_sha, row: r.row, cnt: r.CNT } } : { apps: r.APP_IDS }, "apps")}>
                            {n(Math.max(r.CNT, r.APP_IDS.length))} шт.
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                    {open.has(i) && detail && (
                      <tr className="border-b border-line bg-surface-2">
                        <td colSpan={7} className="px-4 py-2"><Mono className="whitespace-pre-wrap break-all">{detail}</Mono></td>
                      </tr>
                    )}
                    </Fragment>
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
