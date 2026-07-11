"""Lead assist: which Pokemon to bring to a matchup, and who leads.

Team preview decision support: enumerate my (bring, leads) options, play
each against a shared set of sampled opponent scenarios, rank by win rate.
Opponent uncertainty is handled the recommender's way — every scenario
fixes the opponent's bring/leads/builds and battle seed, and EVERY option
faces the same scenarios (common random numbers), so options differ only
by my choice, never by luck of the draw.

Opponent entries may be concrete PokemonSets (a known/hypothesized team)
or StatEstimators (scouted — builds are drawn from the posterior per
scenario, movesets filled from revealed moves).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import combinations

from pokechamp.estimator import StatEstimator
from pokechamp.schemas import PokemonSet
from pokechamp.sim import Agent, Battle, MaxDamageAgent
from pokechamp.sim.determinize import sample_opponent

DEFAULTS = {"singles": (3, 1), "doubles": (4, 2)}  # (bring, leads)


@dataclass
class LeadOption:
    bring: tuple[int, ...]  # indices into my team, leads first
    leads: tuple[int, ...]
    win_rate: float
    species: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        lead = "+".join(self.species[: len(self.leads)])
        rest = "/".join(self.species[len(self.leads):])
        return f"<lead {lead} bring {rest}: {self.win_rate:.0%}>"


@dataclass
class LeadPlan:
    ranked: list[LeadOption]  # best first
    games_per_option: int

    @property
    def best(self) -> LeadOption:
        return self.ranked[0]


def _ordered(team: list[PokemonSet], bring: tuple[int, ...],
             leads: tuple[int, ...]) -> list[PokemonSet]:
    """Bring subset as a battle team: leads first, bench in team order."""
    bench = [i for i in bring if i not in leads]
    return [team[i] for i in (*leads, *bench)]


def recommend_leads(
    my_team: list[PokemonSet],
    opp_team: list[PokemonSet | StatEstimator],
    format: str = "singles",
    bring: int | None = None,
    n_leads: int | None = None,
    games: int = 8,
    seed: int = 0,
    ruleset: str = "champions",
    agent: Agent | None = None,
    opp_revealed_moves: dict[int, list[str]] | None = None,
) -> LeadPlan:
    """Rank every (bring, leads) choice for my team against this opponent.

    `games` scenarios are sampled once (opponent builds, their bring/leads,
    battle seed) and reused for every option. Battles are played with
    `agent` on both sides (MaxDamage by default — fast and honest enough
    to expose matchup structure).
    """
    default_bring, default_leads = DEFAULTS[format]
    bring = min(bring or default_bring, len(my_team))
    n_leads = min(n_leads or default_leads, bring)
    opp_bring = min(default_bring, len(opp_team))
    rng = random.Random(seed)
    pilot = agent or MaxDamageAgent()

    def opp_set(i: int) -> PokemonSet:
        entry = opp_team[i]
        if isinstance(entry, StatEstimator):
            return sample_opponent(
                entry, rng, (opp_revealed_moves or {}).get(i)
            )
        return entry

    scenarios = []
    for _ in range(games):
        choice = rng.sample(range(len(opp_team)), opp_bring)
        leads = tuple(rng.sample(choice, n_leads))
        order = (*leads, *[i for i in choice if i not in leads])
        scenarios.append(
            ([opp_set(i) for i in order], rng.randrange(1 << 30))
        )

    options = [
        (combo, leads)
        for combo in combinations(range(len(my_team)), bring)
        for leads in combinations(combo, n_leads)
    ]
    ranked = []
    for combo, leads in options:
        mine = _ordered(my_team, combo, leads)
        score = 0.0
        for opp_sets, game_seed in scenarios:
            b = Battle(
                mine, opp_sets,
                ruleset=ruleset, format=format, seed=game_seed,
            )
            while not b.is_over:
                b.step(pilot.choose(b, 0), pilot.choose(b, 1))
            w = b.winner()
            score += 0.5 if w is None else (1.0 if w == 0 else 0.0)
        ranked.append(LeadOption(
            bring=(*leads, *[i for i in combo if i not in leads]),
            leads=leads,
            win_rate=score / games,
            species=[p.species for p in mine],
        ))
    ranked.sort(key=lambda o: o.win_rate, reverse=True)
    return LeadPlan(ranked=ranked, games_per_option=games)
