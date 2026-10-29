"""Web UI for the Campaign Planning Agent: Flask API + React front end.

    python -m campaign_agent.web                     # http://127.0.0.1:8000
    python -m campaign_agent.web --db runs/state.db  # runs survive a server restart
    python -m campaign_agent.web --lrm-mode off      # deterministic agents only

Agent work runs in a background thread per run: POST returns 202 at once and
the browser follows progress on /api/runs/<id>/events (Server-Sent Events),
so a stage that takes minutes on the live LRM never holds a request open.
The React app (campaign_agent/web/frontend) is pre-built into web/dist, so
running the UI needs Python only; Node is needed only to change the UI.

Internal use only: binds to localhost by default.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from ..config import DEFAULT_SETTINGS, Settings
from ..guardrails import AccessDenied, display_safe
from ..harness import resolve_mode
from ..llm import LRMConfig
from ..memory import EpisodicMemory
from ..session import CampaignSession
from .view import run_summary, run_view

DIST = Path(__file__).parent / "dist"
MAX_MESSAGE = 2000
EVENT_HISTORY = 2000
EXAMPLES = [
    "Plan a Multi-Step for Crunchwell crisps to win back lapsed shoppers, 4 weeks from 16 Nov",
    "Set up something for Golden this Christmas",
    "Grow Meadow Fresh yoghurt frequency, 4 weeks from 2 Mar",
]


class EventLog:
    """Append-only, numbered events for one run; readers block until something new arrives."""

    def __init__(self):
        self.events: list[dict] = []
        self.seq = 0
        self.cond = threading.Condition()

    def publish(self, event: dict) -> None:
        with self.cond:
            self.seq += 1
            self.events.append({"seq": self.seq, "ts": time.strftime("%H:%M:%S"), **display_safe(event)})
            del self.events[:-EVENT_HISTORY]
            self.cond.notify_all()

    def since(self, after: int, timeout: float) -> list[dict]:
        with self.cond:
            if self.seq <= after:
                self.cond.wait(timeout)
            return [e for e in self.events if e["seq"] > after]


class Run:
    def __init__(self, session: CampaignSession):
        self.session = session
        self.events = EventLog()
        self.busy = False
        self.error: str | None = None
        self.lock = threading.Lock()
        session.harness.listener = self.events.publish


class RunStore:
    """Runs by id. With a persistent checkpointer, unknown ids are re-opened from disk."""

    def __init__(self, settings: Settings, checkpointer=None, transport=None, handoff=None):
        self.settings = settings
        self.checkpointer = checkpointer
        self.transport = transport
        self.handoff = handoff
        self.memory = EpisodicMemory(settings.memory_path)
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _session(self, user, run_id=None) -> CampaignSession:
        kw = {"memory": self.memory, "transport": self.transport}
        if self.checkpointer is not None:
            kw["checkpointer"] = self.checkpointer
        if self.handoff is not None:
            kw["handoff"] = self.handoff
        return CampaignSession(user=user, settings=self.settings, run_id=run_id, **kw)

    def new(self, user: dict) -> Run:
        run = Run(self._session(user))
        with self._lock:
            self.runs[run.session.run_id] = run
        return run

    def get(self, run_id: str, user: dict) -> Run | None:
        with self._lock:
            if run_id in self.runs:
                return self.runs[run_id]
        if self.checkpointer is None:
            return None
        s = self._session(user, run_id)
        if not s.values:
            return None
        run = Run(s)
        with self._lock:
            self.runs[run_id] = run
        return run

    def all(self) -> list[Run]:
        with self._lock:
            return list(self.runs.values())

    def submit(self, run: Run, fn, wait: bool) -> bool:
        """Run fn(session) in the background (or inline when wait=True). False if the run is busy."""
        if not run.lock.acquire(blocking=False):
            return False
        run.busy, run.error = True, None
        run.events.publish({"type": "busy"})

        def work():
            try:
                fn(run.session)
            except Exception as exc:  # surface the error in the UI; the run stays at its checkpoint
                run.error = f"{type(exc).__name__}: {exc}"
                run.events.publish({"type": "error", "text": run.error})
            finally:
                run.busy = False
                run.lock.release()
                run.events.publish({"type": "idle", "stage": (run.session.pending or {}).get("stage"),
                                    "done": run.session.done})

        if wait:
            work()
        else:
            threading.Thread(target=work, daemon=True, name=f"run-{run.session.run_id}").start()
        return True


def _view(run: Run) -> dict:
    v = run_view(run.session)
    v.update(busy=run.busy, error=run.error, last_event=run.events.seq)
    return v


def create_app(settings: Settings | None = None, checkpointer=None, transport=None, handoff=None,
               user: dict | None = None) -> Flask:
    settings = settings or DEFAULT_SETTINGS
    app = Flask(__name__, static_folder=None)
    store = RunStore(settings, checkpointer, transport, handoff)
    app.config["RUNS"] = store
    default_user = user or {"id": "cm.local", "name": "Campaign Manager", "role": "campaign_manager_role"}

    def current_user() -> dict:
        # Entra ID in production; locally the user id can be set with X-User-Id.
        uid = "".join(c for c in request.headers.get("X-User-Id", default_user["id"]) if c.isalnum() or c in "._@-")
        return {**default_user, "id": uid[:64] or default_user["id"]}

    def message() -> str:
        body = request.get_json(silent=True)
        return str((body if isinstance(body, dict) else {}).get("message", "")).strip()[:MAX_MESSAGE]

    def wait() -> bool:
        return request.args.get("wait") in ("1", "true")

    @app.errorhandler(AccessDenied)
    def denied(exc):
        return jsonify(error=str(exc)), 403

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # ── Front end ────────────────────────────────────────────────────────
    @app.get("/")
    def index():
        if not (DIST / "index.html").exists():
            return ("The UI is not built. Run:  cd campaign_agent/web/frontend && npm install && npm run build",
                    503, {"Content-Type": "text/plain"})
        return send_from_directory(DIST, "index.html", max_age=0)

    @app.get("/assets/<path:name>")
    def assets(name):
        return send_from_directory(DIST / "assets", name, max_age=86400)

    # ── API ─────────────────────────────────────────────────────────────
    @app.get("/api/config")
    def config():
        mode = "live" if transport is not None and settings.lrm_mode != "off" else \
            resolve_mode(settings.lrm_mode, LRMConfig.from_env())
        return jsonify(lrm_mode=mode, user=current_user()["id"], autonomy=settings.autonomy_level,
                       examples=EXAMPLES)

    @app.get("/api/runs")
    def list_runs():
        return jsonify([{**run_summary(r.session), "busy": r.busy} for r in reversed(store.all())])

    @app.post("/api/runs")
    def start_run():
        text = message()
        if not text:
            return jsonify(error="message is required"), 400
        run = store.new(current_user())
        store.submit(run, lambda s: s.start(text), wait())
        return jsonify(_view(run)), 201 if wait() else 202

    @app.get("/api/runs/<run_id>")
    def get_run(run_id):
        run = store.get(run_id, current_user())
        if run is None:
            return jsonify(error="unknown run"), 404
        return jsonify(_view(run))

    @app.post("/api/runs/<run_id>/reply")
    def reply(run_id):
        run = store.get(run_id, current_user())
        if run is None:
            return jsonify(error="unknown run"), 404
        text = message()
        if not text:
            return jsonify(error="message is required"), 400
        if run.busy:
            return jsonify(error="agents are still working on the previous message"), 409
        if not run.session.awaiting:
            return jsonify(error="run is not waiting at a checkpoint"), 409
        if not store.submit(run, lambda s: s.reply(text), wait()):
            return jsonify(error="agents are still working on the previous message"), 409
        return jsonify(_view(run)), 200 if wait() else 202

    @app.get("/api/runs/<run_id>/events")
    def events(run_id):
        """Server-Sent Events: live agent activity. ?after=<seq> resumes after a reconnect."""
        run = store.get(run_id, current_user())
        if run is None:
            return jsonify(error="unknown run"), 404
        after = request.args.get("after", type=int) or int(request.headers.get("Last-Event-ID", 0) or 0)

        def stream(after=after):
            yield "retry: 1500\n\n"
            while True:
                batch = run.events.since(after, timeout=15)
                if not batch:
                    yield ": keep-alive\n\n"
                    continue
                for e in batch:
                    after = e["seq"]
                    yield f"id: {e['seq']}\ndata: {json.dumps(e, default=str)}\n\n"

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/<run_id>/brief.json")
    def brief(run_id):
        run = store.get(run_id, current_user())
        if run is None or not run.session.values.get("brief"):
            return jsonify(error="no brief yet"), 404
        return Response(json.dumps(run.session.values["brief"], indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=agreed_brief_{run_id}.json"})

    @app.get("/api/memory")
    def memory():
        return jsonify(store.memory.data)

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
