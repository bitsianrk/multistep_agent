import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentEvent, AppConfig, RunSummary, RunView } from "./api";
import { api } from "./api";
import { Composer, type ComposerHandle } from "./components/Composer";
import { Inspector, type TabKey } from "./components/Inspector";
import { StageRail } from "./components/StageRail";
import { Thread } from "./components/Thread";

const STAGE_TAB: Record<string, TabKey> = {
  understand: "evidence", plan: "plan", diagnose: "evidence", discuss: "debate", brief: "brief",
};
const MODE_LABEL = { live: "LRM live", simulated: "LRM simulated", off: "Rules only" } as const;
const MODE_HINT = {
  live: "Agents run on the Tesco v1/lrm/ endpoint",
  simulated: "No LRM credentials found: an offline simulated model drives the agent harness",
  off: "Deterministic agent policies only",
} as const;

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [view, setView] = useState<RunView | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [pendingText, setPendingText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("evidence");
  const [showInspector, setShowInspector] = useState(false);
  const composer = useRef<ComposerHandle>(null);
  const runId = view?.run_id ?? null;

  const refreshRuns = useCallback(() => { api.runs().then(setRuns).catch(() => undefined); }, []);

  const applyView = useCallback((v: RunView) => {
    setView(v);
    setBusy(v.busy);
    if (!v.busy) setPendingText(null);
    if (v.error) setError(v.error);
    const st = v.done ? "brief" : v.pending?.stage;
    if (st && !v.busy) setTab(STAGE_TAB[st] ?? "evidence");
    history.replaceState(null, "", `#${v.run_id}`);
  }, []);

  const refresh = useCallback(async (id: string) => {
    try { applyView(await api.run(id)); refreshRuns(); } catch (e) { setError((e as Error).message); }
  }, [applyView, refreshRuns]);

  // Boot: config, run list, and the run in the URL hash.
  useEffect(() => {
    api.config().then(setConfig).catch((e: Error) => setError(`Server not reachable: ${e.message}`));
    refreshRuns();
    const id = location.hash.slice(1);
    if (id) refresh(id);
  }, [refresh, refreshRuns]);

  // Live agent activity for the open run (SSE), resumed after the last event we have seen.
  const lastSeq = useRef(0);
  useEffect(() => {
    if (!runId) return;
    const es = api.events(runId, lastSeq.current);
    es.onmessage = (msg) => {
      const e = JSON.parse(msg.data) as AgentEvent;
      lastSeq.current = Math.max(lastSeq.current, e.seq);
      if (e.type === "busy") setBusy(true);
      else if (e.type === "idle") refresh(runId);
      else if (e.type === "error") setError(e.text ?? "error");
      else setEvents((xs) => [...xs, e]);
    };
    return () => es.close();
  }, [runId, refresh]);

  // Safety net: if a proxy blocks SSE, poll while agents are working.
  useEffect(() => {
    if (!busy || !runId) return;
    const t = setInterval(() => refresh(runId), 4000);
    return () => clearInterval(t);
  }, [busy, runId, refresh]);

  const send = useCallback(async (raw: string) => {
    const text = raw.trim();
    if (!text || busy) return;
    setError(null); setEvents([]); setPendingText(text); setBusy(true);
    try {
      if (!view || view.done) {
        lastSeq.current = 0;
        applyView(await api.start(text));
      } else {
        const v = await api.reply(view.run_id, text);
        setView(v);
        setBusy(v.busy);
      }
      refreshRuns();
    } catch (e) {
      setError((e as Error).message); setBusy(false); setPendingText(null);
    }
  }, [busy, view, applyView, refreshRuns]);

  const openRun = async (id: string) => {
    if (!id) return;
    setEvents([]); setError(null);
    try {
      const v = await api.run(id);
      lastSeq.current = v.last_event;
      applyView(v);
    } catch (e) { setError((e as Error).message); }
  };

  const newRun = () => {
    setView(null); setEvents([]); setError(null); setBusy(false); setPendingText(null);
    lastSeq.current = 0; history.replaceState(null, "", "#");
  };

  const mode = config?.lrm_mode;
  const req = view?.request;
  return (
    <div className="app">
      <header className="topbar">
        <div className="wordmark">
          <svg viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="8" /><path d="M7 23h6v-5h6v-5h6" /></svg>
          <div><b>Multi-Step Planner</b><span>Campaign planning agents</span></div>
        </div>
        {req?.brand && (
          <div className="run-chip" title={req.text ?? ""}>
            <b>{req.brand.name}</b>
            <span>{req.objective ?? "—"}{req.window ? ` · ${req.window.weeks} wks from ${req.window.start}` : ""}</span>
          </div>
        )}
        <div className="topbar-end">
          {mode && <span className={`mode mode-${mode}`} title={config?.lrm_reason ?? MODE_HINT[mode]}>{MODE_LABEL[mode]}</span>}
          <select aria-label="Open a run" value={runId ?? ""} onChange={(e) => openRun(e.target.value)}>
            <option value="">{runs.length ? `Runs (${runs.length})` : "No runs yet"}</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.brand?.name ?? "?"} · {r.done ? "done" : r.busy ? "working" : r.stage ?? ""}
              </option>
            ))}
          </select>
          <button className="btn" onClick={newRun}>New plan</button>
          <button className="btn btn-quiet only-narrow" onClick={() => setShowInspector((s) => !s)} aria-expanded={showInspector}>
            {showInspector ? "Chat" : "Details"}
          </button>
        </div>
      </header>

      <div className={`workspace ${showInspector ? "show-inspector" : ""}`}>
        <StageRail view={view} />
        <main className="conversation">
          <Thread view={view} examples={config?.examples ?? []} events={events} busy={busy} pendingText={pendingText}
                  error={error} onSend={send} onPrefill={(t) => composer.current?.prefill(t)} />
          <Composer ref={composer} view={view} busy={busy} onSend={send} />
        </main>
        <Inspector view={view} tab={tab} onTab={setTab} />
      </div>
    </div>
  );
}
