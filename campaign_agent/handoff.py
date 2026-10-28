"""Handoff of the Agreed Brief to the external Recommendation Draft process.

How the external process receives the Brief (API, file, queue) is an open
question in the design, so the sink is pluggable. The default writes files
to an internal folder: nothing goes to suppliers or other external parties.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class HandoffSink(Protocol):
    def send(self, run_id: str, brief: dict, summary_md: str, audit: list[dict]) -> dict: ...


class FileHandoff:
    def __init__(self, root: Path):
        self.root = Path(root)

    def send(self, run_id: str, brief: dict, summary_md: str, audit: list[dict]) -> dict:
        d = self.root / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "agreed_brief.json").write_text(json.dumps(brief, indent=2))
        (d / "summary.md").write_text(summary_md + "\n")
        (d / "audit.jsonl").write_text("".join(json.dumps(a) + "\n" for a in audit))
        return {"sink": "file", "path": str(d / "agreed_brief.json")}


class MemoryHandoff:
    """In-process sink for tests and notebooks."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, run_id: str, brief: dict, summary_md: str, audit: list[dict]) -> dict:
        self.sent.append({"run_id": run_id, "brief": brief, "summary_md": summary_md, "audit": audit})
        return {"sink": "memory", "index": len(self.sent) - 1}
