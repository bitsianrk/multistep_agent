"""Data layer: synthetic feature tables, live APIs and market feeds."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from .generator import DataStore, generate


@lru_cache(maxsize=4)
def _cached(seed: int, as_of: date, n_households: int) -> DataStore:
    return generate(seed=seed, as_of=as_of, n_households=n_households)


def get_store(settings=None) -> DataStore:
    from ..config import DEFAULT_SETTINGS

    s = settings or DEFAULT_SETTINGS
    return _cached(s.data_seed, s.as_of, s.n_households)


__all__ = ["DataStore", "generate", "get_store"]
