import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Field, inputCls } from "../components/ui";
import { api } from "../lib/api";

export function LoginPage() {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api.post(`/api/auth/${mode}`, { login, password });
      qc.setQueryData(["me"], me);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-line bg-surface p-6">
        <div className="mb-5 flex items-center gap-2">
          <span aria-hidden className="grid h-7 w-7 place-items-center rounded bg-accent font-mono text-xs text-white">Δ</span>
          <div>
            <h1 className="text-base font-semibold">Decision Diff</h1>
            <p className="text-xs text-muted">Анализ изменений стратегии СПР до выката</p>
          </div>
        </div>
        <div className="flex flex-col gap-3">
          <Field label="Логин">
            <input className={inputCls} value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" required minLength={2} />
          </Field>
          <Field label="Пароль" hint={mode === "register" ? "Не короче 8 символов" : undefined}>
            <input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={8} />
          </Field>
          {error && <p role="alert" className="text-xs text-fail">{error}</p>}
          <Button variant="primary" disabled={busy} type="submit">
            {mode === "login" ? "Войти" : "Зарегистрироваться"}
          </Button>
          <button type="button" className="text-xs text-accent hover:underline" onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "Нет учётной записи? Зарегистрироваться" : "Уже есть учётная запись? Войти"}
          </button>
        </div>
      </form>
    </div>
  );
}
