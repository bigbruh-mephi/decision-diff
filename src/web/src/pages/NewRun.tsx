import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Fragment, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorBox, Field, Filter, Skeleton, inputCls, titleCls } from "../components/ui";
import { api, type Config, type Dataset, type Procedure, type Run, type Selection } from "../lib/api";
import { KIND, n, plural } from "../lib/format";
import { ParamsEditor, type Params } from "../components/ParamsEditor";
import { missing, mine, stepCount } from "./Configs";

const STEPS: { kind: "parser" | "normalize" | "compare" | "segments" | "lineage" | "whatif" | "ai_prompt"; title: string; hint: string; empty?: string }[] = [
  { kind: "parser", title: "Разбор формата", hint: "Свой разборщик входных файлов (новый протокол или тип данных); исполняется в песочнице", empty: "встроенный: JSON, XML, YAML, CSV и другие (автоопределение)" },
  { kind: "normalize", title: "Приведение (нормализация)", hint: "Ключи массивов, исключения, бакеты, переименования" },
  { kind: "compare", title: "Сравнение", hint: "Эквивалентные значения и правила сравнения" },
  { kind: "segments", title: "Разрезы и факты отчета", hint: "Слой разрезов: решение, продукты, модель, порог" },
  { kind: "lineage", title: "Этапы цепочки решения", hint: "Этапы до и после продукта по путям атрибутов: для атрибуции «логика или входные данные»", empty: "не задавать" },
  { kind: "whatif", title: "Модель решения", hint: "По ней система находит причину каждой смены решения", empty: "последняя измененная" },
  { kind: "ai_prompt", title: "Запрос к ИИ-рецензенту", hint: "Тексты запросов к языковой модели; используются и при повторном запросе мнения ИИ", empty: "по умолчанию (procedures/ai_prompt.py)" },
];

function ProcSelect({ items, value, onChange, empty }: { items: Procedure[]; value: string; onChange: (v: string) => void; empty?: string }) {
  const desc = items.find((p) => p.id === value)?.description;
  return (
    <div>
    <div className="flex items-center gap-2">
      <select className={inputCls + " min-w-0 flex-1"} value={value} onChange={(e) => onChange(e.target.value)}>
        {empty && <option value="">{empty}</option>}
        {items.map((p) => (
          <option key={p.id} value={p.id}>
            {p.origin === "file" ? "📄 " : "✎ "}
            {p.name} {p.path ? `(${p.path})` : "(моя версия)"}{p.overridden ? " - заменен моей версией" : ""}
          </option>
        ))}
      </select>
      {value && (
        <Link to="/workspace" search={{ proc: value }} className="text-xs text-accent hover:underline">
          код
        </Link>
      )}
    </div>
    {desc && <p className="mt-1 text-xs text-muted">{desc}</p>}
    </div>
  );
}

function StepList({ items, value, onChange }: { items: Procedure[]; value: string[]; onChange: (v: string[]) => void }) {
  const move = (i: number, d: number) => { const v = [...value]; [v[i], v[i + d]] = [v[i + d], v[i]]; onChange(v); };
  return (
    <Field label="Подготовка документов" hint={value.length ? undefined : "Шаги до приведения: каждый - функция (doc, params) -> doc, выполняются по порядку"}>
      <div className="flex flex-col gap-1">
        {value.map((id, i) => {
          const p = items.find((x) => x.id === id);
          return (
            <div key={id + i} className="flex items-start gap-2 rounded border border-line px-2 py-1 text-[13px]">
              <span className="w-5 text-muted">{i + 1}.</span>
              <span className="min-w-0 flex-1">
                {p?.name ?? id}
                {p?.description && <span className="block text-xs text-muted">{p.description}</span>}
              </span>
              <Link to="/workspace" search={{ proc: id }} className="text-xs text-accent hover:underline">код</Link>
              <Button size="sm" variant="ghost" aria-label="Выше" disabled={i === 0} onClick={() => move(i, -1)}>↑</Button>
              <Button size="sm" variant="ghost" aria-label="Ниже" disabled={i === value.length - 1} onClick={() => move(i, 1)}>↓</Button>
              <Button size="sm" variant="ghost" aria-label="Убрать шаг" onClick={() => onChange(value.filter((_, j) => j !== i))}>×</Button>
            </div>
          );
        })}
        <div className="flex items-center gap-2">
          <select className={inputCls + " w-64"} value="" aria-label="Добавить шаг" onChange={(e) => e.target.value && onChange([...value, e.target.value])}>
            <option value="">+ добавить шаг…</option>
            {items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <Link to="/workspace" className="text-xs text-accent hover:underline">написать новый шаг</Link>
        </div>
      </div>
    </Field>
  );
}

export function NewRunPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { config } = useSearch({ strict: false }) as { config?: string };
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.get<Dataset[]>("/api/datasets") });
  const procs = useQuery({ queryKey: ["procedures"], queryFn: () => api.get<Procedure[]>("/api/procedures") });
  const configs = useQuery({ queryKey: ["configs"], queryFn: () => api.get<Config[]>("/api/configs") });
  const byKind = useMemo(() => {
    const m: Record<string, Procedure[]> = {};
    for (const p of procs.data ?? []) (m[p.kind] ??= []).push(p);
    return m;
  }, [procs.data]);
  const [dataset, setDataset] = useState("");
  const [two, setTwo] = useState(false);
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [name, setName] = useState("");
  const [sel, setSel] = useState<Selection | null>(null);
  const [configId, setConfigId] = useState<string>("");
  const [incremental, setIncremental] = useState(false);
  const [sample, setSample] = useState("");
  const [params, setParams] = useState<Params>({});
  const [invalid, setInvalid] = useState<string[]>([]);
  const [setup, setSetup] = useState(false);

  useEffect(() => {
    if (!sel && procs.data)
      setSel({
        normalize: byKind.normalize?.[0]?.id ?? "",
        compare: byKind.compare?.[0]?.id ?? "",
        segments: byKind.segments?.[0]?.id ?? "",
        lineage: byKind.lineage?.[0]?.id ?? null,
        steps: [],
        checks: (byKind.check ?? []).filter((p) => !p.overridden).map((p) => p.id),
      });
  }, [procs.data, byKind, sel]);
  useEffect(() => {
    if (!dataset && datasets.data?.length) setDataset(datasets.data[0].path);
  }, [datasets.data, dataset]);
  useEffect(() => {
    if (!configId && procs.data && configs.data?.length) applyConfig(config ?? configs.data[0].id);
  }, [procs.data, configs.data, config]);

  const applyConfig = (id: string) => {
    setConfigId(id);
    const c = configs.data?.find((x) => x.id === id);
    if (c) {
      const m = <T extends string | null | undefined>(x: T) => (x ? mine(procs.data ?? [], x) : x);
      const ids = c.procedure_ids;
      setSel({ ...ids, normalize: m(ids.normalize), compare: m(ids.compare), segments: m(ids.segments), lineage: m(ids.lineage), whatif: m(ids.whatif), parser: m(ids.parser), ai_prompt: m(ids.ai_prompt), steps: (ids.steps ?? []).map(m), checks: [...new Set(ids.checks.map(m))] });
      if (c.dataset) setDataset(c.dataset);
      setName(c.name);
      setSample(c.params?.sample_apps ? String(c.params.sample_apps) : "");
      setParams((c.params ?? {}) as Params);
      setInvalid([]);
      setSetup(false);
    }
  };

  const upload = useMutation({
    mutationFn: async (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      return api.post<Dataset>("/api/datasets", fd);
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setDataset(d.path);
    },
  });
  const chosen = sel ? [sel.parser, sel.normalize, sel.compare, sel.segments, sel.lineage, sel.whatif, sel.ai_prompt, ...(sel.steps ?? []), ...sel.checks].filter((x): x is string => !!x) : [];
  const chosenParams = Object.fromEntries(Object.entries(params).filter(([k]) => chosen.includes(k)));
  const sampleApps = sample ? Number(sample) : null;
  const configParams = { ...chosenParams, ...(sampleApps ? { sample_apps: sampleApps } : {}) };
  // null overrides a sample saved in the chosen configuration when the field is cleared
  const runParams = { ...chosenParams, sample_apps: sampleApps };
  const blocked = invalid.some((k) => chosen.includes(k));
  const saveConfig = useMutation({
    mutationFn: () =>
      configId
        ? api.put<Config>(`/api/configs/${configId}`, { name: name || dataset, dataset, procedures: sel, params: configParams })
        : api.post<Config>("/api/configs", { name: name || dataset, dataset, procedures: sel, params: configParams }),
    onSuccess: (c) => {
      setConfigId(c.id);
      qc.invalidateQueries({ queryKey: ["configs"] });
    },
  });
  const start = useMutation({
    mutationFn: () => api.post<Run>("/api/runs", { name: name || undefined, dataset: two ? before : dataset, dataset_after: two ? after : undefined, procedures: sel, params: runParams, config_id: configId || undefined, seal: !incremental }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      nav({ to: "/runs/$runId", params: { runId: r.id }, search: {} });
    },
  });

  if (procs.isLoading || datasets.isLoading) return <Skeleton rows={8} />;
  if (procs.error || datasets.error) return <ErrorBox title="Не удалось загрузить процедуры и датасеты." error={procs.error ?? datasets.error} />;
  const ds = datasets.data?.find((d) => d.path === dataset);
  const slices = (datasets.data ?? []).flatMap((d) => d.slices ?? []);
  const cfg = configs.data?.find((c) => c.id === configId);
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className={titleCls}>Новый прогон</h1>
        <p className="mt-1 text-xs text-muted">Выберите, что сравнивать, и конфигурацию с готовыми шагами и автопроверками. Остается нажать "Запустить".</p>
      </div>
      <Card>
        <CardHeader title="1. Данные" hint="Результат модулей 1-2: ответы СПР по одним и тем же заявкам до и после изменения. Файлы сопоставляются по имени" actions={<Filter value={two ? "two" : "one"} onChange={(v) => { setTwo(v === "two"); if (v === "two") setIncremental(false); }} items={[{ id: "one", label: "Папка прогона" }, { id: "two", label: "Два датасета" }]} />} />
        <div className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2">
          {two ? (
            <>
              <Field label="Датасет 1 (BEFORE)">
                <select className={inputCls} value={before} onChange={(e) => setBefore(e.target.value)}>
                  <option value="">выберите папку…</option>
                  {slices.map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
              </Field>
              <Field label="Датасет 2 (AFTER)">
                <select className={inputCls} value={after} onChange={(e) => setAfter(e.target.value)}>
                  <option value="">выберите папку…</option>
                  {slices.map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
              </Field>
            </>
          ) : (
            <Field label="Прогон модуля 2 (папка с BEFORE и AFTER)">
              <select className={inputCls} value={dataset} onChange={(e) => setDataset(e.target.value)}>
                {(datasets.data ?? []).map((d) => (
                  <option key={d.path} value={d.path}>
                    {d.path} · {n(d.before)} / {plural(d.after, "ответ", "ответа", "ответов")}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {!two && (
            <Field label="или загрузить архив">
              <label className="inline-flex h-9 cursor-pointer items-center rounded-md border border-dashed border-line-strong px-3 text-[13px] text-muted hover:border-accent hover:text-fg">
                {upload.isPending ? "Загрузка…" : "Выбрать .zip"}
                <input type="file" accept=".zip" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
              </label>
            </Field>
          )}
          {ds && !two && (
            <p className="text-xs text-muted sm:col-span-2">
              BEFORE: {n(ds.before)} · AFTER: {n(ds.after)}
              {ds.excluded.length > 0 && <> · технические поля из manifest: {ds.excluded.map((x) => <Badge key={x}>{x}</Badge>)}</>}
            </p>
          )}
          {upload.error && <ErrorBox error={upload.error} />}
        </div>
      </Card>
      <Card>
        <CardHeader title="2. Конфигурация" actions={<Link to="/configs" className="text-xs text-accent hover:underline">Все конфигурации</Link>} />
        <div className="flex flex-col gap-3 p-5">
          {configs.data?.length ? (
            <div className="flex items-center gap-3">
              <select className={inputCls + " flex-1"} value={configId} onChange={(e) => (e.target.value ? applyConfig(e.target.value) : (setConfigId(""), setSetup(true)))} aria-label="Конфигурация">
                {configs.data.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                <option value="">без конфигурации - выбрать процедуры вручную</option>
              </select>
              {cfg && <Link to="/configs/$configId" params={{ configId: cfg.id }} search={{}} className="text-xs text-accent hover:underline">открыть</Link>}
            </div>
          ) : (
            <p className="text-[13px] text-muted">
              Конфигураций пока нет. <Link to="/configs" className="text-accent hover:underline">Создайте конфигурацию</Link> или выберите процедуры вручную ниже.
            </p>
          )}
          {cfg && sel && (
            <p className="text-xs text-muted">
              {plural(stepCount(sel), "шаг", "шага", "шагов")}, из них {plural(sel.checks.length, "автопроверка", "автопроверки", "автопроверок")}
              {missing(sel).length > 0 && <span className="text-warn"> · не выбрано: {missing(sel).join(", ")}</span>}
            </p>
          )}
          <div className="flex flex-wrap items-end gap-4">
            <Field label="Заявок в выборке">
              <input
                title="Случайные заявки: одно и то же число дает те же заявки, поэтому прогоны можно сравнивать"
                type="number"
                min={1}
                max={ds?.before}
                className={inputCls + " w-40"}
                value={sample}
                onChange={(e) => {
                  setSample(e.target.value);
                  if (e.target.value) setIncremental(false);
                }}
                placeholder={ds && !two ? `все (${n(ds.before)})` : "все"}
              />
            </Field>
            <Field label="Название прогона">
              <input className={inputCls + " w-64"} value={name} onChange={(e) => setName(e.target.value)} placeholder={two ? before : dataset} />
            </Field>
            <Button className="ml-auto" variant="ghost" onClick={() => setSetup(!setup)} aria-expanded={setup}>{setup ? "Скрыть настройки" : "Настроить"}</Button>
          </div>
        </div>
      </Card>
      {sel && setup && (
        <Card>
          <CardHeader title="Процедуры" hint="📄 файл, размещённый в системе · ✎ код из рабочей области. Обработчик меняется без изменения кода приложения." />
          <div className="grid grid-cols-1 gap-3 p-4">
            {STEPS.map((st) => (
              <Fragment key={st.kind}>
                <Field label={st.title} hint={sel[st.kind] ? undefined : st.hint}>
                  <ProcSelect items={byKind[st.kind] ?? []} value={sel[st.kind] ?? ""} empty={st.empty} onChange={(v) => setSel({ ...sel, [st.kind]: v || null })} />
                </Field>
                {st.kind === "parser" && <StepList items={byKind.step ?? []} value={sel.steps ?? []} onChange={(steps) => setSel({ ...sel, steps })} />}
              </Fragment>
            ))}
          </div>
        </Card>
      )}
      {sel && setup && (
        <Card>
          <CardHeader
            title={`Автопроверки (${sel.checks.length} из ${(byKind.check ?? []).length})`}
            actions={
              <Link to="/workspace" className="text-xs text-accent hover:underline">
                Написать проверку
              </Link>
            }
          />
          <div className="grid grid-cols-1 gap-1 p-4 sm:grid-cols-2">
            {(byKind.check ?? []).map((p) => (
              <label key={p.id} title={p.description} className={"flex min-w-0 items-center gap-2 rounded px-2 py-1 hover:bg-surface-2" + (p.overridden ? " opacity-60" : "")}>
                <input
                  type="checkbox"
                  checked={sel.checks.includes(p.id)}
                  onChange={(e) => setSel({ ...sel, checks: e.target.checked ? [...sel.checks, p.id] : sel.checks.filter((x) => x !== p.id) })}
                />
                <span className="truncate">{p.name}</span>
                <Badge>{p.origin === "file" ? (p.overridden ? "заменен моей версией" : "файл") : "моя версия"}</Badge>
              </label>
            ))}
          </div>
        </Card>
      )}
      {sel && setup && (
        <Card>
          <CardHeader title="Параметры процедур" hint="JSON поверх параметров, объявленных в коде; раскройте процедуру, чтобы изменить" />
          <div className="grid grid-cols-1 gap-1.5 p-4">
            {chosen.map((id) => {
              const p = procs.data?.find((x) => x.id === id);
              return p ? (
                <ParamsEditor
                  key={id + configId}
                  id={id}
                  name={`${p.name} · ${KIND[p.kind] ?? p.kind}`}
                  value={params[id]}
                  onChange={(v) => setParams((cur) => { const next = { ...cur }; if (v) next[id] = v; else delete next[id]; return next; })}
                  onValid={(ok) => setInvalid((cur) => (ok ? cur.filter((x) => x !== id) : [...new Set([...cur, id])]))}
                />
              ) : null;
            })}
          </div>
        </Card>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {!two && (
          <label className="flex items-center gap-2 text-xs text-muted" title="Прогон остается открытым: можно догружать новые порции ответов, обработка начинается сразу">
            <input type="checkbox" checked={incremental} disabled={!!sample} onChange={(e) => setIncremental(e.target.checked)} />
            принимать догрузку порций
          </label>
        )}
        <div className="ml-auto flex gap-2">
          {setup && (
            <Button onClick={() => saveConfig.mutate()} disabled={!sel || blocked || saveConfig.isPending}>
              {configId ? "Обновить конфигурацию" : "Сохранить как конфигурацию"}
            </Button>
          )}
          <Button variant="primary" className="h-11 px-8 text-[15px]" onClick={() => start.mutate()} disabled={!sel || (two ? !before || !after : !dataset) || blocked || start.isPending}>
            {start.isPending ? "Отправка…" : "Запустить ▶"}
          </Button>
        </div>
      </div>
      {blocked && <p className="text-xs text-fail">Исправьте JSON параметров, чтобы сохранить или запустить</p>}
      {(start.error || saveConfig.error) && <ErrorBox error={start.error ?? saveConfig.error} />}
    </div>
  );
}
