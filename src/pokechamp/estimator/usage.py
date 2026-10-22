"""Usage-based priors (Phase 2 completion).

scripts/fetch_usage.py aggregates tournament team lists (Limitless: item,
ability, nature, moves for every player) and Pikalytics usage pages into
rulesets/<ruleset>/usage/<regulation>.json. This module turns those shares
into:

- a PRIOR over the estimator's candidate grid: P(item bucket) x
  P(ability) x P(nature), applied once through StatEstimator.apply_prior
  before any observation. Usage says nothing about EVs (neither source has
  them), so EV templates stay uniform;
- extra candidate natures the archetype grid lacks but the meta runs
  (Hasty / Naive / Quiet ...);
- move weights for determinization (sim.determinize.fill_moves): rollout
  opponents carry the moves people actually click, not random learnset
  picks;
- a compact 'meta' read for the LLM (opponent_summary).

Priors are softened so they inform without vetoing: every factor mixes in
a uniform floor (FLOOR, more for thin samples via SHRINK pseudo-counts), so
an off-meta build is unlikely a priori but never impossible — damage and
speed evidence still overturn it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from pokechamp.damage.items import IMPLEMENTED as DAMAGE_ITEMS
from pokechamp.data.loader import DATA_DIR, norm
from pokechamp.estimator.candidates import NATURES
from pokechamp.schemas import PokemonSet
from pokechamp.stats import NATURES as ALL_NATURES

MIN_SAMPLES = 20  # Limitless sets below which Pikalytics (if present) wins
FLOOR = 0.05  # uniform mass mixed into every factor
SHRINK = 5.0  # pseudo-counts toward uniform: thin samples lean uniform
EXTRA_NATURE_SHARE = 0.05  # usage natures this common join the grid
EXTRA_ITEM_SHARE = 0.05  # ...and damage-relevant items this common


@dataclass(frozen=True)
class SpeciesUsage:
    species: str
    regulation: str
    source: str  # "limitless" | "pikalytics"
    samples: int  # team lists (limitless) / game appearances (pikalytics)
    usage: float | None  # share of teams carrying it
    items: dict[str, float] = field(default_factory=dict)
    abilities: dict[str, float] = field(default_factory=dict)
    natures: dict[str, float] = field(default_factory=dict)
    moves: dict[str, float] = field(default_factory=dict)
    teammates: dict[str, float] = field(default_factory=dict)
    sets: tuple = ()
    win_rate: float | None = None

    @property
    def epsilon(self) -> float:
        """Uniform weight in each factor: FLOOR, or more for thin samples."""
        return max(FLOOR, SHRINK / (self.samples + SHRINK))

    def extra_natures(self) -> list[str]:
        return [
            n for n, share in self.natures.items()
            if share >= EXTRA_NATURE_SHARE and n not in NATURES
            and n in ALL_NATURES  # free-text sheets: never trust the string
        ]

    def extra_items(self) -> list[str]:
        """Items the calculator models (type boosters, gems, ...) that this
        species runs often enough to deserve their own hypothesis."""
        return [
            i for i, share in self.items.items()
            if share >= EXTRA_ITEM_SHARE and norm(i) in DAMAGE_ITEMS
        ]

    def _factor(self, shares: dict, options: list) -> dict | None:
        """Prior over one feature's candidate options, renormalized over
        those options and floored. None when it can't discriminate."""
        if len(options) < 2:
            return None
        observed = {o: shares.get(o, 0.0) for o in options}
        total = sum(observed.values())
        if total <= 0:
            return None
        eps, k = self.epsilon, len(options)
        return {o: (1 - eps) * v / total + eps / k for o, v in observed.items()}

    def prior(self, candidates: list[PokemonSet]) -> Callable[[PokemonSet], float]:
        """score(candidate) for StatEstimator.apply_prior.

        Items: the grid only distinguishes damage/speed-relevant items, with
        None standing for "anything else" (Sitrus, Sash, Mega stones, ...),
        so None's share is whatever usage mass the named candidates don't
        claim."""
        items = sorted({c.item for c in candidates}, key=str)
        named = [i for i in items if i is not None]
        item_shares = {i: self.items.get(i, 0.0) for i in named}
        if None in items:
            item_shares[None] = max(0.0, 1.0 - sum(item_shares.values()))
        f_item = self._factor(item_shares, items)
        f_ability = self._factor(
            self.abilities, sorted({c.ability for c in candidates}, key=str)
        )
        f_nature = self._factor(
            self.natures, sorted({c.nature for c in candidates})
        )

        def score(c: PokemonSet) -> float:
            s = 1.0
            if f_item is not None:
                s *= f_item[c.item]
            if f_ability is not None:
                s *= f_ability[c.ability]
            if f_nature is not None:
                s *= f_nature[c.nature]
            return s
        return score

    @property
    def description(self) -> str:
        return (f"usage prior ({self.regulation}, {self.source}, "
                f"n={self.samples})")

    def summary(self, top: int = 3) -> dict:
        """Compact meta read for the LLM: what this species usually runs."""
        def pick(d: dict, n: int = top) -> dict:
            return {k: round(v, 3) for k, v in list(d.items())[:n]}
        out = {
            "source": f"{self.source} ({self.regulation}, n={self.samples})",
            "usage": self.usage,
            "items": pick(self.items),
            "abilities": pick(self.abilities),
            "moves": pick(self.moves, max(top, 4)),  # a full moveset's worth
            "teammates": pick(self.teammates),
        }
        if self.natures:
            out["natures"] = pick(self.natures)
        return out


@lru_cache(maxsize=None)
def load_usage(ruleset: str, regulation: str) -> dict[str, SpeciesUsage]:
    """Every species' usage for one regulation ({} if none was fetched).
    Limitless is preferred; Pikalytics fills species with fewer than
    MIN_SAMPLES Limitless sets (natures always come from Limitless)."""
    path = DATA_DIR / ruleset / "usage" / f"{regulation}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for name, srcs in raw["species"].items():
        lim, pika = srcs.get("limitless"), srcs.get("pikalytics")
        if lim and (lim["n"] >= MIN_SAMPLES or not pika):
            out[name] = SpeciesUsage(
                species=name, regulation=regulation, source="limitless",
                samples=lim["n"], usage=lim["usage"], items=lim["items"],
                abilities=lim["abilities"], natures=lim["natures"],
                moves=lim["moves"], teammates=lim["teammates"],
                sets=tuple(lim.get("sets", ())), win_rate=lim.get("win_rate"),
            )
        elif pika:
            out[name] = SpeciesUsage(
                species=name, regulation=regulation, source="pikalytics",
                samples=pika.get("games") or 0, usage=pika["usage"],
                items=pika["items"], abilities=pika["abilities"],
                natures=lim["natures"] if lim else {},
                moves=pika["moves"], teammates=pika["teammates"],
            )
    return out


def usage_for(
    species: str, ruleset: str = "champions", regulation: str | None = None
) -> SpeciesUsage | None:
    if regulation is None:
        return None
    return load_usage(ruleset, regulation).get(species)
