import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ParamsEditor, type Params } from "../components/ParamsEditor";
import { Badge, Button, Card, Empty, ErrorBox, Skeleton, inputCls, titleCls } from "../components/ui";
import { api, type Config, type Dataset, type Procedure, type Selection } from "../lib/api";
import { dt, plural } from "../lib/format";

type Single = "parser" | "normalize" | "compare" | "segments" | "lineage" | "ai_prompt";
type Group = { key: Single | "steps" | "checks"; kind: string; title: string; desc: string; required?: boolean };

export const GROUPS: Group[] = [
  { key: "parser", kind: "parser", title: "Разбор формата", desc: "Как читать входные файлы. Без шага работает встроенный разбор JSON, XML, YAML, CSV и других форматов" },
  { key: "steps", kind: "step", title: "Подготовка документов", desc: "Функции doc -> doc до приведения, выполняются по порядку сверху вниз" },
  { key: "normalize", kind: "normalize", title: "Приведение", desc: "Ключи массивов, исключения технических полей, бакеты значений", required: true },
  { key: "compare", kind: "compare", title: "Сравнение", desc: "Правила сравнения значений BEFORE и AFTER", required: true },
  { key: "segments", kind: "segments", title: "Разрезы", desc: "Слой разрезов отчета: решение, продукты, модель", required: true },
  { key: "lineage", kind: "lineage", title: "Цепочка этапов решения", desc: "Этапы до и после продукта: чтобы отличить изменение логики от изменения входных данных" },
  { key: "checks", kind: "check", title: "Автопроверки", desc: "Проверки витрины после сравнения, каждая дает PASS или FAIL" },
  { key: "ai_prompt", kind: "ai_prompt", title: "Мнение ИИ", desc: "Тексты запросов к языковой модели-рецензенту" },
];

const ids = (g: Group, sel: Selection): string[] => {
  const v = sel[g.key];
  return Array.isArray(v) ? v : v ? [v] : [];
};

export const stepCount = (sel: Selection) => GROUPS.reduce((n, g) => n + ids(g, sel).length, 0);
export const missing = (sel: Selection) => GROUPS.filter((g) => g.required && !sel[g.key]).map((g) => g.title);

/** The user's own version of a shipped procedure when they saved one ("моя версия" replaces the file in new runs). */
export function mine(procs: Procedure[], id: string) {
  const p = procs.find((x) => x.id === id);
  return (p?.overridden && procs.find((x) => x.origin === "workspace" && x.kind === p.kind && x.cid === p.cid)?.id) || id;
}

function demo(procs: Procedure[]): Selection {
  const file = (kind: string) => procs.filter((p) => p.kind === kind && p.origin === "file").map((p) => mine(procs, p.id));
  return {
    parser: null, normalize: file("normalize")[0], compare: file("compare")[0], segments: file("segments")[0],
    lineage: file("lineage")[0] ?? null, ai_prompt: file("ai_prompt")[0] ?? null, steps: file("step"), checks: file("check"),
  };
}

export function ConfigsPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const configs = useQuery({ queryKey: ["configs"], queryFn: () => api.get<Config[]>("/api/configs") });
  const procs = useQuery({ queryKey: ["procedures"], queryFn: () => api.get<Procedure[]>("/api/procedures") });
  const create = useMutation({
    mutationFn: (from: "empty" | "demo") =>
      api.post<Config>("/api/configs", from === "empty"
        ? { name: "Новая конфигурация", procedures: { steps: [], checks: [] } }
        : { name: "СПР кредиты", procedures: demo(procs.data ?? []) }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["configs"] });
      nav({ to: "/configs/$configId", params: { configId: c.id }, search: {} });
    },
  });
  if (configs.isLoading) return <Skeleton rows={6} />;
  if (configs.error) return <ErrorBox error={configs.error} />;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h1 className={titleCls}>Конфигурации</h1>
          <p className="mt-1 text-xs text-muted">Готовый набор шагов и автопроверок для одного процесса. Настройте один раз, потом запускайте на любых датасетах.</p>
        </div>
        <Button onClick={() => create.mutate("demo")} disabled={create.isPending || !procs.data}>Из готовой: СПР кредиты</Button>
        <Button variant="primary" onClick={() => create.mutate("empty")} disabled={create.isPending}>+ Новая конфигурация</Button>
      </div>
      {create.error && <ErrorBox error={create.error} />}
      <Card>
        {configs.data?.length ? (
          <ul className="divide-y divide-line">
            {configs.data.map((c) => {
              const gaps = missing(c.procedure_ids);
              return (
                <li key={c.id}>
                  <Link to="/configs/$configId" params={{ configId: c.id }} search={{}} className="flex items-center gap-4 px-5 py-3.5 hover:bg-surface-2">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[14px] font-medium">{c.name}</span>
                      <span className="block truncate text-xs text-muted">{c.dataset ?? "датасет выбирается при запуске"} · изменена {dt(c.updated_at)}</span>
                    </span>
                    {gaps.length > 0 && <Badge tone="warn" title={`Не выбрано: ${gaps.join(", ")}`}>черновик</Badge>}
                    <span className="num shrink-0 text-xs text-muted">{plural(stepCount(c.procedure_ids), "шаг", "шага", "шагов")}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty title="Конфигураций пока нет">Создайте новую с нуля или возьмите готовую "СПР кредиты" как образец.</Empty>
        )}
      </Card>
    </div>
  );
}

function StepRow({ no, proc, id, configId, params, onParams, onValid, onUp, onDown, onRemove, leave }: {
  no: number; proc?: Procedure; id: string; configId: string; params?: Record<string, unknown>;
  onParams: (v: Record<string, unknown> | undefined) => void; onValid: (ok: boolean) => void;
  onUp?: () => void; onDown?: () => void; onRemove: () => void; leave: (proc: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="flex gap-3 py-2.5">
      <span className="num mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-medium">{no}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{proc?.name ?? "процедура удалена"}</span>
          {proc && <Badge>{proc.origin === "file" ? "файл" : "моя версия"}</Badge>}
          {proc && (
            <button className="text-xs text-muted hover:text-accent" aria-expanded={open} onClick={() => { if (open) onValid(true); setOpen(!open); }}>
              {params ? "параметры изменены" : "параметры"}
            </button>
          )}
          <button className="text-xs text-accent hover:underline" onClick={() => leave(id)}>код</button>
          {(onUp || onDown) && (
            <>
              <Button size="sm" variant="ghost" aria-label="Выше" disabled={!onUp} onClick={onUp}>↑</Button>
              <Button size="sm" variant="ghost" aria-label="Ниже" disabled={!onDown} onClick={onDown}>↓</Button>
            </>
          )}
          <Button size="sm" variant="ghost" aria-label="Убрать шаг" onClick={onRemove}>×</Button>
        </div>
        {proc?.description && <p className="mt-0.5 text-xs text-muted">{proc.description}</p>}
        {proc && open && (
          <div className="mt-1.5">
            <ParamsEditor key={id + configId} open id={id} name="Параметры" value={params} onChange={onParams} onValid={onValid} />
          </div>
        )}
      </div>
    </li>
  );
}

function Picker({ group, items, taken, onPick, onNew, onClose }: { group: Group; items: Procedure[]; taken: string[]; onPick: (id: string) => void; onNew: () => void; onClose: () => void }) {
  const free = items.filter((p) => !taken.includes(p.id) && !p.overridden);
  return (
    <div className="mt-2 rounded-md border border-line bg-surface-2/50 p-2">
      <div className="flex items-center gap-2 px-2 pb-1.5">
        <span className="flex-1 text-xs font-medium text-muted">Добавить шаг: {group.title.toLowerCase()}</span>
        <Button size="sm" variant="ghost" onClick={onClose}>Отмена</Button>
      </div>
      <ul className="max-h-72 overflow-y-auto">
        {free.map((p) => (
          <li key={p.id}>
            <button className="w-full rounded px-2 py-1.5 text-left hover:bg-surface" onClick={() => onPick(p.id)}>
              <span className="flex items-center gap-2 text-[13px]">
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                <Badge>{p.origin === "file" ? "файл" : "моя версия"}</Badge>
              </span>
              {p.description && <span className="block text-xs text-muted">{p.description}</span>}
            </button>
          </li>
        ))}
        {!free.length && <li className="px-2 py-1.5 text-xs text-muted">Готовых процедур этого вида больше нет</li>}
      </ul>
      <button className="mt-1 w-full rounded px-2 py-1.5 text-left text-[13px] text-accent hover:bg-surface" onClick={onNew}>+ Написать новый шаг в рабочей области</button>
    </div>
  );
}

export function ConfigEditorPage() {
  const { configId } = useParams({ strict: false }) as { configId: string };
  const { add } = useSearch({ strict: false }) as { add?: string };
  const nav = useNavigate();
  const qc = useQueryClient();
  const configs = useQuery({ queryKey: ["configs"], queryFn: () => api.get<Config[]>("/api/configs") });
  const procs = useQuery({ queryKey: ["procedures"], queryFn: () => api.get<Procedure[]>("/api/procedures") });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.get<Dataset[]>("/api/datasets") });
  const cfg = configs.data?.find((c) => c.id === configId);
  const [draft, setDraft] = useState<{ name: string; dataset: string | null; sel: Selection; params: Params } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [picking, setPicking] = useState<string | null>(null);
  const [invalid, setInvalid] = useState<string[]>([]);
  const byKind = useMemo(() => {
    const m: Record<string, Procedure[]> = {};
    for (const p of procs.data ?? []) (m[p.kind] ??= []).push(p);
    return m;
  }, [procs.data]);

  useEffect(() => {
    if (cfg && !draft) setDraft({ name: cfg.name, dataset: cfg.dataset, sel: { steps: [], ...cfg.procedure_ids }, params: (cfg.params ?? {}) as Params });
  }, [cfg, draft]);

  const update = (d: Partial<NonNullable<typeof draft>>) => {
    setDraft((cur) => cur && { ...cur, ...d });
    setDirty(true);
  };
  const put = (g: Group, list: string[]) => update({ sel: { ...draft!.sel, [g.key]: g.key === "steps" || g.key === "checks" ? list : list[0] ?? null } });

  useEffect(() => {
    if (!add || !draft || !procs.data) return;
    const p = procs.data.find((x) => x.id === add);
    const g = p && GROUPS.find((x) => x.kind === p.kind);
    if (g) {
      const list = ids(g, draft.sel);
      const same = list.findIndex((x) => x === add || (p.cid && procs.data!.find((y) => y.id === x)?.cid === p.cid));
      put(g, g.key !== "steps" && g.key !== "checks" ? [add] : same < 0 ? [...list, add] : list.map((x, i) => (i === same ? add : x)));
    }
    nav({ to: "/configs/$configId", params: { configId }, search: {}, replace: true });
  }, [add, draft, procs.data]);

  const chosen = draft ? GROUPS.flatMap((g) => ids(g, draft.sel)) : [];
  const save = useMutation({
    mutationFn: () => {
      const params = Object.fromEntries(Object.entries(draft!.params).filter(([k]) => chosen.includes(k) || k === "sample_apps"));
      return api.put<Config>(`/api/configs/${configId}`, { name: draft!.name.trim() || "Без названия", dataset: draft!.dataset, procedures: draft!.sel, params });
    },
    onSuccess: () => {
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["configs"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/configs/${configId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["configs"] });
      nav({ to: "/configs" });
    },
  });
  const leave = async (search: { proc?: string; kind?: string }) => {
    if (dirty) await save.mutateAsync();
    nav({ to: "/workspace", search: { ...search, config: configId } });
  };

  if (configs.isLoading || procs.isLoading) return <Skeleton rows={8} />;
  if (configs.error || procs.error) return <ErrorBox error={configs.error ?? procs.error} />;
  if (!cfg) return <Empty title="Конфигурация не найдена">Возможно, ее удалили. <Link to="/configs" className="text-accent hover:underline">Все конфигурации</Link></Empty>;
  if (!draft) return <Skeleton rows={8} />;
  const blocked = invalid.some((k) => chosen.includes(k));
  const gaps = missing(draft.sel);
  let no = 0;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <div className="flex flex-col gap-3">
        <Link to="/configs" className="text-xs text-muted hover:text-fg">Конфигурации /</Link>
        <input
          className="w-full rounded-md border border-transparent bg-transparent px-1 font-display text-[24px] leading-tight tracking-tight outline-none hover:border-line focus:border-accent"
          value={draft.name}
          onChange={(e) => update({ name: e.target.value })}
          aria-label="Название конфигурации"
          maxLength={200}
        />
        <div className="flex flex-wrap items-center gap-2">
          <select className={inputCls + " w-72"} value={draft.dataset ?? ""} onChange={(e) => update({ dataset: e.target.value || null })} aria-label="Датасет по умолчанию">
            <option value="">датасет выбирается при запуске</option>
            {(datasets.data ?? []).map((d) => <option key={d.path} value={d.path}>{d.path}</option>)}
          </select>
          <span className="ml-auto" />
          <Button variant="ghost" onClick={() => confirm(`Удалить конфигурацию "${draft.name}"? Прогоны останутся.`) && remove.mutate()} disabled={remove.isPending}>Удалить</Button>
          <Button onClick={() => save.mutate()} disabled={!dirty || blocked || save.isPending}>{save.isPending ? "Сохранение…" : dirty ? "Сохранить" : "Сохранено"}</Button>
          <Button variant="primary" disabled={dirty || gaps.length > 0} title={gaps.length ? `Не выбрано: ${gaps.join(", ")}` : dirty ? "Сначала сохраните изменения" : undefined} onClick={() => nav({ to: "/runs/new", search: { config: configId } })}>
            Запустить ▶
          </Button>
        </div>
        {blocked && <p className="text-xs text-fail">Исправьте JSON параметров, чтобы сохранить</p>}
        {(save.error || remove.error) && <ErrorBox inline error={save.error ?? remove.error} />}
      </div>
      <Card>
        <ol className="divide-y divide-line">
          {GROUPS.map((g) => {
            const list = ids(g, draft.sel);
            const multi = g.key === "steps" || g.key === "checks";
            return (
              <li key={g.key} className="px-5 py-4">
                <div className="flex items-baseline gap-2">
                  <h2 className="font-display text-[15px] font-medium">{g.title}</h2>
                  {g.required && !list.length && <Badge tone="warn">обязательный этап</Badge>}
                </div>
                <p className="text-xs text-muted">{g.desc}</p>
                {list.length > 0 && (
                  <ul>
                    {list.map((id, i) => (
                      <StepRow
                        key={id}
                        no={++no}
                        id={id}
                        configId={configId}
                        proc={procs.data?.find((p) => p.id === id)}
                        params={draft.params[id]}
                        onParams={(v) => { const params = { ...draft.params }; if (v) params[id] = v; else delete params[id]; update({ params }); }}
                        onValid={(ok) => setInvalid((cur) => (ok ? cur.filter((x) => x !== id) : [...new Set([...cur, id])]))}
                        onUp={multi && i > 0 ? () => { const v = [...list]; [v[i - 1], v[i]] = [v[i], v[i - 1]]; put(g, v); } : undefined}
                        onDown={multi && i < list.length - 1 ? () => { const v = [...list]; [v[i + 1], v[i]] = [v[i], v[i + 1]]; put(g, v); } : undefined}
                        onRemove={() => put(g, list.filter((x) => x !== id))}
                        leave={(proc) => leave({ proc })}
                      />
                    ))}
                  </ul>
                )}
                {picking === g.key ? (
                  <Picker
                    group={g}
                    items={byKind[g.kind] ?? []}
                    taken={list}
                    onPick={(id) => { put(g, multi ? [...list, id] : [id]); setPicking(null); }}
                    onNew={() => leave({ kind: g.kind })}
                    onClose={() => setPicking(null)}
                  />
                ) : (
                  (multi || !list.length) && (
                    <button className="mt-2 text-xs text-accent hover:underline" onClick={() => setPicking(g.key)}>+ Добавить шаг</button>
                  )
                )}
              </li>
            );
          })}
        </ol>
      </Card>
    </div>
  );
}
