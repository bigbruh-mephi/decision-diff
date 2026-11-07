import type { ButtonHTMLAttributes, ReactNode } from "react";
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
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-[13px]",
        variant === "primary" && "bg-accent text-white hover:opacity-90",
        variant === "secondary" && "border border-line-strong bg-surface hover:bg-surface-2",
        variant === "ghost" && "hover:bg-surface-2 text-muted hover:text-fg",
        variant === "danger" && "border border-fail/40 text-fail hover:bg-fail-bg",
        className,
      )}
    />
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx("rounded-lg border border-line bg-surface", className)}>{children}</section>;
}

export function CardHeader({ title, hint, actions }: { title: ReactNode; hint?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-4 py-2.5">
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold">{title}</h2>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
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
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-[11px] font-medium", tones[tone])}>
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

export function Stat({ label, value, sub, tone, onClick }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "fail" | "accent" | "warn"; onClick?: () => void }) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      onClick={onClick}
      className={cx(
        "flex min-w-0 flex-col items-start rounded-lg border border-line bg-surface px-4 py-3 text-left",
        onClick && "transition-colors hover:border-accent/60 hover:bg-accent-bg/40 cursor-pointer",
      )}
    >
      <span className="text-xs text-muted">{label}</span>
      <span className={cx("num mt-0.5 text-[22px] font-semibold leading-tight", tone === "fail" && "text-fail", tone === "accent" && "text-accent", tone === "warn" && "text-warn")}>{value}</span>
      {sub && <span className="num mt-0.5 text-xs text-muted">{sub}</span>}
    </Comp>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: ReactNode }[] }) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-line">
      {items.map((it) => (
        <button
          key={it.id}
          role="tab"
          aria-selected={value === it.id}
          onClick={() => onChange(it.id)}
          className={cx(
            "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-[13px] font-medium transition-colors",
            value === it.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
          )}
        >
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

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div role="alert" className="m-4 rounded-md border border-fail/40 bg-fail-bg px-3 py-2 text-xs text-fail">
      {error instanceof Error ? error.message : String(error)}
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
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "h-8 rounded-md border border-line-strong bg-surface px-2.5 text-[13px] outline-none focus:border-accent";

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cx("font-mono text-[12px]", className)}>{children}</code>;
}
