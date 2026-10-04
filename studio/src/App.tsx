import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { ArtifactsPage } from "./features/artifacts/ArtifactsPage";
import { GlobalErrorHandler, ToastProvider } from "./components/Toaster";
import { OneShotLauncher } from "./features/oneshot/OneShotLauncher";
import { BoardPage } from "./features/board";
import { Dashboard } from "./views/Dashboard";
import { Projects } from "./views/Projects";
import { Propose } from "./views/Propose";
import { Roster } from "./views/Roster";
import { Run } from "./views/Run";
import { Settings } from "./views/Settings";
import { Studio } from "./views/Studio";
import { Tournament } from "./views/Tournament";
import { Training } from "./views/Training";
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
      <AppShell token={token} setTokenLocal={setTokenLocal} />
    </ToastProvider>
  );
}

function AppShell({
  token,
  setTokenLocal,
}: {
  token: string;
  setTokenLocal: (s: string) => void;
}) {
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">GuudFlow</Link>
        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Dashboard
          </NavLink>
          <NavLink to="/projects" className={({ isActive }) => (isActive ? "active" : "")}>
            Projects
          </NavLink>
          <NavLink to="/oneshot" className={({ isActive }) => (isActive ? "active" : "")}>
            One-shot
          </NavLink>
          <NavLink to="/propose" className={({ isActive }) => (isActive ? "active" : "")}>
            Propose
          </NavLink>
          <NavLink to="/roster" className={({ isActive }) => (isActive ? "active" : "")}>
            Roster
          </NavLink>
          <NavLink to="/training" className={({ isActive }) => (isActive ? "active" : "")}>
            Training
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => (isActive ? "active" : "")}>
            Settings
          </NavLink>
        </nav>
        <input
          className="token-input"
          type="password"
          placeholder="API token"
          value={token}
          onChange={(e) => setTokenLocal(e.target.value)}
        />
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:id" element={<Studio />} />
          <Route path="/propose" element={<Propose />} />
          <Route path="/oneshot" element={<OneShotLauncher />} />
          <Route path="/runs/:runId" element={<Run />} />
          <Route path="/runs/:runId/board" element={<BoardPage />} />
          <Route path="/runs/:id/artifacts" element={<ArtifactsPage />} />
          <Route path="/roster" element={<Roster />} />
          <Route path="/roster/:id" element={<Roster />} />
          <Route path="/training" element={<Training />} />
          <Route path="/training/tournaments/:id" element={<Tournament />} />
          <Route path="/settings" element={<Settings />} />
          <Route
            path="*"
            element={
              <div className="page">
                <h1>Not found</h1>
                <Link to="/projects">← projects</Link>
              </div>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
