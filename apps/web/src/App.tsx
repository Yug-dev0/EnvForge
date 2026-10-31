import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Download,
  GitCompare,
  Layers,
  Play,
  RefreshCw,
  Server,
  Shield,
  Terminal,
  XCircle,
  Search,
  KeyRound,
  LockKeyhole,
  Menu,
  X,
} from "lucide-react";
import Dashboard from "./Dashboard";
import { RunProgress } from "./RunProgress";
import "./stitch.css";
import { api, downloadJson } from "./api";
import type {
  Agent,
  Environment,
  Episode,
  Evaluation,
  QA,
  Report,
  Step,
  Trajectory,
  Verification,
} from "./api";

const tabs = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "evaluations", label: "Evaluations & Runs", icon: Play },
  { id: "trajectory", label: "Trajectory Explorer", icon: Activity },
  { id: "environments", label: "Scenario Catalog", icon: Layers },
  { id: "compare", label: "Agent Benchmarks", icon: GitCompare },
  { id: "qa", label: "Verifier Assurance", icon: Shield },
  { id: "guards", label: "Safety Guards", icon: LockKeyhole },
  { id: "run", label: "Launch Evaluation", icon: Terminal },
  { id: "settings", label: "Connection Settings", icon: KeyRound },
] as const;
type Tab = (typeof tabs)[number]["id"];
const terminal = new Set(["COMPLETED", "CANCELLED", "INFRA_FAILED"]);
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

function Badge({ value }: { value: string }) {
  const kind = ["COMPLETED", "PASS", "APPROVED"].includes(value)
    ? "success"
    : ["INFRA_FAILED", "FAIL", "REJECTED", "BLOCK"].includes(value)
      ? "danger"
      : "cyan";
  return (
    <span className={`badge badge-${kind}`}>{value.replaceAll("_", " ")}</span>
  );
}
function Json({ value }: { value: unknown }) {
  return <pre className="code-block">{pretty(value)}</pre>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-state">{children}</div>;
}
function Metric({
  title,
  value,
  detail,
}: {
  title: string;
  value: string | number;
  detail: string;
}) {
  return (
    <div className="kpi-card">
      <div className="kpi-title">{title}</div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-sub">{detail}</div>
    </div>
  );
}

import { SessionGate, AccountPanel, type Account } from "./SessionGate";

export default function App() {
  return <SessionGate>{(account, logout) => <WorkspaceApp account={account} logout={logout} />}</SessionGate>;
}

function WorkspaceApp({ account, logout }: { account: Account; logout: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [runs, setRuns] = useState<Evaluation[]>([]);
  const [online, setOnline] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState(
    sessionStorage.getItem("envforge-token") ?? "",
  );
  const [selectedRun, setSelectedRun] = useState("");
  const [launchEnvironment, setLaunchEnvironment] = useState("sre.incident-response");
  const runDetailsRef = useRef<HTMLElement>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [selectedEpisode, setSelectedEpisode] = useState("");
  const [trajectory, setTrajectory] = useState<Trajectory | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [qa, setQA] = useState<QA | null>(null);
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0 });
  }, [tab]);
  const navigate = (next: Tab) => {
    setTab(next);
    setMenuOpen(false);
    setSearch("");
  };
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "k") {
        event.preventDefault();
        searchInput.current?.focus();
      }
      if (event.key === "Escape") {
        setSearch("");
        setMenuOpen(false);
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [catalog, registry, evaluations] = await Promise.all([
        api<Environment[]>("/v1/environments"),
        api<Agent[]>("/v1/agents?limit=200"),
        api<Evaluation[]>("/v1/evaluations?limit=200"),
      ]);
      setEnvironments(catalog);
      setAgents(registry);
      setRuns(evaluations);
      setOnline(true);
    } catch (e) {
      setOnline(false);
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    const first = setTimeout(() => void refresh(), 0);
    const timer = setInterval(() => void refresh(), 3000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh]);
  useEffect(() => {
    if (!selectedRun) return;
    let active = true;
    const load = async () => {
      try {
        const [items, exported] = await Promise.all([
          api<Episode[]>(`/v1/evaluations/${selectedRun}/episodes?limit=500`),
          api<Report>(`/v1/reports/${selectedRun}/export`),
        ]);
        if (active) {
          setEpisodes(items);
          setReport(exported);
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    };
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [selectedRun]);
  useEffect(() => {
    if (!selectedEpisode) return;
    let active = true;
    void Promise.all([
      api<Trajectory>(`/v1/episodes/${selectedEpisode}/trajectory`),
      api<Verification>(`/v1/episodes/${selectedEpisode}/verification`),
    ])
      .then(([trace, result]) => {
        if (active) {
          setTrajectory(trace);
          setVerification(result);
          setStepIndex(0);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [selectedEpisode]);
  useEffect(() => {
    if (online && ["qa", "overview", "guards"].includes(tab))
      void api<QA>("/v1/environments/sre.incident-response/qa")
        .then(setQA)
        .catch((e) => setError(e.message));
  }, [tab, online]);

  async function perform(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function chooseRun(id: string) {
    setSearch("");
    setMenuOpen(false);
    setSelectedRun(id);
    if (id !== selectedRun) {
      setEpisodes([]);
      setReport(null);
      setSelectedEpisode("");
      setTrajectory(null);
      setVerification(null);
    }
    setTab("evaluations");
  }
  const selected = runs.find((run) => run.id === selectedRun);
  useEffect(() => {
    if (tab === "evaluations" && selected?.id) {
      runDetailsRef.current?.focus({ preventScroll: true });
      runDetailsRef.current?.scrollIntoView({ block: "start" });
    }
  }, [tab, selected?.id]);
  const activeRuns = runs.filter((run) => !terminal.has(run.status)).length;
  const matchingRuns = runs.filter((run) =>
    `${run.name} ${run.id} ${run.suite} ${Array.isArray(run.manifest?.seeds) ? run.manifest.seeds.join(" ") : ""} ${agents.find((a) => a.id === run.agent_id)?.name ?? ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const step: Step | undefined = trajectory?.action_history[stepIndex];

  return (
    <div className="app-container">
      {menuOpen && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={`sidebar ${menuOpen ? "is-open" : ""}`}>
        <div className="brand">
          <span className="brand-icon">
            <Shield size={18} />
          </span>
          <span>
            ENV<span className="brand-accent">FORGE</span>
          </span>
          <small className="version-tag">
            v{environments[0]?.version ?? "—"}
          </small>
        </div>
        <p className="sidebar-label">Evaluation workspace</p>
        <nav className="nav-links" aria-label="Main navigation">
          {tabs.map((item) => (
            <button
              key={item.id}
              className={`nav-btn ${tab === item.id ? "active" : ""}`}
              onClick={() => navigate(item.id)}
              aria-current={tab === item.id ? "page" : undefined}
            >
              <item.icon size={15} />
              {item.label}
              {item.id === "evaluations" && activeRuns > 0 && (
                <span className="nav-count">{activeRuns} LIVE</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-runtime">
          <div className="row">
            <span>CONTROL PLANE</span>
            <span className={online ? "text-green" : "text-amber"}>
              {online ? "CONNECTED" : "OFFLINE"}
            </span>
          </div>
          <p>
            <Server size={14} /> SRE simulation runtime
          </p>
          <div className="row">
            <span>{account.workspace_name}</span>
            <span>{agents.length} agents</span>
          </div>
        </div>
      </aside>
      <header className="workspace-bar">
        <button
          className="icon-button mobile-menu"
          aria-label={menuOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen(!menuOpen)}
        >
          {menuOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
        <div className="workspace-id">
          <Server size={14} /> {account.workspace_name}{" "}
          <span className="version-tag">SIMULATION</span>
        </div>
        <div className="global-search">
          <Search size={16} />
          <input
            ref={searchInput}
            aria-label="Search evaluations and scenarios"
            placeholder="Search evaluations, agents, scenarios…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <kbd>⌘ K</kbd>
          {search.trim() && (
            <div className="search-results">
              <p>SEARCH RESULTS · LATEST 200 RUNS</p>
              {matchingRuns.slice(0, 6).map((run) => (
                <button key={run.id} onClick={() => chooseRun(run.id)}>
                  <Play size={14} />
                  <span>
                    {run.name}
                    <small>
                      {run.suite} / {run.tier} · {run.status}
                    </small>
                  </span>
                  <ArrowRight size={14} />
                </button>
              ))}
              {environments
                .filter((env) =>
                  `${env.name} ${env.supported_suites.join(" ")}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
                )
                .map((env) => (
                  <button key={env.id} onClick={() => navigate("environments")}>
                    <Layers size={14} />
                    <span>
                      {env.name}
                      <small>Scenario catalog</small>
                    </span>
                    <ArrowRight size={14} />
                  </button>
                ))}
              {!matchingRuns.length &&
                !environments.some((env) =>
                  `${env.name} ${env.supported_suites.join(" ")}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
                ) && <p>No matching evaluations or scenarios.</p>}
            </div>
          )}
        </div>
        <span className="server-pill">
          <span className={`status-dot ${online ? "" : "offline"}`} />
          {online ? "API ONLINE" : "API OFFLINE"}
        </span>
        <button
          className="icon-button"
          aria-label="Connection settings"
          onClick={() => navigate("settings")}
        >
          <KeyRound size={16} />
        </button>
      </header>
      <main className="main-content">
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button onClick={() => setError("")} aria-label="Dismiss error">
              Dismiss
            </button>
          </div>
        )}
        {account.mode === "local" && (!online || tab === "settings") && (
          <form
            className="connection-panel"
            onSubmit={(e) => {
              e.preventDefault();
              sessionStorage.setItem("envforge-token", token);
              setError("");
              void refresh();
            }}
          >
            <strong>Connect to your control plane</strong>
            <p>
              Start the API, then enter its workspace token if one is
              configured.
            </p>
            <label htmlFor="token">Workspace API token</label>
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
            <button className="btn-primary">Connect</button>
          </form>
        )}
        {tab !== "overview" && (
          <div className="section-header">
            <div>
              <p className="eyebrow">AGENT EVALUATION / SRE</p>
              <h1 className="section-title">
                {tabs.find((item) => item.id === tab)?.label}
              </h1>
              <p className="section-desc">
                Reproducible incidents. Recorded actions. Verifiable outcomes.
              </p>
            </div>
            <button
              className="nav-btn outlined"
              onClick={() => void refresh()}
              aria-label="Refresh"
            >
              <RefreshCw size={16} />
              Refresh
            </button>
          </div>
        )}

        {tab === "overview" && (
          <Dashboard
            runs={runs}
            agents={agents}
            environments={environments}
            qa={qa}
            online={online}
            navigate={navigate}
            chooseRun={chooseRun}
            inspect={(runId, episodeId) => {
              chooseRun(runId);
              setSelectedEpisode(episodeId);
              setTab("trajectory");
            }}
            exportLatest={() =>
              void perform(async () => {
                if (runs[0])
                  downloadJson(
                    await api(`/v1/reports/${runs[0].id}/export`),
                    `${runs[0].id}.json`,
                  );
              })
            }
          />
        )}

        {tab === "guards" && (
          <section className="form-panel wide">
            <h2>Safety guards</h2>
            <p className="section-desc">
              Enforced checks for the typed SRE simulation.
            </p>
            <div className="guard-grid">
              {[
                [
                  "Protected resources",
                  "Database and cache mutations are checked for collateral damage.",
                ],
                [
                  "Healthy services",
                  "Unnecessary changes to unaffected services cause a hard failure.",
                ],
                [
                  "Agent access",
                  "Agents receive observations and typed tools. Private fault truth stays with the evaluator.",
                ],
                [
                  "Evidence integrity",
                  "Snapshots and action traces are sealed before verification in a separate process.",
                ],
              ].map(([title, detail]) => (
                <article className="stage-card" key={title}>
                  <Shield size={17} />
                  <h3>{title}</h3>
                  <p>{detail}</p>
                </article>
              ))}
            </div>
            <p className="notice">
              These are simulation checks. Hardware attestation, microVM
              containment, and kernel-level auditing are not part of this
              runtime.
            </p>
            <button className="nav-btn outlined" onClick={() => navigate("qa")}>
              View exploit checks <ArrowRight size={14} />
            </button>
          </section>
        )}
        {tab === "settings" && account.mode === "saas" && <AccountPanel account={account} logout={logout} />}
        {tab === "settings" && account.mode === "local" && (
          <section className="form-panel wide">
            <h2>Workspace connection</h2>
            <p className="section-desc">
              The token is stored for this browser tab’s session and sent to
              your configured control plane. Agent endpoint credentials are
              configured separately when registering an agent.
            </p>
            <button
              className="nav-btn outlined"
              onClick={() => {
                sessionStorage.removeItem("envforge-token");
                setToken("");
                setRuns([]);
                setAgents([]);
                setEnvironments([]);
                setSelectedRun("");
                setSelectedEpisode("");
                setTrajectory(null);
                setVerification(null);
                setReport(null);
                setQA(null);
                void refresh();
              }}
            >
              Clear saved token
            </button>
            <button
              className="nav-btn outlined"
              onClick={() => navigate("run")}
            >
              Manage agent connections <ArrowRight size={14} />
            </button>
          </section>
        )}

        {tab === "environments" && (
          <div className="catalog-grid">
            {environments.map((env) => (
              <article className="form-panel" key={env.id}>
                <div className="row">
                  <Server color="#38bdf8" />
                  <Badge value={`v${env.version}`} />
                </div>
                <h2>{env.name}</h2>
                <p className="section-desc">{env.description}</p>
                <h3>Agent tools</h3>
                <div className="tool-chips">
                  {env.tools.map((tool) => (
                    <code key={tool}>{tool}</code>
                  ))}
                </div>
                <h3>Execution model</h3>
                <p>{env.execution}</p>
                <h3>Current limits</h3>
                <ul>
                  {env.limitations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                {env.reason && <p className="notice">{env.reason}</p>}
                <button className="btn-primary" disabled={env.enabled === false} onClick={() => { setLaunchEnvironment(env.id); setTab("run"); }}>
                  Run evaluation
                  <ArrowRight size={16} />
                </button>
              </article>
            ))}
            {!environments.length && <Empty>No environments loaded.</Empty>}
          </div>
        )}

        {tab === "run" && (
          account.role === "viewer" ? <Empty>Your viewer account can inspect runs. Ask an administrator for evaluator access to launch evaluations.</Empty> :
          <RunBuilder
            initialEnvironmentId={launchEnvironment}
            saas={account.mode === "saas"}
            agents={agents}
            environments={environments}
            busy={busy}
            perform={perform}
            onCreated={(run) => chooseRun(run.id)}
          />
        )}

        {tab === "evaluations" && (
          <>
            {!selected && <RunTable runs={runs} agents={agents} choose={chooseRun} />}
            {selected && (
              <section ref={runDetailsRef} className="detail-section" tabIndex={-1} aria-label="Run details" style={{ scrollMarginTop: "5rem" }}>
                <button
                  className="nav-btn outlined"
                  onClick={() => {
                    setSelectedRun("");
                    setEpisodes([]);
                    setReport(null);
                    setSelectedEpisode("");
                    setTrajectory(null);
                    setVerification(null);
                  }}
                >
                  <ArrowRight size={16} style={{ transform: "rotate(180deg)" }} />
                  Back to runs
                </button>
                <div className="row">
                  <div>
                    <h2>{selected.name}</h2>
                    <code>{selected.id}</code>
                  </div>
                  <div className="row">
                    <Badge value={selected.status} />
                    {!terminal.has(selected.status) && account.role !== "viewer" && (
                      <button
                        className="nav-btn outlined"
                        disabled={busy}
                        onClick={() =>
                          void perform(async () => {
                            await api(
                              `/v1/evaluations/${selected.id}/cancel`,
                              {},
                            );
                          })
                        }
                      >
                        Cancel run
                      </button>
                    )}
                    <button
                      className="btn-primary"
                      onClick={() =>
                        void perform(async () =>
                          downloadJson(
                            await api(`/v1/reports/${selected.id}/export`),
                            `${selected.id}.json`,
                          ),
                        )
                      }
                    >
                      <Download size={16} />
                      Export report
                    </button>
                  </div>
                </div>
                {selected.error && (
                  <p className="error-banner">{selected.error}</p>
                )}
                {selected.status === "RETRY_WAIT" && <p className="notice">The provider or connection is temporarily unavailable. Retry {selected.retry_count}/3 is scheduled for {new Date(selected.next_attempt_at * 1000).toLocaleTimeString()}. Completed episodes are preserved; this interruption is not an agent score.</p>}
                <AttemptHistory evaluationId={selected.id} status={selected.status} />
                <RunProgress key={selected.id} evaluationId={selected.id} status={selected.status} />
                {selected.status === "INFRA_FAILED" && selected.completed_episodes === 0 && (
                  <p className="notice">
                    No episodes completed. This is a setup or provider failure, not an agent score. Test the agent connection before starting a new run.
                  </p>
                )}
                {selected?.manifest?.baseline_uses_private_truth === true && (
                  <p className="notice">
                    Reference oracle: this solver receives private fault
                    information. Its pass rate checks solvability and cannot be
                    used as a model performance claim.
                  </p>
                )}
                {report && (
                  <div className="kpi-grid">
                    <Metric
                      title="Completed"
                      value={`${report.metrics.completed_episodes}/${report.metrics.total_episodes}`}
                      detail="Recorded episode evidence"
                    />
                    <Metric
                      title="Full pass rate"
                      value={percent(report.metrics.success_rate)}
                      detail={
                        report.metrics.success_rate_95_ci
                          ? `95% interval ${report.metrics.success_rate_95_ci.map(percent).join(" – ")}`
                          : "No completed episodes"
                      }
                    />
                    <Metric title="Recovery" value={percent(report.metrics.recovery_rate)} detail="Fault resolved and sustained healthy probes" />
                    <Metric title="Safety" value={percent(report.metrics.safety_pass_rate)} detail="Integrity, protected resources and safe changes" />
                    <Metric title="Report quality" value={percent(report.metrics.report_quality_rate)} detail="Correct diagnosis and valid evidence" />
                    <Metric
                      title="Release gate"
                      value={report.metrics.release_gate_status.replaceAll(
                        "_",
                        " ",
                      )}
                      detail="Policy: ≥30 episodes, ≥80% pass, no safety failures"
                    />
                  </div>
                )}
                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>Episode</th>
                        <th>Seed</th>
                        <th>Outcome</th>
                        <th>Reward</th>
                        <th>Inspect</th>
                      </tr>
                    </thead>
                    <tbody>
                      {episodes.map((ep) => (
                        <tr key={ep.id}>
                          <td>
                            <code>{ep.id.slice(0, 18)}</code>
                          </td>
                          <td>{ep.seed}</td>
                          <td>
                            <Badge value={ep.success ? "PASS" : "FAIL"} />
                          </td>
                          <td>{ep.reward.toFixed(2)}</td>
                          <td>
                            <button
                              className="nav-btn"
                              onClick={() => {
                                setSelectedEpisode(ep.id);
                                setTrajectory(null);
                                setVerification(null);
                                setTab("trajectory");
                              }}
                            >
                              Explore
                              <ArrowRight size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!episodes.length && (
                    <Empty>
                      Episode results will appear as the run progresses.
                    </Empty>
                  )}
                </div>
                {report &&
                  Object.keys(report.metrics.failure_clusters).length > 0 && (
                    <>
                      <h3>Failure clusters</h3>
                      <Json value={report.metrics.failure_clusters} />
                    </>
                  )}
              </section>
            )}
          </>
        )}

        {tab === "trajectory" && (
          <>
            <div className="row selection-row">
              <label htmlFor="trace-run">Evaluation</label>
              <select
                id="trace-run"
                value={selectedRun}
                onChange={(e) => {
                  chooseRun(e.target.value);
                  setTab("trajectory");
                }}
              >
                <option value="">Choose run</option>
                {runs.map((run) => (
                  <option value={run.id} key={run.id}>
                    {run.name}
                  </option>
                ))}
              </select>
              <label htmlFor="trace-episode">Episode</label>
              <select
                id="trace-episode"
                value={selectedEpisode}
                onChange={(e) => {
                  setSelectedEpisode(e.target.value);
                  setTrajectory(null);
                  setVerification(null);
                }}
              >
                <option value="">Choose episode</option>
                {episodes.map((ep) => (
                  <option key={ep.id} value={ep.id}>
                    Seed {ep.seed} / {ep.success ? "pass" : "fail"}
                  </option>
                ))}
              </select>
            </div>
            {trajectory && verification ? (
              <div className="trajectory-explorer">
                <aside className="pane">
                  <div className="pane-header">
                    Action timeline
                    <span>{trajectory.action_history.length} steps</span>
                  </div>
                  <div className="pane-body">
                    {trajectory.action_history.map((item, i) => (
                      <button
                        className={`step-item ${stepIndex === i ? "selected" : ""}`}
                        key={item.action_id}
                        onClick={() => setStepIndex(i)}
                      >
                        <small>STEP {item.step}</small>
                        {trajectory.agent_run && (
                          <small>{trajectory.agent_run.decisions.find((decision) => decision.action_id === item.action_id)?.source === "recovery_policy" ? "Automatic recovery check" : "Model decision"}</small>
                        )}
                        <div className="step-action-name">{item.tool}</div>
                        <code>{item.action_id}</code>
                      </button>
                    ))}
                    <p className="section-desc">
                      Wall duration: {trajectory.duration_seconds?.toFixed(2)}s
                    </p>
                    {trajectory.agent_run && (
                      <p className="section-desc">
                        {trajectory.agent_run.usage.model_calls} model calls · {trajectory.agent_run.usage.total_tokens.toLocaleString()} tokens
                      </p>
                    )}
                  </div>
                </aside>
                <section className="pane">
                  <div className="pane-header">Observation & action</div>
                  <div className="pane-body">
                    <h3>Task prompt</h3>
                    <p className="prompt">
                      {trajectory.initial_observation.prompt}
                    </p>
                    {step ? (
                      <>
                        <h3>Action</h3>
                        <Json
                          value={{
                            action_id: step.action_id,
                            tool: step.tool,
                            arguments: step.arguments,
                          }}
                        />
                        <h3>Tool output</h3>
                        <pre className="code-block">{step.output}</pre>
                        <h3>State changes</h3>
                        <Json value={step.state_diff} />
                      </>
                    ) : (
                      <Empty>The agent took no actions.</Empty>
                    )}
                  </div>
                </section>
                <aside className="pane">
                  <div className="pane-header">
                    Verifier v{verification.verifier_version}
                    <Badge value={verification.success ? "PASS" : "FAIL"} />
                  </div>
                  <div className="pane-body">
                    <Metric
                      title="Reward"
                      value={verification.reward.toFixed(2)}
                      detail={verification.reason}
                    />
                    {Object.entries(verification.checks).map(
                      ([name, passed]) => (
                        <div className="verifier-check-row" key={name}>
                          <span>{name.replaceAll("_", " ")}</span>
                          {passed ? (
                            <CheckCircle2 size={17} color="#10b981" />
                          ) : (
                            <XCircle size={17} color="#f87171" />
                          )}
                        </div>
                      ),
                    )}
                    {verification.hard_failures.map((failure) => (
                      <p className="error-banner" key={failure}>
                        {failure}
                      </p>
                    ))}
                    <h3>Sealed artifact</h3>
                    <code className="digest">{trajectory.state_digest}</code>
                    <details>
                      <summary>Probe evidence</summary>
                      <Json value={verification.metadata} />
                    </details>
                    <button
                      className="nav-btn outlined"
                      onClick={() =>
                        downloadJson(
                          { trajectory, verification },
                          `${selectedEpisode}.json`,
                        )
                      }
                    >
                      <Download size={15} />
                      Export trajectory
                    </button>
                  </div>
                </aside>
              </div>
            ) : (
              <Empty>
                Select an evaluation and a completed episode to inspect its
                recorded trajectory.
              </Empty>
            )}
          </>
        )}

        {tab === "compare" && (
          <Compare runs={runs} perform={perform} busy={busy} />
        )}
        {tab === "qa" && (
          <div className="form-panel wide">
            <div className="row">
              <h2>Verifier hardening report</h2>
              <Badge value={qa?.status ?? "LOADING"} />
            </div>
            <p className="section-desc">
              Results from the local QA command. Published claims are limited to
              the checks actually executed.
            </p>
            {qa?.instances_tested ? (
              <>
                <div className="kpi-grid">
                  <Metric
                    title="Instances checked"
                    value={qa.instances_tested}
                    detail="Across four suites and three tiers"
                  />
                  <Metric
                    title="Oracle successes"
                    value={qa.reference_successes ?? 0}
                    detail="Solvability checks"
                  />
                  <Metric
                    title="Determinism"
                    value={qa.determinism_passed ? "PASS" : "FAIL"}
                    detail={`${qa.determinism_repeats} repeated action traces`}
                  />
                </div>
                {Object.entries(qa.exploit_checks ?? {}).map(
                  ([name, passed]) => (
                    <div className="verifier-check-row" key={name}>
                      <span>{name.replaceAll("_", " ")}</span>
                      <Badge value={passed ? "PASS" : "FAIL"} />
                    </div>
                  ),
                )}
                <h3>Manifest digest</h3>
                <code className="digest">{qa.manifest_digest}</code>
              </>
            ) : (
              <Empty>
                Run <code>uv run envforge qa --output data/qa-report.json</code>{" "}
                to produce this report.
              </Empty>
            )}
            <h3>Evidence limits</h3>
            <ul>
              {(
                qa?.limitations ?? [
                  "No independent model calibration yet",
                  "Human SRE review is still required",
                ]
              ).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </main>
      <footer>
        EnvForge · SRE world v{environments[0]?.version ?? "—"} · {account.workspace_name} · Recorded results only
      </footer>
    </div>
  );
}

function AttemptHistory({ evaluationId, status }: { evaluationId: string; status: string }) {
  const [attempts, setAttempts] = useState<{ id: string; seed: number; status: string; error: string; created_at: number }[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    void api<typeof attempts>(`/v1/evaluations/${evaluationId}/attempts`).then((rows) => { if (live) { setAttempts(rows); setError(""); } }).catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [evaluationId, status]);
  return <details><summary>Infrastructure attempts ({attempts.length})</summary>{error && <p role="alert">{error}</p>}{attempts.map((attempt) => <p key={attempt.id}>Seed {attempt.seed} · {attempt.status} · {attempt.error} <button className="nav-btn outlined" onClick={() => void api(`/v1/evaluations/${evaluationId}/attempts/${attempt.id}`).then((trace) => downloadJson(trace, `${attempt.id}.json`)).catch((e: Error) => setError(e.message))}>Download unscored trace</button></p>)}</details>;
}

function RunTable({
  runs,
  agents,
  choose,
}: {
  runs: Evaluation[];
  agents: Agent[];
  choose: (id: string) => void;
}) {
  return (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Evaluation</th>
            <th>Agent</th>
            <th>Suite / tier</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Pass rate</th>
            <th>Open</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <strong>{run.name}</strong>
                <small className="block">
                  {new Date(run.created_at * 1000).toLocaleString()}
                </small>
              </td>
              <td>
                {agents.find((a) => a.id === run.agent_id)?.name ??
                  run.agent_id.slice(0, 14)}
              </td>
              <td>
                {run.suite} / {run.tier}
              </td>
              <td>
                <Badge value={run.status} />
              </td>
              <td>
                {run.completed_episodes}/{run.total_episodes}
              </td>
              <td>
                {percent(
                  run.completed_episodes
                    ? run.success_count / run.completed_episodes
                    : null,
                )}
              </td>
              <td>
                <button
                  className="nav-btn"
                  aria-label={`Open ${run.name}`}
                  onClick={() => choose(run.id)}
                >
                  <ArrowRight size={17} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!runs.length && (
        <Empty>
          No evaluations yet. Register an agent and launch your first run.
        </Empty>
      )}
    </div>
  );
}

function RunBuilder({
  initialEnvironmentId,
  saas,
  agents,
  environments,
  busy,
  perform,
  onCreated,
}: {
  initialEnvironmentId: string;
  saas: boolean;
  agents: Agent[];
  environments: Environment[];
  busy: boolean;
  perform: (task: () => Promise<void>) => Promise<void>;
  onCreated: (run: Evaluation) => void;
}) {
  const [agentId, setAgentId] = useState("");
  const [name, setName] = useState("SRE evaluation");
  const [suite, setSuite] = useState("standard");
  const [tier, setTier] = useState("easy");
  const [count, setCount] = useState(initialEnvironmentId.startsWith("live.") ? 1 : 10);
  const [environmentId, setEnvironmentId] = useState(initialEnvironmentId);
  const [seed, setSeed] = useState(42);
  const [agentName, setAgentName] = useState("EnvForge Gemini");
  const [adapter, setAdapter] = useState("gemini");
  const [model, setModel] = useState("gemini-3.5-flash-lite");
  const [endpoint, setEndpoint] = useState("http://127.0.0.1:9001/agent");
  const [tokenEnv, setTokenEnv] = useState("GEMINI_API_KEY");
  const [credential, setCredential] = useState("");
  const [handshake, setHandshake] = useState("");
  const env = environments.find((item) => item.id === environmentId) ?? environments[0];
  const effectiveAgent = agents.find((agent) => agent.id === agentId);
  function create(e: FormEvent) {
    e.preventDefault();
    void perform(async () => {
      const run = await api<Evaluation>("/v1/evaluations", {
        name,
        agent_id: agentId,
        environment_id: env.id,
        environment_version: env.version,
        suite,
        tier,
        episodes_count: count,
        base_seed: seed,
      });
      onCreated(run);
    });
  }
  return (
    <div className="builder-grid">
      <form className="form-panel" onSubmit={create}>
        <h2>Launch an evaluation</h2>
        <label className="form-label" htmlFor="run-environment">Environment</label>
        <select id="run-environment" value={env?.id ?? ""} onChange={(event) => {
          const selected = environments.find((item) => item.id === event.target.value);
          if (!selected) return;
          setEnvironmentId(selected.id); setSuite(selected.supported_suites[0]); setTier(selected.supported_tiers[0]);
          if (selected.id.startsWith("live.")) setCount(1);
        }}>
          {environments.map((item) => <option key={item.id} value={item.id} disabled={item.enabled === false}>{item.name}{item.enabled === false ? " (setup required)" : ""}</option>)}
        </select>
        {env?.id.startsWith("live.") && <p className="notice">Real disposable PostgreSQL, shell and Chromium. Local preview only. Live shell actions cannot resume after interruption. Maximum three episodes.</p>}
        <label className="form-label" htmlFor="run-name">
          Run name
        </label>
        <input
          id="run-name"
          required
          maxLength={160}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label className="form-label" htmlFor="run-agent">
          Registered agent
        </label>
        <select
          id="run-agent"
          required
          value={agentId}
          onChange={(e) => {
            setAgentId(e.target.value);
            setHandshake("");
          }}
        >
          <option value="">Choose agent</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name} ({agent.adapter})
            </option>
          ))}
        </select>
        {effectiveAgent?.adapter === "reference_solver" && (
          <p className="notice">
            Oracle baseline: verifies setup and solvability using private fault
            truth.
          </p>
        )}
        {effectiveAgent?.adapter === "http_endpoint" && (
          <p className="notice">
            This agent needs a running external service. Use Test connection to check it before launch. For the built-in runner, choose an EnvForge Gemini agent.
          </p>
        )}
        <div className="form-columns">
          <div>
            <label className="form-label" htmlFor="suite">
              Suite
            </label>
            <select
              id="suite"
              value={suite}
              onChange={(e) => setSuite(e.target.value)}
            >
              {env?.supported_suites.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="form-label" htmlFor="tier">
              Difficulty
            </label>
            <select
              id="tier"
              value={tier}
              onChange={(e) => setTier(e.target.value)}
            >
              {env?.supported_tiers.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="form-label" htmlFor="count">
              Episodes
            </label>
            <input
              id="count"
              type="number"
              min={1}
              max={env?.id.startsWith("live.") ? 3 : 500}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
          </div>
          <div>
            <label className="form-label" htmlFor="seed">
              Base seed
            </label>
            <input
              id="seed"
              type="number"
              min={0}
              max={2147483147}
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </div>
        </div>
        <p className="form-desc">
          Environment v{env?.version ?? "—"}. Same seeds and versions are
          required for comparisons. {env?.id.startsWith("live.") ? "Live preview runs are limited to three episodes." : "Runs are limited to 500 episodes."}
        </p>
        <div className="row">
          <button
            type="button"
            disabled={!agentId || busy}
            className="nav-btn outlined"
            onClick={() =>
              void perform(async () => {
                const result = await api<{ roundtrip_latency_ms: number }>(
                  `/v1/agents/${agentId}/test-connection`,
                  {},
                );
                setHandshake(`Connected in ${result.roundtrip_latency_ms} ms`);
              })
            }
          >
            Test connection
          </button>
          <button className="btn-primary" disabled={busy || !agentId || !env || env.enabled === false}>
            <Play size={16} />
            {busy ? "Submitting…" : "Launch run"}
          </button>
        </div>
        <p role="status">{handshake}</p>
      </form>
      <form
        className="form-panel"
        onSubmit={(e) => {
          e.preventDefault();
          void perform(async () => {
            const agent = await api<Agent>("/v1/agents", {
              name: agentName,
              adapter: ["reference_solver", "gemini"].includes(adapter) ? adapter : "http_endpoint",
              ...(adapter === "gemini" ? { model, token_env: tokenEnv || "GEMINI_API_KEY" } : {}),
              ...(["holmesgpt", "kubectl_ai"].includes(adapter)
                ? { metadata: { integration: adapter } }
                : {}),
              ...(!["reference_solver", "gemini"].includes(adapter)
                ? {
                    endpoint_url: endpoint,
                    ...(saas ? (credential ? { credential } : {}) : (tokenEnv ? { token_env: tokenEnv } : {})),
                  }
                : {}),
            });
            setAgentId(agent.id);
            setCredential("");
          });
        }}
      >
        <h2>Register an agent</h2>
        <p className="section-desc">
          Start with EnvForge Gemini, or connect another agent for comparison.
        </p>
        <label className="form-label" htmlFor="agent-name">
          Agent name
        </label>
        <input
          id="agent-name"
          required
          value={agentName}
          maxLength={160}
          onChange={(e) => setAgentName(e.target.value)}
        />
        <label className="form-label" htmlFor="adapter">
          Adapter
        </label>
        <select
          id="adapter"
          value={adapter}
          onChange={(e) => {
            const value = e.target.value;
            setAdapter(value);
            if (value === "gemini") {
              setAgentName("EnvForge Gemini");
              setTokenEnv("GEMINI_API_KEY");
            } else if (value === "reference_solver") {
              setAgentName("Reference oracle");
              setTokenEnv("");
            } else if (value === "http_endpoint") {
              setAgentName("HTTP agent");
              setTokenEnv("");
            }
            if (value === "holmesgpt" || value === "kubectl_ai") {
              setAgentName(value === "holmesgpt" ? "HolmesGPT" : "kubectl-ai");
              setEndpoint(`http://127.0.0.1:${value === "holmesgpt" ? 9002 : 9003}/agent`);
              setTokenEnv("ENVFORGE_BRIDGE_TOKEN");
            }
          }}
        >
          <option value="gemini">EnvForge Gemini (recommended)</option>
          <option value="reference_solver">
            Reference oracle (setup only)
          </option>
          <option value="http_endpoint">HTTP action endpoint</option>
          {!saas && <option value="holmesgpt">HolmesGPT (native agent bridge)</option>}
          {!saas && <option value="kubectl_ai">kubectl-ai (native agent bridge)</option>}
        </select>
        {adapter === "gemini" && (
          <>
            <p className="form-desc">{env?.id.startsWith("live.") ? "Gemini chooses shell and browser actions. Trusted probes check the real workload after the episode." : "Runs directly with Gemini. Evidence is attached automatically and each change is followed by recovery checks."}</p>
            <label className="form-label" htmlFor="gemini-model">Gemini model</label>
            <input id="gemini-model" required value={model} pattern="gemini-[a-zA-Z0-9.-]+" onChange={(e) => setModel(e.target.value)} />
            {!saas && <><label className="form-label" htmlFor="gemini-credential">Credential variable</label>
            <input id="gemini-credential" required value={tokenEnv} pattern="[A-Z][A-Z0-9_]*" onChange={(e) => setTokenEnv(e.target.value)} />
            <p className="form-desc">Set this variable on the API and worker. Enter the variable name, never the API key.</p></>}
            {saas && <p className="form-desc">Uses the managed provider account within your workspace allowance.</p>}
          </>
        )}
        {!["reference_solver", "gemini"].includes(adapter) && (
          <>
            {["holmesgpt", "kubectl_ai"].includes(adapter) && (
              <p className="form-desc">
                Start the matching bridge and configure its model provider first.
                Each episode runs the native agent in a fresh container. See deploy/agents/README.md for setup.
              </p>
            )}
            <label className="form-label" htmlFor="endpoint">
              Action endpoint URL
            </label>
            <input
              id="endpoint"
              type="url"
              value={endpoint}
              required
              onChange={(e) => setEndpoint(e.target.value)}
            />
            {saas ? <><label className="form-label" htmlFor="endpoint-secret">Endpoint access token (optional)</label><input id="endpoint-secret" type="password" autoComplete="off" value={credential} maxLength={4096} onChange={(e) => setCredential(e.target.value)} /><p className="form-desc">Stored encrypted for this workspace. Your operator must approve the public HTTPS endpoint host.</p></> : <><label className="form-label" htmlFor="credential">
              Credential variable (optional)
            </label>
            <input
              id="credential"
              value={tokenEnv}
              placeholder="MY_AGENT_TOKEN"
              pattern="[A-Z][A-Z0-9_]*"
              onChange={(e) => setTokenEnv(e.target.value)}
            />
            <p className="form-desc">
              Name of an environment variable set on the server. Never enter the
              secret itself.
            </p></>}
            <details>
              <summary>HTTP protocol</summary>
              <Json
                value={{
                  request: {
                    operation: "act",
                    episode_id: "…",
                    observation: { prompt: "…", tool_output: "…", step: 0 },
                    tools: ["JSON schemas"],
                  },
                  response: {
                    action: {
                      action_id: "unique-1",
                      tool: "read_logs",
                      arguments: { service: "checkout-api" },
                    },
                  },
                }}
              />
              <p>
                For health requests, return{" "}
                <code>{'{"status":"healthy"}'}</code>. Return{" "}
                <code>{'{"action":null}'}</code> to stop.
              </p>
            </details>
          </>
        )}
        <button className="btn-primary" disabled={busy}>
          Register agent
        </button>
      </form>
    </div>
  );
}

function Compare({
  runs,
  perform,
  busy,
}: {
  runs: Evaluation[];
  perform: (task: () => Promise<void>) => Promise<void>;
  busy: boolean;
}) {
  const [baseline, setBaseline] = useState("");
  const [candidate, setCandidate] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const completed = runs.filter((run) => run.status === "COMPLETED");
  return (
    <div className="form-panel wide">
      <h2>Compare two agent builds</h2>
      <p className="section-desc">
        Uses paired episodes with identical environment, verifier, scenario
        version, suite, tier, and seeds.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setResult(null);
          void perform(async () =>
            setResult(
              await api("/v1/comparisons", {
                baseline_eval_id: baseline,
                candidate_eval_id: candidate,
              }),
            ),
          );
        }}
      >
        <div className="form-columns">
          <div>
            <label className="form-label" htmlFor="baseline">
              Baseline
            </label>
            <select
              id="baseline"
              required
              value={baseline}
              onChange={(e) => {
                setBaseline(e.target.value);
                setResult(null);
              }}
            >
              <option value="">Choose run</option>
              {completed.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="form-label" htmlFor="candidate">
              Candidate
            </label>
            <select
              id="candidate"
              required
              value={candidate}
              onChange={(e) => {
                setCandidate(e.target.value);
                setResult(null);
              }}
            >
              <option value="">Choose run</option>
              {completed
                .filter((run) => run.id !== baseline)
                .map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.name}
                  </option>
                ))}
            </select>
          </div>
        </div>
        <button
          className="btn-primary"
          disabled={busy || !baseline || !candidate}
        >
          <GitCompare size={17} />
          Compare runs
        </button>
      </form>
      {result && (
        <>
          <div className="row">
            <h3>Comparison result</h3>
            <Badge value={String(result.recommendation)} />
          </div>
          <Json value={result} />
        </>
      )}
    </div>
  );
}
