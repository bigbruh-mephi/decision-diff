import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
import { EditorView, basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

const theme = EditorView.theme({
  "&": { backgroundColor: "var(--surface)", color: "var(--fg)" },
  ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", borderRight: "1px solid var(--line)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in oklch, var(--accent) 6%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--fg)" },
  "&.cm-focused .cm-cursor": { borderLeftColor: "var(--accent)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { backgroundColor: "color-mix(in oklch, var(--accent) 22%, transparent)" },
});

export function CodeView({ value, onChange, readOnly, className, highlightLine }: { value: string; onChange?: (v: string) => void; readOnly?: boolean; className?: string; highlightLine?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const cb = useRef(onChange);
  cb.current = onChange;

  useEffect(() => {
    view.current = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          python(),
          theme,
          EditorState.readOnly.of(!!readOnly),
          EditorView.updateListener.of((u) => u.docChanged && cb.current?.(u.state.doc.toString())),
        ],
      }),
    });
    return () => view.current?.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  useEffect(() => {
    const v = view.current;
    if (v && highlightLine && highlightLine <= v.state.doc.lines) {
      const line = v.state.doc.line(highlightLine);
      v.dispatch({ selection: { anchor: line.from }, scrollIntoView: true });
    }
  }, [highlightLine]);

  return <div ref={host} className={className ?? "h-[60vh] overflow-hidden rounded-md border border-line"} />;
}
