import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Badge, Button, ErrorBox, Skeleton, Tabs, inputCls } from "../components/ui";
import { api, type Dataset, type Run, type Scope } from "../lib/api";
import { useRunEvents } from "../lib/events";
import { plural } from "../lib/format";
import { StateBadge } from "./Runs";
import { AppList } from "./run/AppList";
import { ChecksView } from "./run/Checks";
import { JournalView } from "./run/Journal";
import { Overview } from "./run/Overview";
import { PivotView } from "./run/Pivot";
import { Progress } from "./run/Progress";
import { WhatIf } from "./run/WhatIf";

export type RunSearch = { tab?: "overview" | "pivot" | "whatif" | "checks" | "apps" | "journal"; scope?: string; drill?: string };

export function parseScope(raw?: string): Scope {
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function useScopeNav(runId: string) {
  const nav = useNavigate();
  return (scope: Scope, tab?: RunSearch["tab"], drill?: Drill) =>
    nav({
      to: "/runs/$runId",
      params: { runId },
      search: (prev: RunSearch) => ({
        tab: tab ?? prev.tab,
        scope: Object.values(scope).some((v) => v !== undefined) ? JSON.stringify(scope) : undefined,
        drill: drill ? JSON.stringify(drill) : undefined,
      }),
    });
}

export type Drill = { cell?: Record<string, string>; path?: string; filters?: Record<string, string[]>; transition?: { path_id: number; before: string; after: string }; title?: string };

export function parseDrill(raw?: string): Drill | undefined {
  try {
    return raw ? JSON.parse(raw) : undefined;
  } catch {
    return undefined;
  }
}

function ScopeBar({ runId, name, scope }: { runId: string; name: string; scope: Scope }) {
  const go = useScopeNav(runId);
  const chips: { label: string; drop: () => void }[] = [];
  for (const [f, vals] of Object.entries(scope.seg ?? {}))
    chips.push({
      label: `${f} = ${vals.join(" | ")}`,
      drop: () => {
        const seg = { ...scope.seg };
        delete seg[f];
        go({ ...scope, seg: Object.keys(seg).length ? seg : undefined });
      },
    });
  if (scope.cluster) chips.push({ label: `кластер №${scope.cluster}`, drop: () => go({ ...scope, cluster: undefined }) });
  if (scope.apps) chips.push({ label: plural(scope.apps.length, "выбранная заявка", "выбранные заявки", "выбранных заявок"), drop: () => go({ ...scope, apps: undefined }) });
  if (scope.check) chips.push({ label: `${plural(scope.check.cnt, "заявка", "заявки", "заявок")} строки проверки`, drop: () => go({ ...scope, check: undefined }) });
  if (scope.cause) chips.push({ label: `причина смены решения: ${scope.cause.label}`, drop: () => go({ ...scope, cause: undefined }) });
  return (
    <nav aria-label="Текущий срез" className="flex flex-wrap items-center gap-1.5 text-xs">
      <button onClick={() => go({})} className="font-medium text-accent hover:underline">
        {name}: вся популяция
      </button>
      {chips.map((c) => (
        <span key={c.label} className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent-bg px-1.5 py-0.5 text-accent">
          <span aria-hidden className="text-muted">›</span>
          {c.label}
          <button onClick={c.drop} aria-label={`Убрать фильтр ${c.label}`} className="ml-0.5 text-muted hover:text-fail">
            ×
          </button>
        </span>
      ))}
    </nav>
  );
}

function BatchControls({ run }: { run: Run }) {
  const qc = useQueryClient();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.get<Dataset[]>("/api/datasets") });
  const [ds, setDs] = useState("");
  const add = useMutation({ mutationFn: () => api.post(`/api/runs/${run.id}/batches`, { dataset: ds || datasets.data?.[0]?.path }) });
  const seal = useMutation({ mutationFn: () => api.post(`/api/runs/${run.id}/seal`), onSuccess: () => qc.invalidateQueries({ queryKey: ["run", run.id] }) });
  const err = add.error ?? seal.error ?? datasets.error;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-accent/40 bg-accent-bg px-3 py-2 text-xs">
      <span>Прогон открыт для догрузки порций.</span>
      <select className={inputCls + " h-7 text-xs"} value={ds} onChange={(e) => setDs(e.target.value)} aria-label="Датасет для догрузки">
        {(datasets.data ?? []).map((d) => (
          <option key={d.path} value={d.path}>{d.path}</option>
        ))}
      </select>
      <Button size="sm" onClick={() => add.mutate()} disabled={add.isPending}>Догрузить порцию</Button>
      <Button size="sm" variant="primary" onClick={() => seal.mutate()} disabled={seal.isPending}>Завершить приём</Button>
      {add.isSuccess && <span className="text-pass">Порция поставлена в очередь</span>}
      {err && <div className="basis-full"><ErrorBox inline error={err} /></div>}
    </div>
  );
}

export function RunPage() {
  const { runId } = useParams({ strict: false }) as { runId: string };
  const search = useSearch({ strict: false }) as RunSearch;
  const scope = useMemo(() => parseScope(search.scope), [search.scope]);
  const nav = useNavigate();
  const qc = useQueryClient();
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.get<Run>(`/api/runs/${runId}`) });
  const [live, setLive] = useState<{ type: string; data: Record<string, unknown> }[]>([]);
  useRunEvents(runId, (type, data) => {
    setLive((l) => [...l.slice(-300), { type, data }]);
    if (type.startsWith("run.") || type === "chunk.done" || type === "check.done") {
      qc.invalidateQueries({ queryKey: ["run", runId] });
      if (type === "check.done") qc.invalidateQueries({ queryKey: ["checks", runId] });
    }
  });
  const share = useMutation({ mutationFn: () => api.post<{ url: string }>(`/api/runs/${runId}/share`) });
  const cancel = useMutation({ mutationFn: () => api.post(`/api/runs/${runId}/cancel`), onSuccess: () => run.refetch() });

  if (run.isLoading) return <Skeleton rows={8} />;
  if (run.error || !run.data) return <ErrorBox error={run.error} />;
  const r = run.data;
  const done = r.state === "done";
  const tab = search.tab ?? (done ? "overview" : "journal");
  const setTab = (t: RunSearch["tab"]) => nav({ to: "/runs/$runId", params: { runId }, search: { tab: t, scope: search.scope, drill: t === "apps" ? search.drill : undefined } });
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-muted">
            <Link to="/" className="hover:text-fg">Прогоны</Link>
            <span aria-hidden>/</span>
          </div>
          <h1 className="flex flex-wrap items-center gap-2 text-lg font-semibold">
            {r.name} <StateBadge state={r.state} />
          </h1>
          <p className="num text-xs text-muted">
            {plural(r.apps, "заявка", "заявки", "заявок")} · {r.total_chunks != null ? plural(r.total_chunks, "порция", "порции", "порций") : "… порций"}
            {r.stats?.mart_rows ? <> · витрина {plural(r.stats.mart_rows, "строка", "строки", "строк")}</> : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {done && (
            <>
              <a href={`/api/runs/${runId}/export.html`} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-surface px-3 text-[13px] font-medium hover:bg-surface-2">
                Скачать HTML
              </a>
              <Button onClick={() => share.mutate()}>Ссылка на отчёт</Button>
              <Link to="/workspace" search={{ run: runId }} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-surface px-3 text-[13px] font-medium hover:bg-surface-2">
                Новая проверка
              </Link>
            </>
          )}
          {!done && r.state !== "failed" && r.state !== "cancelled" && (
            <Button variant="danger" onClick={() => cancel.mutate()}>Отменить</Button>
          )}
        </div>
      </div>
      {share.data && (
        <div className="flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 text-xs">
          <Badge tone="accent">только чтение · 30 дней</Badge>
          <input readOnly value={share.data.url} className={inputCls + " h-7 flex-1 font-mono text-xs"} onFocus={(e) => e.target.select()} aria-label="Ссылка на отчёт" />
          <Button size="sm" onClick={() => navigator.clipboard?.writeText(share.data.url)}>Копировать</Button>
          <Button size="sm" variant="danger" onClick={() => api.del(`/api/runs/${runId}/share`).then(() => share.reset())}>Отозвать все ссылки</Button>
        </div>
      )}
      {r.state === "receiving" && !r.seal_requested && <BatchControls run={r} />}
      {(share.error || cancel.error) && <ErrorBox error={share.error ?? cancel.error} />}
      {r.error && <ErrorBox title="Прогон завершился с ошибкой." error={r.error} debug={r.error_debug} />}
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "overview", label: "Обзор" },
          { id: "pivot", label: "Сводная таблица" },
          { id: "whatif", label: "Что если" },
          { id: "checks", label: "Автопроверки" },
          { id: "apps", label: "Заявки" },
          { id: "journal", label: "Журнал этапов" },
        ]}
      />
      {done && tab !== "journal" && <ScopeBar runId={runId} name={r.name} scope={scope} />}
      {tab === "journal" && <JournalView runId={runId} live={live} />}
      {tab !== "journal" && !done && <Progress run={r} live={live} />}
      {done && tab === "overview" && <Overview runId={runId} scope={scope} />}
      {done && tab === "pivot" && <PivotView runId={runId} scope={scope} />}
      {done && tab === "whatif" && <WhatIf runId={runId} scope={scope} />}
      {done && tab === "checks" && <ChecksView runId={runId} />}
      {done && tab === "apps" && <AppList runId={runId} scope={scope} drill={parseDrill(search.drill)} />}
    </div>
  );
}
