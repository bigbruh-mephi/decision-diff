import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorBox, Field, Skeleton, inputCls, titleCls } from "../components/ui";
import { api, type Config, type Dataset, type Procedure, type Run, type Selection } from "../lib/api";
import { n } from "../lib/format";

const STAGES: { kind: "normalize" | "compare" | "segments"; title: string; hint: string }[] = [
  { kind: "normalize", title: "Приведение (нормализация)", hint: "Ключи массивов, исключения, бакеты, переименования" },
  { kind: "compare", title: "Сравнение", hint: "Эквивалентные значения и правила сравнения" },
  { kind: "segments", title: "Разрезы и факты отчёта", hint: "Слой разрезов: решение, продукты, модель, порог" },
];

function ProcSelect({ items, value, onChange }: { items: Procedure[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-2">
      <select className={inputCls + " min-w-0 flex-1"} value={value} onChange={(e) => onChange(e.target.value)}>
        {items.map((p) => (
          <option key={p.id} value={p.id}>
            {p.origin === "file" ? "📄 " : "✎ "}
            {p.name} {p.path ? `(${p.path})` : "(рабочая область)"}
          </option>
        ))}
      </select>
      {value && (
        <Link to="/workspace" search={{ proc: value }} className="text-xs text-accent hover:underline">
          код
        </Link>
      )}
    </div>
  );
}

export function NewRunPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.get<Dataset[]>("/api/datasets") });
  const procs = useQuery({ queryKey: ["procedures"], queryFn: () => api.get<Procedure[]>("/api/procedures") });
  const configs = useQuery({ queryKey: ["configs"], queryFn: () => api.get<Config[]>("/api/configs") });
  const byKind = useMemo(() => {
    const m: Record<string, Procedure[]> = {};
    for (const p of procs.data ?? []) (m[p.kind] ??= []).push(p);
    return m;
  }, [procs.data]);
  const [dataset, setDataset] = useState("");
  const [name, setName] = useState("");
  const [sel, setSel] = useState<Selection | null>(null);
  const [configId, setConfigId] = useState<string>("");
  const [incremental, setIncremental] = useState(false);
  const [sample, setSample] = useState("");
  const params = { sample_apps: sample ? Number(sample) : null };

  useEffect(() => {
    if (!sel && procs.data)
      setSel({
        normalize: byKind.normalize?.[0]?.id ?? "",
        compare: byKind.compare?.[0]?.id ?? "",
        segments: byKind.segments?.[0]?.id ?? "",
        lineage: byKind.lineage?.[0]?.id ?? null,
        checks: (byKind.check ?? []).map((p) => p.id),
      });
  }, [procs.data, byKind, sel]);
  useEffect(() => {
    if (!dataset && datasets.data?.length) setDataset(datasets.data[0].path);
  }, [datasets.data, dataset]);

  const applyConfig = (id: string) => {
    setConfigId(id);
    const c = configs.data?.find((x) => x.id === id);
    if (c) {
      setSel(c.procedure_ids);
      if (c.dataset) setDataset(c.dataset);
      setName(c.name);
      setSample(c.params?.sample_apps ? String(c.params.sample_apps) : "");
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
  const saveConfig = useMutation({
    mutationFn: () =>
      configId
        ? api.put<Config>(`/api/configs/${configId}`, { name: name || dataset, dataset, procedures: sel, params })
        : api.post<Config>("/api/configs", { name: name || dataset, dataset, procedures: sel, params }),
    onSuccess: (c) => {
      setConfigId(c.id);
      qc.invalidateQueries({ queryKey: ["configs"] });
    },
  });
  const start = useMutation({
    mutationFn: () => api.post<Run>("/api/runs", { name: name || undefined, dataset, procedures: sel, config_id: configId || undefined, seal: !incremental, params }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      nav({ to: "/runs/$runId", params: { runId: r.id }, search: {} });
    },
  });

  if (procs.isLoading || datasets.isLoading) return <Skeleton rows={8} />;
  const ds = datasets.data?.find((d) => d.path === dataset);
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className={titleCls}>Новый прогон</h1>
        <p className="text-xs text-muted">Выберите датасет, процедуры приведения и сравнения и набор автопроверок. Конфигурацию можно сохранить и запускать повторно.</p>
      </div>
      {configs.data && configs.data.length > 0 && (
        <Card>
          <CardHeader title="Сохранённые конфигурации" />
          <div className="flex flex-wrap gap-2 p-4">
            {configs.data.map((c) => (
              <Button key={c.id} size="sm" variant={configId === c.id ? "primary" : "secondary"} onClick={() => applyConfig(c.id)}>
                {c.name}
              </Button>
            ))}
          </div>
        </Card>
      )}
      <Card>
        <CardHeader title="1. Датасет" hint="Результат модулей 1–2: два среза ответов СПР по одним и тем же заявкам" />
        <div className="grid gap-3 p-4 sm:grid-cols-[1fr_auto]">
          <Field label="Прогон модуля 2">
            <select className={inputCls} value={dataset} onChange={(e) => setDataset(e.target.value)}>
              {(datasets.data ?? []).map((d) => (
                <option key={d.path} value={d.path}>
                  {d.path} · {n(d.before)} / {n(d.after)} ответов
                </option>
              ))}
            </select>
          </Field>
          <Field label="или загрузить архив">
            <label className="inline-flex h-9 cursor-pointer items-center rounded-md border border-dashed border-line-strong px-3 text-[13px] text-muted hover:border-accent hover:text-fg">
              {upload.isPending ? "Загрузка…" : "Выбрать .zip"}
              <input type="file" accept=".zip" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
            </label>
          </Field>
          <Field label="Заявок в выборке" hint="Случайные заявки. Одно и то же число даёт те же заявки, поэтому прогоны можно сравнивать между собой">
            <input
              type="number"
              min={1}
              max={ds?.before}
              className={inputCls + " w-40"}
              value={sample}
              onChange={(e) => {
                setSample(e.target.value);
                if (e.target.value) setIncremental(false);
              }}
              placeholder={ds ? `все (${n(ds.before)})` : "все"}
            />
          </Field>
          {ds && (
            <p className="text-xs text-muted sm:col-span-2">
              BEFORE: {n(ds.before)} · AFTER: {n(ds.after)}
              {ds.excluded.length > 0 && <> · технические поля из manifest: {ds.excluded.map((x) => <Badge key={x}>{x}</Badge>)}</>}
            </p>
          )}
          {upload.error && <ErrorBox error={upload.error} />}
        </div>
      </Card>
      {sel && (
        <Card>
          <CardHeader title="2. Процедуры" hint="📄 файл, размещённый в системе · ✎ код из рабочей области. Обработчик меняется без изменения кода приложения." />
          <div className="grid gap-3 p-4">
            {STAGES.map((st) => (
              <Field key={st.kind} label={st.title} hint={st.hint}>
                <ProcSelect items={byKind[st.kind] ?? []} value={sel[st.kind]} onChange={(v) => setSel({ ...sel, [st.kind]: v })} />
              </Field>
            ))}
            {(byKind.lineage ?? []).length > 0 && (
              <Field label="Цепочка этапов" hint="Вход → признаки → скоры → порог → правила → решение: для атрибуции «логика или входные данные»">
                <ProcSelect items={byKind.lineage ?? []} value={sel.lineage ?? ""} onChange={(v) => setSel({ ...sel, lineage: v })} />
              </Field>
            )}
          </div>
        </Card>
      )}
      {sel && (
        <Card>
          <CardHeader
            title={`3. Автопроверки (${sel.checks.length} из ${(byKind.check ?? []).length})`}
            actions={
              <Link to="/workspace" className="text-xs text-accent hover:underline">
                Написать проверку
              </Link>
            }
          />
          <div className="grid gap-1 p-4 sm:grid-cols-2">
            {(byKind.check ?? []).map((p) => (
              <label key={p.id} className="flex min-w-0 items-center gap-2 rounded px-2 py-1 hover:bg-surface-2">
                <input
                  type="checkbox"
                  checked={sel.checks.includes(p.id)}
                  onChange={(e) => setSel({ ...sel, checks: e.target.checked ? [...sel.checks, p.id] : sel.checks.filter((x) => x !== p.id) })}
                />
                <span className="truncate">{p.name}</span>
                <Badge>{p.origin === "file" ? "файл" : "рабочая область"}</Badge>
              </label>
            ))}
          </div>
        </Card>
      )}
      <Card>
        <div className="flex flex-wrap items-end gap-3 p-4">
          <Field label="Название прогона">
            <input className={inputCls + " w-72"} value={name} onChange={(e) => setName(e.target.value)} placeholder={dataset} />
          </Field>
          <label className="flex h-9 items-center gap-2 text-xs text-muted" title="Прогон остаётся открытым: можно догружать новые порции ответов, обработка начинается сразу">
            <input type="checkbox" checked={incremental} disabled={!!sample} onChange={(e) => setIncremental(e.target.checked)} />
            принимать догрузку порций
          </label>
          <div className="ml-auto flex gap-2">
            <Button onClick={() => saveConfig.mutate()} disabled={!sel || saveConfig.isPending}>
              {configId ? "Обновить конфигурацию" : "Сохранить конфигурацию"}
            </Button>
            <Button variant="primary" onClick={() => start.mutate()} disabled={!sel || !dataset || start.isPending}>
              {start.isPending ? "Отправка…" : "Запустить"}
            </Button>
          </div>
        </div>
        {(start.error || saveConfig.error) && <ErrorBox error={start.error ?? saveConfig.error} />}
      </Card>
    </div>
  );
}
