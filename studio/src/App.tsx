import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { ArtifactsPage } from "./features/artifacts/ArtifactsPage";
import { GlobalErrorHandler, ToastProvider } from "./components/Toaster";
import { OneShotLauncher } from "./features/oneshot/OneShotLauncher";
import { BoardPage } from "./features/board";
import { RunReport } from "./features/report/RunReport";
import { Dashboard } from "./views/Dashboard";
import { Projects } from "./views/Projects";
import { NewProject } from "./features/project/NewProject";
import { Propose } from "./views/Propose";
import { Roster } from "./views/Roster";
import { RunRoute } from "./views/RunRoute";
import { JackDock } from "./features/jack/JackDock";
import { JackProvider } from "./features/jack/context";
import { Settings } from "./views/Settings";
import { Studio } from "./views/Studio";
import { Tournament } from "./views/Tournament";
import { Training } from "./views/Training";
import { CommandPalette } from "./components/CommandPalette";
import { Button } from "./components/ui/button";
import { EmptyState, Input, linkClass, Page } from "./components/ui/form-controls";
import { cn } from "./lib/utils";
import { getToken, setToken } from "./api";

export function App() {
  const [token, setTokenLocal] = useState(getToken() ?? "");

  // Persist token on every change.
  useEffect(() => {
    setToken(token || null);
  }, [token]);

  return (
    <ToastProvider>
      <GlobalErrorHandler />
      <JackProvider>
        <AppShell token={token} setTokenLocal={setTokenLocal} />
      </JackProvider>
    </ToastProvider>
  );
}

const NAV: { to: string; label: string; end?: boolean }[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/projects", label: "Projects" },
  { to: "/oneshot", label: "One-shot" },
  { to: "/projects/new", label: "New project" },
  { to: "/roster", label: "Roster" },
  { to: "/training", label: "Training" },
  { to: "/settings", label: "Settings" },
];

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "whitespace-nowrap rounded-md px-3 py-1.5 text-sm no-underline transition-colors hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    isActive
      ? "bg-accent text-accent-foreground hover:text-accent-foreground"
      : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
  );

function AppShell({
  token,
  setTokenLocal,
}: {
  token: string;
  setTokenLocal: (s: string) => void;
}) {
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Cmd/Ctrl+K toggles the palette from anywhere, including while typing in a field.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-1.5 focus:text-primary-foreground"
      >
        Skip to content
      </a>
      <header className="flex h-[50px] shrink-0 items-center gap-3 border-b border-border bg-card px-4 text-card-foreground lg:gap-6 lg:px-5">
        <Link
          to="/"
          className="rounded-sm text-base font-bold tracking-tight text-foreground no-underline hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          GuudFlow
        </Link>
        <nav aria-label="Main" className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={navLinkClass}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setPaletteOpen(true)}
          aria-label="Open command palette"
          aria-keyshortcuts="Control+K Meta+K"
          className="shrink-0 gap-2 text-muted-foreground"
        >
          <span className="hidden sm:inline">Search</span>
          <kbd className="rounded-sm border border-border bg-muted px-1 font-mono text-[11px]">Ctrl K</kbd>
        </Button>
        <Input
          className="w-28 shrink-0 md:w-44 lg:w-60"
          type="password"
          placeholder="API token"
          aria-label="API token"
          value={token}
          onChange={(e) => setTokenLocal(e.target.value)}
        />
      </header>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <JackDock />
      <main id="main" tabIndex={-1} className="flex-1 overflow-auto p-5 focus:outline-none">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/new" element={<NewProject />} />
          <Route path="/projects/:id" element={<Studio />} />
          <Route path="/propose" element={<Propose />} />
          <Route path="/oneshot" element={<OneShotLauncher />} />
          <Route path="/runs/:runId" element={<RunRoute />} />
          <Route path="/runs/:runId/board" element={<BoardPage />} />
          <Route path="/runs/:id/artifacts" element={<ArtifactsPage />} />
          <Route path="/runs/:id/report" element={<RunReport />} />
          <Route path="/roster" element={<Roster />} />
          <Route path="/roster/:id" element={<Roster />} />
          <Route path="/training" element={<Training />} />
          <Route path="/training/tournaments/:id" element={<Tournament />} />
          <Route path="/settings" element={<Settings />} />
          <Route
            path="*"
            element={
              <Page title="Not found">
                <EmptyState title="That page does not exist.">
                  <Link to="/projects" className={linkClass}>
                    ← Back to projects
                  </Link>
                </EmptyState>
              </Page>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
