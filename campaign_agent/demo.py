"""Scripted end-to-end demos on the dummy data.

    python -m campaign_agent.demo                 # Crunchwell -> Snapjack brand switch
    python -m campaign_agent.demo --scenario clarify
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from .config import DEFAULT_SETTINGS
from .session import CampaignSession, format_output

SCENARIOS = {
    # Full five stages: plan adapts to falling share, a new cut is asked for,
    # Brand Scout wins the debate, the pack re-runs for the new brand, the
    # human overrides a pick, and the Brief is handed off.
    "switch": [
        "Plan a Multi-Step for Crunchwell crisps to win back lapsed shoppers, 4 weeks from 16 Nov",
        "yes",
        "Plan looks right, go",
        "What is SPC for lapsed shoppers only?",
        "go",
        "Drop the heavy cohort",
        "approve",          # accept the brand switch -> re-diagnose
        "looks good",
        "approve",
        "include medium",   # human final call overrides the Judge
        "approve",
        "approve",          # approve the Agreed Brief -> handoff
    ],
    # Ambiguous brand and missing objective: the agent asks back.
    "clarify": [
        "Set up something for Golden this Christmas",
        "the coffee one",
        "grow frequency",
        "yes",
        "remove kantar_penetration",
        "go",
        "go",
        "keep the brand",
        "approve",
        "approve",
    ],
}


def run(scenario: str, output_dir: Path, quiet: bool = False) -> CampaignSession:
    settings = replace(DEFAULT_SETTINGS, output_dir=output_dir, memory_path=output_dir / "memory.json")
    s = CampaignSession(settings=settings, run_id=f"demo-{scenario}")
    msgs = SCENARIOS[scenario]
    out = s.start(msgs[0])
    if not quiet:
        print(f"you> {msgs[0]}\n{format_output(out)}")
    for m in msgs[1:]:
        if not s.awaiting:
            break
        out = s.reply(m)
        if not quiet:
            print(f"\nyou> {m}\n{format_output(out)}")
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="switch")
    ap.add_argument("--output-dir", type=Path, default=None, help="where the Brief is written (default: temp dir)")
    args = ap.parse_args(argv)
    out_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="campaign_agent_"))
    s = run(args.scenario, out_dir)
    print("\n" + "=" * 72)
    if s.done:
        print(f"Agreed Brief written to {s.values['handoff']['path']}")
        print(json.dumps(s.values["brief"], indent=2)[:1500])
    else:
        print("Run is still waiting at a checkpoint:", s.pending["stage"] if s.pending else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
