import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { AccountError, accountRequest, api, configureSession } from "./api";

export interface Account {
  mode: "local" | "saas";
  workspace: string;
  workspace_name: string;
  role: string;
  csrf_token: string;
}

export function SessionGate({ children }: { children: (account: Account, logout: () => void) => ReactNode }) {
  const [ready, setReady] = useState(false);
  const [account, setAccount] = useState<Account | null>(null);
  const [accessKey, setAccessKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function profile() {
    const result: Account = await accountRequest("/auth/me");
    configureSession(result.mode === "saas", result.csrf_token);
    setAccount(result);
  }
  useEffect(() => {
    let live = true;
    void accountRequest("/auth/config").then(async (config) => {
      configureSession(config.mode === "saas");
      try { await profile(); } catch { if (live) setAccount(null); }
      if (live) setReady(true);
    }).catch((e: Error) => { if (live) { setError(e.message); setReady(true); } });
    const timer = setInterval(() => { void profile().catch((e: Error) => { if (live && e instanceof AccountError && e.status === 401) setAccount(null); }); }, 60000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await accountRequest("/auth/session", { access_key: accessKey }); setAccessKey(""); await profile(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function logout() {
    void accountRequest("/auth/logout", {}).then(() => { configureSession(true); setAccount(null); }).catch((e: Error) => setError(e.message));
  }
  if (!ready) return <main className="form-panel wide"><h1>Connecting to EnvForge…</h1></main>;
  if (account) return children(account, logout);
  return <main className="form-panel wide" style={{ margin: "10vh auto", maxWidth: 520 }}>
    <h1>Sign in to EnvForge</h1>
    <p>Use the individual workspace access key supplied by your administrator.</p>
    <form onSubmit={(event) => void login(event)}>
      <label className="form-label" htmlFor="account-key">Workspace access key</label>
      <input id="account-key" required type="password" autoComplete="off" value={accessKey} onChange={(event) => setAccessKey(event.target.value)} />
      <p className="form-desc">Your key is exchanged for a secure browser session. It is not saved in browser storage.</p>
      {error && <p role="alert" className="error-banner">{error}</p>}
      <button className="btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  </main>;
}

interface AccessKey { id: string; name: string; role: string; revoked_at: number | null; expires_at: number }

export function AccountPanel({ account, logout }: { account: Account; logout: () => void }) {
  const [keys, setKeys] = useState<AccessKey[]>([]);
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");
  const [createdKey, setCreatedKey] = useState("");
  const [error, setError] = useState("");
  async function load() { setKeys(await api<AccessKey[]>("/v1/workspace/keys")); }
  const [usage, setUsage] = useState<{ limits: { daily_episodes: number; daily_model_calls: number }; usage: { episodes?: number; model_calls?: number } } | null>(null);
  useEffect(() => {
    let live = true;
    if (account.role === "admin") void api<AccessKey[]>("/v1/workspace/keys").then((rows) => { if (live) setKeys(rows); }).catch((e: Error) => { if (live) setError(e.message); });
    void api<NonNullable<typeof usage>>("/v1/workspace/usage").then((result) => { if (live) setUsage(result); }).catch((e: Error) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [account.role]);
  async function create(event: FormEvent) {
    event.preventDefault(); setError(""); setCreatedKey("");
    try { const result = await api<{ access_key: string }>("/v1/workspace/keys", { name, role }); setCreatedKey(result.access_key); setName(""); await load(); }
    catch (e) { setError((e as Error).message); }
  }
  async function revoke(id: string) {
    try { await api(`/v1/workspace/keys/${id}/revoke`, {}); await load(); }
    catch (e) { setError((e as Error).message); }
  }
  return <section className="form-panel wide">
    <h2>{account.workspace_name}</h2><p>Your role: {account.role}</p>
    {usage && <p>Today's allowance (UTC): {usage.usage.episodes ?? 0}/{usage.limits.daily_episodes} admitted episodes · {usage.usage.model_calls ?? 0}/{usage.limits.daily_model_calls} provider requests.</p>}
    <button className="nav-btn outlined" onClick={logout}>Sign out</button>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {account.role === "admin" && <>
      <h3>Individual access keys</h3>
      <form onSubmit={(event) => void create(event)}>
        <label className="form-label" htmlFor="key-name">Team member or integration name</label>
        <input id="key-name" required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
        <label className="form-label" htmlFor="key-role">Role</label>
        <select id="key-role" value={role} onChange={(event) => setRole(event.target.value)}><option value="viewer">Viewer</option><option value="evaluator">Evaluator</option><option value="admin">Administrator</option></select>
        <button className="btn-primary">Create 30-day access key</button>
      </form>
      {createdKey && <div className="notice"><p>Copy this key now; it cannot be retrieved later.</p><code>{createdKey}</code><button onClick={() => setCreatedKey("")}>Hide key</button></div>}
      <ul>{keys.map((key) => <li key={key.id}>{key.name} · {key.role} · {key.revoked_at ? "Revoked" : new Date(key.expires_at * 1000).toLocaleDateString()} {!key.revoked_at && <button className="nav-btn outlined" onClick={() => void revoke(key.id)}>Revoke</button>}</li>)}</ul>
    </>}
  </section>;
}
