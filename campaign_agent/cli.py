"""Terminal chat window: the trigger and control surface for a run.

    python -m campaign_agent                      # new run, in-memory state
    python -m campaign_agent --db runs/state.db   # persistent checkpoints
    python -m campaign_agent --db runs/state.db --run-id run-1234abcd   # resume

Slash commands: /plan /pack /decision /brief /trace /audit /help /quit
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from .config import DEFAULT_SETTINGS
from .guardrails import display_safe
from .session import CampaignSession, format_output

HELP = """Reply in plain language at each checkpoint:
  approve   "yes", "go", "looks good"
  ask       "What is SPC for light shoppers?", "why is kantar_share in the plan?"
  edit      "drop the heavy cohort", "exclude tortilla chips", "make sharing bags a hero",
            "add sentiment_scan", "objective grow", "keep the brand", "note: check funding"
  send back "try Snapjack instead", "send back to plan", "go back to diagnose"
  pause     "pause"
Slash commands: /plan /pack /decision /brief /trace /audit /help /quit"""


def _dump(obj) -> str:
    return json.dumps(display_safe(obj), indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="campaign_agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, help="SQLite file for persistent checkpoints (pause/resume across sessions)")
    ap.add_argument("--run-id", help="resume an existing run (needs --db)")
    ap.add_argument("--autonomy", choices=["L1", "L2"], default="L1")
    ap.add_argument("--user", default="cm.demo", help="campaign manager id")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_SETTINGS.output_dir)
    args = ap.parse_args(argv)

    settings = replace(DEFAULT_SETTINGS, autonomy_level=args.autonomy, output_dir=args.output_dir,
                       memory_path=args.output_dir / "memory.json")
    user = {"id": args.user, "name": args.user, "role": "campaign_manager_role"}

    with ExitStack() as stack:
        checkpointer = None
        if args.db:
            from langgraph.checkpoint.sqlite import SqliteSaver

            args.db.parent.mkdir(parents=True, exist_ok=True)
            checkpointer = stack.enter_context(SqliteSaver.from_conn_string(str(args.db)))
        session = CampaignSession(user=user, settings=settings, checkpointer=checkpointer, run_id=args.run_id)
        print(f"Campaign Planning Agent - run {session.run_id} (autonomy {args.autonomy}). /help for help.")
        if session.awaiting:
            print("Resuming at the saved checkpoint:")
            print(session.pending["card"]["text"])
        else:
            print("Describe the campaign, e.g. 'Plan a Multi-Step for Crunchwell to win back lapsed "
                  "shoppers, 4 weeks from 16 Nov'.")
        while not session.done:
            try:
                line = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\nState saved. Resume with --run-id {session.run_id}" + (" --db ..." if args.db else
                                                                                   " (needs --db to persist)"))
                return 0
            if not line:
                continue
            if line.startswith("/"):
                cmd = line[1:].split()[0]
                v = session.values
                if cmd in ("quit", "exit"):
                    return 0
                if cmd == "help":
                    print(HELP)
                elif cmd in ("plan", "pack", "decision", "brief", "trace", "audit"):
                    print(_dump(v.get(cmd)))
                else:
                    print("Unknown command. /help")
                continue
            out = session.reply(line) if session.awaiting else session.start(line)
            print(format_output(out))
        print(f"\nDone. Brief: {session.values.get('handoff')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
