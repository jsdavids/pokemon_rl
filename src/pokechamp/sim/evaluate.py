"""Evaluation harness (Phase 3): seeded matchups, win rates vs baselines."""

from __future__ import annotations

from dataclasses import dataclass

from pokechamp.schemas import FieldState, PokemonSet
from pokechamp.sim.agents import Agent
from pokechamp.sim.battle import Battle


def play_game(
    agent0: Agent,
    agent1: Agent,
    team0: list[PokemonSet],
    team1: list[PokemonSet],
    seed: int | None = None,
    ruleset: str = "champions",
    field: FieldState | None = None,
    format: str = "singles",
) -> int | None:
    """Play one battle; returns the winning side (0/1) or None for a draw."""
    battle = Battle(team0, team1, ruleset=ruleset, field=field, seed=seed, format=format)
    while not battle.is_over:
        battle.step(
            agent0.choose(battle, 0),
            agent1.choose(battle, 1),
        )
    return battle.winner()


@dataclass
class EvalResult:
    games: int
    wins: int
    losses: int
    draws: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    def __repr__(self) -> str:
        return (
            f"<{self.wins}W-{self.losses}L-{self.draws}D "
            f"({100 * self.win_rate:.0f}%)>"
        )


def evaluate(
    agent: Agent,
    opponent: Agent,
    team_a: list[PokemonSet],
    team_b: list[PokemonSet],
    games: int = 100,
    seed: int = 0,
    ruleset: str = "champions",
    format: str = "singles",
) -> EvalResult:
    """`agent` vs `opponent` over seeded games, alternating sides and teams
    each game so neither side order nor team strength biases the result."""
    wins = losses = draws = 0
    for g in range(games):
        if g % 2 == 0:
            w = play_game(agent, opponent, team_a, team_b, seed=seed + g,
                          ruleset=ruleset, format=format)
            mine = 0
        else:
            w = play_game(opponent, agent, team_a, team_b, seed=seed + g,
                          ruleset=ruleset, format=format)
            mine = 1
        if w is None:
            draws += 1
        elif w == mine:
            wins += 1
        else:
            losses += 1
    return EvalResult(games, wins, losses, draws)
