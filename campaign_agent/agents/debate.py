"""Stage 4: bounded debate on brand, audience and PSGs.

Protocol (from the design):
1. Reach, Margin and Customer advocates each propose a brand decision,
   audience and PSG set, citing pack blocks.
2. The human sees the proposals and can add a position, treated like any
   advocate's (the human votes only on the items they touched).
3. Up to two critique rounds; the Judge rejects any claim outside brand,
   audience or PSGs and logs it as a note for the external draft process.
4. Converge when proposals agree within tolerance or after the maximum
   number of rounds; remaining dissent is recorded. The human's final call
   at the checkpoint overrides the Judge.

Advocates are read-only on the pack: every claim cites the blocks its
numbers come from, and the Judge checks that those blocks exist.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

from ..data.taxonomy import AUDIENCE_COHORTS, BRANDS, COHORT_LABELS
from .base import AgentRuntime
from .diagnose import CriticJudge

CONCEDE_BELOW = {1: 0.6, 2: 0.8}  # strength under which a minority position concedes, per round


def _evidence(pack: dict, brand_id: str, requested_id: str, locked: bool) -> dict:
    b = pack["blocks"]

    def get(block, tool, default=None):
        return b.get(block, {}).get("data", {}).get(tool, default or {})

    scout = get("10", "brand_scout_score")
    alts = [a for a in scout.get("alternatives", []) if a["beats_requested"]]
    return {
        "brand_id": brand_id, "requested_id": requested_id, "locked": locked,
        "scout": scout, "top_alt": alts[0] if alts else None,
        "req_drivers": scout.get("requested", {}).get("drivers", {}),
        "margin": get("8", "margin_after_funding"),
        "history": get("9", "multistep_history").get("by_cohort", {}),
        "psg_scores": {p["psg_id"]: p for p in get("5", "psg_score").get("psgs", [])},
        "audience": {c["cohort"]: c for c in pack["proposals"]["audience"]["cohorts"]},
        "psg_prop": pack["proposals"]["psgs"],
        "spc": get("2", "brand_spc").get("spc"),
        "objective": pack["objective"],
    }


def _name(bid):
    return BRANDS[bid]["name"]


class Advocate:
    key = ""
    label = ""

    def propose(self, ev: dict) -> dict:
        pos, strength, claims = {}, {}, []
        self.brand(ev, pos, strength, claims)
        self.cohorts(ev, pos, strength, claims)
        hero = self.psgs(ev, pos, strength, claims)
        return {"advocate": self.key, "label": self.label, "position": pos, "strength": strength,
                "hero": hero, "claims": [{**c, "speaker": self.label} for c in claims]}

    # brand -----------------------------------------------------------------
    def brand(self, ev, pos, strength, claims):
        if ev["locked"]:
            pos["brand"], strength["brand"] = ev["brand_id"], 0.9
            claims.append({"topic": "brand", "cites": [10],
                           "text": f"Brand already switched once; keep {_name(ev['brand_id'])}."})
            return
        alt = ev["top_alt"]
        switch, s, text, cites = self.brand_case(ev, alt)
        pos["brand"] = alt["brand_id"] if switch else ev["brand_id"]
        strength["brand"] = s
        claims.append({"topic": "brand", "item": "brand", "cites": cites, "text": text})

    def brand_case(self, ev, alt):  # pragma: no cover - overridden
        raise NotImplementedError

    # audience ---------------------------------------------------------------
    def cohorts(self, ev, pos, strength, claims):
        picked = []
        for c in AUDIENCE_COHORTS:
            a = ev["audience"].get(c)
            if not a or a["size"] is None:
                pos[f"cohort:{c}"], strength[f"cohort:{c}"] = False, 0.9
                continue
            inc, s, why = self.cohort_case(ev, c, a)
            pos[f"cohort:{c}"], strength[f"cohort:{c}"] = inc, s
            if inc:
                picked.append(c)
            if why:
                claims.append({"topic": "audience", "item": f"cohort:{c}", "cites": [3, 9], "text": why})
        total = sum(ev["audience"][c]["size"] for c in picked)
        claims.append({"topic": "audience", "cites": [1, 3],
                       "text": f"Target {', '.join(COHORT_LABELS[c] for c in picked) or 'no cohorts'}: "
                               f"{total:,} households."})

    def cohort_case(self, ev, cohort, a):  # pragma: no cover - overridden
        raise NotImplementedError

    # PSGs -----------------------------------------------------------------
    def psgs(self, ev, pos, strength, claims) -> list[str]:
        excluded = {e["psg_id"]: e for e in ev["psg_prop"]["exclude"]}
        for pid in ev["psg_scores"]:
            ex, s, why = self.psg_case(ev, pid, excluded.get(pid))
            pos[f"psg:{pid}"], strength[f"psg:{pid}"] = ex, s
            if why:
                claims.append({"topic": "psgs", "item": f"psg:{pid}", "cites": [5, 8], "text": why})
        included = [p for p in ev["psg_scores"] if not pos[f"psg:{p}"]]
        hero = self.hero(ev, included)
        claims.append({"topic": "psgs", "cites": [5],
                       "text": "Hero PSGs: " + ", ".join(ev["psg_scores"][p]["name"] for p in hero)})
        return hero

    def psg_case(self, ev, pid, exclusion):
        """Default: follow the Qualifying PSG Agent's hard filters."""
        if exclusion:
            return True, 0.9, None
        return False, 0.6, None

    def hero(self, ev, included):
        scores = ev["psg_prop"]["scores"]
        return sorted(included, key=lambda p: -scores.get(p, 0))[:2]


class ReachAdvocate(Advocate):
    key, label = "reach_advocate", "Reach advocate"

    def brand_case(self, ev, alt):
        req = ev["req_drivers"]
        if alt and (alt["drivers"]["seasonal_fit"] > req["seasonal_fit"] or alt["drivers"]["headroom"] > req["headroom"]):
            d = alt["drivers"]
            return True, 0.7, (f"Switch to {alt['name']}: seasonal index {d['seasonal_fit']:.2f} vs "
                               f"{req['seasonal_fit']:.2f} in the window and headroom {d['headroom']:.1f}% "
                               f"vs {req['headroom']:.1f}% of category shoppers."), [6, 10]
        return False, 0.5, f"Keep {_name(ev['brand_id'])}: no alternative reaches more shoppers in the window.", [6, 10]

    def cohort_case(self, ev, cohort, a):
        if cohort == "heavy" and ev["objective"] != "retain":
            return False, 0.6, None
        inc = a["score"] >= 0.2
        s = 0.8 if inc == a["include"] else 0.5
        why = (f"Include {a['label']} ({a['size']:,} hh) - widens reach beyond the core proposal."
               if inc and not a["include"] else None)
        return inc, s, why

    def psg_case(self, ev, pid, exclusion):
        if exclusion and exclusion["filters"] == ["margin"]:
            reach = ev["psg_scores"][pid]["reach_pct"] or 0
            if reach >= 20:
                return False, 0.55, (f"Keep {ev['psg_scores'][pid]['name']} in the basket: bought by "
                                     f"{reach}% of brand shoppers.")
        return super().psg_case(ev, pid, exclusion)

    def hero(self, ev, included):
        return sorted(included, key=lambda p: -(ev["psg_scores"][p]["reach_pct"] or 0))[:2]


class MarginAdvocate(Advocate):
    key, label = "margin_advocate", "Margin advocate"

    def brand_case(self, ev, alt):
        req_m = ev["req_drivers"].get("margin") or 0
        if alt and alt["drivers"]["margin"] >= req_m:
            return True, 0.6, (f"Switch to {alt['name']}: it beats on market signals without giving up "
                               f"margin ({alt['drivers']['margin']}% vs {req_m}%)."), [8, 10]
        gap = (req_m - alt["drivers"]["margin"]) if alt else 0
        text = (f"Keep {_name(ev['brand_id'])}: margin after funding {req_m}% vs {alt['drivers']['margin']}% "
                f"for {alt['name']}." if alt else f"Keep {_name(ev['brand_id'])}: no stronger alternative.")
        return False, 0.85 if gap >= 3 else 0.55, text, [8, 10]

    def cohort_case(self, ev, cohort, a):
        h = ev["history"].get(cohort, {})
        if cohort == "heavy":
            return False, 0.9, (f"Exclude Heavy: GBP {h.get('incremental_spend_per_hh', 0):.2f} incremental/hh "
                                "in past Multi-Steps - mostly subsidy." if h else None)
        if cohort == "non_buyers" and h and h.get("participation_pct", 0) < 10:
            return False, 0.6, (f"Exclude Category non-buyers: {h['participation_pct']}% participation "
                                "in past Multi-Steps.")
        return a["include"], 0.6, None

    def psg_case(self, ev, pid, exclusion):
        if exclusion and "margin" in exclusion["filters"]:
            return True, 0.9, f"Exclude {exclusion['name']}: {exclusion['reason']}."
        return super().psg_case(ev, pid, exclusion)

    def hero(self, ev, included):
        m = {p["psg_id"]: p["margin_after_funding_pct"] for p in ev["margin"].get("psgs", [])}
        return sorted(included, key=lambda p: -(m.get(p, 0) * (ev["psg_scores"][p]["reach_pct"] or 0)))[:2]

    def propose(self, ev):
        prop = super().propose(ev)
        brand_margin = ev["margin"].get("brand_margin_after_funding_pct") or 0
        if brand_margin < 24 and ev["spc"]:
            # Advocates drift; the Judge catches this and logs it for the draft process.
            prop["claims"].append({"speaker": self.label, "topic": None, "cites": [2, 8],
                                   "text": f"Keep the step-1 reward small relative to SPC (GBP {ev['spc']:.2f}) "
                                           "to protect margin."})
        return prop


class CustomerAdvocate(Advocate):
    key, label = "customer_advocate", "Customer advocate"

    def brand_case(self, ev, alt):
        req_s = ev["req_drivers"].get("sentiment", 0)
        if alt and alt["drivers"]["sentiment"] > req_s + 2:
            return True, 0.7, (f"Switch to {alt['name']}: net sentiment trend {alt['drivers']['sentiment']:+.1f} "
                               f"vs {req_s:+.1f} - customers are moving towards it."), [7, 10]
        return False, 0.5, f"Keep {_name(ev['brand_id'])}: sentiment gives no reason to switch.", [7, 10]

    def cohort_case(self, ev, cohort, a):
        hist = ev["history"]
        if not hist or cohort not in hist:
            return a["include"], 0.5, None
        incs = {c: h["incremental_spend_per_hh"] for c, h in hist.items()}
        if cohort == "heavy" and incs[cohort] == min(incs.values()):
            return False, 0.7, (f"Exclude Heavy: lowest past response, GBP {incs[cohort]:.2f} incremental/hh.")
        inc = a["include"] or incs[cohort] >= median(incs.values())
        why = (f"Include {a['label']}: GBP {incs[cohort]:.2f} incremental/hh in past Multi-Steps."
               if inc and not a["include"] else None)
        return inc, 0.7 if why else 0.6, why


ADVOCATES = [ReachAdvocate, MarginAdvocate, CustomerAdvocate]


def evidence(pack: dict, brand_id: str, requested_id: str, locked: bool) -> dict:
    return _evidence(pack, brand_id, requested_id, locked)


def propose(rt: AgentRuntime, pack: dict, brand_id: str, requested_id: str, locked: bool,
            advocate_fn=None) -> dict:
    """advocate_fn(cls, ev) -> proposal lets the harness run each advocate on the LRM."""
    ev = _evidence(pack, brand_id, requested_id, locked)
    if advocate_fn is None:
        proposals = [cls().propose(ev) for cls in ADVOCATES]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(ADVOCATES), thread_name_prefix="advocate") as pool:
            proposals = list(pool.map(lambda cls: advocate_fn(cls, ev), ADVOCATES))
    return {"brand_id": brand_id, "requested_id": requested_id, "locked": locked,
            "top_alternative": ev["top_alt"]["brand_id"] if ev["top_alt"] else None,
            "proposals": proposals}


def human_position(edits: list[dict], pack: dict, top_alt: str | None) -> dict | None:
    pos, hero = {}, []
    for e in edits:
        op = e["op"]
        if op == "include_cohort":
            pos[f"cohort:{e['cohort']}"] = True
        elif op == "exclude_cohort":
            pos[f"cohort:{e['cohort']}"] = False
        elif op == "exclude_psg":
            pos[f"psg:{e['psg_id']}"] = True
        elif op == "include_psg":
            pos[f"psg:{e['psg_id']}"] = False
        elif op == "hero_psg":
            hero.append(e["psg_id"])
            pos[f"psg:{e['psg_id']}"] = False
        elif op == "switch_brand":
            target = e.get("brand_id") or top_alt
            if target:
                pos["brand"] = target
        elif op == "keep_brand":
            pos["brand"] = e.get("brand_id") or pack["brand_id"]
    if not pos and not hero:
        return None
    return {"advocate": "human", "label": "Campaign manager", "position": pos,
            "strength": {k: 1.0 for k in pos}, "hero": hero,
            "claims": [{"speaker": "Campaign manager", "topic": _topic(k), "cites": [],
                        "text": f"Position: {describe_item(k, v, pack)}"} for k, v in pos.items()]}


def describe_item(item: str, value, pack: dict) -> str:
    if item == "brand":
        return f"brand {_name(value)}"
    kind, key = item.split(":", 1)
    if kind == "cohort":
        return f"{'include' if value else 'exclude'} {COHORT_LABELS[key]}"
    name = next((p["name"] for p in pack["blocks"].get("5", {}).get("data", {}).get("psg_score", {}).get("psgs", [])
                 if p["psg_id"] == key), key)
    return f"{'exclude' if value else 'include'} {name}"


def _majority(participants: list[dict], item: str, baseline):
    votes = Counter(p["position"][item] for p in participants if item in p["position"])
    if not votes:
        return baseline, 0, 0
    top = votes.most_common()
    best_n = top[0][1]
    leaders = [v for v, n in top if n == best_n]
    value = baseline if baseline in leaders else leaders[0]
    return value, votes[value], sum(votes.values())


def _baseline(pack: dict, item: str):
    if item == "brand":
        return pack["brand_id"]
    kind, key = item.split(":", 1)
    if kind == "cohort":
        return next(c["include"] for c in pack["proposals"]["audience"]["cohorts"] if c["cohort"] == key)
    return any(e["psg_id"] == key for e in pack["proposals"]["psgs"]["exclude"])


def run_debate(rt: AgentRuntime, pack: dict, proposals: dict, human: dict | None, scope_fn=None) -> dict:
    """scope_fn(claims) -> scope_check-shaped result lets the harness run the Judge on the LRM."""
    s = rt.settings
    participants = [dict(p, position=dict(p["position"])) for p in proposals["proposals"]]
    if human:
        participants.append(dict(human, position=dict(human["position"])))
    items = sorted({i for p in participants for i in p["position"]})
    transcript = [dict(c, round=0) for p in participants for c in p["claims"]]

    def agreement():
        """Share of items on which every participant who voted agrees."""
        voted = [(n, tot) for n, tot in (_majority(participants, i, _baseline(pack, i))[1:] for i in items) if tot]
        return sum(n == tot for n, tot in voted) / len(voted) if voted else 1.0

    rounds, agr = 0, agreement()
    while agr < s.convergence_tolerance and rounds < s.max_debate_rounds:
        rounds += 1
        threshold = CONCEDE_BELOW.get(rounds, 0.8)
        for item in items:
            maj, _, _ = _majority(participants, item, _baseline(pack, item))
            for p in participants:
                if item not in p["position"] or p["position"][item] == maj:
                    continue
                if p["strength"].get(item, 0.5) < threshold:
                    transcript.append({"round": rounds, "speaker": p["label"], "topic": _topic(item), "cites": [],
                                       "text": f"Concedes: {describe_item(item, maj, pack)} (majority view)."})
                    p["position"][item] = maj
                else:
                    transcript.append({"round": rounds, "speaker": p["label"], "topic": _topic(item), "cites": [],
                                       "text": f"Holds: {describe_item(item, p['position'][item], pack)}."})
        agr = agreement()

    claims = [{"text": c["text"], "topic": c["topic"]} for c in transcript]
    scope = scope_fn(claims) if scope_fn else CriticJudge(rt).scope(pack["brand_id"], claims)
    notes = []
    for c, r in zip(transcript, scope["results"]):
        c["in_scope"] = r["in_scope"]
        c["cites_valid"] = all(str(b) in pack["blocks"] for b in c["cites"])
        if not r["in_scope"]:
            notes.append(f"{c['text']} (raised by {c['speaker']}; outside brand/audience/PSGs - for the draft process)")

    final = {item: _majority(participants, item, _baseline(pack, item))[0] for item in items}
    dissent = []
    for p in participants:
        for item, val in p["position"].items():
            if val != final[item]:
                why = next((c["text"] for c in p["claims"] if c.get("item") == item),
                           next((c["text"] for c in p["claims"] if c["topic"] == _topic(item)), ""))
                dissent.append({"by": p["label"], "item": item, "position": describe_item(item, val, pack),
                                "reason": why})

    decision = _assemble(rt, pack, proposals, participants, final)
    decision.update({
        "rounds": rounds, "agreement": round(agr, 3), "converged": agr >= s.convergence_tolerance,
        "dissent": dissent, "notes_for_draft": notes, "transcript": transcript,
        "scope_adherence_pct": scope["in_scope_pct"], "human_overrides": [],
    })
    return decision


def _topic(item: str) -> str:
    return "brand" if item == "brand" else ("audience" if item.startswith("cohort:") else "psgs")


def _assemble(rt, pack, proposals, participants, final) -> dict:
    aud = {c["cohort"]: c for c in pack["proposals"]["audience"]["cohorts"]}
    audience = []
    for c in AUDIENCE_COHORTS:
        include = bool(final.get(f"cohort:{c}", aud[c]["include"]))
        votes = sum(1 for p in participants if p["position"].get(f"cohort:{c}") == include)
        audience.append({"cohort": c, "size": aud[c]["size"], "include": include,
                         "reason": f"{aud[c]['reason']}; panel {votes}/{len(participants)}"})

    prop = pack["proposals"]["psgs"]
    reasons = {e["psg_id"]: e["reason"] for e in prop["exclude"]}
    psg_ids = list(prop["scores"])
    excluded = [p for p in psg_ids if final.get(f"psg:{p}", p in reasons)]
    include = [p for p in psg_ids if p not in excluded]
    hero_votes = Counter()
    for p in participants:
        for h in p["hero"]:
            hero_votes[h] += 2 if p["advocate"] == "human" else 1
    hero = sorted((h for h in include if hero_votes[h]),
                  key=lambda h: (-hero_votes[h], -prop["scores"].get(h, 0)))[:rt.settings.hero_psg_count]

    brand_sel = final.get("brand", pack["brand_id"])
    requested = proposals["requested_id"]
    for_switch = [p["label"] for p in participants if p["position"].get("brand") == brand_sel]
    reason_claim = next((c["text"] for p in participants if p["position"].get("brand") == brand_sel
                         for c in p["claims"] if c["topic"] == "brand"), "")
    scout = pack["blocks"].get("10", {}).get("data", {}).get("brand_scout_score", {})
    return {
        "brand_decision": {
            "requested": requested, "selected": brand_sel,
            "alternatives_considered": [a["brand_id"] for a in scout.get("alternatives", [])
                                        if a["brand_id"] not in (brand_sel, requested)],
            "reason": f"{reason_claim} ({', '.join(for_switch)})",
        },
        "objective": pack["objective"],
        "window": pack["window"],
        "audience": audience,
        "qualifying_psgs": {"include": include,
                            "exclude": [{"psg_id": p, "reason": reasons.get(p, "excluded by the panel")}
                                        for p in excluded],
                            "hero": hero},
    }


def apply_overrides(decision: dict, edits: list[dict], pack: dict, top_alt: str | None) -> tuple[dict, list[str]]:
    """The human's final call overrides the Judge."""
    d = {**decision, "audience": [dict(a) for a in decision["audience"]],
         "qualifying_psgs": {k: list(v) for k, v in decision["qualifying_psgs"].items()},
         "brand_decision": dict(decision["brand_decision"]),
         "human_overrides": list(decision.get("human_overrides", [])),
         "notes_for_draft": list(decision.get("notes_for_draft", []))}
    notes = []
    q = d["qualifying_psgs"]
    names = {p["psg_id"]: p["name"] for p in pack["blocks"].get("5", {}).get("data", {})
             .get("psg_score", {}).get("psgs", [])}
    for e in edits:
        op = e["op"]
        if op in ("include_cohort", "exclude_cohort"):
            want = op == "include_cohort"
            for a in d["audience"]:
                if a["cohort"] == e["cohort"]:
                    if a["size"] is None and want:
                        notes.append(f"{COHORT_LABELS[a['cohort']]} is below the minimum cell size; cannot include.")
                        continue
                    a["include"] = want
                    a["reason"] += "; human override"
                    notes.append(f"{'Included' if want else 'Excluded'} {COHORT_LABELS[a['cohort']]}.")
        elif op in ("exclude_psg", "include_psg", "hero_psg"):
            pid = e["psg_id"]
            if pid not in pack["proposals"]["psgs"]["scores"]:
                notes.append(f"{pid} is not a PSG of this brand.")
                continue
            if op == "exclude_psg":
                if pid in q["include"]:
                    q["include"].remove(pid)
                q["hero"] = [h for h in q["hero"] if h != pid]
                if all(x["psg_id"] != pid for x in q["exclude"]):
                    q["exclude"].append({"psg_id": pid, "reason": "human override"})
                notes.append(f"Excluded {names.get(pid, pid)}.")
            else:
                hard = next((x for x in pack["proposals"]["psgs"]["exclude"] if x["psg_id"] == pid
                             and set(x["filters"]) & {"availability", "promo_clash"}), None)
                if hard:
                    notes.append(f"{names.get(pid, pid)} fails a hard filter ({hard['reason']}); kept excluded.")
                    continue
                q["exclude"] = [x for x in q["exclude"] if x["psg_id"] != pid]
                if pid not in q["include"]:
                    q["include"].append(pid)
                if op == "hero_psg" and pid not in q["hero"]:
                    q["hero"] = [pid] + q["hero"][:1]
                notes.append(f"{'Hero' if op == 'hero_psg' else 'Included'}: {names.get(pid, pid)}.")
        elif op == "switch_brand":
            target = e.get("brand_id") or top_alt
            if target and BRANDS[target]["category"] == BRANDS[pack["brand_id"]]["category"]:
                d["brand_decision"]["selected"] = target
                d["brand_decision"]["reason"] = f"Campaign manager's call: switch to {_name(target)}"
                notes.append(f"Brand set to {_name(target)}.")
            else:
                notes.append("Brand switch must stay within the category.")
        elif op == "keep_brand":
            target = e.get("brand_id") or pack["brand_id"]
            d["brand_decision"]["selected"] = target
            d["brand_decision"]["reason"] = f"Campaign manager's call: keep {_name(target)}"
            notes.append(f"Brand kept as {_name(target)}.")
        elif op == "add_note":
            d["notes_for_draft"].append(f"{e['text']} (campaign manager)")
            notes.append("Note added for the draft process.")
        else:
            continue
        d["human_overrides"].append(e)
    return d, notes
