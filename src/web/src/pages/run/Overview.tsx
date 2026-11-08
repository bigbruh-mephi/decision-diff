import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Card, CardHeader, Empty, ErrorBox, Skeleton, Stat, cx, linkCls } from "../../components/ui";
import { api, scopeQuery, type Cause, type CheckResult, type Cluster, type Scope, type SegmentField, type Structure, type Summary } from "../../lib/api";
import { LABELS, n, objects, ofObjects, pct, plural, rate, tail, type Labels } from "../../lib/format";
import { useScopeNav } from "../Run";
import { AiOpinion } from "./AiOpinion";

const ISSUE: Record<string, string> = {
  key_collision: "совпали ключи после приведения стиля",
  duplicate_key: "повторный ключ элемента массива",
  missing_key_field: "нет поля ключа в элементе массива",
  non_object_in_keyed_array: "не объект в массиве с ключом",
  container_in_value_array: "вложенная структура в массиве значений",
  app_missing_in_slice: "нет в одном из срезов",
  float_beyond_int64: "число вне диапазона int64",
};

// A segment stands out when its share of decision flips is this far from the population's; smaller segments are too noisy to judge
const STANDOUT = 0.05;
const MIN_SEGMENT = 100;

const pp = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(1).replace(".", ",")} п.п.`;

function headline(s: Summary, delta: number | null, l: Labels) {
  if (!s.changed) return "Различий нет: обе версии стратегии дали одинаковый результат.";
  const whole = ofObjects(s.apps, l);
  const outcome = l.outcome[0].toUpperCase() + l.outcome.slice(1);
  if (!s.decision_changed) return `Различия есть у ${n(s.changed)} из ${whole}, но ${l.outcome} не сменилось.`;
  const approval = !delta ? "" : `, ${l.positive_rate} ${delta < 0 ? "упала" : "выросла"} на ${Math.abs(delta * 100).toFixed(1).replace(".", ",")} п.п.`;
  return `${outcome} сменилось у ${n(s.decision_changed)} из ${whole} (${pct(s.decision_changed, s.apps)})${approval || "."}`;
}

function SwapMatrix({ s, l, onCell }: { s: Summary; l: Labels; onCell: (b: string, a: string) => void }) {
  const before = [...new Set(s.swap.map((x) => x.before))].sort();
  const after = [...new Set(s.swap.map((x) => x.after))].sort();
  const get = (b: string, a: string) => s.swap.find((x) => x.before === b && x.after === a)?.apps ?? 0;
  const max = Math.max(1, ...s.swap.filter((x) => x.before !== x.after).map((x) => x.apps));
  return (
    <table className="w-full text-[13px]">
      <thead>
        <tr className="text-xs text-muted">
          <th className="px-5 py-2 text-left font-medium">BEFORE ↓ / AFTER →</th>
          {after.map((a) => (
            <th key={a} className="px-3 py-2 text-right font-medium">{a}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {before.map((b) => (
          <tr key={b}>
            <th className="px-5 py-1 text-left text-xs font-medium text-muted">{b}</th>
            {after.map((a) => {
              const v = get(b, a);
              const flip = a !== b;
              return (
                <td key={a} className="p-1 text-right">
                  <button
                    disabled={!v}
                    onClick={() => onCell(b, a)}
                    className={cx("num w-full rounded px-2 py-1.5 text-right", flip ? "font-semibold hover:ring-2 hover:ring-fail" : "text-muted hover:ring-2 hover:ring-line-strong")}
                    style={flip && v ? { background: `color-mix(in oklch, var(--fail) ${8 + (v / max) * 22}%, transparent)` } : undefined}
                    title={flip ? `${b} → ${a}: ${objects(v, l)}` : `${l.outcome} не изменилось`}
                  >
                    {n(v)}
                    <span className="block text-xs font-normal text-muted">{pct(v, s.apps)}</span>
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

const META = new Set(["input", "several", "unreproduced"]);
const EMULATION = "Причины посчитаны эмуляцией стратегии по данным прогона; она воспроизводит все фактические решения прогона, поэтому причинам можно доверять.";

const causeTitle = (x: Cause) =>
  x.key === "several" ? `Ни одно изменение стратегии по отдельности не возвращает решение BEFORE: сработали несколько сразу. ${EMULATION}`
    : x.key === "input" ? `Стратегия тут ни при чем: решение вернется, если вернуть только входные данные. ${EMULATION}`
      : `${x.hint ?? `Решение возвращается к BEFORE, если вернуть только: ${x.label}`}. ${EMULATION}`;

const clusterName = (c: Cluster) => `№${c.cid} · ${tail(c.transitions?.[0]?.path ?? c.paths?.[0] ?? "")}${(c.paths?.length ?? 0) > 1 ? ` и еще ${c.paths!.length - 1}` : ""}`;

function mainCause(clusters: Cluster[]) {
  const total = new Map<string, { label: string; apps: number }>();
  for (const c of clusters)
    for (const x of c.causes ?? [])
      if (!META.has(x.key)) total.set(x.label, { label: x.label, apps: (total.get(x.label)?.apps ?? 0) + x.apps });
  return [...total.values()].sort((a, b) => b.apps - a.apps)[0];
}

function ShowAll({ all, total, onToggle }: { all: boolean; total: number; onToggle: () => void }) {
  return (
    <button className="w-full rounded-b-lg border-t border-line py-2 text-xs text-muted transition-colors hover:bg-surface-2 hover:text-fg" onClick={onToggle}>
      {all ? "Свернуть" : `Показать все ${total}`}
    </button>
  );
}

function ClusterRow({ c, l, onOpen, onCause }: { c: Cluster; l: Labels; onOpen: (c: number) => void; onCause: (c: Cluster, x: Cause) => void }) {
  if (!c.cid)
    return (
      <li className="flex justify-between gap-4 px-5 py-2 text-xs text-muted">
        <span>еще {plural(c.clusters ?? 0, "кластер", "кластера", "кластеров")}</span>
        <span className="num">{objects(c.apps, l)}</span>
      </li>
    );
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 px-5 py-2.5">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[13px]">
        {c.causes?.length ? (
          c.causes.map((x) => (
            <button key={x.key} className={cx("text-left", linkCls, META.has(x.key) && "text-muted")} title={causeTitle(x)} onClick={() => onCause(c, x)}>
              {x.label} <span className="num text-xs text-muted">{pct(x.apps, c.decision_changed, 0)}</span>
            </button>
          ))
        ) : (
          <span className="text-muted" title={clusterName(c)}>{clusterName(c)}</span>
        )}
      </div>
      <div className="num text-right text-[13px]">
        <button className={linkCls} title={`Открыть кластер ${clusterName(c)}`} onClick={() => onOpen(c.cid!)}>{n(c.decision_changed)} из {n(c.apps)}</button>
      </div>
    </li>
  );
}

function Causes({ s, l, onOpen, onCause }: { s: Summary; l: Labels; onOpen: (c: number) => void; onCause: (c: Cluster, x: Cause) => void }) {
  const [all, setAll] = useState(false);
  const rows = s.clusters.filter((c) => !c.cid || c.decision_changed);
  const main = mainCause(rows);
  return (
    <Card>
      <CardHeader
        title={main ? `Главная причина: ${main.label} - ${objects(main.apps, l)}` : `Из-за чего сменилось ${l.outcome}`}
        hint={s.causes_fidelity != null && s.causes_fidelity < 1
          ? `Причины скрыты: эмуляция стратегии воспроизводит только ${rate(s.causes_fidelity)} фактических решений прогона, ее выводам нельзя доверять`
          : `Кластер - ${l.object[2]} с одинаковым набором изменений. Причина - одно изменение стратегии, откат которого возвращает ${l.outcome} BEFORE. Справа: сколько раз сменилось ${l.outcome} из всех ${l.object[2]} кластера`}
      />
      {!rows.length ? (
        <Empty title="Решения не менялись" />
      ) : (
        <ul className="divide-y divide-line">
          {(all ? rows : rows.slice(0, 3)).map((c) => (
            <ClusterRow key={c.cid ?? "other"} c={c} l={l} onOpen={onOpen} onCause={onCause} />
          ))}
        </ul>
      )}
      {rows.length > 3 && <ShowAll all={all} total={rows.length} onToggle={() => setAll(!all)} />}
    </Card>
  );
}

function Segments({ fields, overall, l, onValue }: { fields: SegmentField[]; overall: number; l: Labels; onValue: (f: string, v: string) => void }) {
  const shown = fields
    .filter((f) => !/_(BEFORE|AFTER|CHANGED)$/.test(f.field) && f.values.length > 1 && f.values.length <= 31)
    .map((f) => {
      const values = f.values.map((v) => ({ ...v, r: v.apps ? v.decision_changed / v.apps : 0, off: false })).sort((a, b) => Number(a.apps < MIN_SEGMENT) - Number(b.apps < MIN_SEGMENT) || b.r - a.r);
      for (const v of values) v.off = v.apps >= MIN_SEGMENT && Math.abs(v.r - overall) >= STANDOUT;
      const rates = values.filter((v) => v.apps >= MIN_SEGMENT).map((v) => v.r);
      return { field: f.field, values, min: rates.length ? Math.min(...rates) : null, max: rates.length ? Math.max(...rates) : null, off: values.some((v) => v.off) };
    })
    .sort((a, b) => Number(b.off) - Number(a.off));
  const off = shown.flatMap((f) => f.values.filter((v) => v.off).map((v) => ({ field: f.field, ...v }))).sort((a, b) => Math.abs(b.r - overall) - Math.abs(a.r - overall))[0];
  const rates = shown.flatMap((f) => (f.min == null || f.max == null ? [] : [f.min, f.max]));
  const title = off ? `Сильнее всего затронуто: ${off.field} = ${off.value}, ${rate(off.r)}`
    : rates.length ? `Сегменты затронуты одинаково: ${rate(Math.min(...rates))}–${rate(Math.max(...rates))}`
      : "Кого затронуло";
  const scale = Math.max(overall, ...shown.flatMap((f) => f.values.map((v) => v.r)), 0.0001);
  return (
    <Card>
      <CardHeader title={title} hint={`Доля смены (${l.outcome}) по значениям разрезов. Вертикальная черта - среднее по срезу (${rate(overall)}); выделены отклонения от ${Math.round(STANDOUT * 100)} п.п. Сегменты меньше ${MIN_SEGMENT} ${l.object[2]} бледные: вывод по ним предварительный. Клик сужает отчет до сегмента`} />
      {!shown.length ? (
        <Empty title="Нет разрезов для сравнения" />
      ) : (
        <div className="divide-y divide-line">
          {shown.map((f) => (
            <details key={f.field} open={f.off} className="px-5 py-2">
              <summary className="-mx-2 flex select-none items-baseline justify-between gap-3 rounded px-2 py-0.5 text-xs transition-colors hover:bg-surface-2">
                <span className="truncate font-mono"><span aria-hidden className="mr-1 text-muted">▸</span>{f.field}</span>
                {f.min != null && f.max != null && <span className="num shrink-0 text-muted">{f.min === f.max ? rate(f.min) : `${rate(f.min)}–${rate(f.max)}`}</span>}
              </summary>
              <ul className="mt-1.5 space-y-1">
                {f.values.slice(0, 10).map((v) => (
                  <li key={v.value}>
                    <button onClick={() => onValue(f.field, v.value)} className={cx("grid w-full grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-2 rounded px-1 text-left text-xs transition-colors hover:bg-accent-bg hover:text-accent", v.apps < MIN_SEGMENT && "opacity-60")}>
                      <span className="truncate font-mono" title={v.value}>{v.value}</span>
                      <span className="relative h-1.5 rounded-full bg-surface-2">
                        <span className={cx("block h-full rounded-full", v.off ? "bg-fail" : "bg-muted/50")} style={{ width: `${(v.r / scale) * 100}%` }} />
                        <span aria-hidden className="absolute -inset-y-0.5 w-px bg-fg" style={{ left: `${(overall / scale) * 100}%` }} />
                      </span>
                      <span className="num text-right text-muted">{pct(v.decision_changed, v.apps)} из {n(v.apps)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
    </Card>
  );
}

function SameDecision({ s, l, onAll, onCluster }: { s: Summary; l: Labels; onAll: () => void; onCluster: (c: Cluster) => void }) {
  const [all, setAll] = useState(false);
  const rows = s.clusters.filter((c) => c.cid && c.apps > c.decision_changed).sort((a, b) => b.apps - b.decision_changed - (a.apps - a.decision_changed));
  return (
    <Card>
      <CardHeader
        title={<>Различия есть, а {l.outcome} то же: <button className={cx("num", linkCls)} onClick={onAll}>{objects(s.changed_same_decision, l)}</button></>}
        hint={`Изменение прошло мимо (${l.outcome}). Это не значит, что оно безвредно: у таких ${l.object[2]} могли смениться другие атрибуты`}
      />
      <ul className="divide-y divide-line">
        {(all ? rows : rows.slice(0, 3)).map((c) => (
          <li key={c.cid} className="flex items-baseline justify-between gap-4 px-5 py-2">
            <span className="min-w-0 truncate font-mono text-xs" title={c.paths?.join(", ")}>{clusterName(c)}</span>
            <button className={cx("num shrink-0 text-[13px]", linkCls)} onClick={() => onCluster(c)}>{n(c.apps - c.decision_changed)}</button>
          </li>
        ))}
      </ul>
      {rows.length > 3 && <ShowAll all={all} total={rows.length} onToggle={() => setAll(!all)} />}
    </Card>
  );
}

function StructureCard({ rows, l, onOpen }: { rows: Structure[]; l: Labels; onOpen: (path: string, type: "ATTRIBUTE_NEW" | "ATTRIBUTE_LOST") => void }) {
  const [all, setAll] = useState(false);
  if (!rows.length) return null;
  return (
    <Card>
      <CardHeader title="Состав атрибутов изменился" hint={`Атрибуты, которые появились или исчезли: например, новые предикторы модели - сигнал «модель перекатали». Считается по всему прогону; число открывает ${l.object[2]}`} />
      <ul className="divide-y divide-line">
        {(all ? rows : rows.slice(0, 6)).map((x) => (
          <li key={x.path_id} className="flex items-baseline justify-between gap-4 px-5 py-2 text-[13px]">
            <span className="min-w-0 truncate font-mono text-xs" title={x.PATH}>{tail(x.PATH)}</span>
            <span className="flex shrink-0 gap-3">
              {x.apps_new > 0 && <button className={cx("num text-ct-new", linkCls)} title="атрибут появился" onClick={() => onOpen(x.PATH, "ATTRIBUTE_NEW")}>+{n(x.apps_new)}</button>}
              {x.apps_lost > 0 && <button className={cx("num text-ct-lost", linkCls)} title="атрибут исчез" onClick={() => onOpen(x.PATH, "ATTRIBUTE_LOST")}>−{n(x.apps_lost)}</button>}
              {x.apps_type_changed > 0 && <span className="num text-muted" title="сменился тип значения">тип {n(x.apps_type_changed)}</span>}
            </span>
          </li>
        ))}
      </ul>
      {rows.length > 6 && <ShowAll all={all} total={rows.length} onToggle={() => setAll(!all)} />}
    </Card>
  );
}

export function Overview({ runId, scope }: { runId: string; scope: Scope }) {
  const q = scopeQuery(scope);
  const go = useScopeNav(runId);
  const summary = useQuery({ queryKey: ["summary", runId, q], queryFn: () => api.get<Summary>(`/api/runs/${runId}/summary${q}`) });
  const segs = useQuery({ queryKey: ["segments", runId, q], queryFn: () => api.get<SegmentField[]>(`/api/runs/${runId}/segments${q}`) });
  const checks = useQuery({ queryKey: ["checks", runId], queryFn: () => api.get<CheckResult[]>(`/api/runs/${runId}/checks`) });
  if (summary.isLoading) return <Skeleton rows={10} />;
  if (summary.error || !summary.data) return <ErrorBox error={summary.error} />;
  const s = summary.data;
  const l = { ...LABELS, ...s.labels };
  const rows = (checks.data ?? []).flatMap((c) => c.rows);
  const failHigh = rows.filter((r) => r.RESULT === "FAIL" && r.CONFIDENCE === "HIGH").length;
  const failLow = rows.filter((r) => r.RESULT === "FAIL" && r.CONFIDENCE === "LOW").length;
  const errors = (checks.data ?? []).filter((c) => c.error).length;
  const seg = (f: string, v: string) => ({ ...scope, seg: { ...scope.seg, [f]: [v] } });
  const delta = s.approval_before != null && s.approval_after != null ? s.approval_after - s.approval_before : null;
  const flips = s.swap.filter((x) => x.before !== x.after).sort((a, b) => b.apps - a.apps);
  const flipped = `${s.decision_field}_CHANGED`;
  const sameDecision = { filters: { DIFF_FLAG: ["1"] }, title: `${l.object[1]} с различиями и тем же исходом` };
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-display text-[22px] font-normal leading-snug">{headline(s, delta, l)}</h2>
        <button className="mt-1 flex flex-wrap items-center gap-2 self-start rounded-lg border border-line-strong bg-surface px-3 py-2 text-[13px] font-medium transition-colors hover:border-accent hover:bg-accent-bg" onClick={() => go(scope, "checks")}>
          Автопроверки:
          <Badge tone={failHigh ? "fail" : "pass"}>FAIL {failHigh}</Badge>
          {failLow > 0 && <Badge tone="low">FAIL с низкой уверенностью {failLow}</Badge>}
          <Badge tone="pass">PASS {rows.length - failHigh - failLow}</Badge>
          {errors > 0 && <Badge tone="fail">ошибок исполнения: {errors}</Badge>}
          <span className="text-xs font-normal text-accent">открыть отчет проверок →</span>
        </button>
      </div>
      <section aria-label="Вердикт" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={`Сменилось ${l.outcome}`} value={n(s.decision_changed)} sub={pct(s.decision_changed, s.apps)} tone={s.decision_changed ? "fail" : undefined} onClick={() => go({ ...scope, seg: { ...scope.seg, [flipped]: ["1"] } }, "apps")} />
        <Stat label={l.positive_rate[0].toUpperCase() + l.positive_rate.slice(1)} value={delta == null ? "—" : pp(delta)} sub={delta == null ? undefined : `${rate(s.approval_before)} → ${rate(s.approval_after)}`} tone={delta != null && delta < 0 ? "fail" : undefined} />
        <Stat label="Есть различия" value={n(s.changed)} sub={`${pct(s.changed, s.apps)} из ${ofObjects(s.apps, l)}`} onClick={() => go(scope, "apps", { filters: { DIFF_FLAG: ["1"] }, title: `${l.object[1]} с различиями` })} />
        <Stat label={`Различия есть, ${l.outcome} то же`} value={n(s.changed_same_decision)} sub={`${pct(s.changed_same_decision, s.changed)} изменений прошли мимо`} onClick={() => go({ ...scope, seg: { ...scope.seg, [flipped]: ["0"] } }, "apps", sameDecision)} />
      </section>
      {!!s.issues?.length && (
        <details className="rounded-lg bg-warn-bg px-5 py-3 text-[13px]">
          <summary className="cursor-pointer select-none text-warn">Проблемы разбора ответов: {plural(s.issues.length, "вид", "вида", "видов")}. Данные не слиты молча, стоит проверить профиль нормализации</summary>
          <ul className="mt-2 space-y-1">
            {s.issues.map((i) => (
              <li key={i.kind} className="flex flex-wrap items-center justify-between gap-2">
                <span>{ISSUE[i.kind] ?? i.kind} <span className="num text-muted">· {objects(i.apps, l)}, {plural(i.rows, "место", "места", "мест")}</span></span>
                <button className={cx("text-xs", linkCls)} onClick={() => go({ ...scope, apps: i.examples }, "apps")}>примеры: {i.examples.join(", ")}</button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Causes
          s={s}
          l={l}
          onOpen={(c) => go({ ...scope, cluster: c }, "apps")}
          onCause={(c, x) => go({ ...scope, cluster: c.cid!, cause: { key: x.key, label: x.label } }, "apps")}
        />
        <Card>
          <CardHeader title={flips.length ? flips.map((x) => `${x.before} → ${x.after}: ${n(x.apps)}`).join(" · ") : `${l.outcome[0].toUpperCase() + l.outcome.slice(1)} не сместилось`} hint={`Матрица переходов BEFORE × AFTER; клик по ячейке открывает ${l.object[2]}`} />
          <SwapMatrix s={s} l={l} onCell={(b, a) => go({ ...scope, seg: { ...scope.seg, [`${s.decision_field}_BEFORE`]: [b], [`${s.decision_field}_AFTER`]: [a] } }, "apps")} />
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {segs.error ? <ErrorBox error={segs.error} /> : segs.data ? <Segments fields={segs.data} overall={s.apps ? s.decision_changed / s.apps : 0} l={l} onValue={(f, v) => go(seg(f, v))} /> : <Card><Skeleton /></Card>}
        {s.changed_same_decision > 0 && (
        <SameDecision s={s} l={l} onAll={() => go({ ...scope, seg: { ...scope.seg, [flipped]: ["0"] } }, "apps", sameDecision)} onCluster={(c) => go({ ...scope, cluster: c.cid!, seg: { ...scope.seg, [flipped]: ["0"] } }, "apps", sameDecision)} />
        )}
      </div>
      <StructureCard
        rows={s.structure ?? []}
        l={l}
        onOpen={(path, type) => go(scope, "apps", { cell: { PATH: path, CHANGE_TYPE: type }, title: `${tail(path)}: ${type === "ATTRIBUTE_NEW" ? "атрибут появился" : "атрибут исчез"}` })}
      />
      <AiOpinion runId={runId} />
    </div>
  );
}
