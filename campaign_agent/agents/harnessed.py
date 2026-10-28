"""LRM-backed agent policies: each binds a skill to the harness.

Every function here follows the same pattern:

    task      - what the agent is asked (compact, JSON-serialisable)
    fallback  - the deterministic policy (also the simulated model's answer)
    validate  - guardrails on the model's output: closed vocabularies, hard
                filters, grounded numbers; raises ValueError to make the
                model fix its output

and records what happened (source, fallback reason, memory writes) on the
HarnessLog so the graph can persist it in run state.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..data.taxonomy import AUDIENCE_COHORTS, BRANDS, COHORT_LABELS
from ..guardrails import display_safe
from ..harness.guards import subset_sums, ungrounded_numbers
from ..tools.servers.planning import BASE_PLAN, CORE_TOOLS, STAGES, TOOL_AGENT
from .findings import AGENT_BLOCKS, SPECIALIST_SKILLS, findings_for

EDIT_OPS = {"include_cohort", "exclude_cohort", "include_psg", "exclude_psg", "hero_psg", "add_tool",
            "remove_tool", "switch_brand", "keep_brand", "set_objective", "set_window", "add_note"}
OBJECTIVES = ("reactivate", "grow", "retain", "trial")
WINDOWED = {t for _, t, _, _, needs in BASE_PLAN if needs}


@dataclass
class HarnessLog:
    """Collected per graph node and written to state (harness_log, working_memory)."""

    runs: list[dict] = field(default_factory=list)
    memory: list[dict] = field(default_factory=list)

    def add(self, res) -> None:
        self.runs.append({"skill": res.skill, "source": res.source, "reason": res.reason,
                          "iterations": res.iterations,
                          "tools": [e["name"] for e in res.events if e["type"] == "tool"],
                          "rejected": sum(e["type"] == "invalid_output" for e in res.events)})
        self.memory.extend(res.memory_writes)


def _run(h, log: HarnessLog, skill, rt, state, task, fallback, validate=None):
    res = h.run(skill, rt=rt, state=state, task=task, fallback=fallback, validate=validate)
    log.add(res)
    return res


def _cohort_totals(pack: dict) -> list[int]:
    return subset_sums([c["size"] for c in pack["proposals"]["audience"]["cohorts"] if c["size"] is not None])


def _check_grounded(texts: list[str], sources: list, derived=None) -> None:
    bad = [n for t in texts for n in ungrounded_numbers(t, sources, derived)]
    if bad:
        raise ValueError(f"numbers not found in tool results or the pack: {sorted(set(bad))[:8]}")


# ── Chat Gateway ───────────────────────────────────────────────────────────

def clean_edits(edits: list[dict], state: dict, store, registry_names: list[str]) -> list[dict]:
    brand = state.get("brand_id")
    out, bad = [], []
    for e in edits:
        op = e.get("op")
        if op not in EDIT_OPS:
            bad.append(f"unknown op {op!r}")
            continue
        if op in ("include_cohort", "exclude_cohort"):
            if e.get("cohort") not in AUDIENCE_COHORTS:
                bad.append(f"{op}: cohort must be one of {AUDIENCE_COHORTS}")
                continue
            out.append({"op": op, "cohort": e["cohort"]})
        elif op in ("include_psg", "exclude_psg", "hero_psg"):
            p = store.psgs.get(e.get("psg_id"))
            if not p or (brand and p["brand_id"] != brand):
                bad.append(f"{op}: {e.get('psg_id')!r} is not a PSG of the current brand")
                continue
            out.append({"op": op, "psg_id": e["psg_id"]})
        elif op in ("add_tool", "remove_tool"):
            if e.get("tool") not in registry_names:
                bad.append(f"{op}: {e.get('tool')!r} is not a registry tool")
                continue
            out.append({"op": op, "tool": e["tool"]})
        elif op == "switch_brand":
            b = e.get("brand_id")
            if b is None:
                out.append({"op": op, "brand_id": None, "hint": "top_alternative"})
            elif b in BRANDS and (not brand or BRANDS[b]["category"] == BRANDS[brand]["category"]):
                out.append({"op": op, "brand_id": b})
            else:
                bad.append(f"switch_brand: {b!r} is not a brand in the current category")
        elif op == "keep_brand":
            b = e.get("brand_id")
            out.append({"op": op, **({"brand_id": b} if b in BRANDS else {})})
        elif op == "set_objective":
            if e.get("objective") not in OBJECTIVES:
                bad.append(f"set_objective: one of {OBJECTIVES}")
                continue
            out.append({"op": op, "objective": e["objective"]})
        elif op == "set_window":
            w = e.get("window") or {}
            try:
                start = date.fromisoformat(w["start"])
                weeks = int(w["weeks"])
                assert 1 <= weeks <= 26
            except (KeyError, ValueError, TypeError, AssertionError):
                bad.append("set_window: window needs start (YYYY-MM-DD) and weeks 1-26")
                continue
            start -= timedelta(days=start.weekday())
            out.append({"op": op, "window": {"start": start.isoformat(), "weeks": weeks, "assumed": []}})
        elif op == "add_note":
            out.append({"op": op, "text": str(e.get("text", ""))[:500]})
    if bad:
        raise ValueError("; ".join(bad))
    return out


def route(h, log, rt, state, reply, stage, awaiting, card, registry_names, fallback):
    task = {"text": reply, "stage": stage, "brand_id": state.get("brand_id"),
            "awaiting_clarification": awaiting, "known_tools": registry_names,
            "checkpoint_card": (card or {}).get("text", "")[:1500]}

    def validate(o, _outs):
        edits = clean_edits(o.get("edits", []), state, rt.store, registry_names)
        if o["action"] == "approve" and edits:
            raise ValueError("approve cannot carry edits; use action 'edit'")
        if o["action"] == "edit" and not edits:
            raise ValueError("action 'edit' needs at least one edit; otherwise use 'ask'")
        return {"stage": stage, "action": o["action"], "target_stage": o.get("target_stage"),
                "edits": edits, "text": reply[:2000]}

    return _run(h, log, "chat-gateway", rt, state, task, fallback, validate).output


# ── Entity Resolution ────────────────────────────────────────────────────

def resolve(h, log, rt, state, er, text, pending=None, edits=None):
    task = {"text": text, **({"pending": {k: pending[k] for k in ("brand_id", "candidates", "objective", "window")},
                              "edits": edits or []} if pending else {})}

    def fallback():
        res = er.apply_answer(pending, text, edits or []) if pending else er.resolve(text)
        return {k: res[k] for k in ("brand_id", "candidates", "objective", "window", "confidence")}

    def validate(o, _outs):
        if o["brand_id"] is not None and o["brand_id"] not in BRANDS:
            raise ValueError(f"unknown brand_id {o['brand_id']!r}")
        if any(c not in BRANDS for c in o["candidates"]):
            raise ValueError("candidates must be brand ids from the taxonomy")
        try:
            date.fromisoformat(o["window"]["start"])
        except ValueError as exc:
            raise ValueError("window.start must be YYYY-MM-DD") from exc
        return o

    out = _run(h, log, "entity-resolution", rt, state, task, fallback, validate).output
    return er._questions(dict(out))  # questions, status and taxonomy lookups stay deterministic


# ── Planner ───────────────────────────────────────────────────────────────

def plan(h, log, rt, state, planner):
    brand, objective, window = state["brand_id"], state["objective"], state["window"]
    task = {"brand_id": brand, "brand": BRANDS[brand]["name"], "objective": objective, "window": window}

    def fallback():
        p = planner.plan(brand, objective, window)
        return {"steps": [{k: v for k, v in s.items() if k != "id"} for s in p["steps"]], "signals": p["signals"]}

    def validate(o, _outs):
        steps, bad = [], []
        for s in o["steps"]:
            t = s["tool"]
            if t not in rt.registry.tools or TOOL_AGENT.get(t) != s["agent"]:
                bad.append(f"{t!r} is not a diagnostic tool owned by {s['agent']!r}")
                continue
            allowed = set(rt.registry.get(t).inputs) - {"brand_id"}
            extra = set(s["params"]) - allowed
            if extra:
                bad.append(f"{t}: unknown params {sorted(extra)}")
                continue
            params = dict(s["params"])
            if t in WINDOWED:
                params.update(window_start=window["start"], window_weeks=window["weeks"])
            steps.append({**s, "params": params})
        missing = CORE_TOOLS - {s["tool"] for s in steps}
        if missing:
            bad.append(f"core tools missing: {sorted(missing)}")
        if bad:
            raise ValueError("; ".join(bad))
        for i, s in enumerate(steps, start=1):
            s["id"] = f"s{i}"
        return {"brand_id": brand, "objective": objective, "window": {"start": window["start"],
                                                                       "weeks": window["weeks"]},
                "signals": o["signals"], "steps": steps}

    return _run(h, log, "campaign-planner", rt, state, task, fallback, validate).output


# ── Specialist findings + audience ────────────────────────────────────────

def findings(h, log, rt, state):
    pack = state["pack"]

    def one(agent):
        blocks = AGENT_BLOCKS[agent]
        data = {str(b): pack["blocks"].get(str(b), {}).get("data") for b in blocks}
        sources = [display_safe(data), pack["proposals"]]

        def validate(o, _outs):
            _check_grounded([f["text"] for f in o["findings"]], sources)
            return o

        task = {"agent": agent, "blocks": blocks, "objective": state.get("objective")}
        return agent, h.run(SPECIALIST_SKILLS[agent], rt=rt, state=state, task=task,
                            fallback=lambda: findings_for(agent, pack), validate=validate)

    with ThreadPoolExecutor(max_workers=len(SPECIALIST_SKILLS), thread_name_prefix="findings") as pool:
        results = list(pool.map(one, SPECIALIST_SKILLS))
    out = {}
    for agent, res in results:
        log.add(res)
        out[agent] = res.output
    return out


def audience(h, log, rt, state, pack):
    base = pack["proposals"]["audience"]
    by = {c["cohort"]: c for c in base["cohorts"]}
    task = {"objective": state["objective"],
            "cohorts": [{k: c[k] for k in ("cohort", "size", "score", "include", "reason")} for c in base["cohorts"]]}

    def fallback():
        return {"cohorts": [{"cohort": c["cohort"], "include": c["include"], "reason": c["reason"]}
                            for c in base["cohorts"]], "rationale": "baseline audience model"}

    def validate(o, _outs):
        seen = [c["cohort"] for c in o["cohorts"]]
        if sorted(seen) != sorted(AUDIENCE_COHORTS):
            raise ValueError(f"give each of {AUDIENCE_COHORTS} exactly once")
        for c in o["cohorts"]:
            if c["include"] and by[c["cohort"]]["size"] is None:
                raise ValueError(f"{c['cohort']} is below the minimum cell size and cannot be included")
        _check_grounded([c["reason"] for c in o["cohorts"]] + [o.get("rationale", "")],
                        [task, pack["blocks"].get("9", {}), pack["blocks"].get("3", {})], _cohort_totals(pack))
        return o

    o = _run(h, log, "audience", rt, state, task, fallback, validate).output
    cohorts = []
    for c in o["cohorts"]:
        b = by[c["cohort"]]
        cohorts.append({**b, "include": c["include"], "reason": c["reason"]})
    cohorts.sort(key=lambda c: -c["score"])
    return {**base, "cohorts": cohorts, "rationale": o.get("rationale", ""),
            "total_included": sum(c["size"] or 0 for c in cohorts if c["include"])}


# ── Pack Analyst ──────────────────────────────────────────────────────────

def answer(h, log, rt, state, question, fallback_fn):
    pack = state.get("pack")
    holder = {}

    def fallback():
        agent, text, cut = fallback_fn()
        holder["cut"] = cut
        return {"agent": agent, "answer": text, "blocks": []}

    def validate(o, outs):
        sources = [display_safe(pack["blocks"]) if pack else {}, [x["output"] for x in outs],
                   (pack or {}).get("proposals", {}), state.get("plan") or {},
                   (holder.get("cut") or {}).get("output", {})]  # a cut the fallback policy really ran
        _check_grounded([o["answer"]], sources, _cohort_totals(pack) if pack else None)
        return o

    res = _run(h, log, "pack-analyst", rt, state, {"question": question}, fallback, validate)
    cut = holder.get("cut")
    if res.source == "llm" and res.tool_outputs:
        last = res.tool_outputs[-1]
        cut = {"tool": last["tool"], "params": {k: v for k, v in last["args"].items() if k != "brand_id"},
               "output": last["output"], "provenance": last["provenance"]}
    return res.output["agent"], res.output["answer"], cut


# ── Debate ───────────────────────────────────────────────────────────────

def _advocate_task(ev: dict, pack: dict) -> dict:
    psg = ev["psg_prop"]
    excl = {e["psg_id"]: e for e in psg["exclude"]}
    margin = {p["psg_id"]: p for p in ev["margin"].get("psgs", [])}
    return {
        "objective": ev["objective"], "brand_id": ev["brand_id"], "requested_brand_id": ev["requested_id"],
        "brand_locked": ev["locked"], "requested_drivers": ev["req_drivers"],
        "top_alternative": ev["top_alt"] and {k: ev["top_alt"][k] for k in ("brand_id", "name", "score", "drivers")},
        "spc": ev["spc"], "brand_margin_after_funding_pct": ev["margin"].get("brand_margin_after_funding_pct"),
        "audience": [{k: c[k] for k in ("cohort", "size", "score", "include")} for c in ev["audience"].values()],
        "past_response": ev["history"],
        "psgs": [{"psg_id": pid, "name": s["name"], "reach_pct": s["reach_pct"], "hero_score": psg["scores"].get(pid),
                  "margin_after_funding_pct": margin.get(pid, {}).get("margin_after_funding_pct"),
                  "excluded": pid in excl, "filters": excl.get(pid, {}).get("filters", []),
                  "reason": excl.get(pid, {}).get("reason")} for pid, s in ev["psg_scores"].items()],
    }


def advocate_fn(h, log, rt, state, pack):
    skills = {"reach_advocate": "reach-advocate", "margin_advocate": "margin-advocate",
              "customer_advocate": "customer-advocate"}

    def run(cls, ev):
        task = _advocate_task(ev, pack)
        adv = cls()
        hard = {p["psg_id"] for p in task["psgs"] if set(p["filters"]) & {"availability", "promo_clash"}}
        brands = {b for b, s in BRANDS.items() if s["category"] == BRANDS[ev["brand_id"]]["category"]}

        def validate(o, _outs):
            pos, bad = dict(o["position"]), []
            if pos.get("brand") not in brands:
                bad.append("position.brand must be a brand in the category")
            if ev["locked"] and pos.get("brand") != ev["brand_id"]:
                bad.append("brand is locked; keep the current brand")
            for c in AUDIENCE_COHORTS:
                if not isinstance(pos.get(f"cohort:{c}"), bool):
                    bad.append(f"position['cohort:{c}'] must be true/false")
            for pid in ev["psg_scores"]:
                if not isinstance(pos.get(f"psg:{pid}"), bool):
                    bad.append(f"position['psg:{pid}'] must be true (excluded) / false")
                elif pid in hard and not pos[f"psg:{pid}"]:
                    bad.append(f"{pid} fails a hard filter and must stay excluded")
            for c in AUDIENCE_COHORTS:
                if pos.get(f"cohort:{c}") and ev["audience"][c]["size"] is None:
                    bad.append(f"{c} is below the minimum cell size")
            extra = set(pos) - {"brand"} - {f"cohort:{c}" for c in AUDIENCE_COHORTS} - {f"psg:{p}" for p in ev["psg_scores"]}
            if extra:
                bad.append(f"unknown position keys {sorted(extra)[:5]}")
            hero = [x for x in o["hero"] if x in ev["psg_scores"] and not pos.get(f"psg:{x}", True)]
            if len(hero) != len(o["hero"]):
                bad.append("hero PSGs must be included PSGs of the brand")
            if bad:
                raise ValueError("; ".join(bad))
            _check_grounded([c["text"] for c in o["claims"]], [task, display_safe(pack["blocks"])],
                            _cohort_totals(pack))
            strength = {k: min(1.0, max(0.0, float(o["strength"].get(k, 0.5)))) for k in pos}
            return {"advocate": adv.key, "label": adv.label, "position": pos, "strength": strength, "hero": hero,
                    "claims": [{**c, "speaker": adv.label} for c in o["claims"]]}

        def fallback():
            p = adv.propose(ev)
            return {k: p[k] for k in ("position", "strength", "hero", "claims")}

        res = h.run(skills[adv.key], rt=rt, state=state, task=task, fallback=fallback, validate=validate)
        log.add(res)
        out = res.output
        return out if "advocate" in out else {"advocate": adv.key, "label": adv.label, **out}

    return run


def scope_fn(h, log, rt, state):
    brand = state["brand_id"]

    def run(claims):
        rule = rt.registry.call("scope_check", {"brand_id": brand, "claims": claims}, caller="critic_judge",
                                allow_list={"scope_check"}, role=rt.user["role"])["output"]

        def validate(o, _outs):
            if [r["index"] for r in o["results"]] != list(range(len(claims))):
                raise ValueError(f"give one result per claim, index 0..{len(claims) - 1} in order")
            results = []
            for r, base in zip(o["results"], rule["results"]):
                ok = r["in_scope"] and base["in_scope"]  # the rule-based check can only be tightened
                results.append({**r, "in_scope": ok, "topic": r["topic"] if ok else base["topic"]})
            pct = round(100 * sum(r["in_scope"] for r in results) / len(results), 1) if results else 100.0
            return {"brand_id": brand, "results": results, "in_scope_pct": pct}

        res = _run(h, log, "critic-judge", rt, state, {"brand_id": brand, "claims": claims},
                   lambda: {"results": rule["results"], "in_scope_pct": rule["in_scope_pct"]}, validate)
        return res.output if "brand_id" in res.output else {"brand_id": brand, **res.output}

    return run


# ── Brief Writer ───────────────────────────────────────────────────────────

def brief_summary(h, log, rt, state, brief, fallback_md):
    names = {p: rt.store.psgs[p]["name"] for p in brief["qualifying_psgs"]["include"]}
    task = {"brief": brief, "brand_names": {b: BRANDS[b]["name"] for b in
                                            {brief["brand_id"], brief["brand_decision"]["requested"]}},
            "psg_names": names, "cohort_labels": COHORT_LABELS}

    def validate(o, _outs):
        md = o["summary_md"].strip()
        if not md.startswith("**Agreed Brief"):
            raise ValueError("summary_md must start with '**Agreed Brief - <brand>**'")
        _check_grounded([md], [task], subset_sums([a["size"] for a in brief["audience"] if a["size"]]))
        return {"summary_md": md}

    return _run(h, log, "brief-writer", rt, state, task, lambda: {"summary_md": fallback_md}, validate).output["summary_md"]


__all__ = ["HarnessLog", "advocate_fn", "answer", "audience", "brief_summary", "clean_edits", "findings", "plan",
           "resolve", "route", "scope_fn", "STAGES"]
