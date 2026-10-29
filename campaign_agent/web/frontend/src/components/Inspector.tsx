import { useEffect, useState } from "react";
import type { EpisodicMemory, RunView } from "../api";
import { agentName, api, fmt } from "../api";
import { Empty, Markdown, Metrics, Pill, Section } from "./ui";

export type TabKey = "evidence" | "plan" | "debate" | "brief" | "agents" | "memory" | "audit";
export const TABS: [TabKey, string][] = [
  ["evidence", "Evidence"], ["plan", "Plan"], ["debate", "Debate"], ["brief", "Brief"],
  ["agents", "Agents"], ["memory", "Memory"], ["audit", "Audit"],
];

function Evidence({ v }: { v: RunView }) {
  const p = v.pack;
  if (!p) return <Empty>The Diagnostic Pack appears at stage 3, Diagnose.</Empty>;
  const rec = p.reconciliation;
  const heroIds = new Set(p.psgs.hero.map((h) => h.psg_id));
  return (
    <>
      <Section title="Key numbers" aside={<code className="muted">{p.pack_id}</code>}>
        <Metrics items={p.key_numbers} />
      </Section>
      <Section title="What the agents found">
        {p.findings.map((f) => (
          <div className="finding" key={f.agent}>
            <b>{agentName(f.agent)}</b>
            {f.findings.map((x, i) => <p key={i}>{x.text} <span className="cite">block {x.blocks.join(", ")}</span></p>)}
            {f.watch.map((w) => <p key={w}><Pill tone="warn">watch</Pill> {w}</p>)}
          </div>
        ))}
      </Section>
      <Section title="Audience" aside={<span className="muted">{fmt(p.audience_total)} households proposed</span>}>
        <div className="cohorts">
          {p.audience.map((c) => (
            <div key={c.cohort} className={`cohort ${c.include ? "is-in" : ""}`} title={c.reason}>
              <span>{c.label}</span><b>{fmt(c.size)}</b>
              <Pill tone={c.include ? "good" : "neutral"}>{c.include ? "include" : "exclude"}</Pill>
            </div>
          ))}
        </div>
      </Section>
      <Section title="Qualifying PSGs">
        <ul className="psgs">
          {p.psgs.include.map((x) => (
            <li key={x.psg_id}><span>{x.name}</span>{heroIds.has(x.psg_id) ? <Pill tone="accent">hero</Pill> : <Pill tone="good">include</Pill>}</li>
          ))}
          {p.psgs.exclude.map((x) => (
            <li key={x.psg_id} className="is-out"><span>{x.name}<small>{x.reason}</small></span><Pill tone="bad">exclude</Pill></li>
          ))}
        </ul>
      </Section>
      <Section title="Reconciliation" aside={<Pill tone={rec.passed ? "good" : "bad"}>{rec.checks.filter((c) => c.passed).length}/{rec.checks.length} passed</Pill>}>
        <ul className="checks">
          {rec.checks.map((c) => (
            <li key={c.check}><span className={c.passed ? "ok" : "fail"}>{c.passed ? "✓" : "✕"}</span><div><b>{c.check}</b><small>{c.detail}</small></div></li>
          ))}
        </ul>
      </Section>
      {p.extra_cuts.length > 0 && (
        <Section title="Extra cuts you asked for">
          {p.extra_cuts.map((c, i) => <pre key={i} className="code">{JSON.stringify(c.output, null, 2)}</pre>)}
        </Section>
      )}
    </>
  );
}

function Plan({ v }: { v: RunView }) {
  if (!v.plan) return <Empty>The tool plan appears at stage 2, Plan.</Empty>;
  const added = v.plan.steps.filter((s) => s.added_by !== "base");
  return (
    <>
      {added.length > 0 && (
        <Section title="Added for this brand">
          {added.map((s, i) => <p key={i} className="added"><Pill tone={s.added_by === "human" ? "accent" : "warn"}>{s.added_by}</Pill> <code>{s.tool}</code> — {s.reason}</p>)}
        </Section>
      )}
      <Section title={`All steps · ${v.plan.steps.length}`}>
        <table className="table">
          <thead><tr><th>Block</th><th>Agent</th><th>Tool</th><th>Why</th></tr></thead>
          <tbody>
            {v.plan.steps.map((s, i) => (
              <tr key={i}>
                <td>{s.block ?? "—"}</td><td>{agentName(s.agent)}</td>
                <td><code>{s.tool}{Object.keys(s.params).length ? ` ${JSON.stringify(s.params)}` : ""}</code></td>
                <td>{s.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </>
  );
}

function Debate({ v }: { v: RunView }) {
  const d = v.discussion;
  if (!d) return <Empty>Advocates argue brand, audience and PSGs at stage 4, Discuss.</Empty>;
  const x = d.decision;
  return (
    <>
      {d.top_alternative && !d.locked && (
        <div className="callout"><b>Brand Scout:</b> {d.top_alternative.name} beats the requested brand on seasonal and market signals.</div>
      )}
      {x && (
        <Section title="Outcome" aside={<span className="muted">{x.rounds} round{x.rounds === 1 ? "" : "s"} · {Math.round(x.agreement * 100)}% agreement</span>}>
          <Metrics items={x.key_numbers} />
          <p className="reason"><b>Why this brand:</b> {x.brand_decision.reason}</p>
          {x.dissent.length > 0 && (
            <div className="dissent">
              <h4>Dissent on record</h4>
              {x.dissent.map((y, i) => <p key={i}><b>{y.by}:</b> {y.position}</p>)}
            </div>
          )}
          {x.notes_for_draft.length > 0 && (
            <div className="notes"><h4>Passed to the draft process (off-scope)</h4>{x.notes_for_draft.map((n, i) => <p key={i}>{n}</p>)}</div>
          )}
        </Section>
      )}
      {d.proposals.length > 0 && (
        <Section title="Advocates">
          <div className="advocates">
            {d.proposals.map((a) => (
              <div className="advocate" key={a.label}>
                <header><b>{a.label}</b><Pill tone="accent">{a.brand?.name ?? "?"}</Pill></header>
                <p className="muted">{a.cohorts.join(", ") || "no cohorts"} · {a.excluded_psgs} PSGs excluded</p>
                <ul>{a.claims.map((c, i) => <li key={i} className={c.topic ? "" : "off"}>{c.text}</li>)}</ul>
              </div>
            ))}
          </div>
        </Section>
      )}
      {x && x.transcript.some((t) => t.round > 0) && (
        <Section title="Critique rounds">
          <ol className="rounds">
            {x.transcript.filter((t) => t.round > 0).map((t, i) => <li key={i}><span className="muted">R{t.round}</span> <b>{t.speaker}</b> {t.text}</li>)}
          </ol>
        </Section>
      )}
    </>
  );
}

function Brief({ v }: { v: RunView }) {
  if (!v.brief) return <Empty>The Agreed Brief appears at stage 5.</Empty>;
  return (
    <>
      <div className="brief-card">
        <Markdown text={v.brief.summary_md} />
        <footer>
          <a className="btn" href={api.briefUrl(v.run_id)}>Download JSON</a>
          <span className="muted">{v.brief.handoff ? `Handed off via ${v.brief.handoff.sink}` : "Waiting for your approval"}</span>
        </footer>
      </div>
      <Section title="Handoff contract"><pre className="code">{JSON.stringify(v.brief.json, null, 2)}</pre></Section>
    </>
  );
}

function Agents({ v }: { v: RunView }) {
  if (!v.harness_log.length) return <Empty>No agent runs yet.</Empty>;
  const llm = v.harness_log.filter((r) => r.source === "llm").length;
  return (
    <>
      <div className="callout">{llm} of {v.harness_log.length} agent runs completed on the LRM path (mode: <b>{v.lrm_mode}</b>). Fallbacks use each agent's rule-based policy.</div>
      <table className="table">
        <thead><tr><th>Stage</th><th>Skill</th><th>Source</th><th>Tools</th></tr></thead>
        <tbody>
          {[...v.harness_log].reverse().map((r, i) => (
            <tr key={i}>
              <td>{r.stage ?? ""}</td><td><code>{r.skill}</code></td>
              <td><Pill tone={r.source === "llm" ? "good" : "warn"}>{r.source}</Pill>{r.reason && <small className="block">{r.reason}</small>}{r.rejected > 0 && <small className="block">{r.rejected} output(s) rejected by guards</small>}</td>
              <td><small>{r.tools.join(", ")}</small></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Memory({ v }: { v: RunView }) {
  const [mem, setMem] = useState<EpisodicMemory | null>(null);
  useEffect(() => { api.memory().then(setMem).catch(() => setMem(null)); }, [v.run_id, v.done]);
  return (
    <>
      <Section title="This run">
        {v.working_memory.length
          ? <ul className="notes-list">{v.working_memory.map((m, i) => <li key={i}><b>{agentName(m.agent)}</b> <span className="muted">{m.stage}</span><p>{m.note}</p></li>)}</ul>
          : <Empty>Notes agents save, and your “note: …” messages, appear here and follow the run to later stages.</Empty>}
      </Section>
      <Section title="Past runs" aside={mem && <span className="muted">{mem.overrides.length} human overrides learned</span>}>
        {mem?.runs.length
          ? <table className="table"><thead><tr><th>Run</th><th>Brand</th><th>Objective</th><th>Overrides</th></tr></thead>
              <tbody>{[...mem.runs].reverse().map((r) => <tr key={r.run_id}><td><code>{r.run_id}</code></td><td>{r.brand_requested} → {r.brand_selected}</td><td>{r.objective}</td><td>{r.overrides}</td></tr>)}</tbody></table>
          : <Empty>Completed runs are remembered here; your overrides re-weight audience, hero-PSG and Brand Scout scoring next time.</Empty>}
      </Section>
    </>
  );
}

function Audit({ v }: { v: RunView }) {
  if (!v.audit.length) return <Empty>No events yet.</Empty>;
  return (
    <ol className="audit">
      {[...v.audit].reverse().map((a, i) => (
        <li key={i}>
          <time>{a.ts.replace("T", " ").replace("+00:00", "")}</time>
          <span><b>{a.actor}</b> {a.event.replace(/_/g, " ")} <span className="muted">· {a.stage}</span></span>
          {(a.text || a.target || a.pack_id) && <small>{a.text ?? a.target ?? a.pack_id}</small>}
        </li>
      ))}
    </ol>
  );
}

const PANELS: Record<TabKey, (p: { v: RunView }) => React.JSX.Element> = {
  evidence: Evidence, plan: Plan, debate: Debate, brief: Brief, agents: Agents, memory: Memory, audit: Audit,
};

export function Inspector({ view, tab, onTab }: { view: RunView | null; tab: TabKey; onTab: (t: TabKey) => void }) {
  const Panel = PANELS[tab];
  return (
    <aside className="inspector" aria-label="Run details">
      <div className="tabs" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className="tab" onClick={() => onTab(k)}>
            {label}
            {k === "agents" && view && view.harness_log.length > 0 && <span className="count">{view.harness_log.length}</span>}
          </button>
        ))}
      </div>
      <div className="inspector-body">
        {view ? <Panel v={view} /> : <Empty>Start a run — evidence, the plan, the debate and the brief collect here as you go.</Empty>}
      </div>
    </aside>
  );
}
