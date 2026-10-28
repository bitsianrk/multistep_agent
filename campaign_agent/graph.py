"""The orchestrator: a LangGraph graph with a human checkpoint after every stage.

    understand -> cp_understand -> plan -> cp_plan -> diagnose -> cp_diagnose
      -> propose -> cp_proposals -> debate -> cp_discuss -> brief -> cp_brief
      -> handoff -> END

Each cp_* node renders a checkpoint card and calls interrupt(); the run's
state is checkpointed, so it can pause for hours and resume from the same
stage. The human reply is classified by the Chat Gateway:

* approve   -> next stage
* ask       -> the stage's agent answers from the pack (or runs one more
               skill), then the same checkpoint is shown again
* edit      -> state is updated and re-validated, same checkpoint again
* send back -> the graph jumps to the named earlier stage with the new input
* pause     -> nothing changes; the checkpoint waits

A brand switch agreed in the discussion sends the run back to Diagnose for
the new brand. Every human message and decision is written to the audit log.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents.base import ROSTER, AgentRuntime
from .agents.debate import apply_overrides, human_position, propose, run_debate
from .agents.diagnose import build_pack
from .agents.gateway import BriefWriter, ChatGateway, brand_name, gbp
from .agents.understand import EntityResolutionAgent, PlannerAgent
from .config import Settings
from .data import get_store
from .data.taxonomy import BRANDS, COHORT_LABELS
from .guardrails import display_safe
from .handoff import FileHandoff, HandoffSink
from .memory import EpisodicMemory
from .skills.registry import RunBudget, SkillCallError, get_registry

STAGE_NODE = {"understand": "understand", "plan": "plan", "diagnose": "diagnose", "discuss": "propose",
              "brief": "brief"}


class RunState(TypedDict, total=False):
    run_id: str
    user: dict
    request: str
    stage: str
    status: str
    resolution: dict
    brand_id: str
    requested_brand_id: str
    objective: str
    window: dict
    plan: dict
    pack: dict
    pack_seq: int
    proposals: dict
    position_edits: list
    decision: dict
    switch_record: dict
    brief: dict
    brief_summary: str
    handoff: dict
    send_backs: int
    brand_switches: int
    skill_calls: int
    compact: bool
    chat: Annotated[list, operator.add]
    audit: Annotated[list, operator.add]
    trace: Annotated[list, operator.add]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def say(agent: str, text: str) -> dict:
    return {"role": "agent", "agent": agent, "text": text}


def build_graph(settings: Settings, checkpointer=None, memory: EpisodicMemory | None = None,
                handoff: HandoffSink | None = None):
    store = get_store(settings)
    registry = get_registry(settings, store)
    memory = memory or EpisodicMemory(settings.memory_path)
    handoff = handoff or FileHandoff(settings.output_dir)

    # Helpers ------------------------------------------------------------
    class Ctx:
        """Runtime for one node execution; collects trace and budget use."""

        def __init__(self, state: RunState):
            self.rt = AgentRuntime(registry=registry, settings=settings, store=store, user=state["user"],
                                   run_id=state["run_id"],
                                   budget=RunBudget(settings.max_skill_calls, state.get("skill_calls", 0)))
            self.gw = ChatGateway(self.rt)

        def update(self, **kw) -> dict:
            kw.setdefault("trace", [])
            kw["trace"] = kw["trace"] + self.rt.trace
            kw["skill_calls"] = self.rt.budget.used
            return kw

    def audit(state: RunState, stage: str, actor: str, event: str, **extra) -> dict:
        return {"ts": _now(), "run_id": state["run_id"], "stage": stage, "actor": actor, "event": event, **extra}

    def category(state: RunState) -> str:
        return BRANDS[state["brand_id"]]["category"]

    def checkpoint(state: RunState, c: Ctx, stage: str, title: str, summary: list[str], key_numbers=None,
                   actions=None, awaiting: str = "checkpoint", auto_confidence: float | None = None):
        """Show the card and wait. Returns (reply, route) or (None, None) if auto-advanced (L2)."""
        if (settings.autonomy_level == "L2" and stage in ("understand", "plan", "diagnose")
                and auto_confidence is not None and auto_confidence >= settings.l2_confidence_threshold):
            return None, None
        card = c.gw.card(stage, title, summary, key_numbers, actions, compact=state.get("compact", False))
        reply = interrupt({"stage": stage, "awaiting": awaiting, "card": card})
        reply = str(reply)[:2000]
        route = c.gw.route(reply, stage, state.get("brand_id"), awaiting == "clarification", registry.names())
        return reply, route

    def human_turn(state, stage, reply, route) -> dict:
        return {"chat": [{"role": "human", "text": reply}], "status": "running",
                "audit": [audit(state, stage, state["user"]["id"], "message", text=reply,
                                action=route["action"], edits=route["edits"])]}

    def merge(*parts: dict) -> dict:
        out: dict[str, Any] = {}
        for p in parts:
            for k, v in p.items():
                if k in ("chat", "audit", "trace") and k in out:
                    out[k] = out[k] + v
                else:
                    out[k] = v
        return out

    def ask(state, c: Ctx, stage, reply, route, node) -> Command:
        try:
            agent, text, cut = c.gw.answer(reply, state)
        except SkillCallError as exc:
            agent, text, cut = "Chat Gateway", f"Could not run that cut: {exc}", None
        upd = {"chat": [say(agent, display_safe(text))], "compact": True}
        if cut and state.get("pack"):
            pack = dict(state["pack"])
            pack["extra_cuts"] = pack.get("extra_cuts", []) + [display_safe(cut)]
            upd["pack"] = pack
        return Command(goto=node, update=c.update(**merge(human_turn(state, stage, reply, route), upd)))

    def pause(state, c, stage, reply, route, node) -> Command:
        return Command(goto=node, update=c.update(**merge(
            human_turn(state, stage, reply, route),
            {"chat": [say("Campaign Planning Agent", "Paused. State is saved; reply here to resume.")],
             "status": "paused", "compact": True})))

    def send_back(state, c: Ctx, stage, reply, route, node) -> Command:
        base = human_turn(state, stage, reply, route)
        if state.get("send_backs", 0) >= settings.max_send_backs:
            return Command(goto=node, update=c.update(**merge(base, {"chat": [say(
                "Campaign Planning Agent", f"This run has used its {settings.max_send_backs} send-backs; "
                "approve, edit or start a new run.")], "compact": True})))
        target = route["target_stage"] or "plan"
        planner = PlannerAgent(c.rt)
        resume = planner.resume(state["brand_id"], target)
        upd: dict[str, Any] = {k: None for k in resume["clears"]}
        upd.update(send_backs=state.get("send_backs", 0) + 1, compact=False, status="running")
        notes = []
        for e in route["edits"]:
            if e["op"] == "switch_brand":
                new = e.get("brand_id")
                if new is None:
                    alts = (state.get("pack") or {}).get("blocks", {}).get("10", {}).get("data", {}) \
                        .get("brand_scout_score", {}).get("alternatives", [])
                    new = alts[0]["brand_id"] if alts else None
                if new and new != state["brand_id"]:
                    upd["brand_id"] = new
                    upd["brand_switches"] = state.get("brand_switches", 0) + 1
                    upd["switch_record"] = {"from": state["brand_id"], "to": new,
                                            "reason": f"Campaign manager sent the run back: \"{reply}\""}
                    upd["position_edits"] = []
                    notes.append(f"brand -> {brand_name(new)}")
            elif e["op"] == "set_objective":
                upd["objective"] = e["objective"]
                notes.append(f"objective -> {e['objective']}")
            elif e["op"] == "set_window":
                upd["window"] = {"start": e["window"]["start"], "weeks": e["window"]["weeks"]}
                notes.append(f"window -> {e['window']['weeks']} wks from {e['window']['start']}")
        if target == "understand":
            upd["request"] = f"{state['request']} {reply}"
        msg = f"Sent back to {target}" + (f" with {', '.join(notes)}" if notes else "") + "."
        return Command(goto=STAGE_NODE[target], update=c.update(**merge(
            base, upd, {"chat": [say("Campaign Planning Agent", msg)],
                        "audit": [audit(state, stage, "system", "send_back", target=target)]})))

    def dispatch(state, c, stage, reply, route, node, on_approve, on_edit):
        action = route["action"]
        if action == "pause":
            return pause(state, c, stage, reply, route, node)
        if action == "send_back":
            return send_back(state, c, stage, reply, route, node)
        if action == "approve":
            return on_approve(human_turn(state, stage, reply, route))
        if action in ("edit", "answer"):
            return on_edit(human_turn(state, stage, reply, route))
        return ask(state, c, stage, reply, route, node)

    # Stage 1: Understand -------------------------------------------------
    def understand(state: RunState):
        c = Ctx(state)
        er = EntityResolutionAgent(c.rt)
        res = er.resolve(state["request"])
        msg = ("Resolved " + f"{res['brand']['name']} ({res['brand']['category_name']})" if res["brand_id"]
               else "I need a clarification before planning.")
        return c.update(stage="understand", status="running", resolution=res, compact=False,
                        chat=[say(er.name, msg)],
                        audit=[audit(state, "understand", "system", "stage_complete", status=res["status"])])

    def cp_understand(state: RunState):
        c = Ctx(state)
        res = state["resolution"]
        if res["status"] == "needs_clarification":
            reply, route = checkpoint(state, c, "understand", "Clarification needed", res["questions"],
                                      actions=["Answer", "Pause"], awaiting="clarification")
        else:
            w = res["window"]
            assumed = f" (assumed {', '.join(w['assumed'])})" if w.get("assumed") else ""
            summary = [f"Brand: {res['brand']['name']} - {res['brand']['category_name']}, "
                       f"supplier {res['brand']['supplier']}, {len(res['brand']['psgs'])} PSGs",
                       f"Objective: {res['objective']}",
                       f"Window: {w['weeks']} weeks from {w['start']}{assumed}"]
            conf = res["confidence"] if not w.get("assumed") else 0.0
            reply, route = checkpoint(state, c, "understand", "Confirm brand and objective", summary,
                                      auto_confidence=conf)
            if reply is None:
                return Command(goto="plan", update=c.update(**_accept_resolution(state, res, auto=True)))

        def approve(base):
            if res["status"] != "resolved":
                return Command(goto="cp_understand", update=c.update(**merge(base, {"chat": [say(
                    "Entity Resolution Agent", "Still need: " + " ".join(res["questions"]))], "compact": True})))
            return Command(goto="plan", update=c.update(**merge(base, _accept_resolution(state, res))))

        def edit(base):
            new = EntityResolutionAgent(c.rt).apply_answer(res, reply, route["edits"])
            text = ("Got it." if new["status"] == "resolved" else "Thanks - " + " ".join(new["questions"]))
            return Command(goto="cp_understand", update=c.update(**merge(
                base, {"resolution": new, "compact": False, "chat": [say("Entity Resolution Agent", text)]})))

        return dispatch(state, c, "understand", reply, route, "cp_understand", approve, edit)

    def _accept_resolution(state, res, auto=False) -> dict:
        upd = {"brand_id": res["brand_id"], "objective": res["objective"],
               "window": {"start": res["window"]["start"], "weeks": res["window"]["weeks"]},
               "audit": [audit(state, "understand", "system" if auto else state["user"]["id"], "approved")]}
        if not state.get("requested_brand_id"):
            upd["requested_brand_id"] = res["brand_id"]
        if auto:
            upd["chat"] = [say("Campaign Planning Agent", "Auto-advanced past Understand (L2, high confidence).")]
        return upd

    # Stage 2: Plan ---------------------------------------------------------
    def plan(state: RunState):
        c = Ctx(state)
        p = PlannerAgent(c.rt).plan(state["brand_id"], state["objective"], state["window"])
        added = [s for s in p["steps"] if s["added_by"] == "planner"]
        msg = (f"Built a {len(p['steps'])}-step skill plan for {brand_name(state['brand_id'])}"
               + (f"; added {len(added)} brand-specific {'analysis' if len(added) == 1 else 'analyses'}."
                  if added else "."))
        return c.update(stage="plan", plan=p, compact=False, chat=[say("Campaign Planning Agent", msg)],
                        audit=[audit(state, "plan", "system", "stage_complete", steps=len(p["steps"]))])

    def cp_plan(state: RunState):
        c = Ctx(state)
        p = state["plan"]
        summary = []
        for agent in dict.fromkeys(s["agent"] for s in p["steps"]):
            steps = [s for s in p["steps"] if s["agent"] == agent]
            summary.append(f"{ROSTER[agent].name}: " + ", ".join(
                s["skill"] + (f"({', '.join(f'{k}={v}' for k, v in s['params'].items() if k not in ('window_start', 'window_weeks'))})"
                              if any(k not in ("window_start", "window_weeks") for k in s["params"]) else "")
                for s in steps))
        for s in p["steps"]:
            if s["added_by"] != "base":
                summary.append(f"+ {s['skill']}: {s['reason']}")
        reply, route = checkpoint(state, c, "plan", f"Skill plan for {brand_name(state['brand_id'])} "
                                  f"({state['objective']})", summary, auto_confidence=0.95)
        if reply is None:
            return Command(goto="diagnose", update=c.update(chat=[say("Campaign Planning Agent",
                                                                      "Auto-advanced past Plan (L2).")]))

        def approve(base):
            return Command(goto="diagnose", update=c.update(**merge(
                base, {"audit": [audit(state, "plan", state["user"]["id"], "approved")]})))

        def edit(base):
            planner = PlannerAgent(c.rt)
            replan = {e["op"] for e in route["edits"]} & {"set_objective", "set_window", "switch_brand"}
            if replan:
                upd = {}
                for e in route["edits"]:
                    if e["op"] == "set_objective":
                        upd["objective"] = e["objective"]
                    elif e["op"] == "set_window":
                        upd["window"] = {"start": e["window"]["start"], "weeks": e["window"]["weeks"]}
                    elif e["op"] == "switch_brand" and e.get("brand_id"):
                        upd["brand_id"] = e["brand_id"]
                return Command(goto="plan", update=c.update(**merge(base, upd, {"chat": [say(
                    "Campaign Planning Agent", "Re-planning with the new inputs.")]})))
            new_plan, notes = planner.apply_edits(p, route["edits"], registry.names())
            return Command(goto="cp_plan", update=c.update(**merge(base, {
                "plan": new_plan, "compact": False,
                "chat": [say("Campaign Planning Agent", " ".join(notes) or "No plan change recognised.")]})))

        return dispatch(state, c, "plan", reply, route, "cp_plan", approve, edit)

    # Stage 3: Diagnose -----------------------------------------------------
    def diagnose(state: RunState):
        c = Ctx(state)
        cat = category(state)
        rt = c.rt
        plan_ = state["plan"]
        if plan_["brand_id"] != state["brand_id"]:  # brand switched: re-plan for the new brand
            plan_ = PlannerAgent(rt).plan(state["brand_id"], state["objective"], state["window"])
        seq = state.get("pack_seq", 0) + 1
        pack = build_pack(rt, state["brand_id"], state["objective"], state["window"], plan_,
                          cohort_bias=memory.cohort_bias(cat), hero_bias=memory.hero_bias(cat),
                          switch_bias=memory.switch_bias(cat), pack_seq=seq)
        rec = pack["reconciliation"]
        msgs = [say("Campaign Planning Agent", f"Ran {len(plan_['steps'])} skill calls across "
                    f"{len({s['agent'] for s in plan_['steps']})} agents in parallel -> {pack['pack_id']}.")]
        failed = [x for x in rec["checks"] if not x["passed"]]
        msgs.append(say("Critic / Judge", f"Reconciliation: {len(rec['checks']) - len(failed)}/{len(rec['checks'])} "
                        "checks passed" + (": " + "; ".join(f"{x['check']} ({x['detail']})" for x in failed)
                                           if failed else ".")))
        for e in pack["errors"]:
            msgs.append(say("Critic / Judge", f"Block {e['block']} incomplete: {e['error']}"))
        return c.update(stage="diagnose", plan=plan_, pack=pack, pack_seq=seq, compact=False, chat=msgs,
                        audit=[audit(state, "diagnose", "system", "stage_complete", pack_id=pack["pack_id"],
                                     reconciled=rec["passed"])])

    def cp_diagnose(state: RunState):
        c = Ctx(state)
        pack = state["pack"]
        summary, kn = diagnose_card(pack)
        conf = 0.95 if pack["reconciliation"]["passed"] and not pack["errors"] else 0.5
        reply, route = checkpoint(state, c, "diagnose", f"Diagnostic Pack - {brand_name(state['brand_id'])}",
                                  summary, kn, auto_confidence=conf)
        if reply is None:
            return Command(goto="propose", update=c.update(chat=[say("Campaign Planning Agent",
                                                                     "Auto-advanced past Diagnose (L2).")]))

        def approve(base):
            return Command(goto="propose", update=c.update(**merge(
                base, {"audit": [audit(state, "diagnose", state["user"]["id"], "approved")]})))

        def edit(base):
            ops = {e["op"] for e in route["edits"]}
            if ops & {"add_skill", "remove_skill"}:
                new_plan, notes = PlannerAgent(c.rt).apply_edits(state["plan"], route["edits"], registry.names())
                return Command(goto="diagnose", update=c.update(**merge(base, {"plan": new_plan, "chat": [
                    say("Campaign Planning Agent", " ".join(notes) + " Re-running the pack.")]})))
            if ops & {"set_objective", "set_window"}:
                return send_back(state, c, "diagnose", reply, {**route, "target_stage": "plan"}, "cp_diagnose")
            pos = [e for e in route["edits"] if e["op"] not in ("add_note",)]
            return Command(goto="cp_diagnose", update=c.update(**merge(base, {
                "position_edits": (state.get("position_edits") or []) + pos, "compact": True,
                "chat": [say("Campaign Planning Agent", "Noted - I'll bring that into the discussion as your position.")]})))

        return dispatch(state, c, "diagnose", reply, route, "cp_diagnose", approve, edit)

    # Stage 4: Discuss --------------------------------------------------------
    def propose_node(state: RunState):
        c = Ctx(state)
        locked = state.get("brand_switches", 0) >= settings.max_brand_switches
        props = propose(c.rt, state["pack"], state["brand_id"], state["requested_brand_id"], locked)
        msgs = []
        for p in props["proposals"]:
            brand = next(cl["text"] for cl in p["claims"] if cl["topic"] == "brand")
            aud = next(cl["text"] for cl in reversed(p["claims"]) if cl["topic"] == "audience")
            hero = next(cl["text"] for cl in reversed(p["claims"]) if cl["topic"] == "psgs")
            msgs.append(say(p["label"], f"{brand} {aud} {hero}. [cites blocks "
                            f"{sorted({b for cl in p['claims'] for b in cl['cites']})}]"))
        return c.update(stage="discuss", proposals=props, compact=False, chat=msgs,
                        audit=[audit(state, "discuss", "system", "proposals")])

    def cp_proposals(state: RunState):
        c = Ctx(state)
        props = state["proposals"]
        pos = state.get("position_edits") or []
        summary = ["Three advocates have proposed; add your position (e.g. 'drop the heavy cohort', "
                   "'make sharing bags a hero', 'keep the brand') or approve to start the critique rounds."]
        if pos:
            summary.append("Your position so far: " + ", ".join(_edit_text(e) for e in pos))
        if props["top_alternative"] and not props["locked"]:
            summary.append(f"Brand Scout: {brand_name(props['top_alternative'])} beats "
                           f"{brand_name(state['brand_id'])} on seasonal and market signals.")
        kn = []
        for p in props["proposals"]:
            inc = [COHORT_LABELS[k.split(':')[1]] for k, v in p["position"].items() if k.startswith("cohort:") and v]
            ex = sum(1 for k, v in p["position"].items() if k.startswith("psg:") and v)
            kn.append([p["label"], f"brand {brand_name(p['position']['brand'])} | {', '.join(inc)} | "
                                   f"{ex} PSGs excluded"])
        reply, route = checkpoint(state, c, "discuss", "Advocate proposals", summary, kn)

        def approve(base):
            return Command(goto="debate", update=c.update(**base))

        def edit(base):
            new_pos = [e for e in route["edits"] if e["op"] != "add_note"]
            return Command(goto="debate", update=c.update(**merge(base, {
                "position_edits": pos + new_pos,
                "chat": [say("Critic / Judge", "Your position joins the panel; running critique rounds.")]})))

        return dispatch(state, c, "discuss", reply, route, "cp_proposals", approve, edit)

    def debate(state: RunState):
        c = Ctx(state)
        props = state["proposals"]
        human = human_position(state.get("position_edits") or [], state["pack"], props["top_alternative"])
        dec = run_debate(c.rt, state["pack"], props, human)
        if state.get("decision") and state["decision"].get("notes_for_draft"):
            dec["notes_for_draft"] = list(dict.fromkeys(state["decision"]["notes_for_draft"] + dec["notes_for_draft"]))
        msgs = [say(t["speaker"], t["text"]) for t in dec["transcript"] if t["round"] > 0]
        msgs.append(say("Critic / Judge", f"{'Converged' if dec['converged'] else 'Stopped'} after {dec['rounds']} "
                        f"critique round(s), agreement {dec['agreement']:.0%}; scope adherence "
                        f"{dec['scope_adherence_pct']}%; {len(dec['notes_for_draft'])} off-scope note(s) logged."))
        return c.update(decision=dec, compact=False, chat=msgs,
                        audit=[audit(state, "discuss", "system", "debate_complete", rounds=dec["rounds"],
                                     agreement=dec["agreement"])])

    def cp_discuss(state: RunState):
        c = Ctx(state)
        dec = state["decision"]
        summary, kn = decision_card(dec, store)
        bd = dec["brand_decision"]
        switching = bd["selected"] != state["brand_id"]
        if switching:
            summary.insert(0, f"Proposed brand switch: {brand_name(state['brand_id'])} -> {brand_name(bd['selected'])}. "
                              "Approving re-runs the Diagnostic Pack for the new brand.")
        reply, route = checkpoint(state, c, "discuss", "Discussion outcome", summary, kn)

        def approve(base):
            # Agent-proposed switches stop once the brand is locked (max_brand_switches);
            # a switch from a human override is always honoured.
            if switching:
                return Command(goto="diagnose", update=c.update(**merge(base, {
                    "brand_id": bd["selected"], "brand_switches": state.get("brand_switches", 0) + 1,
                    "switch_record": {"from": state["brand_id"], "to": bd["selected"], "reason": bd["reason"],
                                      "alternatives_considered": bd["alternatives_considered"]},
                    "position_edits": [], "proposals": None,
                    "chat": [say("Campaign Planning Agent", f"Switching to {brand_name(bd['selected'])}; "
                                 "re-running the Diagnostic Pack.")],
                    "audit": [audit(state, "discuss", state["user"]["id"], "brand_switch",
                                    to=bd["selected"])]})))
            return Command(goto="brief", update=c.update(**merge(
                base, {"audit": [audit(state, "discuss", state["user"]["id"], "approved")]})))

        def edit(base):
            new, notes = apply_overrides(dec, route["edits"], state["pack"], state["proposals"]["top_alternative"])
            for e in new["human_overrides"][len(dec.get("human_overrides", [])):]:
                memory.record_override(state["run_id"], category(state), "discuss", e, _proposed(dec, e))
            return Command(goto="cp_discuss", update=c.update(**merge(base, {
                "decision": new, "compact": False,
                "chat": [say("Critic / Judge", "Human call recorded (overrides the Judge). " + " ".join(notes))]})))

        return dispatch(state, c, "discuss", reply, route, "cp_discuss", approve, edit)

    # Stage 5: Agreed Brief -----------------------------------------------
    def _decision_for_brief(state: RunState) -> dict:
        dec = dict(state["decision"])
        bd = dict(dec["brand_decision"])
        sw = state.get("switch_record")
        if sw and bd["selected"] == sw["to"]:
            bd["reason"] = sw["reason"]
            bd["alternatives_considered"] = [b for b in dict.fromkeys(
                sw.get("alternatives_considered", []) + bd["alternatives_considered"])
                if b not in (bd["selected"], state["requested_brand_id"])]
        bd["requested"] = state["requested_brand_id"]
        dec["brand_decision"] = bd
        return dec

    def brief(state: RunState):
        c = Ctx(state)
        out = BriefWriter(c.rt).write(state["brand_id"], _decision_for_brief(state), state["pack"])
        return c.update(stage="brief", brief=out["brief"], brief_summary=out["summary_md"], compact=False,
                        chat=[say("Brief Writer", out["summary_md"])],
                        audit=[audit(state, "brief", "system", "stage_complete")])

    def cp_brief(state: RunState):
        c = Ctx(state)
        b = state["brief"]
        summary = state["brief_summary"].splitlines()[1:]
        kn = [["diagnostics_ref", b["diagnostics_ref"]], ["provenance", f"{len(b['provenance'])} blocks"]]
        reply, route = checkpoint(state, c, "brief", "Agreed Brief - approve to hand off", summary, kn,
                                  actions=["Approve (hands off)", "Ask", "Edit", "Send back", "Pause"])

        def approve(base):
            out = BriefWriter(c.rt).write(state["brand_id"], _decision_for_brief(state), state["pack"],
                                          approved_by=state["user"]["id"])
            return Command(goto="handoff", update=c.update(**merge(base, {
                "brief": out["brief"], "brief_summary": out["summary_md"],
                "audit": [audit(state, "brief", state["user"]["id"], "approved")]})))

        def edit(base):
            new, notes = apply_overrides(state["decision"], route["edits"], state["pack"],
                                         (state.get("proposals") or {}).get("top_alternative"))
            if new["brand_decision"]["selected"] != state["brand_id"]:
                return send_back(state, c, "brief", reply, {**route, "target_stage": "plan"}, "cp_brief")
            for e in new["human_overrides"][len(state["decision"].get("human_overrides", [])):]:
                memory.record_override(state["run_id"], category(state), "brief", e, _proposed(state["decision"], e))
            st = {**state, "decision": new}
            out = BriefWriter(c.rt).write(state["brand_id"], _decision_for_brief(st), state["pack"])
            return Command(goto="cp_brief", update=c.update(**merge(base, {
                "decision": new, "brief": out["brief"], "brief_summary": out["summary_md"], "compact": False,
                "chat": [say("Brief Writer", " ".join(notes) + " Brief updated.")]})))

        return dispatch(state, c, "brief", reply, route, "cp_brief", approve, edit)

    def handoff_node(state: RunState):
        b = state["brief"]
        audit_log = state.get("audit", []) + [audit(state, "handoff", "system", "handoff")]
        receipt = handoff.send(state["run_id"], b, state["brief_summary"], audit_log)
        memory.record_run(state["run_id"], {
            "brand_requested": state["requested_brand_id"], "brand_selected": state["brand_id"],
            "category_id": category(state), "objective": state["objective"],
            "send_backs": state.get("send_backs", 0), "overrides": len(state["decision"].get("human_overrides", [])),
        })
        return {"status": "done", "handoff": receipt, "stage": "handoff",
                "chat": [say("Brief Writer", "Agreed Brief handed off to the Recommendation Draft process "
                             f"(external) via {receipt['sink']}: {receipt.get('path', receipt.get('index'))}.")],
                "audit": [audit(state, "handoff", "system", "handoff", receipt=receipt)]}

    g = StateGraph(RunState)
    for name, fn in [("understand", understand), ("cp_understand", cp_understand), ("plan", plan),
                     ("cp_plan", cp_plan), ("diagnose", diagnose), ("cp_diagnose", cp_diagnose),
                     ("propose", propose_node), ("cp_proposals", cp_proposals), ("debate", debate),
                     ("cp_discuss", cp_discuss), ("brief", brief), ("cp_brief", cp_brief),
                     ("handoff", handoff_node)]:
        g.add_node(name, fn)
    g.add_edge(START, "understand")
    g.add_edge("understand", "cp_understand")
    g.add_edge("plan", "cp_plan")
    g.add_edge("diagnose", "cp_diagnose")
    g.add_edge("propose", "cp_proposals")
    g.add_edge("debate", "cp_discuss")
    g.add_edge("brief", "cp_brief")
    g.add_edge("handoff", END)
    return g.compile(checkpointer=checkpointer)


def _proposed(decision: dict, edit: dict) -> dict:
    """What the agents had proposed for the item a human overrode (for learning)."""
    op = edit["op"]
    if op in ("include_cohort", "exclude_cohort"):
        a = next((a for a in decision["audience"] if a["cohort"] == edit["cohort"]), {})
        return {"include": a.get("include")}
    if op in ("exclude_psg", "include_psg", "hero_psg"):
        return {"hero": edit["psg_id"] in decision["qualifying_psgs"]["hero"]}
    if op in ("switch_brand", "keep_brand"):
        bd = decision["brand_decision"]
        return {"switch": bd["selected"] != bd["requested"]}
    return {}


def _edit_text(e: dict) -> str:
    op = e["op"]
    if op in ("include_cohort", "exclude_cohort"):
        return f"{op.split('_')[0]} {COHORT_LABELS[e['cohort']]}"
    if op in ("exclude_psg", "include_psg", "hero_psg"):
        return f"{op.split('_')[0]} {e['psg_id']}"
    if op == "switch_brand":
        return f"switch to {brand_name(e.get('brand_id')) if e.get('brand_id') else 'top alternative'}"
    if op == "keep_brand":
        return "keep the brand"
    return op


def diagnose_card(pack: dict) -> tuple[list[str], list[list[str]]]:
    b = pack["blocks"]

    def d(block, skill):
        return b.get(block, {}).get("data", {}).get(skill, {})

    b1, b2, b3 = d("1", "brand_shoppers"), d("2", "brand_spc"), d("3", "cohort_split")
    b6, b7k, b7s, b8 = d("6", "seasonality_index"), display_safe(d("7", "kantar_share")), d("7", "sentiment_scan"), \
        d("8", "margin_after_funding")
    scout = d("10", "brand_scout_score")
    psg, aud = pack["proposals"]["psgs"], pack["proposals"]["audience"]
    names = {p["psg_id"]: p["name"] for p in d("5", "psg_score").get("psgs", [])}
    rec = pack["reconciliation"]
    kn = []
    if b1:
        kn += [["Brand shoppers (6 wks)", f"{b1['shoppers_6w']:,} ({b1['change_vs_prior_pct']:+}% vs prior, "
                                          f"{b1['yoy_pct']:+}% YoY)"],
               ["Penetration", f"{b1['penetration_pct']}% of {b1['category_shoppers_6w']:,} category shoppers"],
               ["Lapsed pool / headroom", f"{b1['lapsed_pool']:,} / {b1['headroom_households']:,} hh"]]
    if b2:
        kn.append(["SPC", f"{gbp(b2['spc'])} = {b2['frequency']} trips x {gbp(b2['spend_per_trip'])}"])
    if b3:
        kn.append(["Loyalty tiers (shoppers/sales)", ", ".join(
            f"{c['cohort']} {c['shoppers_pct']:.0f}/{c['sales_pct']:.0f}%" for c in b3["loyalty_tier"]
            if not c["suppressed"])])
    if b6:
        kn.append(["Seasonality index (window)", f"{b6['seasonality_index']}"])
    if b7k:
        kn.append(["Kantar value share", f"{b7k['value_share']}% ({b7k['value_share_trend_pts']:+} pts), "
                                         f"{b7k['period']}, licence {b7k['licence_scope']}"])
    if b7s:
        kn.append(["Net sentiment (30d)", f"{b7s['net_sentiment_30d']} ({b7s['sentiment_trend']:+})"])
    if b8:
        kn.append(["Margin after funding", f"{b8['brand_margin_after_funding_pct']}% (floor {b8['margin_floor_pct']}%)"])
    kn.append(["Qualifying PSGs", f"{len(psg['include'])} in, {len(psg['exclude'])} out; heroes: "
                                  + ", ".join(names.get(h, h) for h in psg["hero"])])
    if scout:
        beats = [a for a in scout["alternatives"] if a["beats_requested"]]
        kn.append(["Brand Scout", (f"{beats[0]['name']} beats requested ({beats[0]['score']:+.2f} vs "
                                   f"{scout['requested']['score']:+.2f})") if beats else "no stronger alternative"])
    inc = [c for c in aud["cohorts"] if c["include"]]
    kn.append(["Audience proposal", ", ".join(f"{c['label']} {c['size']:,}" for c in inc)
               + f" = {aud['total_included']:,} hh"])
    kn.append(["Reconciliation", f"{sum(x['passed'] for x in rec['checks'])}/{len(rec['checks'])} checks passed"])
    summary = ["Ask about any number, ask for a new cut (e.g. 'SPC for light shoppers?'), or approve to discuss."]
    for e in psg["exclude"]:
        summary.append(f"Excluded {names.get(e['psg_id'], e['psg_id'])}: {e['reason']}")
    return summary, kn


def decision_card(dec: dict, store) -> tuple[list[str], list[list[str]]]:
    bd = dec["brand_decision"]
    q = dec["qualifying_psgs"]
    inc = [a for a in dec["audience"] if a["include"]]
    kn = [["Brand", f"{brand_name(bd['selected'])} (requested {brand_name(bd['requested'])})"],
          ["Audience", ", ".join(f"{COHORT_LABELS[a['cohort']]} {a['size']:,}" for a in inc)
           + f" = {sum(a['size'] for a in inc):,} hh"],
          ["PSGs", f"{len(q['include'])} in, {len(q['exclude'])} out"],
          ["Hero PSGs", ", ".join(store.psgs[h]["name"] for h in q["hero"])],
          ["Rounds / agreement", f"{dec['rounds']} / {dec['agreement']:.0%}"]]
    summary = [f"Brand reason: {bd['reason']}"]
    for x in dec["dissent"]:
        summary.append(f"Dissent - {x['by']}: {x['position']}")
    for n in dec["notes_for_draft"]:
        summary.append(f"Note for draft: {n}")
    if dec.get("human_overrides"):
        summary.append(f"Human overrides applied: {len(dec['human_overrides'])}")
    return summary, kn
