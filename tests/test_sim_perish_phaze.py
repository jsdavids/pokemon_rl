"""Weight/HP-scaled moves in the sim, Perish Song, Soundproof, Disable
(incl. the Disable+Encore struggle-lock), and phazing."""

import pytest

from pokechamp.damage import calc_damage
from pokechamp.data.loader import DATA_DIR
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.sim import Action, Battle

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)


def mon(species, moves, nature="Serious", evs=None, item=None, ability=None):
    return PokemonSet(
        species=species, nature=nature, evs={**stat_table(), **(evs or {})},
        moves=list(moves), item=item, ability=ability,
    )


def chomp(moves=("Earthquake", "Dragon Claw")):
    return mon("Garchomp", moves, "Jolly", {"atk": 252, "spe": 252})


def azu(moves=("Play Rough", "Aqua Jet", "Liquidation")):
    return mon("Azumarill", moves, "Adamant", {"hp": 252, "atk": 252})


# --------------------------------------------------- weight & HP scaling ---

def test_grass_knot_scales_with_champions_weights():
    knotter = mon("Venusaur", ["Grass Knot"], "Modest", {"spa": 252})
    heavy = calc_damage(knotter, mon("Snorlax", ["Tackle"]), "Grass Knot", ruleset="champions")
    light = calc_damage(knotter, mon("Ditto", ["Transform"]), "Grass Knot", ruleset="champions")
    assert any("120" in n for n in heavy.notes)  # Snorlax 460 kg
    assert any("BP 20" in n for n in light.notes)  # Ditto 4 kg


def test_heavy_slam_ratio_in_champions():
    slammer = mon("Snorlax", ["Heavy Slam"], "Adamant", {"atk": 252})  # 460 kg
    res = calc_damage(slammer, mon("Ditto", ["Transform"]), "Heavy Slam", ruleset="champions")
    assert any("BP 120" in n for n in res.notes)  # 460/4: max tier


def test_eruption_weakens_in_sim_as_user_takes_damage():
    erupter = mon("Tyranitar", ["Eruption"], "Modest", {"spa": 252})
    target = mon("Corviknight", ["Iron Head"], evs={"hp": 252})

    b = Battle([erupter], [target], seed=3)
    b.step(Action("move", 0), Action("pass"))
    full_dmg = b.sides[1].active(0).max_hp - b.sides[1].active(0).hp

    b2 = Battle([erupter], [target], seed=3)
    b2.sides[0].active(0).hp = b2.sides[0].active(0).max_hp // 10
    b2.step(Action("move", 0), Action("pass"))
    weak_dmg = b2.sides[1].active(0).max_hp - b2.sides[1].active(0).hp
    assert 0 < weak_dmg < full_dmg / 3  # ~15 BP vs 150 BP


# ------------------------------------------------------------ perish song ---

def test_perish_song_faints_both_hearers_in_three_turns():
    singer = mon("Politoed", ["Perish Song"], evs={"hp": 252})
    b = Battle([singer], [azu(("Recover",))], seed=1)
    b.step(Action("move", 0), Action("pass"))
    s, v = b.sides[0].active(0), b.sides[1].active(0)
    assert s.perish_count == 3 and v.perish_count == 3  # song turn elapsed
    b.step(Action("pass"), Action("pass"))
    b.step(Action("pass"), Action("pass"))
    assert not s.fainted and not v.fainted
    b.step(Action("pass"), Action("pass"))  # count hits 0
    assert s.fainted and v.fainted


def test_switching_escapes_perish_song():
    singer = mon("Politoed", ["Perish Song"], evs={"hp": 252})
    b = Battle([singer, azu()], [azu(("Recover",)), chomp()], seed=1)
    b.step(Action("move", 0), Action("pass"))
    b.step(Action("switch", 1), Action("switch", 1))  # both escape
    b.step(Action("pass"), Action("pass"))
    b.step(Action("pass"), Action("pass"))
    assert all(not p.fainted for side in b.sides for p in side.team)


def test_soundproof_ignores_the_song_and_hyper_voice():
    singer = mon("Politoed", ["Perish Song"], evs={"hp": 252})
    deaf = mon("Snorlax", ["Tackle"], ability="Soundproof")
    b = Battle([singer], [deaf], seed=1)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).perish_count == 0
    assert b.sides[0].active(0).perish_count == 3  # singer still hears it

    voicer = mon("Gholdengo", ["Hyper Voice"], "Modest", {"spa": 252})
    b2 = Battle([voicer], [deaf], seed=1)
    b2.step(Action("move", 0), Action("pass"))
    assert b2.sides[1].active(0).hp == b2.sides[1].active(0).max_hp


# ---------------------------------------------------------------- disable ---

def test_disable_blocks_last_move():
    disabler = mon("Gholdengo", ["Disable"], "Timid", {"spe": 252})
    victim = mon("Azumarill", ["Aqua Jet", "Liquidation"])
    b = Battle([disabler], [victim], seed=1)
    b.step(Action("pass"), Action("move", 0))  # victim reveals Aqua Jet
    b.step(Action("move", 0), Action("pass"))  # Disable it
    v = b.sides[1].active(0)
    assert v.disabled_index == 0 and v.disabled_turns > 0
    legal = [a.index for a in b.legal_actions(1, 0) if a.kind == "move"]
    assert legal == [1]


def test_disable_encore_forces_struggle():
    # the classic lock: Encore into move X, then Disable move X
    victim = mon("Azumarill", ["Aqua Jet", "Liquidation"])
    b = Battle([mon("Gholdengo", ["Encore", "Disable"], "Timid", {"spe": 252})], [victim], seed=1)
    b.step(Action("pass"), Action("move", 0))  # Aqua Jet used
    b.step(Action("move", 0), Action("pass"))  # Encore locks Aqua Jet
    b.step(Action("move", 1), Action("pass"))  # Disable turns it off
    v = b.sides[1].active(0)
    assert v.encore_index == 0 and v.disabled_index == 0
    legal = b.legal_actions(1, 0)
    assert legal == [Action("struggle")]  # nothing left but Struggle


# ----------------------------------------------------------------- phazing ---

def test_whirlwind_drags_target_out():
    phazer = mon("Corviknight", ["Whirlwind"], evs={"hp": 252})
    b = Battle([phazer], [azu(("Recover",)), chomp(), mon("Tyranitar", ["Crunch"])], seed=5)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).set.species != "Azumarill"
    assert not b.sides[1].team[0].fainted  # dragged out, not KO'd


def test_dragon_tail_damages_then_phazes():
    tailer = chomp(("Dragon Tail",))
    b = Battle([tailer], [mon("Tyranitar", ["Crunch"], evs={"hp": 252}), azu()], seed=2)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).set.species == "Azumarill"  # dragged
    assert b.sides[1].team[0].hp < b.sides[1].team[0].max_hp  # and hurt


def test_phazed_replacement_takes_hazards():
    phazer = mon("Corviknight", ["Stealth Rock", "Whirlwind"], evs={"hp": 252})
    b = Battle([phazer], [azu(("Recover",)), chomp()], seed=1)
    b.step(Action("move", 0), Action("pass"))  # rocks up
    b.step(Action("move", 1), Action("pass"))  # whirlwind
    incoming = b.sides[1].active(0)
    assert incoming.set.species == "Garchomp"
    assert incoming.hp < incoming.max_hp  # rocks on the way in


def test_boosts_are_lost_when_phazed():
    phazer = mon("Corviknight", ["Whirlwind"], evs={"hp": 252})
    sweeper = mon("Garchomp", ["Swords Dance", "Earthquake"], "Jolly", {"spe": 252})
    b = Battle([phazer], [sweeper, azu()], seed=1)
    b.step(Action("pass"), Action("move", 0))  # +2
    assert b.sides[1].active(0).boosts["atk"] == 2
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].team[0].boosts["atk"] == 0  # setup erased
