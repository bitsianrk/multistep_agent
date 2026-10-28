"""Run-time settings for the Campaign Planning Agent.

Every threshold the workflow document leaves as "a floor" or "a threshold"
lives here so it can be tuned without touching agents or tools.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Settings:
    # Data layer -----------------------------------------------------------
    data_seed: int = 7
    # T-1: the date the nightly feature tables were last refreshed. Fixed so
    # the dummy data, golden tests and demo are reproducible.
    as_of: date = date(2026, 10, 27)
    n_households: int = 6000

    # PSG hard filters (Qualifying PSGs table) ----------------------------
    margin_floor_pct: float = 20.0          # margin after supplier funding
    stock_cover_min_ratio: float = 1.0      # forecast cover / window weeks
    hero_psg_count: int = 2

    # Privacy -------------------------------------------------------------
    min_cell_size: int = 30                 # households per reported cell

    # Discussion (stage 4) -------------------------------------------------
    max_debate_rounds: int = 2
    convergence_tolerance: float = 0.85     # mean agreement across advocates
    max_brand_switches: int = 1

    # Runaway-cost controls -------------------------------------------------
    max_send_backs: int = 3
    max_tool_calls: int = 250
    tool_timeout_multiplier: float = 1.0   # x latency_sla_ms from manifest

    # Brand Scout ---------------------------------------------------------
    scout_beat_margin: float = 0.25         # score lead needed to "beat"
    scout_max_alternatives: int = 3

    # Reconciliation tolerances (Critic) ---------------------------------
    reconcile_share_tol_pct: float = 0.5
    reconcile_sales_tol_pct: float = 2.0
    kantar_max_staleness_weeks: int = 6

    # Autonomy ladder: "L1" waits at every checkpoint; "L2" auto-advances
    # stages 1-3 when confidence is high.
    autonomy_level: str = "L1"
    l2_confidence_threshold: float = 0.9

    # Defaults when the request does not say
    default_window_weeks: int = 4
    default_lead_days: int = 14

    # LRM / harness ---------------------------------------------------------
    # "auto": live Tesco v1/lrm/ when LRM_EMPLOYEE_ID + LRM_API_KEY are set,
    # otherwise the offline simulated model. "live" | "simulated" | "off".
    lrm_mode: str = field(default_factory=lambda: os.getenv("LRM_MODE", "auto"))

    # Outputs -------------------------------------------------------------
    output_dir: Path = field(default_factory=lambda: Path("runs"))
    memory_path: Path = field(default_factory=lambda: Path("runs/memory.json"))


DEFAULT_SETTINGS = Settings()
