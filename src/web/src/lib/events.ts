import { useEffect, useRef } from "react";

/** Subscribe to run progress over SSE; the browser resumes after Last-Event-ID, so a reload keeps the state. */
export function useRunEvents(runId: string | null, onEvent: (type: string, data: Record<string, unknown>) => void) {
  const cb = useRef(onEvent);
  cb.current = onEvent;
  useEffect(() => {
    const url = runId ? `/api/events?run=${runId}` : "/api/events";
    const es = new EventSource(url, { withCredentials: true });
    const types = ["run.created", "ingest.started", "ingest.done", "run.sealed", "chunk.done", "chunk.failed", "finalize.started", "run.done", "run.failed", "run.cancelled", "check.queued", "check.done"];
    const handlers = types.map((t) => {
      const h = (e: MessageEvent) => cb.current(t, JSON.parse(e.data));
      es.addEventListener(t, h);
      return [t, h] as const;
    });
    return () => {
      handlers.forEach(([t, h]) => es.removeEventListener(t, h));
      es.close();
    };
  }, [runId]);
}
