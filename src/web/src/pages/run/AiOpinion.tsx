import { useQuery } from "@tanstack/react-query";
import { Badge, Card, CardHeader, Empty, Skeleton } from "../../components/ui";
import { ApiError, api } from "../../lib/api";

type Finding = { title: string; severity: "high" | "medium" | "low"; text: string; derived_attribute?: string; evidence?: { label: string; apps?: number }[]; verified: boolean; check_next?: string };
type Ai = { state: "disabled" | "pending" | "done" | "failed"; model?: string; findings?: Finding[]; error?: string };

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
        hint="Второй взгляд на прогон: метавыводы и производные признаки. Каждое число сверено с витриной; непроверенные утверждения отбрасываются"
        actions={d?.model ? <Badge>{d.model}</Badge> : undefined}
      />
      {ai.isLoading && <Skeleton rows={3} />}
      {ai.error && <Empty title="Мнение ИИ недоступно">{ai.error instanceof Error ? ai.error.message : String(ai.error)}</Empty>}
      {d?.state === "disabled" && <Empty title="Языковая модель не подключена">Укажите DD_LLM_BASE_URL, DD_LLM_API_KEY и DD_LLM_MODEL (любой OpenAI-совместимый endpoint).</Empty>}
      {d?.state === "pending" && <Empty title="Модель анализирует прогон…">Отчёт уже доступен, мнение появится здесь.</Empty>}
      {d?.state === "failed" && <Empty title="Не удалось получить мнение">{d.error}</Empty>}
      {d?.state === "done" && (
        <ul className="divide-y divide-line">
          {(d.findings ?? []).map((f, i) => (
            <li key={i} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={f.severity === "high" ? "fail" : f.severity === "medium" ? "warn" : "neutral"}>{f.severity}</Badge>
                <span className="font-medium">{f.title}</span>
                {f.verified ? <Badge tone="pass">числа сверены</Badge> : <Badge tone="low">не проверено</Badge>}
              </div>
              <p className="mt-1 text-[13px] text-muted">{f.text}</p>
              {f.derived_attribute && <p className="mt-1 text-xs">Производный признак: <code className="font-mono">{f.derived_attribute}</code></p>}
              {f.check_next && <p className="mt-1 text-xs text-accent">Что проверить: {f.check_next}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
