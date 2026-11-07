import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Bar, Card, CardHeader, ChangeGlyph, Empty, ErrorBox, Mono, Skeleton, Stat, cx } from "../../components/ui";
import { api, scopeQuery, type Attribute, type CheckResult, type Cluster, type Scope, type SegmentField, type Summary } from "../../lib/api";
import { n, pct, plural, rate, tail } from "../../lib/format";
import { useScopeNav } from "../Run";
import { AiOpinion } from "./AiOpinion";

const ISSUE: Record<string, string> = {
  key_collision: "совпали ключи после приведения стиля",
  duplicate_key: "повторный ключ элемента массива",
  missing_key_field: "нет поля ключа в элементе массива",
  non_object_in_keyed_array: "не объект в массиве с ключом",
  container_in_value_array: "вложенная структура в массиве значений",
  app_missing_in_slice: "заявки нет в одном из срезов",
  float_beyond_int64: "число вне диапазона int64",
};

function SwapMatrix({ s, onCell }: { s: Summary; onCell: (b: string, a: string) => void }) {
  const before = [...new Set(s.swap.map((x) => x.before))].sort();
  const after = [...new Set(s.swap.map((x) => x.after))].sort();
  const get = (b: string, a: string) => s.swap.find((x) => x.before === b && x.after === a)?.apps ?? 0;
  const max = Math.max(1, ...s.swap.filter((x) => x.before !== x.after).map((x) => x.apps));
  return (
    <table className="w-full text-[13px]">
      <thead>
        <tr className="text-xs text-muted">
          <th className="px-3 py-2 text-left font-medium">BEFORE ↓ / AFTER →</th>
          {after.map((a) => (
            <th key={a} className="px-3 py-2 text-right font-medium">{a}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {before.map((b) => (
          <tr key={b} className="border-t border-line">
            <th className="px-3 py-2 text-left text-xs font-medium text-muted">{b}</th>
            {after.map((a) => {
              const v = get(b, a);
              const flip = a !== b;
              return (
                <td key={a} className="p-1 text-right">
                  <button
                    disabled={!v}
                    onClick={() => onCell(b, a)}
                    className={cx("num w-full rounded px-2 py-1.5 text-right", flip ? "font-semibold hover:ring-1 hover:ring-accent" : "text-muted hover:bg-surface-2")}
                    style={flip && v ? { background: `color-mix(in oklch, var(--accent) ${8 + (v / max) * 30}%, transparent)` } : undefined}
                    title={flip ? `${b} → ${a}: ${plural(v, "заявка", "заявки", "заявок")}` : "решение не изменилось"}
                  >
                    {n(v)}
                    <span className="block text-[11px] font-normal text-muted">{pct(v, s.apps)}</span>
                  </button>
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ClusterTable({ clusters, total, onOpen, onTransition }: { clusters: Cluster[]; total: number; onOpen: (c: number) => void; onTransition: (c: Cluster) => void }) {
  if (!clusters.length) return <Empty title="Различий нет">Ни одна заявка не изменилась.</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-4 py-2 font-medium">Кластер изменений</th>
            <th className="px-2 py-2 text-right font-medium">Заявок</th>
            <th className="px-2 py-2 text-right font-medium">Смена решения</th>
            <th className="px-2 py-2 font-medium">Источник</th>
            <th className="px-4 py-2 font-medium">Что изменилось вместе (главные переходы)</th>
          </tr>
        </thead>
        <tbody>
          {clusters.map((c) => (
            <tr key={c.cid ?? "other"} className="border-b border-line align-top last:border-0 hover:bg-surface-2">
              <td className="px-4 py-2">
                {c.cid ? (
                  <button onClick={() => onOpen(c.cid!)} className="font-medium text-accent hover:underline">
                    №{c.cid} · {tail(c.transitions?.[0]?.path ?? c.paths?.[0] ?? "")}{(c.paths?.length ?? 0) > 1 ? ` и ещё ${c.paths!.length - 1}` : ""}
                  </button>
                ) : (
                  <span className="text-muted">ещё {plural(c.clusters ?? 0, "кластер", "кластера", "кластеров")}</span>
                )}
                <div className="mt-1 w-28"><Bar total={total} parts={[{ value: c.apps, className: "bg-accent", label: "заявок" }]} /></div>
              </td>
              <td className="num px-2 py-2 text-right">{n(c.apps)}<div className="text-[11px] text-muted">{pct(c.apps, total)}</div></td>
              <td className="num px-2 py-2 text-right">
                {c.decision_changed ? n(c.decision_changed) : <Badge>не меняет решение</Badge>}
                {c.swap?.map((s) => (
                  <div key={s.before + s.after} className="text-[11px] text-muted">{s.before}→{s.after}: {n(s.apps)}</div>
                ))}
              </td>
              <td className="px-2 py-2">
                {c.origin === "input" ? (
                  <Badge tone="warn" title={`Изменились входные данные: ${c.input_paths?.join(", ")}`}>входные данные</Badge>
                ) : (
                  <Badge tone="accent" title="Изменения только в логике стратегии">логика стратегии</Badge>
                )}
              </td>
              <td className="px-4 py-2">
                <ul className="space-y-0.5">
                  {c.transitions?.map((t) => (
                    <li key={t.path + t.before + t.after} className="flex flex-wrap items-baseline gap-x-1.5">
                      <span title={t.path}><Mono className="text-muted">{tail(t.path!)}</Mono></span>
                      <span className="font-mono text-[12px]"><span className="text-before">{t.before}</span> → <span className="text-after">{t.after}</span></span>
                      <span className="num text-[11px] text-muted">{n(t.apps)}</span>
                    </li>
                  ))}
                </ul>
                {c.cid && <button className="mt-1 text-[11px] text-accent hover:underline" onClick={() => onTransition(c)}>заявки кластера</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Attributes({ rows, onPath }: { rows: Attribute[]; onPath: (a: Attribute) => void }) {
  const [all, setAll] = useState(false);
  const max = Math.max(1, ...rows.map((r) => r.apps));
  const shown = all ? rows : rows.slice(0, 12);
  return (
    <div>
      <ul className="divide-y divide-line">
        {shown.map((r) => (
          <li key={r.path_id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-1.5">
            <button onClick={() => onPath(r)} className="min-w-0 truncate text-left font-mono text-[12px] hover:text-accent" title={r.PATH}>
              {r.PATH}
              {r.input && <Badge tone="warn">вход</Badge>}
            </button>
            <span className="num text-right text-xs">{n(r.apps)} <span className="text-muted">· смена решения {n(r.decision_changed)}</span></span>
            <div className="col-span-2 flex items-center gap-2">
              <div className="flex-1">
                <Bar
                  total={max}
                  parts={[
                    { value: r.by_type.VALUE_CHANGED ?? 0, className: "bg-ct-changed", label: "значение" },
                    { value: r.by_type.ATTRIBUTE_NEW ?? 0, className: "bg-ct-new", label: "появился" },
                    { value: r.by_type.ATTRIBUTE_LOST ?? 0, className: "bg-ct-lost", label: "исчез" },
                  ]}
                />
              </div>
              <span className="flex gap-2 text-[11px]">
                {Object.entries(r.by_type).map(([k, v]) => (
                  <span key={k} className="num"><ChangeGlyph type={k} /> {n(v)}</span>
                ))}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {rows.length > 12 && (
        <button className="w-full border-t border-line py-2 text-xs text-accent hover:bg-surface-2" onClick={() => setAll(!all)}>
          {all ? "свернуть" : `показать все ${rows.length}`}
        </button>
      )}
    </div>
  );
}

function Segments({ fields, onValue }: { fields: SegmentField[]; onValue: (f: string, v: string) => void }) {
  const show = fields.filter((f) => !/_(BEFORE|AFTER|CHANGED)$/.test(f.field) && f.values.length > 1 && f.values.length <= 31);
  if (!show.length) return <Empty title="Нет разрезов для сравнения" />;
  return (
    <div className="grid gap-4 p-4 md:grid-cols-2 xl:grid-cols-3">
      {show.map((f) => {
        const max = Math.max(...f.values.map((v) => (v.apps ? v.decision_changed / v.apps : 0)), 0.0001);
        return (
          <div key={f.field}>
            <h3 className="mb-1 text-xs font-semibold text-muted">{f.field}</h3>
            <ul className="space-y-1">
              {f.values.slice(0, 10).map((v) => {
                const r = v.apps ? v.decision_changed / v.apps : 0;
                const small = v.apps < 100;
                return (
                  <li key={v.value}>
                    <button onClick={() => onValue(f.field, v.value)} className={cx("grid w-full grid-cols-[7rem_1fr_auto] items-center gap-2 text-left text-xs hover:text-accent", small && "opacity-60")} title={small ? "мало заявок: вывод предварительный" : undefined}>
                      <span className="truncate font-mono text-[11.5px]">{v.value}</span>
                      <span className="h-1.5 rounded-full bg-surface-2">
                        <span className="block h-full rounded-full bg-accent" style={{ width: `${(r / max) * 100}%` }} />
                      </span>
                      <span className="num w-28 text-right text-muted">{pct(v.decision_changed, v.apps)} из {n(v.apps)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

export function Overview({ runId, scope }: { runId: string; scope: Scope }) {
  const q = scopeQuery(scope);
  const go = useScopeNav(runId);
  const summary = useQuery({ queryKey: ["summary", runId, q], queryFn: () => api.get<Summary>(`/api/runs/${runId}/summary${q}`) });
  const attrs = useQuery({ queryKey: ["attributes", runId, q], queryFn: () => api.get<Attribute[]>(`/api/runs/${runId}/attributes${q}`) });
  const segs = useQuery({ queryKey: ["segments", runId, q], queryFn: () => api.get<SegmentField[]>(`/api/runs/${runId}/segments${q}`) });
  const checks = useQuery({ queryKey: ["checks", runId], queryFn: () => api.get<CheckResult[]>(`/api/runs/${runId}/checks`) });
  if (summary.isLoading) return <Skeleton rows={10} />;
  if (summary.error || !summary.data) return <ErrorBox error={summary.error} />;
  const s = summary.data;
  const rows = (checks.data ?? []).flatMap((c) => c.rows);
  const failHigh = rows.filter((r) => r.RESULT === "FAIL" && r.CONFIDENCE === "HIGH").length;
  const failLow = rows.filter((r) => r.RESULT === "FAIL" && r.CONFIDENCE === "LOW").length;
  const errors = (checks.data ?? []).filter((c) => c.error).length;
  const seg = (f: string, v: string) => ({ ...scope, seg: { ...scope.seg, [f]: [v] } });
  const top = s.clusters.filter((c) => c.cid && c.decision_changed).slice(0, 3);
  return (
    <div className="flex flex-col gap-4">
      <section aria-label="Вердикт" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Заявок в срезе" value={n(s.apps)} sub={`без изменений ${n(s.unchanged)}`} />
        <Stat label="С различиями" value={n(s.changed)} sub={pct(s.changed, s.apps)} tone="accent" onClick={() => go(scope, "apps", { filters: { DIFF_FLAG: ["1"] }, title: "заявки с различиями" })} />
        <Stat label="Сменилось решение" value={n(s.decision_changed)} sub={pct(s.decision_changed, s.apps)} tone="fail" onClick={() => go({ ...scope, seg: { ...scope.seg, [`${s.decision_field}_CHANGED`]: ["1"] } }, "apps")} />
        <Stat label="Различия есть, решение то же" value={n(s.changed_same_decision)} sub={`${pct(s.changed_same_decision, s.changed)} изменений прошли мимо`} />
        <Stat label="Approval rate" value={s.approval_before == null ? "—" : `${rate(s.approval_before)} → ${rate(s.approval_after)}`} sub={s.approval_before != null && s.approval_after != null ? `${((s.approval_after - s.approval_before) * 100).toFixed(1).replace(".", ",")} п.п.` : undefined} tone={s.approval_after != null && s.approval_before != null && s.approval_after < s.approval_before ? "fail" : undefined} />
        <Stat label="Из-за входных данных" value={n(s.input_driven)} sub="не логика стратегии: возможна ложная тревога" tone={s.input_driven ? "warn" : undefined} onClick={s.input_driven ? () => { const c = s.clusters.find((x) => x.origin === "input"); if (c?.cid) go({ ...scope, cluster: c.cid }); } : undefined} />
      </section>
      <div className="rounded-lg border border-line bg-surface px-4 py-3 text-[13px] leading-relaxed">
        <span className="font-semibold">Вывод. </span>
        Изменение затрагивает {n(s.changed)} из {plural(s.apps, "заявки", "заявок", "заявок")} ({pct(s.changed, s.apps)}); у {n(s.decision_changed)} сменилось решение
        {s.swap.filter((x) => x.before !== x.after).map((x) => ` (${x.before}→${x.after}: ${n(x.apps)})`).join(",")}.
        {top.length > 0 && <> Главные причины: {top.map((c, i) => <span key={c.cid}>{i ? "; " : ""}кластер №{c.cid} — {c.transitions?.[0] ? <Mono>{tail(c.transitions[0].path!)}</Mono> : null} {c.transitions?.[0] ? `${c.transitions[0].before}→${c.transitions[0].after}` : ""} ({plural(c.apps, "заявка", "заявки", "заявок")}{c.origin === "input" ? ", входные данные" : ""})</span>)}.</>}
        {s.changed_same_decision > 0 && <> У {plural(s.changed_same_decision, "заявки", "заявок", "заявок")} различия не повлияли на решение.</>}
        {" "}Строки проверок: <Badge tone={failHigh ? "fail" : "pass"}>FAIL {failHigh}</Badge> <Badge tone="low">FAIL с низкой уверенностью {failLow}</Badge> <Badge tone="pass">PASS {rows.length - failHigh - failLow}</Badge>
        {errors > 0 && <Badge tone="fail">ошибок исполнения: {errors}</Badge>}
      </div>
      {!!s.issues?.length && (
        <Card>
          <CardHeader title="Проблемы разбора ответов" hint="Данные не слиты молча: такие места стоит проверить в профиле нормализации. Клик по примерам открывает заявки" />
          <ul className="divide-y divide-line">
            {s.issues.map((i) => (
              <li key={i.kind} className="flex flex-wrap items-center justify-between gap-2 px-4 py-1.5 text-[13px]">
                <span>
                  <Badge tone="warn">{ISSUE[i.kind] ?? i.kind}</Badge> <span className="num text-muted">{plural(i.apps, "заявка", "заявки", "заявок")}, мест {n(i.rows)}</span>
                </span>
                <button className="text-xs text-accent hover:underline" onClick={() => go({ ...scope, apps: i.examples }, "apps")}>
                  примеры: {i.examples.join(", ")}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <div className="grid gap-4 xl:grid-cols-[1fr_2fr]">
        <Card>
          <CardHeader title="Куда сместились решения" hint="Матрица переходов BEFORE × AFTER; клик по ячейке открывает заявки" />
          <SwapMatrix s={s} onCell={(b, a) => go({ ...scope, seg: { ...scope.seg, [`${s.decision_field}_BEFORE`]: [b], [`${s.decision_field}_AFTER`]: [a] } }, "apps")} />
        </Card>
        <Card>
          <CardHeader title="Кластеры изменений" hint="Заявки с одинаковым набором изменившихся атрибутов: из-за чего сменилось решение" />
          <ClusterTable clusters={s.clusters} total={s.apps} onOpen={(c) => go({ ...scope, cluster: c })} onTransition={(c) => go({ ...scope, cluster: c.cid! }, "apps", { path: c.transitions?.[0]?.path })} />
        </Card>
      </div>
      <AiOpinion runId={runId} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Какие атрибуты меняются чаще всего" hint="Точное число заявок; ~ значение, + появился, − исчез" />
          {attrs.data ? <Attributes rows={attrs.data} onPath={(a) => go(scope, "apps", { cell: { PATH: a.PATH }, filters: { DIFF_FLAG: ["1"] }, title: a.PATH })} /> : <Skeleton />}
        </Card>
        <Card>
          <CardHeader title="Структурные изменения" hint="Атрибуты появились, исчезли или сменили тип: перекатанные модели, новые правила" />
          {s.structure.length === 0 ? (
            <Empty title="Состав атрибутов не изменился" />
          ) : (
            <ul className="max-h-96 divide-y divide-line overflow-y-auto">
              {s.structure.map((r) => (
                <li key={r.path_id} className="flex items-center justify-between gap-2 px-4 py-1.5">
                  <button className="min-w-0 truncate text-left font-mono text-[12px] hover:text-accent" onClick={() => go(scope, "apps", { cell: { PATH: r.PATH }, filters: { DIFF_FLAG: ["1"] }, title: r.PATH })}>
                    {r.PATH}
                  </button>
                  <span className="num flex shrink-0 gap-2 text-xs">
                    {r.apps_new > 0 && <span className="text-ct-new">+{n(r.apps_new)}</span>}
                    {r.apps_lost > 0 && <span className="text-ct-lost">−{n(r.apps_lost)}</span>}
                    {r.apps_type_changed > 0 && <Badge tone="warn">тип {n(r.apps_type_changed)}</Badge>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Card>
        <CardHeader title="Кого затронуло" hint="Доля смены решения по значениям разрезов; бледные — меньше 100 заявок. Клик сужает отчёт до сегмента" />
        {segs.data ? <Segments fields={segs.data} onValue={(f, v) => go(seg(f, v))} /> : <Skeleton />}
      </Card>
    </div>
  );
}
