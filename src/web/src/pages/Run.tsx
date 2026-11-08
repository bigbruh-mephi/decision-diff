import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Badge, Button, ErrorBox, Skeleton, Tabs, inputCls, titleCls } from "../components/ui";
import { api, type Dataset, type Run, type Scope, type Summary } from "../lib/api";
import { useRunEvents } from "../lib/events";
import { LABELS, objects, plural, type Labels } from "../lib/format";
import { StateBadge } from "./Runs";
import { AppList } from "./run/AppList";
import { ChecksView } from "./run/Checks";
import { JournalView } from "./run/Journal";
import { Overview } from "./run/Overview";
import { PivotView } from "./run/Pivot";
import { Progress } from "./run/Progress";
import { WhatIf } from "./run/WhatIf";

export type RunSearch = { tab?: "overview" | "pivot" | "whatif" | "checks" | "apps" | "journal"; scope?: string; drill?: string; pv?: string };

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
        pv: prev.pv,
      }),
    });
}

/** Domain words of a finished run (segments procedure param ``labels``) over generic defaults. */
export function useLabels(runId: string, enabled = true): Labels {
  const s = useQuery({ queryKey: ["summary", runId, ""], queryFn: () => api.get<Summary>(`/api/runs/${runId}/summary`), enabled });
  return { ...LABELS, ...s.data?.labels };
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
  if (!chips.length) return null;
  return (
    <nav aria-label="Текущий срез" className="flex flex-wrap items-center gap-1.5 text-xs">
      <button onClick={() => go({})} className="font-medium text-muted underline underline-offset-2 hover:text-fg">
        {name}: вся популяция
      </button>
      {chips.map((c) => (
        <span key={c.label} className="inline-flex items-center gap-1 rounded-full border border-line-strong bg-surface px-2.5 py-0.5">
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
  const labels = useLabels(runId, run.data?.state === "done");

  if (run.isLoading) return <Skeleton rows={8} />;
  if (run.error || !run.data) return <ErrorBox error={run.error} />;
  const r = run.data;
  const done = r.state === "done";

  const tab = search.tab ?? (done ? "overview" : "journal");
  const setTab = (t: RunSearch["tab"]) => nav({ to: "/runs/$runId", params: { runId }, search: { tab: t, scope: search.scope, drill: t === "apps" ? search.drill : undefined, pv: search.pv } });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link to="/" className="text-xs text-muted hover:text-fg">Прогоны /</Link>
        <h1 className={titleCls}>{r.name}</h1>
        <StateBadge state={r.state} />
        <p className="num text-xs text-muted">
          {objects(r.apps, labels)}
        </p>
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
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { id: "overview", label: "Обзор" },
            { id: "apps", label: "Заявки" },
            { id: "checks", label: "Автопроверки" },
            { id: "pivot", label: "Сводная таблица", quiet: true },
            { id: "whatif", label: "Что если", quiet: true },
            { id: "journal", label: "Журнал этапов", quiet: true },
          ]}
        />
        <div className="flex flex-wrap items-center gap-1 text-xs text-muted">
          {done && (
            <>
              <a href={`/api/runs/${runId}/export.html`} className="rounded-md px-3 py-1 font-medium hover:bg-surface-2 hover:text-fg">Скачать HTML</a>
              <Button variant="ghost" size="sm" onClick={() => share.mutate()}>Ссылка на отчёт</Button>
              <Link to="/workspace" search={{ run: runId }} className="rounded-md px-3 py-1 font-medium hover:bg-surface-2 hover:text-fg">Новая проверка</Link>
            </>
          )}
          {!done && r.state !== "failed" && r.state !== "cancelled" && (
            <Button variant="danger" size="sm" onClick={() => cancel.mutate()}>Отменить</Button>
          )}
        </div>
      </div>
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
