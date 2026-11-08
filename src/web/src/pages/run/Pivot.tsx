import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { Badge, Button, Card, CardHeader, ChangeGlyph, ErrorBox, Filter, Skeleton, cx, inputCls, linkCls } from "../../components/ui";
import { api, type Fields, type Pivot, type PivotLayout, type PivotTemplate, type Scope } from "../../lib/api";
import { n, pct, plural } from "../../lib/format";
import { useScopeNav, type RunSearch } from "../Run";

type Mode = NonNullable<PivotLayout["mode"]>;
type Zone = "rows" | "cols" | "filters";
type Tpl = { name: string; layout: PivotLayout; hint?: string };

const BUILTIN: Tpl[] = [
  { name: "Значения: было и стало", hint: "Какие значения были и какие стали у изменившихся атрибутов", layout: { rows: ["PATH", "ADJUSTED_VALUE"], cols: ["SOURCE"], filters: { DIFF_FLAG: ["1"] } } },
  { name: "Было, стало, без изменений", hint: "Сколько было, сколько стало и сколько не изменилось по каждому значению", layout: { rows: ["PATH", "ADJUSTED_VALUE"], cols: ["DIFF_FLAG", "SOURCE"], filters: {} } },
  { name: "Атрибут × тип изменения", layout: { rows: ["PATH"], cols: ["CHANGE_TYPE"], filters: { DIFF_FLAG: ["1"] } } },
  { name: "Атрибут × кластер", layout: { rows: ["PATH"], cols: ["CLUSTER"], filters: { DIFF_FLAG: ["1"] } } },
  { name: "Как в прототипе", layout: { rows: ["PREFIX", "CLASS", "SUFFIX", "ARRAY_KEY", "KEY", "ADJUSTED_VALUE"], cols: ["DIFF_FLAG", "SOURCE"], filters: {} } },
];
const EMPTY: PivotLayout = { rows: [], cols: [], filters: {} };

const LABEL: Record<string, string> = {
  PATH: "Атрибут (полный путь)",
  PREFIX: "Раздел (1-й уровень)",
  CLASS: "Блок (2-й уровень)",
  SUFFIX: "Промежуточные уровни",
  KEY: "Имя поля (последний уровень)",
  ARRAY_KEY: "Элемент массива",
  ADJUSTED_VALUE: "Значение",
  SOURCE: "Было / стало",
  DIFF_FLAG: "Изменилось",
  CHANGE_TYPE: "Тип изменения",
  CLUSTER: "Кластер изменений",
};
const GROUPS: { title: string; hint: string; fields: string[] }[] = [
  { title: "Где в ответе", hint: "Положение атрибута в структуре ответа", fields: ["PATH", "PREFIX", "CLASS", "SUFFIX", "KEY", "ARRAY_KEY"] },
  { title: "Что произошло", hint: "Значение и характер изменения", fields: ["ADJUSTED_VALUE", "SOURCE", "DIFF_FLAG", "CHANGE_TYPE", "CLUSTER"] },
];
const VALUE: Record<string, Record<string, string>> = {
  SOURCE: { BEFORE: "было", AFTER: "стало" },
  DIFF_FLAG: { "0": "без изменений", "1": "изменилось" },
};
const MODES: { id: Mode; label: string }[] = [
  { id: "count", label: "число" },
  { id: "row", label: "% строки" },
  { id: "col", label: "% колонки" },
  { id: "total", label: "% всего" },
];

const label = (f: string) => LABEL[f] ?? f.replace(/_BEFORE$/, " · было").replace(/_AFTER$/, " · стало");
const valueText = (d: string, v: string) => VALUE[d]?.[v] ?? v;
const norm = (l: PivotLayout) => JSON.stringify([l.rows, l.cols, Object.entries(l.filters).filter(([, v]) => v.length).sort().map(([k, v]) => [k, [...v].sort()])]);
const sent = (l: PivotLayout): PivotLayout => ({ rows: l.rows, cols: l.cols, filters: Object.fromEntries(Object.entries(l.filters).filter(([, v]) => v.length)) });

const ORDER: Record<string, number> = { BEFORE: 0, AFTER: 1 };
const byKey = (a: string[], b: string[]) => {
  for (let i = 0; i < a.length; i++) {
    const d = (ORDER[a[i]] ?? 2) - (ORDER[b[i]] ?? 2) || a[i].localeCompare(b[i]);
    if (d) return d;
  }
  return 0;
};

function parseLayout(raw?: string): PivotLayout | null {
  try {
    const l = raw ? JSON.parse(raw) : null;
    return l && Array.isArray(l.rows) && Array.isArray(l.cols) && l.filters && typeof l.filters === "object" ? l : null;
  } catch {
    return null;
  }
}

function Header({ d, k }: { d: string; k: string }) {
  if (d === "CHANGE_TYPE") return <ChangeGlyph type={k} withLabel />;
  return <span title={VALUE[d]?.[k] ? `${d}=${k}` : undefined}>{valueText(d, k) || "∅"}</span>;
}

function Popover({ onClose, children, className }: { onClose: () => void; children: ReactNode; className?: string }) {
  return (
    <>
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div className={cx("absolute left-0 top-full z-30 mt-1 rounded-md border border-line bg-surface p-1 text-xs shadow-lg", className)}>{children}</div>
    </>
  );
}

export function PivotView({ runId, scope }: { runId: string; scope: Scope }) {
  const go = useScopeNav(runId);
  const nav = useNavigate();
  const qc = useQueryClient();
  const search = useSearch({ strict: false }) as RunSearch;
  const layout = useMemo(() => parseLayout(search.pv) ?? BUILTIN[0].layout, [search.pv]);
  const mode: Mode = layout.mode ?? "count";
  const setLayout = (l: PivotLayout) =>
    nav({ to: "/runs/$runId", params: { runId }, search: (prev: RunSearch) => ({ ...prev, pv: JSON.stringify(l) }), replace: true });

  const fields = useQuery({ queryKey: ["fields", runId], queryFn: () => api.get<Fields>(`/api/runs/${runId}/fields`) });
  const templates = useQuery({ queryKey: ["pivot-templates"], queryFn: () => api.get<PivotTemplate[]>("/api/pivot-templates") });
  const [drag, setDrag] = useState<{ name: string; from?: Zone } | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [filterOpen, setFilterOpen] = useState<string | null>(null);
  const [filterQ, setFilterQ] = useState("");
  const [q, setQ] = useState("");
  const [skipped, setSkipped] = useState<string[]>([]);
  const [saving, setSaving] = useState<{ name: string; shared: boolean } | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const [collapsed, setCollapsed] = useState(true);
  const [tplOpen, setTplOpen] = useState(false);

  const key = norm(layout);
  const [debounced, setDebounced] = useState(layout);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(layout), 150);
    return () => clearTimeout(t);
  }, [key]);
  const req = sent(debounced);
  const pivot = useQuery({
    queryKey: ["pivot", runId, req, scope],
    queryFn: async () => ({ ...(await api.post<Pivot>(`/api/runs/${runId}/pivot`, { ...req, scope })), rows: req.rows, cols: req.cols, filters: req.filters }),
    placeholderData: (p) => p,
  });

  const values = (f: string) => (fields.data ? (fields.data.fact[f] ?? fields.data.segment[f] ?? (f === "CLUSTER" ? fields.data.cluster : [])) : []);
  const groups = useMemo(() => {
    if (!fields.data) return [];
    const facts = new Set([...Object.keys(fields.data.fact), "CLUSTER"]);
    return [
      ...GROUPS.map((g) => ({ ...g, fields: g.fields.filter((f) => facts.has(f)) })),
      { title: "Кто объекты", hint: "Поля объекта из среза (настраиваются в процедуре срезов)", fields: Object.keys(fields.data.segment) },
    ];
  }, [fields.data]);
  const all = useMemo(() => new Set(groups.flatMap((g) => g.fields)), [groups]);
  const pairs = useMemo(() => {
    const seg = fields.data?.segment ?? {};
    return Object.keys(seg)
      .filter((f) => f.endsWith("_BEFORE") && seg[f.replace(/_BEFORE$/, "_AFTER")] && seg[f].length <= 30)
      .map((f): Tpl => {
        const base = f.replace(/_BEFORE$/, "");
        return { name: `${base}: было → стало`, hint: `Строки - ${f}, колонки - ${base}_AFTER`, layout: { rows: [f], cols: [`${base}_AFTER`], filters: {} } };
      });
  }, [fields.data]);

  const where = (f: string): Zone | null => (layout.rows.includes(f) ? "rows" : layout.cols.includes(f) ? "cols" : f in layout.filters ? "filters" : null);
  const place = (f: string, zone: Zone | null, before?: string) => {
    const next: PivotLayout = { ...layout, rows: layout.rows.filter((x) => x !== f), cols: layout.cols.filter((x) => x !== f), filters: { ...layout.filters } };
    const keepFilter = zone === "filters" && f in layout.filters;
    if (!keepFilter) delete next.filters[f];
    if (zone === "filters") next.filters[f] = layout.filters[f] ?? [];
    else if (zone) {
      const list = [...next[zone]];
      const i = before ? list.indexOf(before) : -1;
      list.splice(i < 0 ? list.length : i, 0, f);
      next[zone] = list;
    }
    setLayout(next);
  };
  const drop = (zone: Zone, before?: string) => {
    if (drag && drag.name !== before) place(drag.name, zone, before);
    setDrag(null);
  };
  const apply = (l: PivotLayout) => {
    if (!fields.data) return setLayout({ ...l, mode: l.mode ?? mode });
    const missing = [...l.rows, ...l.cols, ...Object.keys(l.filters)].filter((f) => !all.has(f));
    setSkipped(missing);
    setLayout({ ...l, mode: l.mode ?? mode, rows: l.rows.filter((f) => all.has(f)), cols: l.cols.filter((f) => all.has(f)), filters: Object.fromEntries(Object.entries(l.filters).filter(([f]) => all.has(f))) });
  };
  const setFilter = (f: string, vals: string[]) => setLayout({ ...layout, filters: { ...layout.filters, [f]: vals } });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["pivot-templates"] });
  const save = useMutation({
    mutationFn: (b: { name: string; shared: boolean }) => api.post<PivotTemplate>("/api/pivot-templates", { ...b, layout: { ...sent(layout), mode } }),
    onSuccess: () => (setSaving(null), invalidate()),
  });
  const rename = useMutation({
    mutationFn: (t: PivotTemplate) => api.put<PivotTemplate>(`/api/pivot-templates/${t.id}`, t),
    onSuccess: () => (setRenaming(null), invalidate()),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/api/pivot-templates/${id}`), onSuccess: invalidate });

  const table = useMemo(() => {
    const p = pivot.data;
    if (!p) return null;
    const nr = p.rows.length;
    const rowKeys = new Map<string, string[]>();
    const colKeys = new Map<string, string[]>();
    const cell = new Map<string, number>();
    for (const c of p.cells) {
      const r = c.slice(0, nr).map(String);
      const k = c.slice(nr, -1).map(String);
      rowKeys.set(r.join("\u0000"), r);
      colKeys.set(k.join("\u0000"), k);
      cell.set(r.join("\u0000") + "\u0001" + k.join("\u0000"), Number(c[c.length - 1]));
    }
    const total = (t?: (string | number)[][]) => (t ? new Map(t.map((x) => [x.slice(0, -1).map(String).join("\u0000"), Number(x[x.length - 1])])) : null);
    return {
      rows: [...rowKeys.values()].sort(byKey), cols: [...colKeys.values()].sort(byKey), cell, rowDims: p.rows, colDims: p.cols, filters: p.filters,
      rowTotal: total(p.row_totals), colTotal: total(p.col_totals), total: p.total, truncated: p.truncated,
    };
  }, [pivot.data]);

  const filterText = (fl: Record<string, string[]>) => Object.entries(fl).map(([k, v]) => `${label(k)} = ${v.map((x) => valueText(k, x)).join(" или ")}`).join("; ");
  const describe = (r: string[], c: string[]) => {
    const t = table!;
    const parts = [...t.rowDims.map((d, i) => r.length && `${label(d)} = ${valueText(d, r[i]) || "∅"}`), ...t.colDims.map((d, i) => c.length && `${label(d)} = ${valueText(d, c[i])}`)].filter(Boolean);
    return (parts.length ? parts.join(", ") : "вся таблица") + (Object.keys(t.filters).length ? ` при фильтрах: ${filterText(t.filters)}` : "");
  };
  const openCell = (r: string[], c: string[]) => {
    const t = table!;
    const spec: Record<string, string> = {};
    t.rowDims.forEach((d, i) => r.length && (spec[d] = r[i]));
    t.colDims.forEach((d, i) => c.length && (spec[d] = c[i]));
    const clusterVal = spec.CLUSTER;
    delete spec.CLUSTER;
    const sc = clusterVal && clusterVal !== "none" ? { ...scope, cluster: Number(clusterVal) } : scope;
    go(sc, "apps", { cell: spec, filters: t.filters, title: describe(r, c) });
  };
  const num = (v: number | undefined, r: string[], c: string[], base?: number, strong?: boolean) =>
    v ? (
      <button onClick={() => openCell(r, c)} title={`${plural(v, "заявка", "заявки", "заявок")}: ${describe(r, c)}. Клик откроет список`} className={cx(linkCls, strong && "font-semibold")}>
        {n(v)}
        {mode !== "count" && base ? <span className="ml-1 text-[11px] text-muted">{pct(v, base)}</span> : null}
      </button>
    ) : (
      <span className="text-line-strong">·</span>
    );
  const base = (r: string[], c: string[]) =>
    mode === "row" ? table!.rowTotal?.get(r.join("\u0000")) : mode === "col" ? table!.colTotal?.get(c.join("\u0000")) : mode === "total" ? table!.total : undefined;

  const zones: { id: Zone; label: string; items: string[]; empty: string }[] = [
    { id: "rows", label: "Строки", items: layout.rows, empty: "поле - строки таблицы" },
    { id: "cols", label: "Колонки", items: layout.cols, empty: "поле - колонки таблицы" },
    { id: "filters", label: "Фильтры", items: Object.keys(layout.filters), empty: "поле - отбор значений" },
  ];
  const active = [...BUILTIN, ...pairs, ...(templates.data ?? [])].find((t) => norm(t.layout) === key)?.name;
  const isEmpty = !layout.rows.length && !layout.cols.length;
  const ql = q.trim().toLowerCase();
  const maxRows = collapsed ? 400 : 5000;
  const sec = "px-2 pb-0.5 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted";

  const tplButton = (t: Tpl, extra?: ReactNode) => (
    <div key={t.name} className={cx("group flex items-center gap-1 rounded-md px-2 py-1 text-xs", active === t.name ? "bg-accent-bg text-accent" : "hover:bg-surface-2")}>
      <button className="min-w-0 flex-1 truncate text-left" title={t.hint ?? t.name} onClick={() => (apply(t.layout), setTplOpen(false))}>{t.name}</button>
      {extra}
    </div>
  );

  return (
    <div className="grid gap-4 xl:grid-cols-[260px_1fr]">
      <Card className="h-fit">
        <div className="border-b border-line p-3">
          <input className={cx(inputCls, "h-8 w-full")} placeholder="Найти поле" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск поля" />
        </div>
        <div className="max-h-[75vh] overflow-y-auto p-2">
          {fields.isLoading && <Skeleton rows={3} />}
          {fields.error && <ErrorBox inline error={fields.error} />}
          {groups.map((g) => {
            const list = g.fields.filter((f) => !ql || f.toLowerCase().includes(ql) || label(f).toLowerCase().includes(ql));
            if (!list.length) return null;
            return (
              <div key={g.title} className="mb-2">
                <p className="px-1 pb-1 text-[11px] font-medium uppercase tracking-wide text-muted" title={g.hint}>{g.title}</p>
                {list.map((f) => {
                  const z = where(f);
                  return (
                    <div key={f} className="relative">
                      <button
                        draggable
                        onDragStart={() => setDrag({ name: f, from: z ?? undefined })}
                        onDragEnd={() => setDrag(null)}
                        onClick={() => setMenu(menu === f ? null : f)}
                        title={`${f}${values(f).length ? ` · ${plural(values(f).length, "значение", "значения", "значений")}` : ""}`}
                        className={cx("flex w-full cursor-grab items-center justify-between gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-surface-2", z && "text-muted")}
                      >
                        <span className="truncate">{label(f)}</span>
                        {z && <span className="shrink-0 text-[10px] text-accent">{zones.find((x) => x.id === z)!.label.toLowerCase()}</span>}
                      </button>
                      {menu === f && (
                        <div className="flex flex-wrap gap-1 px-1.5 pb-1.5 pt-0.5">
                          {zones.filter((x) => x.id !== z).map((x) => (
                            <button key={x.id} onClick={() => (place(f, x.id), setMenu(null))} className="rounded border border-line-strong px-1.5 py-0.5 text-[11px] hover:border-accent hover:text-accent">
                              в {x.label.toLowerCase()}
                            </button>
                          ))}
                          {z && <button onClick={() => (place(f, null), setMenu(null))} className="rounded px-1.5 py-0.5 text-[11px] text-fail hover:bg-fail-bg">убрать</button>}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </Card>

      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="relative">
            <button onClick={() => setTplOpen(!tplOpen)} className="rounded-md px-2 py-1 text-xs hover:bg-surface-2" aria-expanded={tplOpen}>
              <span className="text-muted">Шаблон:</span> {active ?? "своя раскладка"} ▾
            </button>
            {tplOpen && (
              <Popover onClose={() => setTplOpen(false)} className="max-h-[70vh] w-80 overflow-y-auto">
                <p className={sec}>Готовые</p>
                {BUILTIN.map((t) => tplButton(t))}
                {pairs.length > 0 && <p className={sec}>Поле объекта: было и стало</p>}
                {pairs.map((t) => tplButton(t))}
                {templates.data && templates.data.length > 0 && <p className={sec}>Сохраненные</p>}
                {templates.data?.map((t) =>
                renaming?.id === t.id ? (
                <form key={t.id} className="flex gap-1 px-1 py-0.5" onSubmit={(e) => (e.preventDefault(), rename.mutate({ ...t, name: renaming.name }))}>
                <input autoFocus className={cx(inputCls, "h-7 flex-1")} value={renaming.name} onChange={(e) => setRenaming({ ...renaming, name: e.target.value })} onKeyDown={(e) => e.key === "Escape" && setRenaming(null)} aria-label="Новое имя шаблона" />
                </form>
                ) : (
                tplButton(
                { name: t.name, layout: t.layout, hint: t.mine ? (t.shared ? "Ваш шаблон, виден всем" : "Ваш шаблон") : `Общий шаблон пользователя ${t.owner}` },
                t.mine ? (
                <span className="hidden shrink-0 gap-1 group-hover:flex">
                <button title="Переименовать" aria-label={`Переименовать ${t.name}`} onClick={() => setRenaming({ id: t.id, name: t.name })} className="text-muted hover:text-fg">✎</button>
                <button title="Удалить" aria-label={`Удалить ${t.name}`} onClick={() => confirm(`Удалить шаблон "${t.name}"?`) && remove.mutate(t.id)} className="text-muted hover:text-fail">×</button>
                </span>
                ) : (
                <span className="shrink-0 text-[10px] text-muted">{t.owner}</span>
                ),
                )
                ),
                )}
                {(rename.error || remove.error) && <ErrorBox inline error={rename.error || remove.error} />}
              </Popover>
            )}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-1">
            <Button size="sm" variant="ghost" title="Поменять строки и колонки местами" disabled={layout.rows.length > 4} onClick={() => setLayout({ ...layout, rows: layout.cols, cols: layout.rows })}>⇄ Поменять</Button>
            <Button size="sm" variant="ghost" title="Убрать все поля и собрать раскладку заново" onClick={() => (setSkipped([]), setLayout({ ...EMPTY, mode }))}>Очистить</Button>
            <Button size="sm" variant="ghost" title={`Вернуть шаблон "${BUILTIN[0].name}"`} onClick={() => (setSkipped([]), setLayout(BUILTIN[0].layout))}>Сбросить</Button>
            <Button size="sm" variant="secondary" disabled={isEmpty} onClick={() => setSaving(saving ? null : { name: "", shared: false })}>Сохранить как шаблон</Button>
          </div>
        </div>
        {saving && (
          <form className="flex flex-wrap items-center gap-2 rounded-lg bg-surface p-2" onSubmit={(e) => (e.preventDefault(), saving.name.trim() && save.mutate(saving))}>
            <input autoFocus className={cx(inputCls, "h-8 w-72")} placeholder="Название шаблона" maxLength={120} value={saving.name} onChange={(e) => setSaving({ ...saving, name: e.target.value })} aria-label="Название шаблона" />
            <label className="flex items-center gap-1.5 text-xs">
              <input type="checkbox" checked={saving.shared} onChange={(e) => setSaving({ ...saving, shared: e.target.checked })} />
              общий для всех
            </label>
            <Button size="sm" variant="primary" type="submit" disabled={!saving.name.trim() || save.isPending}>Сохранить</Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setSaving(null)}>Отмена</Button>
            {save.error && <ErrorBox inline error={save.error} />}
          </form>
        )}
        {skipped.length > 0 && (
          <p className="rounded-md bg-warn-bg px-3 py-1.5 text-xs">
            В этом прогоне нет полей {skipped.join(", ")} - они пропущены.{" "}
            <button className="underline" onClick={() => setSkipped([])}>Понятно</button>
          </p>
        )}
        <div className="grid gap-2 md:grid-cols-3">
          {zones.map((z) => (
            <div
              key={z.id}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => drop(z.id)}
              className={cx("min-h-14 rounded-lg border border-dashed bg-surface p-2 transition-colors", drag ? "border-accent bg-accent-bg/40" : "border-line-strong")}
              aria-label={`Зона ${z.label}`}
            >
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{z.label}</div>
              <div className="flex flex-wrap gap-1">
                {z.items.map((f) => {
                  const sel = layout.filters[f] ?? [];
                  const vals = values(f);
                  const shown = vals.filter((v) => !filterQ || v.toLowerCase().includes(filterQ.toLowerCase()));
                  return (
                    <span key={f} className="relative" onDragOver={(e) => e.preventDefault()} onDrop={(e) => (e.stopPropagation(), drop(z.id, f))}>
                      <span
                        draggable
                        onDragStart={() => setDrag({ name: f, from: z.id })}
                        onDragEnd={() => setDrag(null)}
                        title={`${f} - перетащите, чтобы переставить`}
                        className="inline-flex cursor-grab items-center gap-1 rounded border border-line-strong bg-surface px-1.5 py-0.5 text-xs active:cursor-grabbing"
                      >
                        {z.id === "filters" ? (
                          <button onClick={() => (setFilterQ(""), setFilterOpen(filterOpen === f ? null : f))} className={cx(sel.length > 0 && "text-accent")}>
                            {label(f)}: {sel.length === 1 ? valueText(f, sel[0]) || "∅" : sel.length ? `${sel.length} из ${vals.length}` : "все"} ▾
                          </button>
                        ) : (
                          label(f)
                        )}
                        <button onClick={() => place(f, null)} aria-label={`Убрать ${f}`} className="text-muted hover:text-fail">×</button>
                      </span>
                      {filterOpen === f && (
                        <Popover onClose={() => setFilterOpen(null)} className="w-64">
                          {vals.length > 10 && <input autoFocus className={cx(inputCls, "mb-1 h-7 w-full")} placeholder="Найти значение" value={filterQ} onChange={(e) => setFilterQ(e.target.value)} />}
                          <div className="flex gap-3 px-1 pb-1 text-accent">
                            <button onClick={() => setFilter(f, [...new Set([...sel, ...shown])])}>выбрать {filterQ ? "найденные" : "все"}</button>
                            <button onClick={() => setFilter(f, [])}>снять отбор</button>
                          </div>
                          <div className="max-h-60 overflow-y-auto">
                            {shown.map((v) => (
                              <label key={v} className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-surface-2">
                                <input type="checkbox" checked={sel.includes(v)} onChange={(e) => setFilter(f, e.target.checked ? [...sel, v] : sel.filter((x) => x !== v))} />
                                <span className="truncate font-mono" title={v}>{valueText(f, v) || "∅"}</span>
                              </label>
                            ))}
                          </div>
                        </Popover>
                      )}
                    </span>
                  );
                })}
                {!z.items.length && <span className="text-xs text-muted">{z.empty}</span>}
              </div>
            </div>
          ))}
        </div>

        <Card>
          <CardHeader
            title="Сводная таблица"
            hint="В ячейке - число различных заявок, а не сумма строк. Итого строки или колонки считает каждую заявку один раз, поэтому оно может быть меньше суммы ячеек. Наведите на число, чтобы увидеть, что откроется по клику."
            actions={
              <>
                <Filter value={mode} onChange={(m) => setLayout({ ...layout, mode: m })} items={MODES} />
                {pivot.data && <Badge>{pivot.data.ms} мс{pivot.isFetching ? " · обновление…" : ""}</Badge>}
              </>
            }
          />
          {table && Object.keys(table.filters).length > 0 && !isEmpty && <p className="border-b border-line px-5 py-1.5 text-xs text-muted">Учтены фильтры: {filterText(table.filters)}</p>}
          {pivot.error && <ErrorBox error={pivot.error} />}
          {!table && !pivot.error && <Skeleton rows={8} />}
          {table && isEmpty && (
            <div className="px-6 py-10 text-center text-[13px]">
              <p className="font-medium">Раскладка пуста</p>
              <p className="mx-auto mt-1 max-w-md text-xs text-muted">
                Нажмите на поле слева и выберите "в строки" или "в колонки", либо перетащите его в зону. Сейчас в отборе {plural(table.total ?? 0, "заявка", "заявки", "заявок")}.
              </p>
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {BUILTIN.slice(0, 2).concat(pairs.slice(0, 1)).map((t) => (
                  <Button key={t.name} size="sm" onClick={() => apply(t.layout)}>{t.name}</Button>
                ))}
              </div>
            </div>
          )}
          {table && !isEmpty && !table.rows.length && !pivot.error && <p className="px-5 py-8 text-center text-xs text-muted">В этом отборе нет заявок. Ослабьте фильтры.</p>}
          {table && !isEmpty && table.rows.length > 0 && (
            <div className={cx("max-h-[70vh] overflow-auto", pivot.isFetching && "opacity-60 transition-opacity")}>
              <table className="w-full border-collapse text-xs">
                <thead className="sticky top-0 z-10 bg-surface">
                  <tr>
                    {table.rowDims.map((d, i) => (
                      <th key={d} title={d} className={cx("border-b border-line bg-surface px-2 py-1.5 text-left text-xs font-medium text-muted", i === 0 && "sticky left-0 z-10")}>{label(d)}</th>
                    ))}
                    {table.cols.map((c) => (
                      <th key={c.join()} className="border-b border-l border-line px-2 py-1.5 text-right text-xs font-medium">
                        {c.length ? c.map((x, i) => <Fragment key={i}>{i > 0 && " · "}<Header d={table.colDims[i]} k={x} /></Fragment>) : "заявок"}
                      </th>
                    ))}
                    {table.cols.length > 1 && <th className="border-b border-l border-line-strong px-2 py-1.5 text-right text-xs font-semibold" title="Различные заявки строки по всем колонкам: каждая заявка один раз">Итого</th>}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.slice(0, maxRows).map((r, ri) => (
                    <tr key={r.join("\u0000")} className="group">
                      {r.map((v, i) => {
                        const same = ri > 0 && r.slice(0, i + 1).join("\u0000") === table.rows[ri - 1].slice(0, i + 1).join("\u0000");
                        return (
                          <td key={i} className={cx("max-w-72 truncate border-b border-line bg-surface px-2 py-1 font-mono text-xs group-hover:bg-surface-2", same && "text-transparent", i === 0 && "sticky left-0")} title={v}>
                            {valueText(table.rowDims[i], v) || <span className="text-muted">∅</span>}
                          </td>
                        );
                      })}
                      {table.cols.map((c) => {
                        const v = table.cell.get(r.join("\u0000") + "\u0001" + c.join("\u0000"));
                        return (
                          <td key={c.join()} className="num border-b border-l border-line px-2 py-1 text-right group-hover:bg-surface-2">
                            {num(v, r, c, v ? base(r, c) : undefined)}
                          </td>
                        );
                      })}
                      {table.cols.length > 1 && (
                        <td className="num border-b border-l border-line-strong px-2 py-1 text-right group-hover:bg-surface-2">
                          {num(table.rowTotal?.get(r.join("\u0000")), r, [], mode === "total" || mode === "col" ? table.total : undefined, true)}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
                {table.rows.length > 1 && table.colTotal && (
                  <tfoot className="sticky bottom-0 bg-surface">
                    <tr>
                      <td colSpan={table.rowDims.length || 1} className="sticky left-0 border-t border-line-strong bg-surface px-2 py-1.5 text-xs font-semibold" title="Различные заявки колонки по всем строкам: каждая заявка один раз">Итого</td>
                      {table.cols.map((c) => (
                        <td key={c.join()} className="num border-l border-t border-line-strong px-2 py-1 text-right">
                          {num(table.colTotal!.get(c.join("\u0000")), [], c, mode === "total" || mode === "row" ? table.total : undefined, true)}
                        </td>
                      ))}
                      {table.cols.length > 1 && (
                        <td className="num border-l border-t border-line-strong px-2 py-1 text-right">
                          {num(table.total, [], [], undefined, true)}
                        </td>
                      )}
                    </tr>
                  </tfoot>
                )}
              </table>
              {table.truncated && <p className="px-5 py-2 text-xs text-warn">Слишком много сочетаний: показана часть таблицы, итоги не считаются. Уберите поле или добавьте фильтр.</p>}
              {table.rows.length > maxRows && (
                <button className="w-full py-2 text-xs text-accent" onClick={() => setCollapsed(false)}>
                  показано {n(maxRows)} из {plural(table.rows.length, "строки", "строк", "строк")} - показать больше
                </button>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
