"""Tesco Analytics LRM (Large Reasoning Model) client.

Same API setup as the PR coupon design agent (tesco_e2e_personalisation_agent/lrm_client.py):

  Endpoint  POST {LRM_BASE_URL}/{LRM_EMPLOYEE_ID}/v1/lrm/
  Header    X-API-Key: {LRM_API_KEY}
  Request   {"system_prompt", "user_prompt", "model", "reasoning_effort", "max_tokens"}
  Response  {"response": "...", "finish_reason": "stop", "usage": {...}}

v1/lrm/ is single-turn and has no native function calling, so
call_conversation() emulates a multi-turn ReAct loop: it compresses the
conversation into one user_prompt, injects TOOL:/ARGS: instructions and
parses TOOL:/ARGS: blocks out of the reply into OpenAI-style tool_calls.

Credentials are read from the environment only (never commit them):
  LRM_EMPLOYEE_ID, LRM_API_KEY, LRM_MODEL (default o4-mini), LRM_BASE_URL,
  LRM_TIMEOUT_S, LRM_RETRIES.

Differences from the coupon agent's client: the transport is pluggable (HTTP
for the real endpoint, a simulated model offline, scripted replies in tests),
the finish tool name is a parameter instead of a hard-coded
finalise_coupon_design, and every call is counted for observability.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("LRM-CLIENT")

DEFAULT_BASE_URL = "https://www.ourtesco.com/analytics/eagarage-toolkit/api/azure-openai"
MAX_HISTORY = 10          # first user message + last 10 turns, as in the coupon agent
TOOL_RESULT_CHARS = 3000  # tool results are truncated in the compressed prompt


class LRMError(RuntimeError):
    """The endpoint could not be reached or returned an error."""


@dataclass
class LRMConfig:
    employee_id: str | None = None
    api_key: str | None = None
    model: str = "o4-mini"
    base_url: str = DEFAULT_BASE_URL
    timeout_s: int = 240
    retries: int = 3

    @classmethod
    def from_env(cls) -> "LRMConfig":
        return cls(
            employee_id=os.getenv("LRM_EMPLOYEE_ID") or None,
            api_key=os.getenv("LRM_API_KEY") or None,
            model=os.getenv("LRM_MODEL", "o4-mini"),
            base_url=os.getenv("LRM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            timeout_s=int(os.getenv("LRM_TIMEOUT_S", "240")),
            retries=int(os.getenv("LRM_RETRIES", "3")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.employee_id and self.api_key)

    @property
    def url(self) -> str:
        return f"{self.base_url}/{self.employee_id}/v1/lrm/"


class Transport(Protocol):
    def post(self, payload: dict, timeout: int) -> dict: ...


class HTTPTransport:
    """POST to v1/lrm/ with exponential backoff on 5xx / timeout / connection errors."""

    def __init__(self, config: LRMConfig, sleep=time.sleep):
        if not config.configured:
            raise LRMError("LRM_EMPLOYEE_ID and LRM_API_KEY must be set for live mode")
        self.config = config
        self._sleep = sleep

    def post(self, payload: dict, timeout: int) -> dict:
        import requests

        last_exc: Exception | None = None
        resp = None
        for attempt in range(self.config.retries):
            try:
                resp = requests.post(self.config.url, headers={"X-API-Key": self.config.api_key},
                                     json=payload, timeout=timeout)
                if resp.status_code < 500:
                    break  # success, or a 4xx we must not retry (auth / bad request)
                log.warning("LRM %s on attempt %d/%d", resp.status_code, attempt + 1, self.config.retries)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                log.warning("LRM timeout/connection error on attempt %d/%d", attempt + 1, self.config.retries)
            if attempt < self.config.retries - 1:
                self._sleep(2 ** attempt)
        if resp is None:
            raise LRMError(f"LRM unreachable: {last_exc}")
        if not resp.ok:
            raise LRMError(f"LRM {resp.status_code}: {resp.text[:300]}")
        return resp.json()


# ── text-based tool calling ────────────────────────────────────────────────────

def extract_json_object(text: str, start: int) -> tuple[str | None, int]:
    """Bracket-match a JSON object starting at text[start] == '{'."""
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if escaped:
            escaped = False
        elif c == "\\" and in_str:
            escaped = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1], i + 1
    return None, start


def parse_tool_calls(text: str) -> list[dict]:
    """Find `TOOL: name` / `ARGS: {...}` blocks; invalid JSON blocks are skipped."""
    calls, pos = [], 0
    tool_re = re.compile(r"TOOL\s*:\s*(\w+)", re.I)
    args_re = re.compile(r"ARGS\s*:\s*(\{)", re.I)
    while pos < len(text):
        tm = tool_re.search(text, pos)
        if not tm:
            break
        am = args_re.search(text, tm.end(), tm.end() + 300)
        if not am:
            pos = tm.end()
            continue
        args_str, end = extract_json_object(text, am.start(1))
        if args_str is None:
            pos = tm.end()
            continue
        try:
            json.loads(args_str)
        except ValueError:
            pos = tm.end()
            continue
        calls.append({"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                      "function": {"name": tm.group(1), "arguments": args_str}})
        pos = end
    return calls


def strip_tool_blocks(text: str) -> str:
    """Remove TOOL:/ARGS: blocks (with properly matched JSON) and keep the reasoning."""
    out, pos = [], 0
    for m in re.finditer(r"TOOL\s*:\s*\w+\s*ARGS\s*:\s*(?=\{)", text, re.I):
        if m.start() < pos:
            continue
        _, end = extract_json_object(text, m.end())
        out.append(text[pos:m.start()])
        pos = end if end > m.end() else m.end()
    out.append(text[pos:])
    return "".join(out).strip()


def build_tool_instructions(tools: list[dict], finish_tool: str = "finish") -> str:
    lines = ["AVAILABLE TOOLS (call one at a time):"]
    for t in tools:
        fn = t.get("function", {})
        props = fn.get("parameters", {}).get("properties", {})
        req = fn.get("parameters", {}).get("required", [])
        params = ", ".join(f"{k}{'*' if k in req else ''}" for k in props)
        lines.append(f"  • {fn.get('name', '?')}({params})  {fn.get('description', '')[:90]}")
    lines += [
        "",
        "When you want to call a tool output EXACTLY this (reasoning is fine BEFORE it):",
        "TOOL: <tool_name>",
        'ARGS: {"param": value}',
        "",
        "Rules:",
        "  - ONE tool per response.",
        "  - Wait for the tool result before calling the next tool.",
        f"  - When your output is ready, call {finish_tool} with it as ARGS.",
        "  - NEVER output TOOL:/ARGS: more than once per response.",
    ]
    return "\n".join(lines)


def extract_text(resp_json: dict) -> str | None:
    if "response" in resp_json:
        return resp_json["response"]
    if "choices" in resp_json:
        return resp_json["choices"][0]["message"].get("content")
    return resp_json.get("content") or resp_json.get("text")


def compress_conversation(messages: list[dict]) -> tuple[str, str]:
    """Split the system message out and flatten the rest into one prompt text."""
    system, rest = "", []
    for msg in messages:
        if msg.get("role") == "system" and not system:
            system = msg.get("content") or ""
        else:
            rest.append(msg)
    if len(rest) > MAX_HISTORY + 1:
        rest = rest[:1] + rest[-MAX_HISTORY:]
    parts = []
    for msg in rest:
        role, content = msg.get("role", "user"), msg.get("content") or ""
        if role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            if content:
                parts.append(f"Assistant reasoning: {content}")
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                parts.append(f"[Tool called] {fn.get('name', '?')}\n[Args] {fn.get('arguments', '{}')}")
        elif role == "tool":
            parts.append(f"[Tool result]\n{content[:TOOL_RESULT_CHARS]}")
    return system, "\n\n".join(parts) if parts else "(no prior conversation)"


# ── client ─────────────────────────────────────────────────────────────────────

@dataclass
class LRMStats:
    calls: int = 0
    failures: int = 0
    latency_ms: float = 0.0
    usage: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, ok: bool, ms: float, usage: dict | None) -> None:
        with self._lock:
            self.calls += 1
            self.failures += 0 if ok else 1
            self.latency_ms += ms
            for k, v in (usage or {}).items():
                if isinstance(v, (int, float)):
                    self.usage[k] = self.usage.get(k, 0) + v


class LRMClient:
    def __init__(self, transport: Transport, model: str = "o4-mini", timeout_s: int = 240,
                 stats: LRMStats | None = None):
        self.transport = transport
        self.model = model
        self.timeout_s = timeout_s
        self.stats = stats or LRMStats()

    def _post(self, payload: dict) -> dict | None:
        started = time.perf_counter()
        try:
            out = self.transport.post(payload, self.timeout_s)
        except Exception as exc:  # network / API failure -> caller falls back
            log.warning("LRM call failed: %s", exc)
            self.stats.record(False, (time.perf_counter() - started) * 1000, None)
            return None
        self.stats.record(True, (time.perf_counter() - started) * 1000, out.get("usage"))
        return out

    def call_simple(self, system_prompt: str, user_prompt: str, max_tokens: int = 1000,
                    reasoning_effort: str = "medium") -> str | None:
        out = self._post({"system_prompt": system_prompt, "user_prompt": user_prompt, "model": self.model,
                          "reasoning_effort": reasoning_effort, "max_tokens": max_tokens})
        return extract_text(out) if out is not None else None

    def call_conversation(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 4000,
                          reasoning_effort: str = "high", finish_tool: str = "finish") -> dict | None:
        system, conversation = compress_conversation(messages)
        tool_block = build_tool_instructions(tools, finish_tool) + "\n\n" if tools else ""
        user_prompt = (f"{tool_block}--- Conversation so far ---\n{conversation}\n---\n"
                       "Continue the task. Call the next tool, or finish if done.")
        out = self._post({"system_prompt": system, "user_prompt": user_prompt, "model": self.model,
                          "reasoning_effort": reasoning_effort, "max_tokens": max_tokens})
        if out is None:
            return None
        text = extract_text(out) or ""
        calls = parse_tool_calls(text)
        if calls:
            return {"message": {"role": "assistant", "content": strip_tool_blocks(text), "tool_calls": calls},
                    "finish_reason": "tool_calls"}
        return {"message": {"role": "assistant", "content": text, "tool_calls": []}, "finish_reason": "stop"}


def to_payload_json(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))
