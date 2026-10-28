"""Load agent skills: campaign_agent/skills/<name>/SKILL.md.

A skill tells an agent what to do. SKILL.md is YAML frontmatter + Markdown:

    ---
    name: entity-resolution
    description: Map the campaign manager's request to exactly one brand ...
    agent: entity_resolution          # roster key; its tool allow-list still applies
    tools: [ner_extract, taxonomy_lookup, get_run_state, recall_memory]
    reasoning_effort: medium
    max_iterations: 6
    max_tokens: 2000
    warmup: [get_run_state, recall_memory]   # what the simulated model consults first
    output_schema: {...}              # contract for the finish tool's ARGS
    ---
    # Instructions ...

The body becomes the agent's system prompt, after the shared HARNESS.md
preamble. Tools are the capabilities (campaign_agent/tools/library); skills
are the instructions for using them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@dataclass
class AgentSkill:
    name: str
    description: str
    agent: str
    tools: list[str]
    instructions: str
    output_schema: dict
    reasoning_effort: str = "medium"
    max_iterations: int = 6
    max_tokens: int = 3000
    warmup: list[str] = field(default_factory=lambda: ["get_run_state", "recall_memory"])
    path: Path | None = None


def parse_skill_md(text: str, path: Path | None = None) -> AgentSkill:
    if not text.startswith("---"):
        raise ValueError(f"{path}: SKILL.md must start with YAML frontmatter")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front) or {}
    for key in ("name", "description", "agent", "output_schema"):
        if key not in meta:
            raise ValueError(f"{path}: frontmatter is missing {key!r}")
    return AgentSkill(
        name=meta["name"], description=" ".join(str(meta["description"]).split()), agent=meta["agent"],
        tools=list(meta.get("tools") or []), instructions=body.strip(), output_schema=meta["output_schema"],
        reasoning_effort=meta.get("reasoning_effort", "medium"), max_iterations=int(meta.get("max_iterations", 6)),
        max_tokens=int(meta.get("max_tokens", 3000)),
        warmup=list(meta.get("warmup", ["get_run_state", "recall_memory"])), path=path,
    )


class SkillLibrary:
    def __init__(self, root: Path = SKILLS_DIR):
        self.root = root
        self.preamble = (root / "HARNESS.md").read_text().strip()
        self.skills: dict[str, AgentSkill] = {}
        for p in sorted(root.glob("*/SKILL.md")):
            s = parse_skill_md(p.read_text(), p)
            if s.name != p.parent.name:
                raise ValueError(f"{p}: name {s.name!r} must match its folder")
            self.skills[s.name] = s

    def get(self, name: str) -> AgentSkill:
        if name not in self.skills:
            raise KeyError(f"no skill {name!r} in {self.root}")
        return self.skills[name]

    def names(self) -> list[str]:
        return sorted(self.skills)
