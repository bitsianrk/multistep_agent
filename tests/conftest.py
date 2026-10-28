from __future__ import annotations

from dataclasses import replace

import pytest

from campaign_agent.config import DEFAULT_SETTINGS
from campaign_agent.data import get_store
from campaign_agent.handoff import MemoryHandoff
from campaign_agent.memory import EpisodicMemory
from campaign_agent.session import CampaignSession
from campaign_agent.tools.registry import get_registry

CM = {"id": "cm.test", "name": "Test CM", "role": "campaign_manager_role"}


@pytest.fixture
def settings(tmp_path):
    return replace(DEFAULT_SETTINGS, output_dir=tmp_path / "runs", memory_path=tmp_path / "runs" / "memory.json")


@pytest.fixture
def registry():
    return get_registry(DEFAULT_SETTINGS, get_store(DEFAULT_SETTINGS))


@pytest.fixture
def make_session(settings):
    def make(**kw):
        kw.setdefault("memory", EpisodicMemory(None))
        kw.setdefault("handoff", MemoryHandoff())
        s = kw.pop("settings", settings)
        return CampaignSession(user=kw.pop("user", CM), settings=s, **kw)
    return make


def drive(session, messages):
    """Start with the first message, then reply with the rest while the run waits."""
    out = [session.start(messages[0])]
    for m in messages[1:]:
        assert session.awaiting, f"run stopped before {m!r}"
        out.append(session.reply(m))
    return out
