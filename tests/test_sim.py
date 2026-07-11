"""Battle engine tests: determinism, ordering, KO/win logic, baselines,
and determinization from the estimator posterior."""

import random

import pytest

from pokechamp.data.loader import DATA_DIR
from pokechamp.estimator import StatEstimator
from pokechamp.schemas import FieldState, PokemonSet, stat_table
from pokechamp.sim import (
    Action,
    Battle,
    MaxDamageAgent,
    RandomAgent,
    evaluate,
    fill_moves,
    play_game,
    sample_opponent,
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


def test_seeded_battles_are_reproducible():
    def run():
        b = Battle([chomp()], [azu()], seed=123)
        while not b.is_over:
            b.step(Action("move", 0), Action("move", 0))
        return b.winner(), [p.hp for p in b.sides[0].team], b.turn

    assert run() == run()


def test_faster_pokemon_moves_first():
    b = Battle([chomp()], [azu()], seed=1)
    b.sides[1].active(0).hp = 1  # Azumarill at 1 HP; Garchomp far faster
    b.step(Action("move", 0), Action("move", 2))
    assert b.sides[1].active(0).fainted
    assert b.sides[0].active(0).hp == b.sides[0].active(0).max_hp  # never got hit


def test_priority_beats_speed():
    b = Battle([chomp()], [azu()], seed=1)
    b.sides[0].active(0).hp = 1  # Garchomp at 1 HP
    # Aqua Jet (+1) from the much slower Azumarill still goes first
    b.step(Action("move", 0), Action("move", 1))
    assert b.sides[0].active(0).fainted
    assert b.sides[1].active(0).hp == b.sides[1].active(0).max_hp


def test_trick_room_flips_order():
    b = Battle(
        [chomp()], [azu()], seed=1, field=FieldState(trick_room=True)
    )
    b.sides[0].active(0).hp = 1
    b.step(Action("move", 0), Action("move", 2))  # both priority 0
    assert b.sides[0].active(0).fainted  # slow Azumarill moved first
    assert b.sides[1].active(0).hp == b.sides[1].active(0).max_hp


def test_switch_resolves_before_moves():
    b = Battle([chomp(), azu()], [azu()], seed=1)
    b.step(Action("switch", 1), Action("move", 2))
    assert b.sides[0].active(0).set.species == "Azumarill"
    # the hit landed on the switched-in Azumarill, not on Garchomp
    assert b.sides[0].team[0].hp == b.sides[0].team[0].max_hp
    assert b.sides[0].team[1].hp < b.sides[0].team[1].max_hp


def test_ground_immunity_in_sim():
    dnite = PokemonSet(species="Dragonite", moves=["Extreme Speed"])
    b = Battle([chomp()], [dnite], seed=1)
    b.step(Action("move", 0), Action("pass"))  # EQ into Flying
    assert b.sides[1].active(0).hp == b.sides[1].active(0).max_hp


def test_faint_forces_replacement_and_winner():
    b = Battle([chomp()], [azu(), azu()], seed=7)
    b.sides[1].team[0].hp = 1
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].team[0].fainted
    assert b.sides[1].active_index == 1  # auto-replaced
    assert not b.is_over
    b.sides[1].team[1].hp = 1
    b.step(Action("move", 0), Action("pass"))
    assert b.is_over
    assert b.winner() == 0


def test_max_damage_beats_random():
    team = [chomp(), azu()]
    res = evaluate(
        MaxDamageAgent(), RandomAgent(seed=99), team, team, games=40, seed=0
    )
    assert res.games == 40
    assert res.win_rate > 0.6


def test_play_game_runs_to_completion():
    w = play_game(RandomAgent(seed=1), RandomAgent(seed=2), [chomp()], [azu()], seed=5)
    assert w in (0, 1, None)


def test_fill_moves_respects_learnset_and_revealed():
    from pokechamp.data import load_ruleset

    rng = random.Random(0)
    moves = fill_moves("Azumarill", rng, revealed=["Play Rough"])
    assert moves[0] == "Play Rough"
    assert len(moves) == 4
    rs = load_ruleset("champions")
    learnset = rs.get_species("Azumarill").learnset
    from pokechamp.data.loader import norm

    for m in moves:
        assert norm(m) in learnset
        assert rs.get_move(m).base_power > 0 or m == "Play Rough"


def test_sample_opponent_respects_posterior():
    est = StatEstimator("Azumarill")
    # only Scarf builds outspeed 130 — posterior collapses to Scarf
    est.observe_speed(opponent_moved_first=True, reference_speed=130)
    rng = random.Random(0)
    for _ in range(10):
        opp = sample_opponent(est, rng, revealed_moves=["Aqua Jet"])
        assert opp.item == "Choice Scarf"
        assert "Aqua Jet" in opp.moves
        assert len(opp.moves) == 4
