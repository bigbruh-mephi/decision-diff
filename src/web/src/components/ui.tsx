import { useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import { CHANGE } from "../lib/format";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md" };

export function Button({ variant = "secondary", size = "md", className, ...p }: BtnProps) {
  return (
    <button
      {...p}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
        size === "sm" ? "h-7 rounded-[10px] px-3 text-xs" : "h-9 px-4 text-[13px]",
        variant === "primary" && "bg-accent text-on-accent hover:opacity-90",
        variant === "secondary" && "border border-line-strong bg-surface hover:border-accent hover:text-accent",
        variant === "ghost" && "hover:bg-surface-2 text-muted hover:text-fg",
        variant === "danger" && "border border-fail/40 text-fail hover:bg-fail-bg",
        className,
      )}
    />
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx("min-w-0 rounded-lg bg-surface", className)}>{children}</section>;
}

export function CardHeader({ title, hint, actions }: { title: ReactNode; hint?: ReactNode; actions?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <header className="border-b border-line px-5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="font-display text-[15px] font-medium">{title}</h2>
          {hint && (
            <button type="button" aria-expanded={open} aria-label="Пояснение к блоку" onClick={() => setOpen(!open)} className="grid h-5 w-5 shrink-0 place-items-center rounded-full border border-line-strong text-xs text-muted transition-colors hover:border-fg hover:text-fg">
              ?
            </button>
          )}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {hint && open && <p className="mt-1 max-w-3xl text-xs text-muted">{hint}</p>}
    </header>
  );
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "fail" | "low" | "pass" | "warn" | "accent"; title?: string }) {
  const tones = {
    neutral: "border-line-strong text-muted",
    fail: "border-fail/50 bg-fail-bg text-fail",
    low: "border-dashed border-fail-low/60 bg-fail-low-bg text-fail-low",
    pass: "border-pass/40 bg-pass-bg text-pass",
    warn: "border-warn/50 bg-warn-bg text-warn",
    accent: "border-accent/40 bg-accent-bg text-accent",
  };
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-px text-xs font-medium", tones[tone])}>
      {children}
    </span>
  );
}

export function ChangeGlyph({ type, withLabel }: { type: string; withLabel?: boolean }) {
  const c = CHANGE[type] ?? CHANGE.SAME;
  return (
    <span className={cx("inline-flex items-center gap-1 font-mono font-medium", c.cls)} title={c.label}>
      <span aria-hidden className="inline-block w-3 text-center">{c.glyph}</span>
      {withLabel ? <span className="font-sans text-xs">{c.label}</span> : <span className="sr-only">{c.label}</span>}
    </span>
  );
}

export function Stat({ label, value, sub, tone, onClick }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "fail" | "warn"; onClick?: () => void }) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      onClick={onClick}
      className={cx(
        "group relative flex min-w-0 flex-col items-start rounded-lg border border-transparent bg-surface px-5 py-4 text-left",
        onClick && "transition-colors hover:border-accent hover:bg-accent-bg",
      )}
    >
      {onClick && <span aria-hidden className="absolute right-4 top-3 text-muted transition-colors group-hover:text-accent">→</span>}
      <span className="pr-4 text-xs text-muted">{label}</span>
      <span className={cx("num mt-1 font-display text-[24px] font-normal leading-tight", tone === "fail" && "text-fail", tone === "warn" && "text-warn")}>{value}</span>
      {sub && <span className="num mt-0.5 text-xs text-muted">{sub}</span>}
    </Comp>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: ReactNode; quiet?: boolean }[] }) {
  return (
    <div role="tablist" className="flex max-w-full items-center gap-1 overflow-x-auto [scrollbar-width:none]">
      {items.map((it) => (
        <button
          key={it.id}
          role="tab"
          aria-selected={value === it.id}
          onClick={() => onChange(it.id)}
          className={cx(
            "whitespace-nowrap rounded-md px-3 py-1.5 font-medium transition-colors",
            it.quiet ? "text-xs" : "text-[13px]",
            value === it.id ? "bg-surface text-fg" : "text-muted hover:bg-surface-2 hover:text-fg",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function Filter<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string }[] }) {
  return (
    <div className="flex gap-1">
      {items.map((it) => (
        <button key={it.id} aria-pressed={value === it.id} onClick={() => onChange(it.id)} className={cx("rounded-md px-2 py-0.5 text-xs", value === it.id ? "bg-surface-2 text-fg" : "text-muted hover:bg-surface-2 hover:text-fg")}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function Skeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cx("space-y-2 p-4", className)} aria-busy="true" aria-label="Загрузка">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-5 animate-pulse rounded bg-surface-2" style={{ width: `${90 - i * 9}%` }} />
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div role="status" className="px-4 py-10 text-center">
      <p className="text-[13px] font-medium">{title}</p>
      {children && <div className="mt-1 text-xs text-muted">{children}</div>}
    </div>
  );
}

/** Russian headline first; the technical text (server debug, traceback of the user's code) only under "Подробности". */
export function ErrorBox({ error, debug, title, inline }: { error: unknown; debug?: string | null; title?: ReactNode; inline?: boolean }) {
  const msg = error instanceof Error ? error.message : error ? String(error) : "Неизвестная ошибка";
  const detail = debug ?? (error instanceof ApiError ? error.debug : null);
  return (
    <div role="alert" className={cx(inline ? "mt-1" : "m-4", "rounded-md border border-fail/40 bg-fail-bg px-3 py-2 text-xs text-fail")}>
      <p>{title && <span className="font-semibold">{title} </span>}{msg}</p>
      {detail && detail !== msg && (
        <details className="mt-1">
          <summary className="cursor-pointer select-none font-medium">Подробности</summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs">{detail}</pre>
        </details>
      )}
    </div>
  );
}

export function Bar({ parts, total }: { parts: { value: number; className: string; label: string }[]; total: number }) {
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-surface-2" role="img" aria-label={parts.map((p) => `${p.label}: ${p.value}`).join(", ")}>
      {parts.map((p) => (
        <div key={p.label} className={p.className} style={{ width: `${total ? (p.value / total) * 100 : 0}%` }} />
      ))}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "h-9 min-w-0 max-w-full rounded-md border border-line-strong bg-surface px-2.5 text-[13px] outline-none focus:border-accent";

export const titleCls = "font-display text-[24px] font-normal leading-tight tracking-tight";

/** A number or name that opens the data behind it: quiet underline instead of the accent, which is kept for actions. */
export const linkCls = "-mx-0.5 rounded px-0.5 underline decoration-muted/60 underline-offset-2 transition-colors hover:bg-accent-bg hover:text-accent hover:decoration-accent";

export function Logo() {
  return (
    <span className="inline-flex flex-col font-display text-xs font-semibold uppercase leading-[1.1] tracking-[0.06em]">
      Decision
      <br />
      Diff
      <span aria-hidden className="mt-1 h-[3px] w-full rounded-full bg-brand" />
    </span>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cx("font-mono text-xs [overflow-wrap:anywhere]", className)}>{children}</code>;
}
