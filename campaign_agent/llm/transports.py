"""Offline transports that speak the v1/lrm/ contract.

SimulatedModel stands in for the LRM when no credentials or VPN are
available. It exercises the real harness end to end: it reads the
TOOL:/ARGS: instructions and the compressed conversation from user_prompt,
calls the skill's warm-up tools in order (run state, memory...), then calls
finish with a reference answer. The harness supplies that answer from the
agent's deterministic policy, so a simulated run decides exactly what the
deterministic fallback would, but through prompts, tool calls, parsing,
schema validation and guardrails.

ScriptedTransport replays fixed replies (for tests of parsing, retries,
loop detection and bad model output).
"""

from __future__ import annotations

import json
import re
import threading


class SimulatedModel:
    def __init__(self, reference: dict, warmup: list, finish_tool: str = "finish"):
        """warmup: tool names, or (name, args) pairs to call before finishing."""
        self.reference = reference
        self.warmup = [w if isinstance(w, tuple) else (w, {}) for w in warmup]
        self.finish_tool = finish_tool
        self.payloads: list[dict] = []

    def post(self, payload: dict, timeout: int) -> dict:
        self.payloads.append(payload)
        prompt = payload["user_prompt"]
        if "AVAILABLE TOOLS" not in prompt:  # call_simple: answer directly
            return {"response": json.dumps(self.reference), "finish_reason": "stop", "usage": _usage(payload)}
        offered = set(re.findall(r"•\s*(\w+)\(", prompt))
        called = set(re.findall(r"\[Tool called\] (\w+)", prompt))
        nxt = next(((t, a) for t, a in self.warmup if t in offered and t not in called), None)
        if nxt:
            text = (f"I'll check {nxt[0].replace('_', ' ')} before deciding.\n"
                    f"TOOL: {nxt[0]}\nARGS: {json.dumps(nxt[1], default=str)}")
        else:
            text = (f"I have what I need from the run state and memory.\n"
                    f"TOOL: {self.finish_tool}\nARGS: {json.dumps(self.reference, default=str)}")
        return {"response": text, "finish_reason": "stop", "usage": _usage(payload)}


class ScriptedTransport:
    """Return queued replies in order; a reply may be a str, a dict, or an Exception to raise."""

    def __init__(self, replies: list):
        self.replies = list(replies)
        self.payloads: list[dict] = []
        self._lock = threading.Lock()

    def post(self, payload: dict, timeout: int) -> dict:
        with self._lock:
            self.payloads.append(payload)
            if not self.replies:
                raise RuntimeError("scripted transport exhausted")
            r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, dict):
            return r
        return {"response": r, "finish_reason": "stop"}


def _usage(payload: dict) -> dict:
    chars = len(payload.get("system_prompt", "")) + len(payload.get("user_prompt", ""))
    return {"prompt_tokens": chars // 4, "completion_tokens": 50}
