"""Ruleset-keyed data loader.

Pokemon Champions' exact roster/mechanics aren't fully documented yet, so all
game data lives in swappable JSON under rulesets/<name>/. We start from Gen 9
(Showdown-compatible) data; when Champions data is datamined, it becomes a new
ruleset directory and nothing in the code changes.

The bundled gen9 ruleset is a small sample for development. scripts/ has a
harness for generating the full dataset from Showdown's data files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "rulesets"


def norm(name: str) -> str:
    """Normalize a name for lookup: 'Great Tusk' == 'great-tusk' == 'greattusk'."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


@dataclass(frozen=True)
class Species:
    name: str
    types: tuple[str, ...]
    base_stats: dict[str, int]
    abilities: tuple[str, ...] = ()
    learnset: frozenset[str] = frozenset()  # norm()ed move names
    base_species: str | None = None  # e.g. "Garchomp" for Mega Garchomp
    weight: float = 0.0  # kg (Grass Knot / Heavy Slam tiers)


@dataclass(frozen=True)
class Move:
    name: str
    type: str
    category: str  # "Physical", "Special", "Status"
    base_power: int
    accuracy: int | None  # None = never misses
    priority: int = 0
    spread: bool = False  # hits multiple targets (doubles spread reduction)
    # canonical target id: "normal", "adjacentFoes" (Hyper Voice),
    # "allAdjacent" (Earthquake — hits your ally!), "self", "allies",
    # "side", "field", "randomNormal"
    target: str = "normal"
    # Showdown-style flags: "contact", "sound", "punch", "bite", "slicing",
    # "bullet", "powder", ... Drive ability/item conditions (Tough Claws,
    # Punk Rock, Punching Glove, Bulletproof).
    flags: frozenset[str] = frozenset()
    # (min_hits, max_hits) for multi-hit moves; None for single-hit.
    multihit: tuple[int, int] | None = None
    pp: int | None = None  # Champions rebalances PP vs mainline games
    effect: str = ""  # human-readable effect text (surfaced to the LLM)


class Ruleset:
    def __init__(
        self,
        name: str,
        species: dict[str, Species],
        moves: dict[str, Move],
        typechart: dict[str, dict[str, float]],
        items: dict[str, dict] | None = None,
        regulations: dict[str, dict] | None = None,
    ):
        self.name = name
        self._species = species
        self._moves = moves
        self.typechart = typechart
        # raw item/regulation records (rulesets that ship them, e.g. champions)
        self.items = items or {}
        self.regulations = regulations or {}

    def get_species(self, name: str) -> Species:
        key = norm(name)
        if key not in self._species:
            raise KeyError(f"Unknown species {name!r} in ruleset {self.name!r}")
        return self._species[key]

    def get_move(self, name: str) -> Move:
        key = norm(name)
        if key not in self._moves:
            raise KeyError(f"Unknown move {name!r} in ruleset {self.name!r}")
        return self._moves[key]

    def all_species(self) -> list[Species]:
        return list(self._species.values())

    def type_effectiveness(self, move_type: str, defender_types: tuple[str, ...]) -> float:
        mult = 1.0
        attacking = self.typechart.get(move_type.lower(), {})
        for t in defender_types:
            mult *= attacking.get(t.lower(), 1.0)
        return mult


@lru_cache(maxsize=None)
def load_ruleset(name: str = "gen9") -> Ruleset:
    root = DATA_DIR / name
    if not root.is_dir():
        raise FileNotFoundError(f"No ruleset directory at {root}")

    raw_species = json.loads((root / "species.json").read_text(encoding="utf-8"))
    species = {
        norm(display): Species(
            name=display,
            types=tuple(entry["types"]),
            base_stats=entry["base_stats"],
            abilities=tuple(entry.get("abilities", ())),
            learnset=frozenset(norm(m) for m in entry.get("moves", ())),
            base_species=entry.get("base"),
            weight=entry.get("weight", 0.0),
        )
        for display, entry in raw_species.items()
    }

    def parse_multihit(raw) -> tuple[int, int] | None:
        if raw is None:
            return None
        if isinstance(raw, int):
            return (raw, raw)
        return (raw[0], raw[1])

    raw_moves = json.loads((root / "moves.json").read_text(encoding="utf-8"))
    moves = {
        norm(display): Move(
            name=display,
            type=entry["type"],
            category=entry["category"],
            base_power=entry.get("base_power", 0),
            accuracy=entry.get("accuracy"),
            priority=entry.get("priority", 0),
            spread=entry.get("spread", False),
            target=entry.get("target", "normal"),
            flags=frozenset(entry.get("flags", [])),
            multihit=parse_multihit(entry.get("multihit")),
            pp=entry.get("pp"),
            effect=entry.get("effect", ""),
        )
        for display, entry in raw_moves.items()
    }

    typechart = json.loads((root / "typechart.json").read_text(encoding="utf-8"))

    def optional(fname: str) -> dict | None:
        path = root / fname
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    return Ruleset(
        name, species, moves, typechart,
        items=optional("items.json"),
        regulations=optional("regulations.json"),
    )
