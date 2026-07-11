"""Baseline agents (Phase 3): the recommender's opponents and sanity floor.

Agents return one Action per active slot (one for singles, two for doubles).
Any recommender that can't beat MaxDamageAgent isn't worth shipping; any
evaluation harness that can't detect RandomAgent losing to MaxDamageAgent
is broken.
"""

from __future__ import annotations

import random

from pokechamp.damage import calc_damage
from pokechamp.sim.battle import Action, Battle


class Agent:
    """Chooses one Action per active slot each turn."""

    def choose(self, battle: Battle, side_idx: int) -> list[Action]:  # pragma: no cover
        raise NotImplementedError


class RandomAgent(Agent):
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def choose(self, battle: Battle, side_idx: int) -> list[Action]:
        return [
            self.rng.choice(battle.legal_actions(side_idx, slot))
            for slot in range(battle.n_active)
        ]


def expected_damage_fraction(
    battle: Battle, side_idx: int, slot: int, action: Action
) -> float:
    """Mean damage of a move action as a fraction of its targets' remaining
    HP (summed over targets, spread-adjusted, accuracy-weighted, no crit)."""
    attacker = battle.sides[side_idx].active(slot)
    if attacker is None or action.kind != "move":
        return 0.0
    move_name = attacker.set.moves[action.index]
    move = battle.rs.get_move(move_name)
    if move.category == "Status" or move.base_power == 0:
        return 0.0
    if move.target in ("self", "allies", "side", "field"):
        return 0.0

    targets = battle._targets_for(side_idx, slot, move.target, action.target, move.type)
    targets = [t for t in targets if not t.fainted]
    if not targets:
        return 0.0
    is_spread = len(targets) >= 2
    acc = (move.accuracy or 100) / 100.0

    total = 0.0
    for defender in targets:
        # Earthquake hitting our own ally counts against the action
        friendly = defender in [
            battle.sides[side_idx].active(s)
            for s in battle.sides[side_idx].alive_slots()
        ]
        res = calc_damage(
            attacker.set, defender.set, move_name,
            field=battle._field_for(side_idx),
            spread=is_spread,
            attacker_boosts=attacker.boosts,
            defender_boosts=defender.boosts,
            attacker_hp_fraction=attacker.hp_fraction,
            ruleset=battle.ruleset,
        )
        mean = sum(res.rolls) / len(res.rolls) * res.hits
        frac = acc * min(1.0, mean / max(1, defender.hp))
        total += -frac if friendly else frac
    return total


class MaxDamageAgent(Agent):
    """Each slot clicks the (move, target) with the highest expected damage
    fraction (ally damage from allAdjacent moves counts negative)."""

    def choose(self, battle: Battle, side_idx: int) -> list[Action]:
        out = []
        for slot in range(battle.n_active):
            acts = battle.legal_actions(side_idx, slot)
            moves = [a for a in acts if a.kind == "move"]
            if not moves:
                out.append(acts[0])
                continue
            out.append(
                max(
                    moves,
                    key=lambda a: expected_damage_fraction(battle, side_idx, slot, a),
                )
            )
        return out


class EpsilonAgent(Agent):
    """A base agent that slips: each slot independently plays a uniform
    random legal action with probability epsilon. As the recommender's
    opponent model it hedges against non-greedy play (Protect, switches)
    instead of assuming a perfectly greedy foe."""

    def __init__(self, base: Agent | None = None, epsilon: float = 0.2,
                 seed: int | None = None):
        self.base = base or MaxDamageAgent()
        self.epsilon = epsilon
        self.rng = random.Random(seed)

    def choose(self, battle: Battle, side_idx: int) -> list[Action]:
        chosen = self.base.choose(battle, side_idx)
        return [
            self.rng.choice(battle.legal_actions(side_idx, slot))
            if self.rng.random() < self.epsilon
            else act
            for slot, act in enumerate(chosen)
        ]


class HeuristicAgent(Agent):
    """MaxDamage plus one idea per slot: if the active Pokemon can barely
    scratch anything (< min_fraction expected) and a benched teammate could
    do meaningfully better, switch to it."""

    def __init__(self, min_fraction: float = 0.08):
        self.min_fraction = min_fraction

    def choose(self, battle: Battle, side_idx: int) -> list[Action]:
        out = []
        side = battle.sides[side_idx]
        for slot in range(battle.n_active):
            acts = battle.legal_actions(side_idx, slot)
            moves = [a for a in acts if a.kind == "move"]
            if not moves:
                out.append(acts[0])
                continue
            best = max(
                moves,
                key=lambda a: expected_damage_fraction(battle, side_idx, slot, a),
            )
            best_frac = expected_damage_fraction(battle, side_idx, slot, best)
            if best_frac >= self.min_fraction:
                out.append(best)
                continue

            # walled: probe the bench for a clearly better matchup
            original = side.slots[slot]
            best_switch, best_switch_frac = None, best_frac
            for bench_idx in side.healthy_bench():
                side.slots[slot] = bench_idx  # peek, restored below
                frac = max(
                    (
                        expected_damage_fraction(battle, side_idx, slot, a)
                        for a in battle.legal_actions(side_idx, slot)
                        if a.kind == "move"
                    ),
                    default=0.0,
                )
                if frac > 2 * best_switch_frac:
                    best_switch, best_switch_frac = bench_idx, frac
            side.slots[slot] = original
            out.append(
                Action("switch", best_switch) if best_switch is not None else best
            )
        return out
