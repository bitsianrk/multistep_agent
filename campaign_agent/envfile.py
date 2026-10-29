"""Load LRM settings from a local, git-ignored .env file.

Entry points (web UI, CLI, LRM check) call load_env_file() first, so the
credentials live in one file instead of being re-typed in every terminal:

    copy .env.example .env      # Windows        (cp on macOS / Linux)
    # then fill in LRM_EMPLOYEE_ID and LRM_API_KEY

Values already set in the environment win over the file. No dependency:
KEY=VALUE lines, optional quotes, '#' comments.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> list[str]:
    """Set variables from `path` that are not already set. Returns the names it set."""
    p = Path(path)
    if not p.is_file():
        return []
    loaded = []
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def default_lrm_mode() -> str:
    """Entry points run live unless LRM_MODE (environment or .env) says otherwise."""
    return os.getenv("LRM_MODE", "live")
