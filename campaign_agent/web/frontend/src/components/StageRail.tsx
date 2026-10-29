import type { RunView, Stage, StageKey } from "../api";
import { fmt } from "../api";

const DEFAULT: Stage[] = [
  { key: "understand", label: "Understand", status: "todo" },
  { key: "plan", label: "Plan", status: "todo" },
  { key: "diagnose", label: "Diagnose", status: "todo" },
  { key: "discuss", label: "Discuss", status: "todo" },
  { key: "brief", label: "Agreed Brief", status: "todo" },
];

const HINT: Record<StageKey, string> = {
  understand: "Brand, objective and window",
  plan: "Which analyses this brand needs",
  diagnose: "Ten evidence blocks, reconciled",
  discuss: "Advocates debate brand, audience, PSGs",
  brief: "Approve and hand off",
};

function detail(key: StageKey, v: RunView | null): string | null {
  if (!v) return null;
  switch (key) {
    case "understand":
      return v.request.brand ? `${v.request.brand.name} · ${v.request.objective ?? "?"}` : null;
    case "plan":
      return v.plan ? `${v.plan.steps.length} tool calls planned` : null;
    case "diagnose": {
      const r = v.pack?.reconciliation;
      return r ? `${r.checks.filter((c) => c.passed).length}/${r.checks.length} checks · ${fmt(v.pack!.audience_total)} hh` : null;
    }
    case "discuss": {
      const d = v.discussion?.decision;
      return d ? `${d.brand_decision.selected_brand?.name ?? "?"} · ${Math.round(d.agreement * 100)}% agreement` : null;
    }
    case "brief":
      return v.brief ? (v.brief.handoff ? "Handed off" : "Awaiting approval") : null;
  }
}

export function StageRail({ view }: { view: RunView | null }) {
  const stages = view?.stages ?? DEFAULT;
  return (
    <nav className="rail" aria-label="Stages">
      <ol>
        {stages.map((s, i) => (
          <li key={s.key} className={`rail-step is-${s.status}`} aria-current={s.status === "current" ? "step" : undefined}>
            <span className="rail-dot">{s.status === "done" ? "✓" : i + 1}</span>
            <div className="rail-text">
              <span className="rail-label">{s.label}</span>
              <span className="rail-detail">{detail(s.key, view) ?? HINT[s.key]}</span>
            </div>
          </li>
        ))}
      </ol>
      {view && (
        <div className="rail-foot">
          <div><span>Tool calls</span><b>{fmt(view.counters.tool_calls)}</b></div>
          <div><span>Send-backs</span><b>{view.counters.send_backs}</b></div>
          <div><span>Agent runs</span><b>{view.harness_log.length}</b></div>
        </div>
      )}
    </nav>
  );
}
