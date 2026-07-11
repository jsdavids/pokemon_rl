"""pokechamp: decision-support tools for Pokemon Champions.

Tools sharing one schema layer, exposed to a local LLM via MCP:

- pokechamp.damage      damage calculator (Phase 1)
- pokechamp.estimator   Bayesian stat/set estimator (Phase 2)
- pokechamp.sim         battle simulator: rollout engine, agents (Phase 3)
- pokechamp.recommender search/RL move recommender (Phase 4, stub)
- pokechamp.server      MCP server exposing the tools (Phase 5, stub)
"""

__version__ = "0.1.0"

from pokechamp.schemas import (
    BattleState,
    FieldState,
    PokemonSet,
    PokemonSnapshot,
    SideState,
    TeamSnapshot,
)
from pokechamp.damage import calc_damage, calc_damage_matrix, DamageResult
from pokechamp.estimator import StatEstimator

__all__ = [
    "BattleState",
    "FieldState",
    "PokemonSet",
    "PokemonSnapshot",
    "SideState",
    "TeamSnapshot",
    "calc_damage",
    "calc_damage_matrix",
    "DamageResult",
    "StatEstimator",
]
