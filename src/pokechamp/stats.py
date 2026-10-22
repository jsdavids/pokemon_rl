"""Stat calculation: base stats + IVs/EVs/nature/level -> actual stats.

Used forward by the damage calculator and inverted by the stat estimator.
"""

from __future__ import annotations

import math

from pokechamp.schemas import STAT_KEYS

# nature -> (boosted stat, hindered stat); neutral natures map to (None, None)
NATURES: dict[str, tuple[str | None, str | None]] = {
    "Hardy": (None, None),
    "Lonely": ("atk", "def"),
    "Brave": ("atk", "spe"),
    "Adamant": ("atk", "spa"),
    "Naughty": ("atk", "spd"),
    "Bold": ("def", "atk"),
    "Docile": (None, None),
    "Relaxed": ("def", "spe"),
    "Impish": ("def", "spa"),
    "Lax": ("def", "spd"),
    "Timid": ("spe", "atk"),
    "Hasty": ("spe", "def"),
    "Serious": (None, None),
    "Jolly": ("spe", "spa"),
    "Naive": ("spe", "spd"),
    "Modest": ("spa", "atk"),
    "Mild": ("spa", "def"),
    "Quiet": ("spa", "spe"),
    "Bashful": (None, None),
    "Rash": ("spa", "spd"),
    "Calm": ("spd", "atk"),
    "Gentle": ("spd", "def"),
    "Sassy": ("spd", "spe"),
    "Careful": ("spd", "spa"),
    "Quirky": (None, None),
}


# Pokemon Champions replaces EVs with Stat Points (SP): 66 per Pokemon, at
# most 32 in one stat, each adding exactly +1 to that level-50 stat (IVs are
# 31). HOME's conversion rule — a stat's first SP is 4 EVs, each further SP
# 8 — reproduces the stat exactly, so builds are stored as EVs (8n - 4 per
# n SP) and the mainline formula above stays the single source of truth.
SP_MAX_PER_STAT = 32
SP_MAX_TOTAL = 66


def sp_to_ev(sp: int) -> int:
    """Stat Points in one stat -> the EVs giving the same level-50 stat."""
    return 0 if sp <= 0 else 8 * sp - 4


def ev_to_sp(ev: int) -> int:
    """EVs in one stat -> the Stat Points they're worth at level 50 (31 IV):
    floor((floor(ev/4) + 1) / 2) == (ev + 4) // 8. 252 -> 32, 4 -> 1."""
    return 0 if ev <= 0 else (ev + 4) // 8


def evs_to_sp(evs: dict[str, int]) -> dict[str, int]:
    return {k: ev_to_sp(evs.get(k, 0)) for k in STAT_KEYS}


def nature_multiplier(nature: str, stat: str) -> float:
    plus, minus = NATURES[nature]
    if stat == plus:
        return 1.1
    if stat == minus:
        return 0.9
    return 1.0


def calc_stat(stat: str, base: int, iv: int, ev: int, level: int, nature: str = "Serious") -> int:
    core = math.floor((2 * base + iv + math.floor(ev / 4)) * level / 100)
    if stat == "hp":
        return core + level + 10
    return math.floor((core + 5) * nature_multiplier(nature, stat))


def calc_all_stats(
    base_stats: dict[str, int],
    level: int = 50,
    nature: str = "Serious",
    ivs: dict[str, int] | None = None,
    evs: dict[str, int] | None = None,
) -> dict[str, int]:
    ivs = ivs or {k: 31 for k in STAT_KEYS}
    evs = evs or {k: 0 for k in STAT_KEYS}
    return {
        k: calc_stat(k, base_stats[k], ivs.get(k, 31), evs.get(k, 0), level, nature)
        for k in STAT_KEYS
    }
