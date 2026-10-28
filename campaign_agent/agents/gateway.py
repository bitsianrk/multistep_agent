"""Chat Gateway (routing, cards, answering questions) and the Brief Writer."""

from __future__ import annotations

import re

from ..data.taxonomy import BRANDS, COHORT_LABELS
from ..guardrails import display_safe
from .base import Agent
from .diagnose import BrandDiagnosticsAgent

COHORT_WORDS = {"lapsed": "lapsed", "light": "light", "medium": "medium", "heavy": "heavy"}


def gbp(x) -> str:
    return "n/a" if x is None else f"GBP {x:,.2f}"


class ChatGateway(Agent):
    key = "chat_gateway"

    def route(self, text: str, stage: str, brand_id: str | None, awaiting_clarification: bool,
              known_tools: list[str]) -> dict:
        return self.out("route_message", text=text, stage=stage, brand_id=brand_id,
                        awaiting_clarification=awaiting_clarification, known_tools=known_tools)

    def card(self, stage: str, title: str, summary: list[str], key_numbers=None, actions=None,
             compact: bool = False) -> dict:
        return self.out("render_checkpoint", stage=stage, title=title, summary=summary,
                        key_numbers=key_numbers, actions=actions, compact=compact)

    # Questions --------------------------------------------------------------
    def answer(self, question: str, state: dict) -> tuple[str, str, dict | None]:
        """Answer from the Diagnostic Pack, or call one more tool for a new cut.

        Returns (agent name, answer text, extra cut or None). Numbers only ever
        come from pack blocks or a fresh tool call, never from the model.
        """
        q = question.lower()
        pack = state.get("pack")
        cohort = next((v for k, v in COHORT_WORDS.items() if re.search(rf"\b{k}\b", q)), None)

        if not pack:
            plan = state.get("plan")
            if plan:
                for s in plan["steps"]:
                    if s["tool"] in q:
                        return "Campaign Planning Agent", f"{s['tool']} (block {s['block']}): {s['reason']}.", None
                return ("Campaign Planning Agent",
                        f"The plan has {len(plan['steps'])} tool calls across "
                        f"{len({s['agent'] for s in plan['steps']})} agents; numbers arrive at Diagnose. "
                        "Ask about a tool by name to see why it is in the plan.", None)
            return ("Entity Resolution Agent",
                    "Nothing is diagnosed yet - confirm the brand, objective and window first.", None)

        blocks = pack["blocks"]
        d = lambda b, tool: blocks.get(b, {}).get("data", {}).get(tool, {})  # noqa: E731
        prov = lambda b: ", ".join(f"{p['source']} as of {p['as_of']}" for p in blocks.get(b, {}).get("provenance", []))  # noqa: E731

        if "spc" in q or "spend per customer" in q:
            if cohort:
                rt_agent = BrandDiagnosticsAgent(self.rt)
                res = rt_agent.call("brand_spc", brand_id=pack["brand_id"], cohort=cohort)
                o = res["output"]
                cut = {"tool": "brand_spc", "params": {"cohort": cohort}, "output": o,
                       "provenance": res["provenance"]}
                if o.get("suppressed"):
                    return rt_agent.name, f"The {cohort} cohort is below the minimum cell size; SPC is suppressed.", cut
                if cohort == "lapsed":
                    return rt_agent.name, (f"{o['note']} Lapsed pool {o['shoppers']:,} hh: prior-6-week SPC "
                                           f"{gbp(o['spc_prior_6w'])}, {o['frequency_prior_6w']} trips each. "
                                           f"[new cut via brand_spc; {prov('2')}]"), cut
                return rt_agent.name, (f"SPC for {cohort} shoppers: {gbp(o['spc'])} ({o['shoppers']:,} hh, "
                                       f"{o['frequency']} trips x {gbp(o['spend_per_trip'])} per trip). "
                                       f"[new cut via brand_spc; {prov('2')}]"), cut
            o = d("2", "brand_spc")
            return "Brand Diagnostics Agent", (f"SPC is {gbp(o.get('spc'))} = {o.get('frequency')} trips x "
                                               f"{gbp(o.get('spend_per_trip'))}; avg basket of brand trips "
                                               f"{gbp(o.get('avg_basket_value'))}. [block 2; {prov('2')}]"), None
        if re.search(r"lapsed", q) and "lapsed_recency" not in q:
            o = d("1", "brand_shoppers")
            rec = d("1", "brand_shoppers[cut=lapsed_recency]").get("lapsed_by_recency")
            extra = f" By recency: {rec}." if rec else ""
            return "Brand Diagnostics Agent", f"Lapsed pool: {o.get('lapsed_pool'):,} households.{extra} [block 1; {prov('1')}]", None
        if re.search(r"\b(shoppers?|penetration|headroom)\b", q) and not re.search(r"cohort|split", q):
            o = d("1", "brand_shoppers")
            return "Brand Diagnostics Agent", (
                f"{o['shoppers_6w']:,} brand shoppers in 6 wks ({o['change_vs_prior_pct']:+}% vs prior, "
                f"{o['yoy_pct']:+}% YoY); penetration {o['penetration_pct']}% of {o['category_shoppers_6w']:,} "
                f"category shoppers; headroom {o['headroom_households']:,} hh. [block 1; {prov('1')}]"), None
        if re.search(r"cohort|split|tier|lifestage|segment", q):
            o = d("3", "cohort_split")
            rows = "; ".join(f"{COHORT_LABELS.get(c['cohort'], c['cohort'])} {c['shoppers_pct']}% shoppers / "
                             f"{c['sales_pct']}% sales" for c in o.get("loyalty_tier", []) if not c["suppressed"])
            return "Brand Diagnostics Agent", f"Loyalty tiers: {rows}. [block 3; {prov('3')}]", None
        if re.search(r"percentile|distribution|median|interval", q):
            o = d("4", "spend_distribution").get("by_cohort", {})
            rows = "; ".join(f"{k}: p25 {gbp(v.get('spend_p25'))}, p50 {gbp(v.get('spend_p50'))}, "
                             f"p90 {gbp(v.get('spend_p90'))}" for k, v in o.items() if not v.get("suppressed"))
            return "Brand Diagnostics Agent", f"Spend distribution (6 wks): {rows}. [block 4; {prov('4')}]", None
        if re.search(r"season", q):
            o = d("6", "seasonality_index")
            return "Performance Memory Agent", (f"Seasonality index for the window: {o.get('seasonality_index')} "
                                                f"(1.0 = average week). [block 6; {prov('6')}]"), None
        if re.search(r"kantar|share|sentiment|market", q):
            k = display_safe(d("7", "kantar_share"))
            s = d("7", "sentiment_scan")
            return "Market Intelligence Agent", (
                f"Kantar value share {k.get('value_share')}% ({k.get('value_share_trend_pts'):+} pts, "
                f"{k.get('share_trend_vs_category_pts'):+} vs category) for {k.get('period')} "
                f"[licence: {k.get('licence_scope')}]; net sentiment {s.get('net_sentiment_30d')} "
                f"({s.get('sentiment_trend'):+} vs prior 30 days). [block 7; {prov('7')}]"), None
        if re.search(r"margin|funding|profit", q):
            o = d("8", "margin_after_funding")
            low = [p["name"] for p in o.get("psgs", []) if p["below_floor"]]
            return "Commercial Agent", (f"Brand margin after funding {o.get('brand_margin_after_funding_pct')}% "
                                        f"(floor {o.get('margin_floor_pct')}%). Below floor: "
                                        f"{', '.join(low) or 'none'}. [block 8; {prov('8')}]"), None
        if re.search(r"\b(history|past (multi-?steps?|campaigns?|response)|response|previous campaigns?)\b", q):
            o = d("9", "multistep_history").get("by_cohort", {})
            rows = "; ".join(f"{COHORT_LABELS[c]} {v['participation_pct']}% part., {gbp(v['incremental_spend_per_hh'])} inc/hh"
                             for c, v in o.items())
            return "Performance Memory Agent", f"Past Multi-Step response: {rows}. [block 9; {prov('9')}]", None
        if re.search(r"alternative|scout|switch|compare|competitor|other brand", q):
            o = d("10", "brand_scout_score")
            req = o.get("requested", {})
            rows = "; ".join(f"{a['name']} score {a['score']:+.2f}{' (beats requested)' if a['beats_requested'] else ''}"
                             for a in o.get("alternatives", []))
            return "Brand Scout Agent", (f"{req.get('name')} scores {req.get('score', 0):+.2f}. Alternatives: {rows}. "
                                         f"[block 10; {prov('10')}]"), None
        if re.search(r"psg|hero|stock|promo|exclude|basket", q):
            p = pack["proposals"]["psgs"]
            names = {x["psg_id"]: x["name"] for x in d("5", "psg_score").get("psgs", [])}
            ex = "; ".join(f"{names.get(e['psg_id'], e['psg_id'])}: {e['reason']}" for e in p["exclude"])
            return "Qualifying PSG Agent", (f"{len(p['include'])} PSGs qualify; heroes "
                                            f"{', '.join(names[h] for h in p['hero'])} ({p['hero_basis']}). "
                                            f"Excluded: {ex or 'none'}. [block 5]"), None
        if re.search(r"audience|cohorts?|who", q):
            a = pack["proposals"]["audience"]
            rows = "; ".join(f"{c['label']} {c['size'] if c['size'] is not None else 'suppressed'} hh "
                             f"{'IN' if c['include'] else 'out'}" for c in a["cohorts"])
            return "Audience Agent", f"Audience proposal: {rows}.", None
        if re.search(r"dissent|disagree", q) and state.get("decision"):
            dis = state["decision"].get("dissent", [])
            return "Critic / Judge", ("Dissent: " + "; ".join(f"{x['by']} - {x['position']}" for x in dis)) if dis \
                else "No dissent recorded.", None
        if re.search(r"source|provenance|as of|fresh", q):
            return "Critic / Judge", "Provenance: " + " | ".join(
                f"block {k}: {prov(k)}" for k in sorted(blocks, key=int)), None
        return ("Chat Gateway",
                "I can answer from the Diagnostic Pack about brand shoppers, SPC (optionally by cohort), cohort split, "
                "spend distribution, seasonality, Kantar share and sentiment, margin, past Multi-Step response, "
                "Brand Scout alternatives, PSGs and the audience. Anything else is noted for the draft process.", None)


class BriefWriter(Agent):
    key = "brief_writer"

    def write(self, brand_id: str, decision: dict, pack: dict, approved_by: str | None = None) -> dict:
        return self.out("write_brief", brand_id=brand_id, decision=decision, pack=pack, approved_by=approved_by)


def brand_name(bid: str | None) -> str:
    return BRANDS[bid]["name"] if bid in BRANDS else "?"
