"""Determinization (Phase 3): turn the estimator's posterior into concrete
opponents to simulate against.

Imperfect information is handled by sampling: each rollout draws one full
opponent build per unrevealed Pokemon from its StatEstimator posterior, then
plays the battle as if it were perfect-information. The recommender (Phase 4)
averages move values across many such determinized worlds.
"""

from __future__ import annotations

import random

from pokechamp.data import load_ruleset
from pokechamp.data.loader import norm
from pokechamp.estimator import StatEstimator
from pokechamp.schemas import PokemonSet


def sample_build(
    estimator: StatEstimator, rng: random.Random
) -> PokemonSet:
    """Draw one candidate build from the posterior (weighted)."""
    pairs = estimator.posterior()
    builds = [c for c, _ in pairs]
    weights = [w for _, w in pairs]
    return rng.choices(builds, weights)[0]


def fill_moves(
    species: str,
    rng: random.Random,
    revealed: list[str] | None = None,
    k: int = 4,
    ruleset: str = "champions",
) -> list[str]:
    """A plausible moveset: every revealed move, topped up to `k` with random
    damaging moves from the species' learnset (usage data will replace the
    random fill when it exists)."""
    rs = load_ruleset(ruleset)
    sp = rs.get_species(species)
    moves = list(revealed or [])
    have = {norm(m) for m in moves}
    damaging = [
        m for m in sp.learnset
        if m not in have and rs.get_move(m).base_power > 0
    ]
    rng.shuffle(damaging)
    for m in damaging:
        if len(moves) >= k:
            break
        moves.append(rs.get_move(m).name)
    return moves


def random_team(
    rng: random.Random,
    size: int = 3,
    ruleset: str = "champions",
    level: int = 50,
) -> list[PokemonSet]:
    """A random plausible team for varied RL training: non-Mega species
    with a workable learnset, a simple 252/252 EV spread pointed at the
    better attacking stat, a random legal ability, damaging moves."""
    rs = load_ruleset(ruleset)
    names = [
        sp.name for sp in rs.all_species()
        if not sp.name.startswith("Mega ") and len(sp.learnset) >= 4
    ]
    team = []
    for name in rng.sample(names, size):
        sp = rs.get_species(name)
        off = "atk" if sp.base_stats["atk"] >= sp.base_stats["spa"] else "spa"
        evs = {off: 252, rng.choice(("spe", "hp")): 252}
        team.append(
            PokemonSet(
                species=name,
                moves=fill_moves(name, rng, ruleset=ruleset),
                level=level,
                evs={**{k: 0 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
                     **evs},
                ability=rng.choice(sp.abilities) if sp.abilities else None,
            )
        )
    return team


def sample_opponent(
    estimator: StatEstimator,
    rng: random.Random,
    revealed_moves: list[str] | None = None,
) -> PokemonSet:
    """One concrete opponent: posterior build + plausible moveset."""
    build = sample_build(estimator, rng)
    moves = fill_moves(
        estimator.species, rng, revealed_moves, ruleset=estimator.ruleset
    )
    return PokemonSet(
        species=build.species,
        moves=moves,
        level=build.level,
        nature=build.nature,
        evs=dict(build.evs),
        ivs=dict(build.ivs),
        item=build.item,
        ability=build.ability,
        status=build.status,
    )
