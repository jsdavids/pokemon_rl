"""Doubles battle mechanics: targeting, spread damage (Earthquake hits your
ally, Hyper Voice doesn't), retargeting after faints, and doubles evaluation."""

import random

import pytest

from pokechamp.data.loader import DATA_DIR
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.sim import (
    Action,
    Battle,
    HeuristicAgent,
    MaxDamageAgent,
    RandomAgent,
    evaluate,
)

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)


def chomp(moves=("Earthquake", "Dragon Claw")):
    return PokemonSet(
        species="Garchomp", nature="Jolly",
        evs={**stat_table(), "atk": 252, "spe": 252}, moves=list(moves),
    )


def azu(moves=("Play Rough", "Aqua Jet", "Liquidation")):
    return PokemonSet(
        species="Azumarill", nature="Adamant",
        evs={**stat_table(), "hp": 252, "atk": 252}, moves=list(moves),
    )


def tar(moves=("Crunch",)):
    return PokemonSet(
        species="Tyranitar", nature="Adamant",
        evs={**stat_table(), "hp": 252, "atk": 252}, moves=list(moves),
    )


def doubles(team0, team1, seed=1):
    return Battle(team0, team1, seed=seed, format="doubles")


def passes():
    return [Action("pass"), Action("pass")]


def test_two_active_per_side():
    b = doubles([chomp(), azu(), tar()], [azu(), tar()])
    assert b.sides[0].alive_slots() == [0, 1]
    assert b.sides[0].active(0).set.species == "Garchomp"
    assert b.sides[0].active(1).set.species == "Azumarill"
    assert b.sides[0].healthy_bench() == [2]


def test_earthquake_hits_both_foes_and_ally():
    b = doubles([chomp(), azu()], [azu(), tar()])
    b.step([Action("move", 0), Action("pass")], passes())
    foe0, foe1 = b.sides[1].active(0), b.sides[1].active(1)
    ally = b.sides[0].active(1)
    assert foe0.hp < foe0.max_hp
    assert foe1.hp < foe1.max_hp
    assert ally.hp < ally.max_hp  # allAdjacent clips our own Azumarill


def test_hyper_voice_spares_the_ally():
    voicer = PokemonSet(
        species="Gholdengo", nature="Modest",
        evs={**stat_table(), "spa": 252}, moves=["Hyper Voice"],
    )
    b = doubles([voicer, azu()], [azu(), tar()])
    b.step([Action("move", 0), Action("pass")], passes())
    assert b.sides[1].active(0).hp < b.sides[1].active(0).max_hp
    assert b.sides[1].active(1).hp < b.sides[1].active(1).max_hp
    assert b.sides[0].active(1).hp == b.sides[0].active(1).max_hp


class NoCritRandom(random.Random):
    """random() pinned mid-range: accuracy always passes, crits never land;
    damage-roll selection (choice) still follows the seed."""

    def random(self):
        return 0.5


def test_spread_reduction_vs_single_target():
    # same seed + no crits: EQ into two targets deals less per target,
    # because 0.75x spread puts even a max roll below a min single roll.
    # Garchomp fields no ally, or EQ would hit it and spread both cases.
    b2 = doubles([chomp()], [tar(), tar()], seed=42)
    b2.rng = NoCritRandom(42)
    b2.step([Action("move", 0), Action("pass")], passes())
    hit_two = b2.sides[1].active(0).max_hp - b2.sides[1].active(0).hp

    b1 = doubles([chomp()], [tar()], seed=42)
    b1.rng = NoCritRandom(42)
    b1.step([Action("move", 0), Action("pass")], passes())
    hit_one = b1.sides[1].active(0).max_hp - b1.sides[1].active(0).hp

    assert 0 < hit_two < hit_one  # 0.75x spread modifier applied


def test_single_target_move_targets_chosen_slot():
    b = doubles([chomp(), azu()], [tar(), tar()])
    b.step([Action("move", 1, target=1), Action("pass")], passes())
    assert b.sides[1].active(0).hp == b.sides[1].active(0).max_hp
    assert b.sides[1].active(1).hp < b.sides[1].active(1).max_hp


def test_retarget_when_chosen_target_fainted():
    b = doubles([chomp(), azu()], [tar(), tar()])
    b.sides[1].active(0).hp = 1
    # both attackers aim at slot 0; Garchomp KOs it, Azumarill retargets slot 1
    b.step(
        [Action("move", 1, target=0), Action("move", 2, target=0)],
        passes(),
    )
    assert b.sides[1].active(0).fainted
    assert b.sides[1].active(1).hp < b.sides[1].active(1).max_hp


def test_switch_conflict_one_bench_mon():
    b = doubles([chomp(), azu(), tar()], [azu(), tar()])
    # both slots try to switch to the single benched Tyranitar
    b.step([Action("switch", 2), Action("switch", 2)], passes())
    species = [b.sides[0].active(s).set.species for s in (0, 1)]
    assert species.count("Tyranitar") == 1


def test_fainted_slot_left_empty_when_bench_exhausted():
    # foes: Tyranitar + Azumarill, no bench (Dragon Claw is neutral into
    # Tyranitar; Azumarill would be immune — Fairy blanks Dragon)
    b = doubles([chomp(), azu()], [tar(), azu()])
    b.sides[1].active(0).hp = 1
    b.step([Action("move", 1, target=0), Action("pass")], passes())
    assert b.sides[1].active(0).fainted
    assert b.sides[1].alive_slots() == [1]
    assert not b.is_over


def test_legal_actions_enumerate_targets():
    b = doubles([chomp(), azu()], [azu(), tar()])
    acts = b.legal_actions(0, 0)
    # Dragon Claw (single-target) offers both foe slots; EQ offers one action
    dc = [a for a in acts if a.kind == "move" and a.index == 1]
    eq = [a for a in acts if a.kind == "move" and a.index == 0]
    assert {a.target for a in dc} == {0, 1}
    assert len(eq) == 1 and eq[0].target is None


def test_doubles_game_runs_and_agents_cope():
    team = [chomp(), azu(), tar(), chomp()]
    res = evaluate(
        MaxDamageAgent(), RandomAgent(seed=5), team, team,
        games=20, seed=0, format="doubles",
    )
    assert res.games == 20
    assert res.win_rate > 0.55


def test_heuristic_agent_runs_in_doubles():
    team = [chomp(), azu(), tar()]
    res = evaluate(
        HeuristicAgent(), RandomAgent(seed=3), team, team,
        games=10, seed=0, format="doubles",
    )
    assert res.wins + res.losses + res.draws == 10
