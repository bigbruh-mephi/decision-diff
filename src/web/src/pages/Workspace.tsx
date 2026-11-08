import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { CodeView } from "../components/CodeView";
import { Badge, Button, Card, CardHeader, ErrorBox, Mono, inputCls } from "../components/ui";
import { ApiError, api, type CellResult, type CheckResult, type Procedure, type Run } from "../lib/api";
import { KIND, dt, plural } from "../lib/format";
import { TEMPLATES, template } from "../lib/templates";

function cells(code: string) {
  const lines = code.split("\n");
  const out: { start: number; end: number }[] = [];
  lines.forEach((l, i) => {
    if (l.startsWith("# %%") || i === 0) out.push({ start: i, end: lines.length });
    if (out.length > 1) out[out.length - 2].end = out[out.length - 1].start;
  });
  return out.map((c) => ({ ...c, text: lines.slice(c.start, c.end).join("\n"), title: lines[c.start].replace(/^# %%\s*/, "") || `строки ${c.start + 1}–${c.end}` }));
}

function Output({ res }: { res: CellResult }) {
  if (res.state === "queued" || res.state === "running") return <p className="text-xs text-muted">выполняется в песочнице…</p>;
  const r = res.result;
  if (!r) return null;
  return (
    <div className="space-y-2 text-xs">
      {r.stdout && <pre className="max-h-40 overflow-auto rounded bg-surface-2 p-2 font-mono">{r.stdout}</pre>}
      {r.error && <ErrorBox inline error={r.error} debug={r.error_debug} />}
      {r.value?.type === "table" && r.value.rows && (
        <div className="max-h-80 overflow-auto rounded border border-line">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-2">
              <tr>{Object.keys(r.value.rows[0] ?? {}).map((k) => <th key={k} className="px-2 py-1 text-left font-mono font-medium">{k}</th>)}</tr>
            </thead>
            <tbody>
              {r.value.rows.map((row, i) => (
                <tr key={i} className="border-t border-line">
                  {Object.values(row).map((v, j) => <td key={j} className="max-w-60 truncate px-2 py-1 font-mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {r.value.more && <p className="p-1 text-muted">показаны первые 200 строк</p>}
        </div>
      )}
      {r.value && r.value.type !== "table" && <pre className="rounded bg-surface-2 p-2 font-mono">{r.value.repr}</pre>}
      {r.check && <p className="text-pass">Проверка «{r.check.label}» вернула {plural(r.check.rows.length, "строку", "строки", "строк")} по контракту.</p>}
    </div>
  );
}

const STAGE_ORDER = ["parser", "step", "normalize", "compare", "segments", "lineage", "whatif", "facts", "check", "ai_prompt"];
const STAGE_TITLE: Record<string, string> = { parser: "1. Разбор формата", step: "2. Подготовка документов", normalize: "3. Приведение", compare: "4. Сравнение", segments: "5. Разрезы отчета", lineage: "6. Цепочка этапов", whatif: "7. Модель решения", facts: "Факты отчета", check: "8. Автопроверки", ai_prompt: "9. Мнение ИИ" };

export function WorkspacePage() {
  const { run: runParam, proc } = useSearch({ strict: false }) as { run?: string; proc?: string };
  const nav = useNavigate();
  const qc = useQueryClient();
  const procs = useQuery({ queryKey: ["procedures"], queryFn: () => api.get<Procedure[]>("/api/procedures") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<Run[]>("/api/runs") });
  const done = (runs.data ?? []).filter((r) => r.state === "done");
  const [runId, setRunId] = useState(runParam ?? "");
  const [code, setCode] = useState(() => template("check"));
  const [current, setCurrent] = useState<Procedure | null>(null);
  const [results, setResults] = useState<Record<number, CellResult>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  useEffect(() => {
    if (!runId && done.length) setRunId(done[0].id);
  }, [done, runId]);
  useEffect(() => {
    if (proc && proc !== current?.id) api.get<Procedure>(`/api/procedures/${proc}`).then((p) => { setCurrent(p); setCode(p.code ?? ""); setMsg(null); setLoadErr(null); }, setLoadErr);
  }, [proc]);
  const parts = useMemo(() => cells(code), [code]);
  const runChecks = useQuery({ queryKey: ["checks", runId], queryFn: () => api.get<CheckResult[]>(`/api/runs/${runId}/checks`), enabled: !!runId });
  const checkId = /^CHECK\s*=\s*\{[^}]*?["']id["']\s*:\s*["']([^"']+)["']/m.exec(code)?.[1];
  const currentDesc = current && procs.data?.find((p) => p.id === current.id)?.description;
  const isCheck = /^CHECK\s*=/m.test(code) || /["']kind["']\s*:\s*["']check["']/.test(code);
  const taken = checkId && runChecks.data?.find((c) => c.check_id === checkId);

  const runCell = async (upto: number) => {
    const text = parts.slice(0, upto + 1).map((c) => c.text).join("\n");
    setResults((r) => ({ ...r, [upto]: { id: "", state: "queued", result: null } }));
    try {
      const { id } = await api.post<{ id: string }>("/api/cells", { code: text, run_id: runId || undefined });
      for (;;) {
        const res = await api.get<CellResult>(`/api/cells/${id}`);
        setResults((r) => ({ ...r, [upto]: res }));
        if (res.state === "done" || res.state === "failed") break;
        await new Promise((ok) => setTimeout(ok, 120));
      }
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      setResults((r) => ({ ...r, [upto]: { id: "", state: "failed", result: { stdout: "", error, error_debug: e instanceof ApiError ? e.debug : null, value: null, check: null } } }));
    }
  };
  const contractCode = () => {
    const i = parts.findIndex((c) => /^(CHECK|PROCEDURE)\s*=/m.test(c.text));
    return (i < 0 ? parts : parts.slice(0, i + 1)).map((c) => c.text).join("\n");
  };
  const save = useMutation({
    mutationFn: () => {
      const body = { code: contractCode() };
      return current && current.origin === "workspace" ? api.put<Procedure>(`/api/procedures/${current.id}`, body) : api.post<Procedure>("/api/procedures", body);
    },
    onSuccess: (p) => {
      setCurrent(p);
      setMsg(`Сохранено: ${p.name} (${KIND[p.kind] ?? p.kind}), версия ${p.sha.slice(0, 7)}. Новые прогоны используют эту версию по умолчанию`);
      qc.invalidateQueries({ queryKey: ["procedures"] });
      nav({ to: "/workspace", search: { run: runId || undefined, proc: p.id }, replace: true });
    },
  });
  const addToReport = useMutation({
    mutationFn: async () => {
      const p = await save.mutateAsync();
      return api.post(`/api/runs/${runId}/checks`, { procedure_id: p.id });
    },
    onSuccess: () => setMsg("Проверка поставлена в очередь и появится в отчёте прогона"),
  });

  return (
    <div className="grid gap-4 xl:grid-cols-[260px_1fr]">
      <Card className="h-fit">
        <CardHeader title="Конфигурация" hint="Алгоритмы по этапам. 📄 поставляемый файл · ✎ моя версия, ее берут новые прогоны" actions={
            <select className={inputCls + " h-7 w-28 text-xs"} value="" aria-label="Новая процедура из шаблона" onChange={(e) => { if (!e.target.value) return; setCurrent(null); setCode(template(e.target.value)); setResults({}); setMsg(null); nav({ to: "/workspace", search: { run: runId || undefined } }); }}>
              <option value="">Новая…</option>
              {TEMPLATES.map((t) => <option key={t.kind} value={t.kind}>{t.title}</option>)}
            </select>
          } />
        <div className="max-h-[75vh] overflow-y-auto py-1">
          {STAGE_ORDER.map((kind) => {
            const items = (procs.data ?? []).filter((p) => p.kind === kind);
            return items.length ? (
              <section key={kind}>
                <h2 className="px-3 pt-2 text-[11px] font-semibold uppercase tracking-wide text-muted">{STAGE_TITLE[kind] ?? KIND[kind] ?? kind}</h2>
                <ul>
                  {items.map((p) => (
                    <li key={p.id}>
                      <Link to="/workspace" search={{ run: runId || undefined, proc: p.id }} title={p.description} className={"block px-3 py-1.5 text-xs hover:bg-surface-2 " + (current?.id === p.id ? "bg-accent-bg " : "") + (p.overridden ? "opacity-60" : "")}>
                        <span className="flex items-center gap-2">
                          <span aria-hidden>{p.origin === "file" ? "📄" : "✎"}</span>
                          <span className="min-w-0 flex-1 truncate font-medium">{p.name}</span>
                          {p.overridden && <Badge>заменен</Badge>}
                        </span>
                        {p.description && <span className="mt-0.5 line-clamp-2 pl-6 text-muted">{p.description}</span>}
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null;
          })}
        </div>
      </Card>
      <div className="flex min-w-0 flex-col gap-3">
        <Card>
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
            <h1 className="text-[13px] font-semibold">{current ? current.name : "Новая процедура"}</h1>
            {current && <Badge>{current.origin === "file" ? `файл ${current.path}` : "рабочая область"}</Badge>}
            {current && <Mono className="text-muted">версия {current.sha.slice(0, 7)}</Mono>}
            {currentDesc && <p className="basis-full text-xs text-muted">{currentDesc}</p>}
            <div className="ml-auto flex max-w-full flex-wrap items-center gap-2">
              <select className={inputCls + " h-7 text-xs"} value={runId} onChange={(e) => setRunId(e.target.value)} aria-label="Прогон для запуска">
                <option value="">без прогона</option>
                {done.map((r) => (
                  <option key={r.id} value={r.id}>{r.name} · {dt(r.created_at)} · {plural(r.apps, "заявка", "заявки", "заявок")}</option>
                ))}
              </select>
              <Button size="sm" onClick={() => runCell(parts.length - 1)} disabled={!runId}>Выполнить всё</Button>
              <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending} title={current?.origin === "file" ? "Файл в системе не меняется: сохраняется ваша версия, новые прогоны берут ее вместо файла" : undefined}>{current?.origin === "file" ? "Сохранить как мою версию" : "Сохранить"}</Button>
              <Button size="sm" variant="primary" onClick={() => addToReport.mutate()} disabled={!runId || !isCheck || addToReport.isPending} title={isCheck ? undefined : "Применить к готовому прогону можно автопроверку; другие процедуры выбираются при запуске нового прогона"}>Применить к прогону</Button>
            </div>
          </div>
          {msg && <p className="border-b border-line bg-pass-bg px-4 py-1.5 text-xs text-pass">{msg}</p>}
          {taken && (
            <p role="status" className="border-b border-line bg-warn-bg px-4 py-1.5 text-xs text-warn">
              В прогоне уже есть проверка с идентификатором <Mono>{checkId}</Mono> («{taken.label}»): при применении её строки в отчёте заменятся строками этого кода. Для новой проверки задайте другой "id" в CHECK.
            </p>
          )}
          {(save.error || addToReport.error || !!loadErr) && <ErrorBox error={save.error ?? addToReport.error ?? loadErr} />}
          <div className="p-3">
            <CodeView value={code} onChange={setCode} />
          </div>
          <p className="px-4 pb-3 text-xs text-muted">
            Код исполняется только в песочнице воркера: без сети, без доступа к файлам кроме витрины прогона, с лимитами времени и памяти. Ячейки разделяются строкой <Mono># %%</Mono>; в ячейке доступен объект <Mono>mart</Mono> (MartAPI). В процедуру сохраняются ячейки до объявления <Mono>CHECK</Mono>/<Mono>PROCEDURE</Mono> включительно, ячейки ниже — черновик для проб.
          </p>
        </Card>
        <Card>
          <CardHeader title="Ячейки" hint="Выполнение до выбранной ячейки включительно" />
          <ul className="divide-y divide-line">
            {parts.map((c, i) => (
              <li key={i} className="px-4 py-2">
                <div className="mb-1 flex items-center gap-2">
                  <Button size="sm" onClick={() => runCell(i)} disabled={!runId} aria-label={`Выполнить ячейку ${i + 1}`}>▶</Button>
                  <span className="text-xs font-medium">{c.title}</span>
                  <span className="text-xs text-muted">строки {c.start + 1}–{c.end}</span>
                </div>
                {results[i] && <Output res={results[i]} />}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
