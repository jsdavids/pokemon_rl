"""Stat Point spread models grounded in ladder data.

Source: Pikalytics ranked-ladder spreads (scripts/fetch_spreads.py), Stat
Points per stat with shares, for the format's top species.
scripts/analyze_spreads.py is the evidence behind the model:

- 97% of builds spend all 66 SP, so stats are NOT independent — a spread
  is sampled whole, never stat by stat;
- what a stat gets depends on its ROLE, which a team sheet determines
  (species + nature): the attacking stat (nature-boosted Atk/SpA, else the
  higher base), Speed, HP, Def, SpD, and the unused "dump" attacking stat.
  With offense natures attack is maxed 61% of the time and the dump stat
  is empty 94%; with bulk natures HP is maxed 73% and Def/SpD take
  benchmark values (14/15, 19/20 ...); Trick Room natures leave Speed
  empty 93%.

Two samplers, used to give held-out team sheets (no spreads) realistic
truth spreads in simulation:

  species  that species' own ladder spreads, by share (falls back to role)
  role     a spread in ROLE space drawn from the pooled ladder spreads of
           OTHER species with the same nature archetype (leave-one-species-
           out), mapped onto this Pokemon's stats — realistic in shape but
           not this species' habits, and often off the estimator's grid
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from functools import lru_cache

from pokechamp.data import load_ruleset
from pokechamp.data.loader import DATA_DIR
from pokechamp.stats import NATURES, sp_to_ev

STATS = ("hp", "atk", "def", "spa", "spd", "spe")
DEFAULT_FORMAT = "battledataregmbs3"


def stat_roles(base: dict, nature: str) -> dict[str, str]:
    """stat -> role, from what a team sheet shows (species, nature)."""
    plus, _ = NATURES[nature]
    if plus in ("atk", "spa"):
        attack = plus
    else:
        attack = "atk" if base["atk"] >= base["spa"] else "spa"
    dump = "spa" if attack == "atk" else "atk"
    return {attack: "attack", dump: "dump", "spe": "spe", "hp": "hp",
            "def": "def", "spd": "spd"}


def archetype(nature: str) -> str:
    plus, minus = NATURES[nature]
    if minus == "spe":
        return "trick room"
    if plus in ("def", "spd"):
        return "bulk"
    return "offense"


@dataclass(frozen=True)
class _RoleSpread:
    species: str
    archetype: str
    weight: float  # each source species sums to 1
    roles: tuple[tuple[str, int], ...]  # (role, SP)


class SpreadModel:
    def __init__(self, data: dict, ruleset: str = "champions"):
        self.rs = load_ruleset(ruleset)
        self.species = data  # our name -> {"spreads": [...], "natures": {...}}
        self.pool: list[_RoleSpread] = []
        for name, entry in data.items():
            spreads = entry["spreads"]
            mass = sum(s[6] for s in spreads) or 1.0
            nature = max(entry["natures"], key=entry["natures"].get)
            roles = stat_roles(self.rs.get_species(name).base_stats, nature)
            arch = archetype(nature)
            for *sp, share in spreads:
                self.pool.append(_RoleSpread(
                    name, arch, share / mass,
                    tuple((roles[s], v) for s, v in zip(STATS, sp)),
                ))

    def _base_name(self, species: str) -> str:
        """Ladder data is per species; a Mega shares its base's spreads."""
        sp = self.rs.get_species(species)
        return sp.base_species if sp.name.startswith("Mega ") else sp.name

    def sample_species(self, species: str, nature: str, rng: random.Random
                       ) -> dict[str, int] | None:
        """One of this species' ladder spreads (SP), or None if no data.
        A Trick Room nature skips spreads that invest in Speed."""
        entry = self.species.get(self._base_name(species))
        if not entry:
            return None
        spreads = entry["spreads"]
        if archetype(nature) == "trick room":
            spreads = [s for s in spreads if s[5] <= 4] or spreads
        pick = rng.choices(spreads, [s[6] for s in spreads])[0]
        return dict(zip(STATS, pick[:6]))

    def sample_role(self, species: str, nature: str, rng: random.Random
                    ) -> dict[str, int]:
        """A pooled role-space spread from OTHER species of the same nature
        archetype, mapped onto this Pokemon's stats (SP)."""
        base_name = self._base_name(species)
        arch = archetype(nature)
        pool = [r for r in self.pool if r.species != base_name and r.archetype == arch]
        if not pool:  # e.g. no other Trick Room species in the data
            pool = [r for r in self.pool if r.species != base_name]
        pick = rng.choices(pool, [r.weight for r in pool])[0]
        by_role = dict(pick.roles)
        roles = stat_roles(self.rs.get_species(species).base_stats, nature)
        return {stat: by_role[roles[stat]] for stat in STATS}

    def sample_evs(self, species: str, nature: str, rng: random.Random,
                   mode: str = "species") -> tuple[dict[str, int], str]:
        """(EVs, how it was sampled) for a truth spread."""
        sp, how = None, "role"
        if mode == "species":
            sp = self.sample_species(species, nature, rng)
            how = "species"
        if sp is None:
            sp, how = self.sample_role(species, nature, rng), "role"
        return {k: sp_to_ev(v) for k, v in sp.items()}, how


ROLE_ORDER = ("attack", "spe", "hp", "def", "spd", "dump")
ARCHETYPES = ("offense", "bulk", "trick room")


def _l1(a: tuple, b: tuple) -> int:
    return sum(abs(x - y) for x, y in zip(a, b))


class SpreadCandidates:
    """Ladder-grounded EV hypotheses for the estimator, replacing the 8
    hand-written EV templates (which match 0.3% of real builds exactly).

    Per nature archetype, K representative ROLE-space spreads are chosen by
    greedy weighted coverage: repeatedly take the spread covering the most
    not-yet-covered build mass within `radius` Stat Points (L1). Each shape
    keeps the mass of the builds nearest to it as its prior weight. A
    candidate's EVs come from mapping its shape onto the species' stats
    through ITS nature's roles — an Adamant candidate's 'attack 32' is
    Attack, a Modest one's is Sp. Atk — so no candidate wastes points on
    the stat its nature hinders."""

    def __init__(self, model: SpreadModel, k: int = 12, radius: int = 4,
                 n_centers: int = 300):
        self.k, self.radius = k, radius
        self.shapes: dict[str, list[tuple[tuple[int, ...], float]]] = {}
        for arch in ARCHETYPES:
            items = [r for r in model.pool if r.archetype == arch] or model.pool
            self.shapes[arch] = self._choose(items, k, radius, n_centers)
        self._cache: dict[tuple[str, str], list[tuple[dict, float]]] = {}

    @staticmethod
    def _choose(items, k, radius, n_centers):
        mass: dict[tuple, float] = {}
        for r in items:
            vec = tuple(dict(r.roles)[role] for role in ROLE_ORDER)
            mass[vec] = mass.get(vec, 0.0) + r.weight
        vecs = list(mass)
        centers = sorted(vecs, key=mass.get, reverse=True)[:n_centers]
        uncovered = set(vecs)
        chosen = []
        while uncovered and len(chosen) < k:
            best = max(centers, key=lambda c: sum(
                mass[v] for v in uncovered if _l1(c, v) <= radius))
            gained = {v for v in uncovered if _l1(best, v) <= radius}
            if not gained:
                break
            chosen.append(best)
            uncovered -= gained
        # prior weight: the mass of every build whose nearest shape it is
        weight = {c: 0.0 for c in chosen}
        for v, m in mass.items():
            weight[min(chosen, key=lambda c: _l1(c, v))] += m
        total = sum(weight.values())
        return [(c, weight[c] / total) for c in chosen]

    def evs_for(self, species: str, nature: str, rs) -> list[tuple[dict, float]]:
        """[(EVs, prior weight)] hypotheses for this species and nature."""
        key = (species, nature)
        if key not in self._cache:
            roles = stat_roles(rs.get_species(species).base_stats, nature)
            out: dict[tuple, float] = {}
            for vec, w in self.shapes[archetype(nature)]:
                by_role = dict(zip(ROLE_ORDER, vec))
                evs = tuple((s, sp_to_ev(by_role[roles[s]])) for s in STATS)
                out[evs] = out.get(evs, 0.0) + w
            self._cache[key] = [(dict(e), w) for e, w in out.items()]
        return self._cache[key]

    def prior(self, rs):
        """score(candidate) for StatEstimator.apply_prior: the shape weight,
        so a candidate's spread is as likely as the builds it stands for."""
        def score(c) -> float:
            for evs, w in self.evs_for(c.species, c.nature, rs):
                if all(c.evs.get(s, 0) == evs[s] for s in STATS):
                    return w
            return 1e-6  # not one of ours (e.g. a hand-made candidate)
        return score


@lru_cache(maxsize=None)
def load_spread_candidates(fmt: str = DEFAULT_FORMAT, k: int = 12,
                           ruleset: str = "champions") -> SpreadCandidates | None:
    model = load_spread_model(fmt, ruleset)
    return SpreadCandidates(model, k=k) if model is not None else None


@lru_cache(maxsize=None)
def load_spread_model(fmt: str = DEFAULT_FORMAT, ruleset: str = "champions"
                      ) -> SpreadModel | None:
    path = DATA_DIR / ruleset / "usage" / f"spreads-{fmt}.json"
    if not path.exists():
        return None
    return SpreadModel(json.loads(path.read_text(encoding="utf-8"))["species"], ruleset)
