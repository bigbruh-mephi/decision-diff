import { useQuery } from "@tanstack/react-query";
import { Badge, Card, CardHeader, Empty, ErrorBox, Skeleton } from "../../components/ui";
import { ApiError, api } from "../../lib/api";

type Finding = { title: string; severity: "high" | "medium" | "low"; text: string; derived_attribute?: string; evidence?: { label: string; apps?: number }[]; verified: boolean; check_next?: string; reason?: string };
type Ai = { state: "disabled" | "pending" | "done" | "failed"; model?: string; findings?: Finding[]; error?: string; error_debug?: string };

const SEVERITY = { high: "важно", medium: "средне", low: "к сведению" };

export function AiOpinion({ runId }: { runId: string }) {
  const ai = useQuery({
    queryKey: ["ai", runId],
    queryFn: async () => {
      try {
        return await api.get<Ai>(`/api/runs/${runId}/ai`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return { state: "disabled" } as Ai;
        throw e;
      }
    },
    refetchInterval: (q) => (q.state.data?.state === "pending" ? 3000 : false),
  });
  const d = ai.data;
  return (
    <Card>
      <CardHeader
        title="Альтернативное мнение ИИ"
        hint="Второй взгляд на прогон, а не источник истины: сначала выводы, подтвержденные измерениями по всем заявкам, затем гипотезы модели без подтверждения - бледно и с причиной"
        actions={d?.model ? <Badge>{d.model}</Badge> : undefined}
      />
      {ai.isLoading && <Skeleton rows={3} />}
      {ai.error && <ErrorBox title="Мнение ИИ недоступно." error={ai.error} />}
      {d?.state === "disabled" && <Empty title="Языковая модель не подключена">Укажите DD_LLM_BASE_URL, DD_LLM_API_KEY и DD_LLM_MODEL (подойдет любой сервер с OpenAI-совместимым API).</Empty>}
      {d?.state === "pending" && <Empty title="Модель анализирует прогон…">Отчёт уже доступен, мнение появится здесь.</Empty>}
      {d?.state === "failed" && <ErrorBox title="Не удалось получить мнение ИИ." error={d.error ?? "причина не сохранилась"} debug={d.error_debug} />}
      {d?.state === "done" && (
        <ul className="divide-y divide-line">
          {(d.findings ?? []).map((f, i) => (
            <li key={i} className={f.verified ? "px-4 py-3" : "px-4 py-3 opacity-70"}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={f.severity === "high" ? "fail" : f.severity === "medium" ? "warn" : "neutral"}>{SEVERITY[f.severity] ?? f.severity}</Badge>
                <span className="font-medium">{f.title}</span>
                {f.verified ? <Badge tone="pass">числа сверены</Badge> : <Badge tone="low">не подтверждено системой</Badge>}
              </div>
              <p className="mt-1 text-[13px] text-muted">{f.text}</p>
              {f.derived_attribute && <p className="mt-1 text-xs">Производный признак: <code className="font-mono">{f.derived_attribute}</code></p>}
              {f.check_next && <p className="mt-1 text-xs text-accent">Что проверить: {f.check_next}</p>}
              {f.reason && <p className="mt-1 text-xs text-muted">Почему не подтверждено: {f.reason}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
