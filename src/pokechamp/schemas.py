"""Shared schemas consumed by every tool.

These are the contract between the damage calculator, stat estimator,
recommender, and the MCP layer. Grow them carefully: a field added here is a
field every tool must handle.

Stats are plain dicts keyed by STAT_KEYS ("def" is a Python keyword, so a
dataclass with attribute access would need an awkward rename; dicts keep the
keys identical to Showdown's).
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")

# Non-volatile statuses, Showdown-style ids.
STATUSES = ("brn", "par", "slp", "frz", "psn", "tox")


def stat_table(default: int = 0) -> dict[str, int]:
    return {k: default for k in STAT_KEYS}


def boost_table() -> dict[str, int]:
    """Stat stages, -6..+6. HP has no stage."""
    return {k: 0 for k in STAT_KEYS if k != "hp"}


@dataclass
class PokemonSet:
    """A full or hypothetical build. The estimator's job (Phase 2) is to
    produce a posterior over these for an opponent's Pokemon."""

    species: str
    moves: list[str] = field(default_factory=list)
    level: int = 50
    nature: str = "Serious"
    evs: dict[str, int] = field(default_factory=stat_table)
    ivs: dict[str, int] = field(default_factory=lambda: stat_table(31))
    item: str | None = None
    ability: str | None = None
    tera_type: str | None = None
    status: str | None = None  # one of STATUSES, or None


@dataclass
class SideState:
    """Per-side field conditions."""

    reflect: bool = False
    light_screen: bool = False
    aurora_veil: bool = False
    tailwind: bool = False
    safeguard: bool = False  # blocks non-volatile status (sim-side only)


@dataclass
class FieldState:
    """Whole-field conditions relevant to damage and speed order."""

    weather: str | None = None  # "sun", "rain", "sand", "snow"
    terrain: str | None = None  # "electric", "grassy", "psychic", "misty"
    trick_room: bool = False
    attacker_side: SideState = field(default_factory=SideState)
    defender_side: SideState = field(default_factory=SideState)


@dataclass
class PokemonSnapshot:
    """One team member's condition mid-battle."""

    set: PokemonSet
    hp_fraction: float = 1.0
    status: str | None = None
    boosts: dict[str, int] = field(default_factory=boost_table)
    pp: list[int] | None = None  # None = full
    sub_hp: int = 0  # active Substitute's remaining HP
    # free-form volatile state (taunt_turns, encore_index/turns,
    # confusion_turns, choice_lock, trapped_turns, transform info)
    volatiles: dict = field(default_factory=dict)


@dataclass
class TeamSnapshot:
    """One side's condition: full team, who's on the field, side conditions."""

    pokemon: list[PokemonSnapshot]
    active: list[int | None] = field(default_factory=lambda: [0])
    conditions: SideState = field(default_factory=SideState)
    # entry hazards on this side's half: stealth_rock/sticky_web bools,
    # spikes 0-3, toxic_spikes 0-2
    hazards: dict = field(default_factory=dict)


@dataclass
class BattleState:
    """A mid-battle snapshot, sufficient to seed simulator rollouts
    (Battle.from_state) and produced by Battle.snapshot(). sides[0] is
    'mine', sides[1] the opponent (fill unknown opponent builds by sampling
    from the estimator's posterior — see pokechamp.sim.determinize)."""

    sides: tuple[TeamSnapshot, TeamSnapshot]
    field: FieldState = field(default_factory=FieldState)
    format: str = "singles"
