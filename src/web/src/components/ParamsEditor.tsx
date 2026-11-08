import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Procedure } from "../lib/api";
import { Badge, ErrorBox, Skeleton, inputCls } from "./ui";

export type Params = Record<string, Record<string, unknown>>;

/** JSON params of one procedure over the ones declared in its code; ``value`` keeps only the changed keys. */
export function ParamsEditor({ id, name, value, onChange, onValid, open }: { open?: boolean; id: string; name: string; value: Record<string, unknown> | undefined; onChange: (v: Record<string, unknown> | undefined) => void; onValid: (ok: boolean) => void }) {
  const proc = useQuery({ queryKey: ["procedure", id], queryFn: () => api.get<Procedure>(`/api/procedures/${id}`) });
  const declared = proc.data?.params ?? {};
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const changed = Object.keys(value ?? {});
  const edit = (t: string) => {
    setText(t);
    try {
      const v = JSON.parse(t);
      if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("нужен объект {\"параметр\": значение}");
      const diff = Object.fromEntries(Object.entries(v).filter(([k, x]) => JSON.stringify(x) !== JSON.stringify(declared[k])));
      setError(null);
      onValid(true);
      onChange(Object.keys(diff).length ? diff : undefined);
    } catch (e) {
      setError(e instanceof SyntaxError ? `Неверный JSON: ${e.message}` : e instanceof Error ? e.message : String(e));
      onValid(false);
    }
  };
  return (
    <details open={open} className="rounded border border-line px-3 py-1.5">
      <summary className="flex cursor-pointer items-center gap-2 text-[13px]">
        <span className="min-w-0 flex-1 truncate">{name}</span>
        {error ? <Badge tone="fail">ошибка JSON</Badge> : changed.length ? <Badge tone="accent">изменено: {changed.join(", ")}</Badge> : <Badge>по умолчанию</Badge>}
      </summary>
      {proc.isLoading && <Skeleton rows={2} />}
      {proc.error && <ErrorBox inline error={proc.error} />}
      {proc.data && (
        <div className="flex flex-col gap-1 py-2">
          <textarea
            className={inputCls + " h-auto min-h-24 py-1.5 font-mono text-xs"}
            rows={Math.min(14, JSON.stringify({ ...declared, ...value }, null, 2).split("\n").length)}
            spellCheck={false}
            value={text ?? JSON.stringify({ ...declared, ...value }, null, 2)}
            onChange={(e) => edit(e.target.value)}
            aria-label={`Параметры: ${name}`}
          />
          {error ? (
            <span className="text-xs text-fail">{error}</span>
          ) : (
            <span className="text-xs text-muted">
              Действующие значения: объявленные в коде процедуры, поверх них ваши изменения. Сохраняются в конфигурации и прогоне.
              {changed.length > 0 && <button className="ml-2 text-accent hover:underline" onClick={() => { setText(null); onChange(undefined); }}>сбросить</button>}
            </span>
          )}
        </div>
      )}
    </details>
  );
}
