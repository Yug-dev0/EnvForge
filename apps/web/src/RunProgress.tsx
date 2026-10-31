import { useEffect, useRef, useState } from "react";
import { api, type Step } from "./api";

interface Progress {
  status: string;
  scored: false;
  trace_unavailable?: boolean;
  episode: {
    id: string;
    seed: number;
    last_step: number;
    actions: (Pick<Step, "step" | "action_id" | "tool" | "arguments" | "output"> & { source: string })[];
    pending_action: { tool: string } | null;
  } | null;
}

export function RunProgress({ evaluationId, status }: { evaluationId: string; status: string }) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState("");
  const cursor = useRef({ episode: "", step: 0 });
  const active = ["QUEUED", "RUNNING", "RETRY_WAIT"].includes(status);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const query = new URLSearchParams({ episode_id: cursor.current.episode, after_step: String(cursor.current.step) });
        const result = await api<Progress>(`/v1/evaluations/${evaluationId}/progress?${query}`);
        if (!live) return;
        setProgress((previous) => {
          if (!result.episode) return result;
          const before = previous?.episode?.id === result.episode.id ? previous.episode.actions : [];
          const actions = Array.from(new Map([...before, ...result.episode.actions].map((action) => [action.action_id, action])).values());
          return { ...result, episode: { ...result.episode, actions } };
        });
        cursor.current = { episode: result.episode?.id ?? "", step: result.episode?.last_step ?? 0 };
        setError("");
      } catch (e) {
        if (live) setError((e as Error).message);
      } finally {
        // Wait after each request so slow connections never stack polling calls.
        if (live && active) timer = setTimeout(() => void poll(), 2000);
      }
    }
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [evaluationId, active]);

  if (!active && !progress?.episode && !error) return null;
  return <section className="live-progress" aria-label="Running episode log">
    <h3>{active ? "Live episode log" : "Interrupted episode log"}</h3>
    <p className="form-desc">Recorded tool actions, refreshed every two seconds while active. This trace is unscored; pass/fail appears after verification.</p>
    {error && <p role="alert" className="error-banner">Progress unavailable: {error}. Retrying while the run is active.</p>}
    {progress?.trace_unavailable && <p role="alert">The saved trace could not be verified. Inspect infrastructure attempts.</p>}
    {!progress?.episode && !error && <p role="status">Waiting for the worker to start an episode.</p>}
    {progress?.episode && <>
      <p>Seed {progress.episode.seed} · {progress.episode.last_step} recorded actions {progress.episode.pending_action && `· Next action: ${progress.episode.pending_action.tool}`}</p>
      <div className="live-action-list">
        {progress.episode.actions.map((action) => <details key={action.action_id}>
          <summary>Step {action.step} · {action.tool} · {action.source.replaceAll("_", " ")}</summary>
          <pre>{JSON.stringify(action.arguments, null, 2)}</pre>
          <pre>{action.output}</pre>
        </details>)}
      </div>
    </>}
  </section>;
}
