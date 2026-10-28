"""Web UI for the Campaign Planning Agent (Flask).

    python -m campaign_agent.web                     # http://127.0.0.1:8000
    python -m campaign_agent.web --db runs/state.db  # runs survive a server restart
    python -m campaign_agent.web --lrm-mode off      # deterministic agents only

The page is the chat window from the design: a message starts a run, every
stage stops at a checkpoint card, and the side panels show the plan,
Diagnostic Pack, discussion, Agreed Brief, harness log and memory. It drives
the same CampaignSession as the CLI. Internal use only: it binds to
localhost by default.
"""

from __future__ import annotations

import argparse
import json
import threading
from dataclasses import replace
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from ..config import DEFAULT_SETTINGS, Settings
from ..guardrails import AccessDenied
from ..harness import resolve_mode
from ..llm import LRMConfig
from ..memory import EpisodicMemory
from ..session import CampaignSession
from .view import run_summary, run_view

STATIC = Path(__file__).parent / "static"
MAX_MESSAGE = 2000


class RunStore:
    """Sessions by run_id. With a persistent checkpointer, unknown ids are re-opened from disk."""

    def __init__(self, settings: Settings, checkpointer=None, transport=None, handoff=None):
        self.settings = settings
        self.checkpointer = checkpointer
        self.transport = transport
        self.handoff = handoff
        self.memory = EpisodicMemory(settings.memory_path)
        self.sessions: dict[str, CampaignSession] = {}
        self.locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def new(self, user: dict) -> CampaignSession:
        s = self._make(user, None)
        with self._lock:
            self.sessions[s.run_id] = s
            self.locks[s.run_id] = threading.Lock()
        return s

    def get(self, run_id: str, user: dict) -> CampaignSession | None:
        with self._lock:
            if run_id in self.sessions:
                return self.sessions[run_id]
        if self.checkpointer is None:
            return None
        s = self._make(user, run_id)
        if not s.values:
            return None
        with self._lock:
            self.sessions[run_id] = s
            self.locks[run_id] = threading.Lock()
        return s

    def lock(self, run_id: str) -> threading.Lock:
        return self.locks[run_id]

    def _make(self, user, run_id):
        kw = {"memory": self.memory, "transport": self.transport}
        if self.checkpointer is not None:
            kw["checkpointer"] = self.checkpointer
        if self.handoff is not None:
            kw["handoff"] = self.handoff
        return CampaignSession(user=user, settings=self.settings, run_id=run_id, **kw)


def create_app(settings: Settings | None = None, checkpointer=None, transport=None, handoff=None,
               user: dict | None = None) -> Flask:
    settings = settings or DEFAULT_SETTINGS
    app = Flask(__name__, static_folder=None)
    runs = RunStore(settings, checkpointer, transport, handoff)
    app.config["RUNS"] = runs
    default_user = user or {"id": "cm.local", "name": "Campaign Manager", "role": "campaign_manager_role"}

    def current_user() -> dict:
        # Entra ID in production; locally the user id can be set with X-User-Id.
        return {**default_user, "id": request.headers.get("X-User-Id", default_user["id"])[:64]}

    def message() -> str:
        text = str((request.get_json(silent=True) or {}).get("message", "")).strip()
        return text[:MAX_MESSAGE]

    @app.errorhandler(AccessDenied)
    def denied(exc):
        return jsonify(error=str(exc)), 403

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC, name)

    @app.get("/api/config")
    def config():
        mode = "live" if transport is not None and settings.lrm_mode != "off" else \
            resolve_mode(settings.lrm_mode, LRMConfig.from_env())
        return jsonify(lrm_mode=mode, user=current_user()["id"],
                       autonomy=settings.autonomy_level, examples=[
                           "Plan a Multi-Step for Crunchwell crisps to win back lapsed shoppers, 4 weeks from 16 Nov",
                           "Set up something for Golden this Christmas",
                           "Grow Meadow Fresh yoghurt frequency, 4 weeks from 2 Mar"])

    @app.get("/api/runs")
    def list_runs():
        with runs._lock:
            items = list(runs.sessions.values())
        return jsonify([run_summary(s) for s in reversed(items)])

    @app.post("/api/runs")
    def start_run():
        text = message()
        if not text:
            return jsonify(error="message is required"), 400
        s = runs.new(current_user())
        with runs.lock(s.run_id):
            s.start(text)
        return jsonify(run_view(s)), 201

    @app.get("/api/runs/<run_id>")
    def get_run(run_id):
        s = runs.get(run_id, current_user())
        if s is None:
            return jsonify(error="unknown run"), 404
        return jsonify(run_view(s))

    @app.post("/api/runs/<run_id>/reply")
    def reply(run_id):
        s = runs.get(run_id, current_user())
        if s is None:
            return jsonify(error="unknown run"), 404
        text = message()
        if not text:
            return jsonify(error="message is required"), 400
        lock = runs.lock(run_id)
        if not lock.acquire(blocking=False):
            return jsonify(error="this run is still working on the previous message"), 409
        try:
            if not s.awaiting:
                return jsonify(error="run is not waiting at a checkpoint"), 409
            s.reply(text)
        finally:
            lock.release()
        return jsonify(run_view(s))

    @app.get("/api/runs/<run_id>/brief.json")
    def brief(run_id):
        s = runs.get(run_id, current_user())
        if s is None or not s.values.get("brief"):
            return jsonify(error="no brief yet"), 404
        return Response(json.dumps(s.values["brief"], indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=agreed_brief_{run_id}.json"})

    @app.get("/api/memory")
    def memory():
        return jsonify(runs.memory.data)

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", type=Path, help="SQLite file for persistent checkpoints")
    ap.add_argument("--lrm-mode", choices=["auto", "live", "simulated", "off"], default=DEFAULT_SETTINGS.lrm_mode)
    ap.add_argument("--autonomy", choices=["L1", "L2"], default="L1")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_SETTINGS.output_dir)
    args = ap.parse_args(argv)
    settings = replace(DEFAULT_SETTINGS, lrm_mode=args.lrm_mode, autonomy_level=args.autonomy,
                       output_dir=args.output_dir, memory_path=args.output_dir / "memory.json")
    checkpointer = None
    if args.db:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        args.db.parent.mkdir(parents=True, exist_ok=True)
        checkpointer = SqliteSaver(sqlite3.connect(str(args.db), check_same_thread=False))
    app = create_app(settings, checkpointer)
    print(f"Campaign Planning Agent UI on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
    return 0
