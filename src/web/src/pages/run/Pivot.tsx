import { useQuery } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, ChangeGlyph, ErrorBox, Skeleton, cx } from "../../components/ui";
import { api, type Fields, type Pivot, type Scope } from "../../lib/api";
import { n, plural } from "../../lib/format";
import { useScopeNav } from "../Run";

const PRESETS: { name: string; rows: string[]; cols: string[]; filters: Record<string, string[]> }[] = [
  { name: "Как в прототипе", rows: ["PREFIX", "CLASS", "SUFFIX", "ARRAY_KEY", "KEY", "ADJUSTED_VALUE"], cols: ["DIFF_FLAG", "SOURCE"], filters: {} },
  { name: "Атрибут × тип изменения", rows: ["PATH"], cols: ["CHANGE_TYPE"], filters: { DIFF_FLAG: ["1"] } },
  { name: "Атрибут × кластер", rows: ["PATH"], cols: ["CLUSTER"], filters: { DIFF_FLAG: ["1"] } },
  { name: "Значения: смещение к новым", rows: ["PATH", "ADJUSTED_VALUE"], cols: ["SOURCE"], filters: { DIFF_FLAG: ["1"] } },
];

type Zone = "rows" | "cols" | "filters";

const ORDER: Record<string, number> = { BEFORE: 0, AFTER: 1 };

const byKey = (a: string[], b: string[]) => {
  for (let i = 0; i < a.length; i++) {
    const d = (ORDER[a[i]] ?? 2) - (ORDER[b[i]] ?? 2) || a[i].localeCompare(b[i]);
    if (d) return d;
  }
  return 0;
};

function Chip({ name, onDragStart, onRemove }: { name: string; onDragStart: () => void; onRemove?: () => void }) {
  return (
    <span draggable onDragStart={onDragStart} className="inline-flex cursor-grab items-center gap-1 rounded border border-line-strong bg-surface px-1.5 py-0.5 font-mono text-[11.5px] active:cursor-grabbing">
      {name}
      {onRemove && (
        <button onClick={onRemove} aria-label={`Убрать ${name}`} className="text-muted hover:text-fail">×</button>
      )}
    </span>
  );
}

function Header({ k }: { k: string }) {
  return k in { VALUE_CHANGED: 1, ATTRIBUTE_NEW: 1, ATTRIBUTE_LOST: 1, SAME: 1 } ? <ChangeGlyph type={k} withLabel /> : <>{k}</>;
}

export function PivotView({ runId, scope }: { runId: string; scope: Scope }) {
  const go = useScopeNav(runId);
  const fields = useQuery({ queryKey: ["fields", runId], queryFn: () => api.get<Fields>(`/api/runs/${runId}/fields`) });
  const [layout, setLayout] = useState(PRESETS[1]);
  const [drag, setDrag] = useState<{ name: string; from?: Zone } | null>(null);
  const [filterOpen, setFilterOpen] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(true);
  const pivot = useQuery({
    queryKey: ["pivot", runId, layout, scope],
    queryFn: async () => ({ ...(await api.post<Pivot>(`/api/runs/${runId}/pivot`, { rows: layout.rows, cols: layout.cols, filters: layout.filters, scope })), rows: layout.rows, cols: layout.cols, filters: layout.filters }),
    placeholderData: (p) => p,
  });
  const all = useMemo(() => (fields.data ? [...Object.keys(fields.data.fact), "CLUSTER", ...Object.keys(fields.data.segment)] : []), [fields.data]);
  const values = (f: string) => (fields.data ? (fields.data.fact[f] ?? fields.data.segment[f] ?? (f === "CLUSTER" ? fields.data.cluster : [])) : []);

  const drop = (zone: Zone) => {
    if (!drag) return;
    const next = { ...layout, rows: layout.rows.filter((x) => x !== drag.name), cols: layout.cols.filter((x) => x !== drag.name), filters: { ...layout.filters } };
    if (zone === "filters") next.filters[drag.name] = next.filters[drag.name] ?? [];
    else {
      if (drag.from === "filters") delete next.filters[drag.name];
      next[zone] = [...next[zone], drag.name];
    }
    setLayout({ ...next, name: "своя раскладка" });
    setDrag(null);
  };
  const remove = (zone: Zone, f: string) => {
    const next = { ...layout, filters: { ...layout.filters } };
    if (zone === "filters") delete next.filters[f];
    else next[zone] = next[zone].filter((x) => x !== f);
    setLayout({ ...next, name: "своя раскладка" });
  };

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
    const cols = [...colKeys.values()].sort(byKey);
    const rows = [...rowKeys.values()].sort(byKey);
    const total = (t?: (string | number)[][]) => (t ? new Map(t.map((x) => [x.slice(0, -1).map(String).join("\u0000"), Number(x[x.length - 1])])) : null);
    return { rows, cols, cell, rowDims: p.rows, colDims: p.cols, filters: p.filters, rowTotal: p.cols.length ? total(p.row_totals) : null, colTotal: p.rows.length ? total(p.col_totals) : null, total: p.total };
  }, [pivot.data]);

  const openCell = (r: string[], c: string[]) => {
    const spec: Record<string, string> = {};
    table!.rowDims.forEach((d, i) => r.length && (spec[d] = r[i]));
    table!.colDims.forEach((d, i) => c.length && (spec[d] = c[i]));
    const clusterVal = spec.CLUSTER;
    delete spec.CLUSTER;
    const sc = clusterVal && clusterVal !== "none" ? { ...scope, cluster: Number(clusterVal) } : scope;
    go(sc, "apps", { cell: spec, filters: table!.filters, title: Object.entries(spec).map(([k, v]) => `${k}=${v}`).join(", ") });
  };

  const zones: { id: Zone; label: string; items: string[] }[] = [
    { id: "filters", label: "Фильтры", items: Object.keys(layout.filters) },
    { id: "rows", label: "Строки", items: layout.rows },
    { id: "cols", label: "Колонки", items: layout.cols },
  ];
  const used = new Set([...layout.rows, ...layout.cols, ...Object.keys(layout.filters)]);
  const maxRows = collapsed ? 400 : 5000;

  return (
    <div className="grid gap-4 xl:grid-cols-[260px_1fr]">
      <Card className="h-fit">
        <CardHeader title="Поля" hint="Перетащите в строки, колонки или фильтры" />
        <div className="flex flex-wrap gap-1 p-3">
          {fields.isLoading && <Skeleton rows={3} />}
          {fields.error && <ErrorBox inline error={fields.error} />}
          {all.filter((f) => !used.has(f)).map((f) => (
            <Chip key={f} name={f} onDragStart={() => setDrag({ name: f })} />
          ))}
        </div>
        <div className="border-t border-line p-3">
          <p className="mb-2 text-xs font-medium text-muted">Готовые раскладки</p>
          <div className="flex flex-col gap-1">
            {PRESETS.map((p) => (
              <Button key={p.name} size="sm" variant={layout.name === p.name ? "primary" : "secondary"} onClick={() => setLayout(p)}>
                {p.name}
              </Button>
            ))}
          </div>
        </div>
      </Card>
      <div className="flex min-w-0 flex-col gap-3">
        <div className="grid gap-2 md:grid-cols-3">
          {zones.map((z) => (
            <div
              key={z.id}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => drop(z.id)}
              className={cx("min-h-12 rounded-lg border border-dashed bg-surface p-2", drag ? "border-accent" : "border-line-strong")}
              aria-label={`Зона ${z.label}`}
            >
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">{z.label}</div>
              <div className="flex flex-wrap gap-1">
                {z.items.map((f) => (
                  <span key={f} className="relative">
                    <Chip name={f} onDragStart={() => setDrag({ name: f, from: z.id })} onRemove={() => remove(z.id, f)} />
                    {z.id === "filters" && (
                      <button className="ml-1 text-[11px] text-accent" onClick={() => setFilterOpen(filterOpen === f ? null : f)}>
                        {layout.filters[f].length ? layout.filters[f].join(", ").slice(0, 24) : "все"} ▾
                      </button>
                    )}
                    {filterOpen === f && (
                      <div className="absolute left-0 top-6 z-20 max-h-64 w-64 overflow-y-auto rounded-md border border-line bg-surface p-2 shadow-lg">
                        {values(f).map((v) => (
                          <label key={v} className="flex items-center gap-2 py-0.5 text-xs">
                            <input
                              type="checkbox"
                              checked={layout.filters[f].includes(v)}
                              onChange={(e) =>
                                setLayout({ ...layout, name: "своя раскладка", filters: { ...layout.filters, [f]: e.target.checked ? [...layout.filters[f], v] : layout.filters[f].filter((x) => x !== v) } })
                              }
                            />
                            <span className="truncate font-mono">{v}</span>
                          </label>
                        ))}
                      </div>
                    )}
                  </span>
                ))}
                {!z.items.length && <span className="text-xs text-muted">перетащите поле</span>}
              </div>
            </div>
          ))}
        </div>
        <Card>
          <CardHeader
            title="Сводная таблица"
            hint="Значения и итоги — точное число различных заявок (не сумма строк). Клик по числу открывает заявки"
            actions={pivot.data && <Badge>{plural(pivot.data.cells.length, "ячейка", "ячейки", "ячеек")} · {pivot.data.ms} мс сервер{pivot.isFetching ? " · обновление…" : ""}</Badge>}
          />
          {pivot.error && <ErrorBox error={pivot.error} />}
          {!table && !pivot.error && <Skeleton rows={8} />}
          {table && (
            <div className="max-h-[70vh] overflow-auto">
              <table className="w-full border-collapse text-[12.5px]">
                <thead className="sticky top-0 z-10 bg-surface">
                  <tr>
                    {table.rowDims.map((d) => (
                      <th key={d} className="border-b border-line px-2 py-1.5 text-left font-mono text-[11px] font-medium text-muted">{d}</th>
                    ))}
                    {table.cols.map((c) => (
                      <th key={c.join()} className={cx("border-b border-l border-line px-2 py-1.5 text-right text-[11px] font-medium", c.includes("1") && table.colDims.includes("DIFF_FLAG") && "bg-accent-bg")}>
                        {c.length ? c.map((x, i) => <Fragment key={i}>{i > 0 && " · "}<Header k={x} /></Fragment>) : "заявок"}
                      </th>
                    ))}
                    {table.rowTotal && <th className="border-b border-l border-line-strong px-2 py-1.5 text-right text-[11px] font-semibold" title="Различные заявки строки по всем колонкам">Итого</th>}
                  </tr>
                </thead>
                <tbody className={cx(pivot.isFetching && "opacity-60 transition-opacity")}>
                  {table.rows.slice(0, maxRows).map((r, ri) => (
                    <tr key={r.join("\u0000")} className="hover:bg-surface-2">
                      {r.map((v, i) => {
                        const same = ri > 0 && r.slice(0, i + 1).join("\u0000") === table.rows[ri - 1].slice(0, i + 1).join("\u0000");
                        return (
                          <td key={i} className={cx("max-w-72 truncate border-b border-line px-2 py-1 font-mono text-[11.5px]", same && "text-transparent")} title={v}>
                            {v || <span className="text-muted">∅</span>}
                          </td>
                        );
                      })}
                      {table.cols.map((c) => {
                        const v = table.cell.get(r.join("\u0000") + "\u0001" + c.join("\u0000"));
                        return (
                          <td key={c.join()} className="num border-b border-l border-line px-2 py-1 text-right">
                            {v ? (
                              <button onClick={() => openCell(r, c)} className="text-accent hover:underline">{n(v)}</button>
                            ) : (
                              <span className="text-line-strong">·</span>
                            )}
                          </td>
                        );
                      })}
                      {table.rowTotal && (
                        <td className="num border-b border-l border-line-strong px-2 py-1 text-right font-medium">
                          <button onClick={() => openCell(r, [])} className="text-accent hover:underline">{n(table.rowTotal.get(r.join("\u0000")))}</button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
                {table.colTotal && (
                  <tfoot className="sticky bottom-0 bg-surface font-medium">
                    <tr>
                      <td colSpan={table.rowDims.length} className="border-t border-line-strong px-2 py-1.5 text-xs font-semibold" title="Различные заявки колонки по всем строкам">Итого</td>
                      {table.cols.map((c) => (
                        <td key={c.join()} className="num border-l border-t border-line-strong px-2 py-1 text-right">
                          <button onClick={() => openCell([], c)} className="text-accent hover:underline">{n(table.colTotal!.get(c.join("\u0000")))}</button>
                        </td>
                      ))}
                      {table.rowTotal && (
                        <td className="num border-l border-t border-line-strong px-2 py-1 text-right font-semibold">
                          <button onClick={() => openCell([], [])} className="text-accent hover:underline">{n(table.total)}</button>
                        </td>
                      )}
                    </tr>
                  </tfoot>
                )}
              </table>
              {table.rows.length > maxRows && (
                <button className="w-full py-2 text-xs text-accent" onClick={() => setCollapsed(false)}>
                  показано {n(maxRows)} из {plural(table.rows.length, "строки", "строк", "строк")} — показать больше
                </button>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
