"""Episodic memory: past runs and human overrides, used to re-weight scoring.

Semantic memory is the taxonomy (data/taxonomy.py); procedural memory is the
tool library. Overrides nudge audience, PSG-hero and Brand Scout scoring for
the same category in later runs; each nudge is small and capped.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

STEP = 0.1
CAP = 0.3


class EpisodicMemory:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.data = {"runs": [], "overrides": []}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                pass

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2))

    def record_override(self, run_id: str, category_id: str, stage: str, edit: dict, proposed: dict) -> None:
        with self._lock:
            self.data["overrides"].append({"run_id": run_id, "category_id": category_id, "stage": stage,
                                           "edit": edit, "proposed": proposed})
            self._save()

    def record_run(self, run_id: str, summary: dict) -> None:
        with self._lock:
            self.data["runs"].append({"run_id": run_id, **summary})
            self._save()

    def _for(self, category_id: str):
        return [o for o in self.data["overrides"] if o["category_id"] == category_id]

    def cohort_bias(self, category_id: str) -> dict:
        bias: dict[str, float] = {}
        for o in self._for(category_id):
            e = o["edit"]
            if e["op"] == "exclude_cohort" and o["proposed"].get("include", True):
                bias[e["cohort"]] = bias.get(e["cohort"], 0) - STEP
            elif e["op"] == "include_cohort" and not o["proposed"].get("include", False):
                bias[e["cohort"]] = bias.get(e["cohort"], 0) + STEP
        return {k: max(-CAP, min(CAP, round(v, 2))) for k, v in bias.items()}

    def hero_bias(self, category_id: str) -> dict:
        bias: dict[str, float] = {}
        for o in self._for(category_id):
            e = o["edit"]
            if e["op"] == "hero_psg":
                bias[e["psg_id"]] = bias.get(e["psg_id"], 0) + STEP
            elif e["op"] == "exclude_psg" and o["proposed"].get("hero"):
                bias[e["psg_id"]] = bias.get(e["psg_id"], 0) - STEP
        return {k: max(-CAP, min(CAP, round(v, 2))) for k, v in bias.items()}

    def switch_bias(self, category_id: str) -> float:
        """Rejected brand switches raise the bar for Brand Scout; accepted ones lower it."""
        b = 0.0
        for o in self._for(category_id):
            if o["edit"]["op"] == "keep_brand" and o["proposed"].get("switch"):
                b += STEP
            elif o["edit"]["op"] == "switch_brand" and not o["proposed"].get("switch"):
                b -= STEP
        return max(-CAP, min(CAP, round(b, 2)))

    def recall(self, category_id: str, last_runs: int = 3) -> dict:
        """What agents are told about this category from earlier runs."""
        runs = [r for r in self.data["runs"] if r.get("category_id") == category_id][-last_runs:]
        counts: dict[str, int] = {}
        for o in self._for(category_id):
            e = o["edit"]
            key = f"{e['op']}:{e.get('cohort') or e.get('psg_id') or e.get('brand_id') or ''}".rstrip(":")
            counts[key] = counts.get(key, 0) + 1
        return {
            "past_runs": runs,
            "human_overrides": counts,
            "learned_bias": {"cohort": self.cohort_bias(category_id), "hero_psg": self.hero_bias(category_id),
                             "brand_switch": self.switch_bias(category_id)},
        }
