import { useEffect, useRef, useState } from "react";
import type { AgentEvent, ChatMessage, Pending, RunView } from "../api";
import { agentName, api } from "../api";
import { Avatar, Markdown } from "./ui";

const STAGE_NAME: Record<string, string> = {
  understand: "Understand", plan: "Plan", diagnose: "Diagnose", discuss: "Discuss", brief: "Agreed Brief",
};

function Message({ m }: { m: ChatMessage }) {
  if (m.role === "human") {
    return <div className="msg msg-human"><div className="bubble">{m.text}</div></div>;
  }
  const name = m.agent ?? "Agent";
  const rich = /\*\*|^\s*- /m.test(m.text);
  return (
    <div className="msg msg-agent">
      <Avatar name={name} />
      <div className="bubble">
        <span className="who">{name}</span>
        {rich ? <Markdown text={m.text} /> : <p>{m.text}</p>}
      </div>
    </div>
  );
}

function DecisionCard({ p, onSend, onPrefill, disabled }: {
  p: Pending; onSend: (t: string) => void; onPrefill: (t: string) => void; disabled: boolean;
}) {
  const clarify = p.awaiting === "clarification";
  const [all, setAll] = useState(false);
  const LIMIT = 6;
  const numbers = all ? p.key_numbers : p.key_numbers.slice(0, LIMIT);
  return (
    <article className={`decision ${clarify ? "is-question" : ""}`} aria-label="Checkpoint">
      <header>
        <span className="decision-kicker">{clarify ? "Question for you" : "Your call"} · {STAGE_NAME[p.stage] ?? p.stage}</span>
        <h2>{p.title}</h2>
      </header>
      {p.summary.length > 0 && (
        <ul className="decision-points">{p.summary.map((s, i) => <li key={i}>{s}</li>)}</ul>
      )}
      {p.key_numbers.length > 0 && (
        <>
          <dl className="decision-grid">
            {numbers.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
          {p.key_numbers.length > LIMIT && (
            <button className="link" onClick={() => setAll((x) => !x)}>
              {all ? "Show fewer" : `Show all ${p.key_numbers.length} numbers`}
            </button>
          )}
        </>
      )}
      <footer className="decision-actions">
        {p.actions.map((a) => {
          if (/^Approve/.test(a)) return <button key={a} className="btn btn-primary" disabled={disabled} onClick={() => onSend("approve")}>{a}</button>;
          if (a === "Pause") return <button key={a} className="btn btn-quiet" disabled={disabled} onClick={() => onSend("pause")}>{a}</button>;
          const pre = a === "Ask" ? "What is " : a === "Send back" ? "Send back to " : "";
          return <button key={a} className="btn" disabled={disabled} onClick={() => onPrefill(pre)}>{a}</button>;
        })}
      </footer>
    </article>
  );
}

function describe(e: AgentEvent): { who: string; text: string; tone: string } | null {
  const who = agentName(e.agent);
  switch (e.type) {
    case "skill_start": return { who, text: `started ${e.skill}`, tone: "start" };
    case "thought": return { who, text: e.text ?? "", tone: "thought" };
    case "tool": return { who, text: `called ${e.name}`, tone: e.ok === false ? "bad" : "tool" };
    case "tool_call": return { who, text: `ran ${e.name}${e.ms !== undefined ? ` · ${e.ms} ms` : ""}`, tone: e.ok === false ? "bad" : "tool" };
    case "invalid_output": return { who, text: `output rejected by guards — ${e.errors?.[0] ?? ""}`, tone: "warn" };
    case "loop_detected": return { who, text: `repeated ${e.name}; nudged to move on`, tone: "warn" };
    case "finish": return { who, text: `done${e.ms !== undefined ? ` in ${Math.round(e.ms)} ms` : ""}`, tone: "good" };
    case "fallback": return { who, text: `used its rule-based policy — ${e.reason}`, tone: "warn" };
    case "error": return { who: "Server", text: e.text ?? "error", tone: "bad" };
    default: return null;
  }
}

function Activity({ events, live }: { events: AgentEvent[]; live: boolean }) {
  const shown = events.map((e) => ({ e, d: describe(e) })).filter((x) => x.d).slice(-60);
  const agents = new Set(events.filter((e) => e.type === "skill_start").map((e) => e.agent));
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }); }, [shown.length]);
  return (
    <article className="activity" aria-live="polite">
      <header>
        <span className={`activity-dot ${live ? "is-live" : ""}`} />
        <b>{live ? "Agents are working" : "Agent activity"}</b>
        <span className="muted">{agents.size} agent{agents.size === 1 ? "" : "s"} · {shown.length} steps</span>
      </header>
      <ol ref={ref}>
        {shown.length === 0 && <li className="muted">Waiting for the first agent…</li>}
        {shown.map(({ e, d }) => (
          <li key={e.seq} className={`act act-${d!.tone}`}>
            <time>{e.ts}</time><b>{d!.who}</b><span>{d!.text}</span>
          </li>
        ))}
      </ol>
    </article>
  );
}

export function Thread({ view, examples, events, busy, pendingText, error, onSend, onPrefill }: {
  view: RunView | null; examples: string[]; events: AgentEvent[]; busy: boolean; pendingText: string | null;
  error: string | null; onSend: (t: string) => void; onPrefill: (t: string) => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [view?.chat.length, busy, events.length, view?.pending?.title]);

  if (!view && !busy) {
    return (
      <div className="thread">
        <div className="welcome">
          <p className="welcome-kicker">New Multi-Step plan</p>
          <h1>What should we plan?</h1>
          <p>Name a brand, what you want shoppers to do, and when. Agents work through five stages with you — you make the call at each one — and finish with an Agreed Brief: brand, audience and PSGs.</p>
          <div className="welcome-examples">
            {examples.map((x) => (
              <button key={x} className="example" onClick={() => onSend(x)}>
                <span>{x}</span><span aria-hidden>→</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="thread">
      <div className="thread-inner">
        {view?.chat.map((m, i) => <Message key={i} m={m} />)}
        {busy && pendingText && !view?.chat.some((m, i) => m.role === "human" && i === view.chat.length - 1 && m.text === pendingText) && (
          <div className="msg msg-human"><div className="bubble">{pendingText}</div></div>
        )}
        {(busy || events.length > 0) && <Activity events={events} live={busy} />}
        {!busy && view?.pending && <DecisionCard p={view.pending} onSend={onSend} onPrefill={onPrefill} disabled={busy} />}
        {!busy && view?.done && (
          <div className="done">
            <b>Agreed Brief handed off.</b> <a href={api.briefUrl(view.run_id)}>Download the JSON</a> or type a new request to start another run.
          </div>
        )}
        {error && <div className="alert" role="alert">{error}</div>}
        <div ref={end} />
      </div>
    </div>
  );
}
