import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Card, CardHeader, Empty, ErrorBox, Skeleton, cx, inputCls } from "../../components/ui";
import { ApiError, api, type Scope } from "../../lib/api";
import { n, ofObjects, plural, rate } from "../../lib/format";
import { useLabels, useScopeNav } from "../Run";

type Param = { type: "enum" | "float" | "float_map" | "multi"; label?: string; values?: string[]; values_from?: string; keys_from?: string; min?: number; max?: number; step?: number; default: unknown };
type Decl = { procedure: { id: string; name: string; sha: string }; label: string; params: Record<string, Param>; outcome: { field: string; values: string[] }; values: Record<string, unknown>; choices: Record<string, string[]> | null };
type SwapRow = { before: string; after: string; apps: number };
type Result = {
  apps: number; slice: string; positive: string; fidelity: number; moved: number; moved_ids?: string[];
  approval_rate: { before: number; after: number; whatif: number };
  swap_vs_before: SwapRow[]; swap_vs_after: SwapRow[];
  segments: { field: string; value: string; apps: number; approval_slice: number; approval_whatif: number; moved: number }[];
  by_segment?: string | null;
  sandbox_ms?: number;
  choices?: Record<string, string[]>;
};

const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** Client-side check of the values against the declaration, so a wrong value never leaves the browser; keys are the
 * param name or ``name.key`` for one entry of a ``float_map``. The worker validates again (trust boundary). */
function problems(params: Record<string, Param>, values: Record<string, unknown>, choices: Record<string, string[]> | null) {
  const out: Record<string, string> = {};
  for (const [k, p] of Object.entries(params)) {
    const v = values[k];
    const range = `нужно число от ${n(p.min)} до ${n(p.max)}`;
    const allowed = p.values ?? choices?.[p.values_from ?? p.keys_from ?? ""];
    if (p.type === "float" && !(isNum(v) && v >= p.min! && v <= p.max!)) out[k] = `Значение вне диапазона: ${range}`;
    if (p.type === "float_map")
      for (const [key, x] of Object.entries((v as Record<string, unknown>) ?? {})) {
        if (allowed && !allowed.includes(key)) out[`${k}.${key}`] = `Неизвестное имя «${key}»`;
        else if (!(isNum(x) && x >= p.min! && x <= p.max!)) out[`${k}.${key}`] = `${key}: значение ${isNum(x) ? n(x) : String(x)} вне диапазона, ${range}`;
      }
    const bad = p.type === "multi" ? ((v as string[]) ?? []).find((x) => allowed && !allowed.includes(x)) : p.type === "enum" && allowed && v !== p.default && !allowed.includes(String(v)) ? String(v) : undefined;
    if (bad !== undefined) out[k] = `Значения «${bad}» нет среди допустимых`;
  }
  return out;
}

function ParamInput({ name, p, value, choices, errors, onChange }: { name: string; p: Param; value: unknown; choices: Record<string, string[]> | null; errors: Record<string, string>; onChange: (v: unknown) => void }) {
  const opts = p.values ?? (p.values_from ? choices?.[p.values_from] ?? [] : []);
  if (p.type === "enum")
    return (
      <select className={inputCls} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} aria-label={p.label ?? name}>
        {p.values_from && <option value="">как в срезе</option>}
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  if (p.type === "float") {
    const v = Number(value ?? p.default ?? 0);
    return (
      <div className="flex items-center gap-2">
        <input type="range" min={p.min} max={p.max} step={p.step} value={v} onChange={(e) => onChange(Number(e.target.value))} className="flex-1 accent-[var(--accent)]" aria-label={p.label ?? name} />
        <span className="num w-14 text-right font-mono text-xs">{v > 0 ? "+" : ""}{v.toFixed(2)}</span>
      </div>
    );
  }
  if (p.type === "multi")
    return (
      <div className="flex flex-wrap gap-1">
        {opts.map((o) => {
          const on = (value as string[] | undefined)?.includes(o);
          return (
            <button key={o} onClick={() => onChange(on ? (value as string[]).filter((x) => x !== o) : [...((value as string[]) ?? []), o])} className={cx("rounded border px-1.5 py-0.5 font-mono text-xs", on ? "border-fail/50 bg-fail-bg text-fail line-through" : "border-line-strong hover:border-accent")} aria-pressed={on}>
              {o}
            </button>
          );
        })}
      </div>
    );
  const keys = p.keys_from ? choices?.[p.keys_from] ?? [] : [];
  const map = (value as Record<string, number>) ?? {};
  return (
    <div className="grid gap-1">
      {keys.map((k) => {
        const bad = errors[`${name}.${k}`];
        return (
          <label key={k} className="grid grid-cols-[1fr_9rem] items-center gap-x-2 text-xs">
            <span className="truncate font-mono" title={k}>{k}</span>
            <input type="number" min={p.min} max={p.max} step={p.step} placeholder="0" aria-invalid={!!bad} className={cx(inputCls, "h-7 px-2 text-xs", bad && "ring-2 ring-fail")} value={map[k] ?? ""} onChange={(e) => { const next = { ...map }; if (e.target.value === "") delete next[k]; else next[k] = Number(e.target.value); onChange(next); }} />
            {bad && <span role="alert" className="col-span-2 text-xs text-fail">{bad}</span>}
          </label>
        );
      })}
    </div>
  );
}

export function WhatIf({ runId, scope }: { runId: string; scope: Scope }) {
  const decl = useQuery({ queryKey: ["whatif-decl", runId], queryFn: () => api.get<Decl>(`/api/runs/${runId}/whatif`), retry: false });
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [res, setRes] = useState<{ result: Result; ms: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ error: unknown; debug?: string | null } | null>(null);
  const seq = useRef(0);
  const go = useScopeNav(runId);
  const l = useLabels(runId);
  useEffect(() => {
    if (decl.data && !values) setValues(decl.data.values);
  }, [decl.data, values]);
  const choices = useMemo(() => (res?.result.choices ? { ...decl.data?.choices, ...res.result.choices } : decl.data?.choices ?? null), [decl.data, res]);
  const bad = useMemo(() => (values && decl.data ? problems(decl.data.params, values, choices) : {}), [values, decl.data, choices]);
  const invalid = Object.keys(bad).length > 0;
  useEffect(() => {
    if (!values || !decl.data) return;
    const my = ++seq.current;
    if (invalid) {
      setBusy(false);
      return;
    }
    const t = setTimeout(async () => {
      setBusy(true);
      setErr(null);
      const t0 = performance.now();
      try {
        const { id } = await api.post<{ id: string }>(`/api/runs/${runId}/whatif`, { values, scope, procedure_id: decl.data!.procedure.id });
        for (;;) {
          const w = await api.get<{ state: string; result: Result; error?: string; error_debug?: string }>(`/api/whatif/${id}`);
          if (my !== seq.current) return;
          if (w.state === "done") { setRes({ result: w.result, ms: performance.now() - t0 }); break; }
          if (w.state === "failed") { setErr({ error: w.error ?? "Расчет не выполнился, причина не сохранилась.", debug: w.error_debug }); break; }
          await new Promise((ok) => setTimeout(ok, 40));
        }
      } catch (e) {
        setErr({ error: e });
      } finally {
        if (my === seq.current) setBusy(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [values, scope, runId, decl.data, invalid]);

  if (decl.isLoading) return <Skeleton rows={8} />;
  if (decl.error && !(decl.error instanceof ApiError && decl.error.code === "whatif_missing")) return <ErrorBox error={decl.error} />;
  if (decl.error) return <Card><Empty title="Процедура «что если» не найдена">Добавьте процедуру вида whatif в procedures/ или в рабочей области.</Empty></Card>;
  const d = decl.data!;
  const r = res?.result;
  const stale = !!r && (!!err || invalid);
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
      <Card className="h-fit">
        <CardHeader title="Сдвиг порогов" hint={d.label} />
        <div className="flex flex-col gap-4 p-4">
          {values && Object.entries(d.params).map(([k, p]) => (
            <div key={k} className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">{p.label ?? k}</span>
              <ParamInput name={k} p={p} value={values[k]} choices={choices} errors={bad} onChange={(v) => setValues({ ...values, [k]: v })} />
              {bad[k] && <span role="alert" className="text-xs text-fail">{bad[k]}</span>}
            </div>
          ))}
          <button className="self-start text-xs text-accent hover:underline" onClick={() => setValues(Object.fromEntries(Object.entries(d.params).map(([k, p]) => [k, p.default])))}>сбросить к стратегии среза</button>
        </div>
      </Card>
      <div className="flex min-w-0 flex-col gap-4">
        {invalid && <ErrorBox inline title="Расчет не запущен." error={`Исправьте выделенные поля: ${plural(Object.keys(bad).length, "ошибка", "ошибки", "ошибок")}. ${Object.values(bad)[0]}.`} />}
        {err && <ErrorBox inline title="Расчет «что если» не выполнился." error={err.error} debug={err.debug} />}
        {stale && <p role="status" className="rounded-md border border-warn/50 bg-warn-bg px-3 py-2 text-xs text-warn">Ниже - результат для прошлых параметров, он устарел. После исправления расчет запустится сам.</p>}
        <div className={cx("flex min-w-0 flex-col gap-4", stale && "pointer-events-none select-none opacity-40")} aria-hidden={stale}>
        <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs text-muted">Приближение: моделирует только порог и стоп-факторы. Точный результат - изменить стратегию, перепрогнать через СПР и сравнить прогоны здесь.</p>
        {r && (
          <Card>
            <CardHeader title={r.by_segment ? `${l.positive_rate[0].toUpperCase() + l.positive_rate.slice(1)} по ${r.by_segment}` : "По сегментам"} hint={`Сейчас - как в срезе ${r.slice}; клик по числу сменивших ${l.outcome} открывает их список`} />
            <div className="overflow-x-auto"><table className="w-full text-[13px]">
              <thead className="text-left text-xs text-muted"><tr className="border-b border-line"><th className="px-4 py-2 font-medium">{r.by_segment ?? "Разрез"}</th><th className="px-2 py-2 text-right font-medium">{l.object[2]}</th><th className="px-2 py-2 text-right font-medium">Сейчас</th><th className="px-2 py-2 text-right font-medium">Что если</th><th className="px-2 py-2 text-right font-medium">Изменение</th><th className="px-4 py-2 text-right font-medium">Сменят {l.outcome}</th></tr></thead>
              <tbody aria-busy={busy}>
                {r.segments.filter((x) => !/_(BEFORE|AFTER|CHANGED)$/.test(x.field)).map((x) => {
                  const d = (x.approval_whatif - x.approval_slice) * 100;
                  return (
                    <tr key={x.field + x.value} className="border-b border-line last:border-0">
                      <td className="px-4 py-1.5 font-mono text-xs">{r.by_segment ? x.value : `${x.field} = ${x.value}`}</td>
                      <td className="num px-2 py-1.5 text-right text-muted">{n(x.apps)}</td>
                      <td className="num px-2 py-1.5 text-right">{rate(x.approval_slice)}</td>
                      <td className="num px-2 py-1.5 text-right font-medium">{rate(x.approval_whatif)}</td>
                      <td className={cx("num px-2 py-1.5 text-right", d > 0 ? "text-pass" : d < 0 ? "text-fail" : "text-muted")}>{d ? `${d > 0 ? "+" : "-"}${Math.abs(d).toFixed(1).replace(".", ",")} п.п.` : "0"}</td>
                      <td className="num px-4 py-1.5 text-right">{x.moved && r.moved_ids?.length ? <button className="text-accent hover:underline" onClick={() => go({ ...scope, seg: { ...scope.seg, [x.field]: [x.value] }, apps: r.moved_ids }, "apps", { title: `${x.value}: ${l.outcome} изменится в сценарии «что если»` })}>{n(x.moved)}</button> : n(x.moved)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table></div>
          </Card>
        )}
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {r && <span className="num">Всего: {l.positive_rate} {rate(r.approval_rate.after)} → {rate(r.approval_rate.whatif)}, сменят {l.outcome} {n(r.moved)} из {ofObjects(r.apps, l)}{r.moved_ids && r.moved > r.moved_ids.length ? ` (список - первые ${n(r.moved_ids.length)})` : ""}</span>}
          {r && <Badge tone={r.fidelity === 1 ? "pass" : "warn"}>точность воспроизведения среза {rate(r.fidelity)}</Badge>}
          {res && <Badge>пересчет {Math.round(res.ms)} мс</Badge>}
          {busy && <Badge>пересчет…</Badge>}
        </div>
        </div>
      </div>
    </div>
  );
}
