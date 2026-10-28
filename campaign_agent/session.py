"""CampaignSession: the chat window's view of one run.

    s = CampaignSession(user={"id": "cm.jane", "name": "Jane", "role": "campaign_manager_role"})
    out = s.start("Plan a Multi-Step for Crunchwell to win back lapsed shoppers, 4 weeks from 16 Nov")
    out = s.reply("yes")

Each call returns the new chat messages plus the checkpoint card the run is
waiting on. State is checkpointed by LangGraph, so a session can be rebuilt
with the same run_id (and a persistent checkpointer) and resumed later.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .config import DEFAULT_SETTINGS, Settings
from .graph import build_graph
from .guardrails import authorize
from .handoff import HandoffSink
from .memory import EpisodicMemory

DEFAULT_USER = {"id": "cm.demo", "name": "Demo Campaign Manager", "role": "campaign_manager_role"}


class CampaignSession:
    def __init__(self, user: dict | None = None, settings: Settings | None = None, checkpointer=None,
                 run_id: str | None = None, memory: EpisodicMemory | None = None,
                 handoff: HandoffSink | None = None, **overrides):
        self.user = user or DEFAULT_USER
        authorize(self.user)
        self.settings = replace(settings or DEFAULT_SETTINGS, **overrides) if overrides else (settings or DEFAULT_SETTINGS)
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = build_graph(self.settings, self.checkpointer, memory=memory, handoff=handoff)
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self.config = {"configurable": {"thread_id": self.run_id}, "recursion_limit": 200}
        self._seen = len(self.values.get("chat", []) or [])

    # Driving the run ----------------------------------------------------
    def start(self, message: str) -> list[dict]:
        """The chat message is the trigger for the run."""
        self.graph.invoke({"run_id": self.run_id, "user": self.user, "request": message,
                           "chat": [{"role": "human", "text": message}], "audit": [], "trace": [],
                           "send_backs": 0, "brand_switches": 0, "skill_calls": 0, "pack_seq": 0},
                          self.config)
        return self._new_output()

    def reply(self, message: str) -> list[dict]:
        if not self.awaiting:
            raise RuntimeError("run is not waiting at a checkpoint")
        self.graph.invoke(Command(resume=message), self.config)
        return self._new_output()

    # Introspection --------------------------------------------------------
    @property
    def snapshot(self):
        return self.graph.get_state(self.config)

    @property
    def values(self) -> dict:
        return self.snapshot.values or {}

    @property
    def pending(self) -> dict | None:
        for task in self.snapshot.tasks:
            for intr in task.interrupts:
                return intr.value
        return None

    @property
    def awaiting(self) -> bool:
        return self.pending is not None

    @property
    def done(self) -> bool:
        return self.values.get("status") == "done"

    def _new_output(self) -> list[dict]:
        chat = self.values.get("chat", []) or []
        out = [m for m in chat[self._seen:] if m["role"] != "human"]
        self._seen = len(chat)
        p = self.pending
        if p:
            out.append({"role": "checkpoint", "stage": p["stage"], "awaiting": p["awaiting"],
                        "text": p["card"]["text"], "card": p["card"]})
        return out


def format_output(items: list[dict]) -> str:
    lines = []
    for m in items:
        if m["role"] == "checkpoint":
            lines.append("")
            lines.append(m["text"])
        else:
            text = m["text"].replace("\n", "\n    ")
            lines.append(f"  {m['agent']}: {text}")
    return "\n".join(lines)
