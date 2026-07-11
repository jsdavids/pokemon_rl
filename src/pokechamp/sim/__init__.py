"""Phase 3: simulation infrastructure.

A fast, seeded singles battle engine on the champions ruleset (the
recommender's rollout backend), baseline agents, determinization from the
estimator's posterior, and an evaluation harness. A Showdown/poke-env bridge
is planned as a later cross-check of battle mechanics, not a rollout path.
"""

from pokechamp.sim.agents import (
    Agent,
    EpsilonAgent,
    HeuristicAgent,
    MaxDamageAgent,
    RandomAgent,
)
from pokechamp.sim.battle import Action, Battle, BattlePokemon, Side
from pokechamp.sim.determinize import (
    fill_moves,
    random_team,
    sample_build,
    sample_opponent,
)
from pokechamp.sim.evaluate import EvalResult, evaluate, play_game

__all__ = [
    "Action", "Agent", "Battle", "BattlePokemon", "EpsilonAgent",
    "EvalResult", "HeuristicAgent", "MaxDamageAgent", "RandomAgent", "Side",
    "evaluate", "fill_moves", "play_game", "random_team", "sample_build",
    "sample_opponent",
]
