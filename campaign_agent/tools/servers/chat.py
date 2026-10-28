"""MCP domain server: chat (message routing and checkpoint cards).

Chat text is treated as data: route_message maps it onto a closed set of
actions and edit operations. Nothing in a message can name a tool, an
endpoint or a free-form instruction for an agent.
"""

from __future__ import annotations

import re

from ...data.taxonomy import BRANDS
from ._common import ToolContext
from .taxonomy import _brand_matches, parse_objective, parse_window

ACTIONS = ("approve", "ask", "edit", "send_back", "pause", "answer")
STAGE_WORDS = [
    ("understand", r"\b(understand|request|stage 1|start)\b"),
    ("plan", r"\b(plan|stage 2)\b"),
    ("diagnose", r"\b(diagnos\w*|pack|stage 3)\b"),
    ("discuss", r"\b(discuss\w*|debate|stage 4)\b"),
]
COHORT_RX = r"(lapsed|light|medium|heavy|non[\s-]?buyers?|category non[\s-]?buyers?)"
APPROVE_RX = re.compile(
    r"^\s*(ok(ay)?|yes|yep|y|approve[sd]?|go( ahead)?|lgtm|looks (good|right|fine)|"
    r"(plan |brief |that )?looks (good|right|fine)|proceed|continue|agree[sd]?|confirm(ed)?|sounds good|ship it|done)\b",
    re.I)
QUESTION_RX = re.compile(r"(\?\s*$)|^\s*(what|why|how|which|who|when|where|show|explain|is|are|can you show|tell me)\b",
                         re.I)


def _cohort_key(word: str) -> str:
    return "non_buyers" if "non" in word.lower() else word.lower()


def _psg_targets(fragment: str, brand_id: str | None, psgs: dict) -> list[str]:
    frag = fragment.lower().strip(" .,'\"")
    if not frag:
        return []
    ids = [p for p in psgs if p.lower() == frag]
    if ids:
        return ids
    pool = [p for p, spec in psgs.items() if brand_id is None or spec["brand_id"] == brand_id]
    words = [w for w in re.split(r"\s+", frag) if w not in ("the", "psg", "psgs", "range", "a", "an")]
    hits = []
    for p in pool:
        name = psgs[p]["name"].lower()
        if words and all(w in name or w.rstrip("s") in name for w in words):
            hits.append(p)
    return hits


def parse_edits(ctx: ToolContext, text: str, brand_id: str | None, known_tools: list[str]) -> list[dict]:
    t = text.lower()
    ops: list[dict] = []
    psgs = ctx.store.psgs

    for m in re.finditer(rf"\b(drop|remove|exclude|without|lose|no)\s+(the\s+)?{COHORT_RX}(\s+(cohort|shoppers|tier))?", t):
        ops.append({"op": "exclude_cohort", "cohort": _cohort_key(m.group(3))})
    for m in re.finditer(rf"\b(include|add|target|keep|bring in)\s+(the\s+)?{COHORT_RX}(\s+(cohort|shoppers|tier))?", t):
        ops.append({"op": "include_cohort", "cohort": _cohort_key(m.group(3))})

    for m in re.finditer(r"\bmake\s+(.+?)\s+(a\s+)?hero\b|\bhero(?:\s+psg)?\s*[:=]?\s+([a-z0-9\- ]+?)(?=$|[,.;]| and )", t):
        frag = m.group(1) or m.group(3)
        for p in _psg_targets(frag, brand_id, psgs):
            ops.append({"op": "hero_psg", "psg_id": p})
    for m in re.finditer(r"\b(exclude|drop|remove)\s+(?:the\s+)?(?:psg\s+)?([a-z0-9\- ]+?)(?=$|[,.;]| and | from )", t):
        if re.fullmatch(rf"(the\s+)?{COHORT_RX}(\s+(cohort|shoppers|tier))?", m.group(2).strip()):
            continue
        frag = m.group(2)
        if frag.strip() in known_tools:
            continue
        for p in _psg_targets(frag, brand_id, psgs):
            ops.append({"op": "exclude_psg", "psg_id": p})
    for m in re.finditer(r"\b(include|add back|keep)\s+(?:the\s+)?(?:psg\s+)?([a-z0-9\- ]+?)(?=$|[,.;]| and )", t):
        if re.fullmatch(rf"(the\s+)?{COHORT_RX}(\s+(cohort|shoppers|tier))?", m.group(2).strip()):
            continue
        for p in _psg_targets(m.group(2), brand_id, psgs):
            ops.append({"op": "include_psg", "psg_id": p})

    for m in re.finditer(r"\b(add|include|run)\s+([a-z_]+)\b", t):
        if m.group(2) in known_tools:
            ops.append({"op": "add_tool", "tool": m.group(2)})
    for m in re.finditer(r"\b(remove|drop|skip)\s+([a-z_]+)\b", t):
        if m.group(2) in known_tools:
            ops.append({"op": "remove_tool", "tool": m.group(2)})

    m = re.search(r"\b(switch to|use|go with|try)\s+(.+?)(\s+instead)?(?=$|[,.;])", t)
    if m:
        hits = _brand_matches(m.group(2))
        if len(hits) == 1:
            ops.append({"op": "switch_brand", "brand_id": hits[0]["brand_id"]})
        elif "competitor" in m.group(2) or "alternative" in m.group(2):
            ops.append({"op": "switch_brand", "brand_id": None, "hint": "top_alternative"})
    if re.search(r"\bkeep (the )?(requested |current |original )?brand\b|\bstay with\b", t):
        ops.append({"op": "keep_brand"})
    else:
        m = re.search(r"\bkeep\s+([a-z ]+?)(?=$|[,.;])", t)
        if m:
            hits = _brand_matches(m.group(1))
            if len(hits) == 1:
                ops.append({"op": "keep_brand", "brand_id": hits[0]["brand_id"]})

    if re.search(r"\bobjective\b", t) and parse_objective(t):
        ops.append({"op": "set_objective", "objective": parse_objective(t)})
    if re.search(r"\b(window|start(ing)?|from|for)\b.*(\d{4}-\d\d-\d\d|\d+\s*(weeks?|wks?)|\d{1,2}\s+[a-z]{3})", t):
        w = parse_window(t, ctx.settings.as_of, ctx.settings.default_window_weeks, ctx.settings.default_lead_days)
        ops.append({"op": "set_window", "window": w})

    m = re.search(r"\bnote\s*[:\-]\s*(.+)$", text, re.I)
    if m:
        ops.append({"op": "add_note", "text": m.group(1).strip()[:500]})

    seen, unique = set(), []
    for op in ops:
        key = tuple(sorted((k, str(v)) for k, v in op.items()))
        if key not in seen:
            seen.add(key)
            unique.append(op)
    return unique


def route_message(ctx: ToolContext, text: str, stage: str, brand_id: str | None = None,
                  awaiting_clarification: bool = False, known_tools: list[str] | None = None) -> dict:
    raw = (text or "").strip()
    t = raw.lower()
    known_tools = known_tools or []
    out = {"stage": stage, "action": "ask", "target_stage": None, "edits": [], "text": raw[:2000]}

    if re.match(r"^\s*(pause|hold( on)?|stop for now|later|park (it|this))\b", t):
        out["action"] = "pause"
        return out

    edits = parse_edits(ctx, raw, brand_id, known_tools)
    send_back = re.search(r"\b(send (it )?back|go back|back to|start over|restart|re-?run)\b", t) or \
        (re.search(r"\binstead\b", t) and any(e["op"] == "switch_brand" for e in edits))
    if send_back:
        target = next((s for s, rx in STAGE_WORDS if re.search(rx, t)), None)
        if any(e["op"] == "switch_brand" for e in edits):
            target = target or "plan"
        out.update(action="send_back", target_stage=target or _previous(stage), edits=edits)
        return out

    if awaiting_clarification:
        out["action"] = "answer"
        out["edits"] = edits
        return out
    if edits:
        out.update(action="edit", edits=edits)
        return out
    if APPROVE_RX.match(t) and not QUESTION_RX.search(t):
        out["action"] = "approve"
        return out
    out["action"] = "ask"
    return out


def _previous(stage: str) -> str:
    order = ["understand", "plan", "diagnose", "discuss", "brief"]
    i = order.index(stage.split(":")[0]) if stage.split(":")[0] in order else 1
    return order[max(0, i - 1)]


def render_checkpoint(ctx: ToolContext, stage: str, title: str, summary: list[str],
                      key_numbers: list[list[str]] | None = None, actions: list[str] | None = None,
                      compact: bool = False) -> dict:
    actions = actions or ["Approve", "Ask", "Edit", "Send back", "Pause"]
    lines = [f"[{stage.upper()}] {title}"]
    if not compact:
        lines += [f"  {s}" for s in summary]
        if key_numbers:
            width = max(len(k) for k, _ in key_numbers)
            lines.append("  Key numbers:")
            lines += [f"    {k.ljust(width)}  {v}" for k, v in key_numbers]
    lines.append("  -> " + " / ".join(actions))
    return {"stage": stage, "title": title, "summary": summary, "key_numbers": key_numbers or [],
            "actions": actions, "text": "\n".join(lines)}


def brand_name(brand_id: str | None) -> str:
    return BRANDS[brand_id]["name"] if brand_id in BRANDS else str(brand_id)
