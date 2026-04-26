import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { Projects } from "./views/Projects";
import { Propose } from "./views/Propose";
import { Studio } from "./views/Studio";
import { Run } from "./views/Run";
import { Roster } from "./views/Roster";
import { getToken, setToken } from "./api";

export function App() {
  const [token, setTokenLocal] = useState(getToken() ?? "");

  // Persist token on every change.
  useEffect(() => {
    setToken(token || null);
  }, [token]);

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">AgentFlow</Link>
        <nav className="nav">
          <NavLink to="/projects" className={({ isActive }) => (isActive ? "active" : "")}>
            Projects
          </NavLink>
          <NavLink to="/propose" className={({ isActive }) => (isActive ? "active" : "")}>
            Propose
          </NavLink>
          <NavLink to="/roster" className={({ isActive }) => (isActive ? "active" : "")}>
            Roster
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
          <Route path="/" element={<Projects />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:id" element={<Studio />} />
          <Route path="/propose" element={<Propose />} />
          <Route path="/runs/:runId" element={<Run />} />
          <Route path="/roster" element={<Roster />} />
          <Route path="/roster/:id" element={<Roster />} />
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
