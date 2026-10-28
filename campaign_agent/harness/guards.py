"""Output guards the harness applies to model text."""

from __future__ import annotations

import json
import re
from itertools import combinations

_NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[str]:
    return [m.lstrip("+-").replace(",", "") for m in _NUM.findall(text)]


def subset_sums(values: list[int]) -> list[int]:
    """Totals a summary may legitimately state (e.g. households across chosen cohorts)."""
    vals = [v for v in values if isinstance(v, int)]
    out = set()
    for r in range(2, len(vals) + 1):
        for combo in combinations(vals, r):
            out.add(sum(combo))
    return sorted(out)


def ungrounded_numbers(text: str, sources: list, derived: list | None = None) -> list[str]:
    """Numbers in `text` that cannot be traced to `sources` (faithfulness check).

    A number is grounded when some source number equals it at the precision
    it is written with (so 1.405 grounds "1.41"). Small integers (<= 52:
    counts, weeks, block numbers) and years are ignored. `derived` adds
    legitimate computed values such as cohort totals.
    """
    blob = " ".join(s if isinstance(s, str) else json.dumps(s, default=str) for s in sources)
    src = [float(n) for n in _numbers(blob) if n] + [float(d) for d in (derived or [])]
    missing = []
    for raw in _numbers(text):
        if not raw:
            continue
        if "." not in raw:
            val = int(raw)
            if val <= 52 or 1990 <= val <= 2100:
                continue
            if not any(round(s) == val for s in src):
                missing.append(raw)
            continue
        k = len(raw.split(".")[1])
        val = float(raw)
        if not any(abs(round(s, k) - val) < 1e-9 for s in src):
            missing.append(raw)
    return missing
