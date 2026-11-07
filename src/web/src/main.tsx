import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import {
  Link,
  Outlet,
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
} from "@tanstack/react-router";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { Button, ErrorBox, Skeleton } from "./components/ui";
import { ApiError, api, type Me } from "./lib/api";
import { AppDetailPage } from "./pages/AppDetail";
import { LoginPage } from "./pages/Login";
import { NewRunPage } from "./pages/NewRun";
import { RunPage, type RunSearch } from "./pages/Run";
import { RunsPage } from "./pages/Runs";
import { WorkspacePage } from "./pages/Workspace";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2 } },
});

function useTheme() {
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("theme") ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* private mode */
    }
  }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")] as const;
}

function Shell() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/me"), retry: false });
  const [theme, toggle] = useTheme();
  const nav = useNavigate();
  if (me.isLoading) return <Skeleton rows={6} />;
  if (me.error && !(me.error instanceof ApiError && me.error.status === 401)) return <ErrorBox title="Приложение недоступно." error={me.error} />;
  if (me.error) return <LoginPage />;
  const shareOnly = !me.data?.user_id;
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-[1440px] items-center gap-4 px-4">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <span aria-hidden className="grid h-6 w-6 place-items-center rounded bg-accent font-mono text-[11px] text-white">Δ</span>
            Decision Diff
          </Link>
          {!shareOnly && (
            <nav className="flex items-center gap-1 text-[13px]">
              <Link to="/" className="rounded px-2 py-1 text-muted hover:text-fg [&.active]:text-fg" activeOptions={{ exact: true }}>
                Прогоны
              </Link>
              <Link to="/workspace" className="rounded px-2 py-1 text-muted hover:text-fg [&.active]:text-fg">
                Рабочая область
              </Link>
            </nav>
          )}
          <div className="ml-auto flex items-center gap-2 text-xs text-muted">
            <Button variant="ghost" size="sm" onClick={toggle} aria-label="Переключить тему">
              {theme === "dark" ? "☀" : "☾"}
            </Button>
            <span>{shareOnly ? "просмотр по ссылке" : me.data?.login}</span>
            {!shareOnly && (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  await api.post("/api/auth/logout");
                  queryClient.clear();
                  nav({ to: "/" });
                  me.refetch();
                }}
              >
                Выйти
              </Button>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1440px] px-4 py-4">
        <Outlet />
      </main>
    </div>
  );
}

const root = createRootRoute({ component: Shell });
const runsRoute = createRoute({ getParentRoute: () => root, path: "/", component: RunsPage });
const newRunRoute = createRoute({ getParentRoute: () => root, path: "/runs/new", component: NewRunPage });
const runRoute = createRoute({
  getParentRoute: () => root,
  path: "/runs/$runId",
  validateSearch: (s: Record<string, unknown>): RunSearch => ({
    tab: (s.tab as RunSearch["tab"]) ?? undefined,
    scope: typeof s.scope === "string" ? s.scope : s.scope ? JSON.stringify(s.scope) : undefined,
    drill: typeof s.drill === "string" ? s.drill : s.drill ? JSON.stringify(s.drill) : undefined,
  }),
  component: RunPage,
});
const clusterRoute = createRoute({
  getParentRoute: () => root,
  path: "/runs/$runId/clusters/$cid",
  component: function ClusterRedirect() {
    const { runId, cid } = clusterRoute.useParams();
    const nav = useNavigate();
    useEffect(() => {
      nav({ to: "/runs/$runId", params: { runId }, search: { tab: "overview", scope: JSON.stringify({ cluster: Number(cid) }) }, replace: true });
    }, [runId, cid, nav]);
    return null;
  },
});
const appsRoute = createRoute({
  getParentRoute: () => root,
  path: "/runs/$runId/apps",
  validateSearch: (s: Record<string, unknown>): { scope?: string } => ({ scope: typeof s.scope === "string" ? s.scope : s.scope ? JSON.stringify(s.scope) : undefined }),
  component: function AppsRedirect() {
    const { runId } = appsRoute.useParams();
    const { scope } = appsRoute.useSearch();
    const nav = useNavigate();
    useEffect(() => {
      nav({ to: "/runs/$runId", params: { runId }, search: { tab: "apps", scope }, replace: true });
    }, [runId, scope, nav]);
    return null;
  },
});
const appRoute = createRoute({
  getParentRoute: () => root,
  path: "/runs/$runId/app/$appId",
  validateSearch: (s: Record<string, unknown>): { path?: string; scope?: string } => ({ path: typeof s.path === "string" ? s.path : undefined, scope: typeof s.scope === "string" ? s.scope : undefined }),
  component: AppDetailPage,
});
const workspaceRoute = createRoute({
  getParentRoute: () => root,
  path: "/workspace",
  validateSearch: (s: Record<string, unknown>): { run?: string; proc?: string } => ({ run: typeof s.run === "string" ? s.run : undefined, proc: typeof s.proc === "string" ? s.proc : undefined }),
  component: WorkspacePage,
});

export const router = createRouter({
  routeTree: root.addChildren([runsRoute, newRunRoute, runRoute, clusterRoute, appsRoute, appRoute, workspaceRoute]),
  defaultPreload: "intent",
});
export { appRoute, runRoute, workspaceRoute };

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
