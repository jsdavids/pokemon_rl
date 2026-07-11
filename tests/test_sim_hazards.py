"""Hazards, terrain effects, PP/Struggle, and trapping."""

import pytest

from pokechamp.data.loader import DATA_DIR
from pokechamp.schemas import FieldState, PokemonSet, stat_table
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


SR_ON = {"stealth_rock": True, "spikes": 0, "toxic_spikes": 0, "sticky_web": False}


# ---------------------------------------------------------------- hazards ---

def test_stealth_rock_damage_scales_with_weakness():
    dnite = mon("Dragonite", ["Extreme Speed"])
    b = Battle([chomp(), dnite], [azu()], seed=1)
    b.sides[0].hazards.update(SR_ON)
    b.step(Action("switch", 1), Action("pass"))
    flyer = b.sides[0].active(0)
    assert flyer.max_hp - flyer.hp == flyer.max_hp * 2 // 8  # rock 2x -> 1/4

    b2 = Battle([mon("Dragonite", ["Extreme Speed"]), chomp()], [azu()], seed=1)
    b2.sides[0].hazards.update(SR_ON)
    b2.step(Action("switch", 1), Action("pass"))
    grounded = b2.sides[0].active(0)  # Garchomp: rock 0.5x -> 1/16
    assert grounded.max_hp - grounded.hp == int(grounded.max_hp * 0.5) // 8


def test_stealth_rock_set_by_move():
    b = Battle([mon("Gholdengo", ["Stealth Rock"])], [azu()], seed=1)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].hazards["stealth_rock"]
    assert not b.sides[0].hazards["stealth_rock"]


def test_spikes_hit_grounded_only():
    dnite = mon("Dragonite", ["Extreme Speed"])
    b = Battle([azu(), chomp(), dnite], [azu()], seed=1)
    b.sides[0].hazards["spikes"] = 3
    b.step(Action("switch", 1), Action("pass"))
    grounded = b.sides[0].active(0)
    assert grounded.max_hp - grounded.hp == grounded.max_hp // 4  # 3 layers
    b.step(Action("switch", 2), Action("pass"))
    flyer = b.sides[0].active(0)
    assert flyer.hp == flyer.max_hp  # Flying: immune


def test_toxic_spikes_poison_and_absorption():
    b = Battle([azu(), chomp()], [azu()], seed=1)
    b.sides[0].hazards["toxic_spikes"] = 2
    b.step(Action("switch", 1), Action("pass"))
    assert b.sides[0].active(0).status == "tox"  # two layers = toxic

    venu = mon("Venusaur", ["Tackle"])  # grounded Poison-type absorbs
    b2 = Battle([azu(), venu], [azu()], seed=1)
    b2.sides[0].hazards["toxic_spikes"] = 2
    b2.step(Action("switch", 1), Action("pass"))
    assert b2.sides[0].active(0).status is None
    assert b2.sides[0].hazards["toxic_spikes"] == 0


def test_sticky_web_drops_speed_on_entry():
    b = Battle([azu(), chomp()], [azu()], seed=1)
    b.sides[0].hazards["sticky_web"] = True
    b.step(Action("switch", 1), Action("pass"))
    assert b.sides[0].active(0).boosts["spe"] == -1


def test_defog_clears_hazards_and_foe_screens():
    defogger = mon("Dragonite", ["Defog"])
    b = Battle([defogger], [azu()], seed=1)
    b.sides[0].hazards.update(SR_ON)
    b.sides[1].hazards["spikes"] = 2
    b.sides[1].timers["reflect"] = 5
    b.sides[1].sync_conditions()
    b.step(Action("move", 0), Action("pass"))
    assert not b.sides[0].hazards["stealth_rock"]
    assert b.sides[1].hazards["spikes"] == 0
    assert not b.sides[1].conditions.reflect


def test_rapid_spin_clears_own_side():
    spinner = mon("Tyranitar", ["Rapid Spin"])
    b = Battle([spinner], [azu()], seed=1)
    b.sides[0].hazards.update(SR_ON)
    b.step(Action("move", 0), Action("pass"))
    assert not b.sides[0].hazards["stealth_rock"]
    assert b.sides[0].active(0).boosts["spe"] == 1  # gen9 spin boost


# ---------------------------------------------------------------- terrain ---

def test_grassy_terrain_heals_grounded_only():
    dnite = mon("Dragonite", ["Extreme Speed"])
    b = Battle(
        [chomp(), dnite], [azu()], seed=1, field=FieldState(terrain="grassy")
    )
    grounded = b.sides[0].active(0)
    grounded.hp = 100
    b.step(Action("pass"), Action("pass"))
    assert grounded.hp == 100 + grounded.max_hp // 16

    b.sides[0].slots[0] = 1
    flyer = b.sides[0].active(0)
    flyer.hp = 100
    b.step(Action("pass"), Action("pass"))
    assert flyer.hp == 100  # not grounded, no heal


def test_electric_terrain_blocks_sleep_on_grounded():
    sporer = mon("Azumarill", ["Spore"])
    b = Battle(
        [sporer], [chomp()], seed=1, field=FieldState(terrain="electric")
    )
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).status is None

    # a flyer isn't protected by terrain
    b2 = Battle(
        [sporer], [mon("Dragonite", ["Extreme Speed"])], seed=1,
        field=FieldState(terrain="electric"),
    )
    b2.step(Action("move", 0), Action("pass"))
    assert b2.sides[1].active(0).status == "slp"


def test_misty_terrain_blocks_all_status_on_grounded():
    waver = mon("Gholdengo", ["Thunder Wave"])
    b = Battle([waver], [azu()], seed=1, field=FieldState(terrain="misty"))
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).status is None


def test_psychic_terrain_blocks_priority_into_grounded():
    b = Battle(
        [azu(("Aqua Jet",))], [chomp()], seed=1,
        field=FieldState(terrain="psychic"),
    )
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).hp == b.sides[1].active(0).max_hp

    # non-priority move goes through
    b2 = Battle(
        [azu(("Liquidation",))], [chomp()], seed=1,
        field=FieldState(terrain="psychic"),
    )
    b2.step(Action("move", 0), Action("pass"))
    assert b2.sides[1].active(0).hp < b2.sides[1].active(0).max_hp


# ------------------------------------------------------------------- pp ---

def test_pp_decrements_and_gates_legality():
    b = Battle([azu(("Aqua Jet", "Liquidation"))], [chomp()], seed=1)
    user = b.sides[0].active(0)
    start = user.pp[0]
    b.step(Action("move", 0), Action("pass"))
    assert user.pp[0] == start - 1
    user.pp[0] = 0
    legal = b.legal_actions(0, 0)
    assert all(not (a.kind == "move" and a.index == 0) for a in legal)
    assert any(a.kind == "move" and a.index == 1 for a in legal)


def test_struggle_when_out_of_pp():
    b = Battle([azu(("Aqua Jet",))], [chomp()], seed=1)
    user = b.sides[0].active(0)
    user.pp = [0]
    legal = b.legal_actions(0, 0)
    assert legal == [Action("struggle")]
    foe = b.sides[1].active(0)
    b.step(Action("struggle"), Action("pass"))
    assert foe.hp < foe.max_hp  # struggle dealt damage
    assert user.hp == user.max_hp - user.max_hp // 4  # 1/4 recoil


# --------------------------------------------------------------- trapping ---

def test_shadow_tag_prevents_switching_except_ghosts():
    tagger = mon("Gholdengo", ["Shadow Ball"], ability="Shadow Tag")
    b = Battle([azu(), chomp()], [tagger], seed=1)
    assert all(a.kind != "switch" for a in b.legal_actions(0, 0))

    ghost = mon("Gholdengo", ["Shadow Ball"])
    b2 = Battle([ghost, mon("Gholdengo", ["Recover"])], [tagger], seed=1)
    assert any(a.kind == "switch" for a in b2.legal_actions(0, 0))  # ghost escapes


def test_arena_trap_grounded_only():
    trapper = mon("Tyranitar", ["Crunch"], ability="Arena Trap")
    b = Battle([chomp(), azu()], [trapper], seed=1)
    assert all(a.kind != "switch" for a in b.legal_actions(0, 0))
    b2 = Battle([mon("Dragonite", ["Extreme Speed"]), azu()], [trapper], seed=1)
    assert any(a.kind == "switch" for a in b2.legal_actions(0, 0))  # flyer free


def test_fire_spin_traps_and_chips():
    spinner = mon("Gholdengo", ["Fire Spin"])
    b = Battle([spinner], [azu(), chomp()], seed=3)
    b.step(Action("move", 0), Action("pass"))
    victim = b.sides[1].active(0)
    assert victim.trapped_turns > 0
    assert all(a.kind != "switch" for a in b.legal_actions(1, 0))
    hp_after_turn1 = victim.hp
    b.step(Action("pass"), Action("pass"))
    assert victim.hp == hp_after_turn1 - victim.max_hp // 8  # binding chip
    for _ in range(5):
        b.step(Action("pass"), Action("pass"))
    assert victim.trapped_turns == 0
    assert any(a.kind == "switch" for a in b.legal_actions(1, 0))  # freed


# ---------------------------------------------------------------- seeding ---

def test_snapshot_preserves_pp_and_hazards():
    b = Battle([azu(("Aqua Jet", "Liquidation"))], [chomp()], seed=1)
    b.sides[1].hazards["spikes"] = 2
    b.step(Action("move", 0), Action("move", 0))
    snap = b.snapshot()
    b2 = Battle.from_state(snap, seed=9)
    assert b2.sides[0].active(0).pp == b.sides[0].active(0).pp
    assert b2.sides[1].hazards["spikes"] == 2
