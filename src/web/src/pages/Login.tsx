import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Field, Logo, inputCls } from "../components/ui";
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
    <div className="mx-auto grid min-h-screen max-w-[1200px] content-center items-center gap-10 px-6 py-10 lg:grid-cols-[1fr_400px]">
      <div>
        <Logo />
        <h1 className="mt-10 font-display text-[40px] font-normal leading-[1.1] tracking-tight sm:text-[56px]">
          Анализ изменений стратегии СПР <span className="text-brand">до выката</span>
        </h1>
        <p className="mt-4 text-sm text-muted">Два среза ответов — один отчёт: от вердикта до заявки и атрибута</p>
        <ol className="mt-8 flex flex-col gap-3 text-[13px]">
          {["Куда сместились решения и кого это затронуло", "Логика или входные данные: кластеры изменений", "Автопроверки и свои процедуры в песочнице"].map((t, i) => (
            <li key={t} className="flex items-center gap-3">
              <span aria-hidden className="num grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line-strong text-xs text-muted">{i + 1}</span>
              {t}
            </li>
          ))}
        </ol>
      </div>
      <form onSubmit={submit} className="w-full rounded-[32px] bg-surface p-8">
        <h2 className="mb-5 font-display text-xl font-normal">{mode === "login" ? "Вход в систему" : "Регистрация"}</h2>
        <div className="flex flex-col gap-3">
          <Field label="Логин">
            <input className={inputCls} value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" required minLength={2} />
          </Field>
          <Field label="Пароль" hint={mode === "register" ? "Не короче 8 символов" : undefined}>
            <input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={mode === "register" ? 8 : undefined} />
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
