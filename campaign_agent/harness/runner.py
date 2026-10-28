"""The agent harness: run one skill as a ReAct loop on the LRM.

    result = harness.run("entity-resolution", rt=rt, state=state, task={"text": ...},
                         fallback=lambda: er.resolve(text), validate=check_resolution)

`validate(args, tool_outputs)` gets the finish ARGS plus every registry tool
result from this run; it returns the cleaned output or raises ValueError.

What the harness does for every run:

1. Loads the skill (SKILL.md): instructions, tool list, output contract.
2. Builds the prompt: HARNESS.md preamble + skill instructions + output
   schema as the system prompt; task, run state, memory and recent
   conversation as the first user message.
3. Exposes tools: the skill's registry tools (still filtered by the agent's
   roster allow-list, role scope, typed contracts, SLA and run budget) plus
   built-ins: get_run_state, recall_memory, remember, read_pack_block, finish.
4. Loops: call_conversation -> parse TOOL:/ARGS: -> dispatch -> observe,
   with loop detection (repeat calls are nudged), a "finish now" nudge near
   the iteration cap, and re-prompting on invalid output.
5. Checks the finish ARGS against the schema and the caller's validator
   (closed vocabularies, grounded numbers); errors go back to the model.
6. Falls back to the agent's deterministic policy when the LRM is off,
   unreachable, or never produces valid output - the run always continues.

Modes (Settings.lrm_mode / LRM_MODE): "live" calls the Tesco v1/lrm/
endpoint; "simulated" uses the offline SimulatedModel; "off" skips the LRM;
"auto" picks live when LRM credentials are set, otherwise simulated.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from ..agents.base import ROSTER, AgentRuntime
from ..guardrails import display_safe
from ..llm import HTTPTransport, LRMClient, LRMConfig, LRMStats, SimulatedModel
from .context import memory_view, recent_conversation, state_view
from .schema import validate as schema_validate
from .skills import SkillLibrary

MODES = ("auto", "live", "simulated", "off")
_TYPE = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "dict": "object",
         "list": "array", "date": "string"}
SECTION_CHARS = 6000


@dataclass
class HarnessResult:
    skill: str
    output: dict
    source: str                      # "llm" | "fallback"
    reason: str | None = None        # why the fallback was used
    iterations: int = 0
    events: list[dict] = field(default_factory=list)
    memory_writes: list[dict] = field(default_factory=list)
    tool_outputs: list[dict] = field(default_factory=list)


def resolve_mode(mode: str, config: LRMConfig) -> str:
    if mode not in MODES:
        raise ValueError(f"lrm_mode must be one of {MODES}")
    if mode == "auto":
        return "live" if config.configured else "simulated"
    return mode


def _clip(obj) -> str:
    s = json.dumps(obj, default=str, separators=(",", ":"))
    return s if len(s) <= SECTION_CHARS else s[:SECTION_CHARS] + "...(truncated; use the tools for detail)"


class AgentHarness:
    def __init__(self, registry, episodic, mode: str = "auto", library: SkillLibrary | None = None,
                 lrm_config: LRMConfig | None = None, transport=None):
        self.registry = registry
        self.episodic = episodic
        self.library = library or SkillLibrary()
        self.config = lrm_config or LRMConfig.from_env()
        self.mode = "live" if transport is not None and mode != "off" else resolve_mode(mode, self.config)
        self.stats = LRMStats()
        self._transport = transport
        if self.mode == "live" and self._transport is None:
            self._transport = HTTPTransport(self.config)

    # Tool specs ------------------------------------------------------------
    def _registry_spec(self, name: str) -> dict:
        t = self.registry.get(name)
        props, req = {}, []
        for arg, type_name in t.inputs.items():
            props[arg] = {"type": _TYPE[type_name.rstrip("?")]}
            if not type_name.endswith("?"):
                req.append(arg)
        return {"type": "function", "function": {"name": name, "description": t.description,
                                                 "parameters": {"type": "object", "properties": props,
                                                                "required": req}}}

    @staticmethod
    def _builtin_specs(has_pack: bool) -> list[dict]:
        def spec(name, desc, props=None, req=None):
            return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
                "type": "object", "properties": props or {}, "required": req or []}}}

        out = [
            spec("get_run_state", "Where the run is now: stage, brand, objective, window, plan, pack, decisions.",
                 {"section": {"type": "string"}}),
            spec("recall_memory", "Working notes from earlier in this run and past runs/overrides in this category."),
            spec("remember", "Save a short note for later stages of this run (state is kept across checkpoints).",
                 {"note": {"type": "string"}}, ["note"]),
        ]
        if has_pack:
            out.append(spec("read_pack_block", "Read one Diagnostic Pack block (1-10), optionally one tool's output.",
                            {"block": {"type": "integer"}, "tool": {"type": "string"}}, ["block"]))
        out.append(spec("finish", "Return your final output. ARGS must match the output contract."))
        return out

    # Run ---------------------------------------------------------------------
    def run(self, skill_name: str, *, rt: AgentRuntime, state: dict, task: dict,
            fallback: Callable[[], dict], validate: Callable[[dict, list[dict]], dict] | None = None) -> HarnessResult:
        skill = self.library.get(skill_name)
        started = time.perf_counter()

        def fall(reason: str, it: int, events, writes, outs) -> HarnessResult:
            out = fallback()
            events.append({"type": "fallback", "reason": reason})
            return HarnessResult(skill_name, out, "fallback", reason, it, events, writes, outs)

        if self.mode == "off":
            return fall("LRM disabled (lrm_mode=off)", 0, [], [], [])

        roster_tools = ROSTER[skill.agent].tools if skill.agent in ROSTER else frozenset()
        registry_tools = [t for t in skill.tools if t in self.registry.tools and t in roster_tools]
        has_pack = bool(state.get("pack"))
        builtin = {s["function"]["name"]: s for s in self._builtin_specs(has_pack)}
        specs = [self._registry_spec(t) for t in registry_tools] + \
                [s for n, s in builtin.items() if n in skill.tools or n == "finish"]
        offered = {s["function"]["name"] for s in specs}

        if self.mode == "simulated":
            reference = fallback()
            transport = SimulatedModel(reference, [(w, self._warmup_args(w, task, state)) for w in skill.warmup
                                                   if w in offered])
        else:
            transport = self._transport
        client = LRMClient(transport, model=self.config.model, timeout_s=self.config.timeout_s, stats=self.stats)

        system = (f"{self.library.preamble}\n\n# Skill: {skill.name}\n{skill.instructions}\n\n"
                  f"## Output contract (ARGS of finish)\n{json.dumps(skill.output_schema, indent=1)}")
        user = (f"## Task\n{_clip(task)}\n\n## Run state\n{_clip(state_view(state))}\n\n"
                f"## Memory\n{_clip(memory_view(state, self.episodic, skill.agent))}\n\n"
                f"## Recent conversation\n{_clip(recent_conversation(state))}")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

        events, writes, outs = [], [], []
        seen: dict[str, int] = {}
        bad_finish = no_tool = 0
        for it in range(1, skill.max_iterations + 1):
            if it == skill.max_iterations - 1:
                messages.append({"role": "user", "content": "You are near the step limit. Call finish now "
                                                            "with the best output you can support."})
            choice = client.call_conversation(messages, tools=specs, max_tokens=skill.max_tokens,
                                              reasoning_effort=skill.reasoning_effort)
            if choice is None:
                return fall("LRM unavailable", it, events, writes, outs)
            msg = choice["message"]
            messages.append(msg)
            if msg.get("content"):
                events.append({"type": "thought", "text": msg["content"][:300]})
            calls = msg.get("tool_calls") or []
            if not calls:
                direct = _json_in(msg.get("content") or "")
                if direct is None:
                    no_tool += 1
                    if no_tool >= 2:
                        return fall("model did not call a tool", it, events, writes, outs)
                    messages.append({"role": "user", "content": "Reply with exactly one TOOL:/ARGS: block. "
                                                                "Use finish when your output is ready."})
                    continue
                calls = [{"id": "direct", "function": {"name": "finish", "arguments": json.dumps(direct)}}]

            tc = calls[0]  # one tool per response
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except ValueError:
                args = None
            if not isinstance(args, dict):
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": '{"error":"ARGS must be a JSON object"}'})
                continue

            if name == "finish":
                errors = schema_validate(args, skill.output_schema)
                if not errors and validate:
                    try:
                        args = validate(args, outs)
                    except ValueError as exc:
                        errors = [str(exc)]
                if errors:
                    bad_finish += 1
                    events.append({"type": "invalid_output", "errors": errors[:5]})
                    if bad_finish >= 3:
                        return fall(f"invalid output: {errors[0]}", it, events, writes, outs)
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": json.dumps({"error": "output rejected", "fix": errors[:8]})})
                    continue
                events.append({"type": "finish", "ms": round((time.perf_counter() - started) * 1000, 1)})
                return HarnessResult(skill_name, args, "llm", None, it, events, writes, outs)

            key = f"{name}:{json.dumps(args, sort_keys=True)}"
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 2:
                events.append({"type": "loop_detected", "tool": name})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(
                    {"note": "You already have this result. Use it, call a different tool, or finish."})})
                continue

            result = self._dispatch(name, args, offered, registry_tools, skill, rt, state, writes, outs)
            events.append({"type": "tool", "name": name, "ok": "error" not in result})
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(display_safe(result), default=str)})
        return fall("max iterations reached", skill.max_iterations, events, writes, outs)

    def _warmup_args(self, name: str, task: dict, state: dict) -> dict:
        """Arguments the simulated model passes: the tool's inputs that the task or run state supply."""
        if name not in self.registry.tools:
            return {}
        inputs = self.registry.get(name).inputs
        pool = {"brand_id": state.get("brand_id"), **(state.get("window") and {
            "window_start": state["window"]["start"], "window_weeks": state["window"]["weeks"]} or {}),
            "objective": state.get("objective"), **task}
        return {k: pool[k] for k in inputs if pool.get(k) is not None}

    def _dispatch(self, name, args, offered, registry_tools, skill, rt, state, writes, outs) -> dict:
        if name not in offered:
            return {"error": f"tool {name!r} is not available to this skill"}
        if name == "get_run_state":
            return state_view(state, args.get("section"))
        if name == "recall_memory":
            return memory_view(state, self.episodic, skill.agent)
        if name == "remember":
            note = {"agent": skill.agent, "stage": state.get("stage"), "note": str(args.get("note", ""))[:500]}
            writes.append(note)
            return {"saved": True}
        if name == "read_pack_block":
            block = (state.get("pack") or {}).get("blocks", {}).get(str(args.get("block")))
            if not block:
                return {"error": f"no block {args.get('block')!r} in the pack"}
            data = block["data"].get(args["tool"]) if args.get("tool") else block["data"]
            return {"block": block["block"], "name": block["name"], "data": data, "provenance": block["provenance"]}
        if name in registry_tools:
            try:
                res = self.registry.call(name, args, caller=skill.agent, allow_list=set(registry_tools),
                                         role=rt.user["role"], budget=rt.budget)
            except Exception as exc:
                rt.trace.append({"agent": skill.agent, "tool": name, "ok": False, "error": str(exc), "via": "harness"})
                return {"error": str(exc)}
            rt.trace.append({"agent": skill.agent, "tool": name, "version": res["version"], "ok": True,
                             "latency_ms": res["latency_ms"], "via": "harness"})
            outs.append({"tool": name, "args": args, "output": res["output"], "provenance": res["provenance"]})
            return res["output"]
        return {"error": f"unknown tool {name!r}"}


def _json_in(text: str):
    """A model that answers with bare JSON instead of calling finish."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1]
    if not t.startswith("{"):
        return None
    try:
        v = json.loads(t)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None
