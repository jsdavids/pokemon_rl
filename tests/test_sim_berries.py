"""Berries + Unnerve denial, Destiny Bond, Counter/Mirror Coat, Wish,
Heal Bell."""

import pytest

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


def chomp(moves=("Earthquake", "Dragon Claw"), **kw):
    return mon("Garchomp", moves, "Jolly", {"atk": 252, "spe": 252}, **kw)


# ---------------------------------------------------------------- berries ---

def test_sitrus_triggers_once_at_half():
    holder = mon("Azumarill", ["Aqua Jet"], evs={"hp": 252}, item="Sitrus Berry")
    b = Battle([holder], [chomp(("Crunch",))], seed=1)
    azu = b.sides[0].active(0)
    azu.hp = azu.max_hp // 2 + 30  # one Dragon Claw crosses the threshold
    b.step(Action("pass"), Action("move", 0))
    # berry ate: healed 1/4 above where the hit left it, and it's gone
    assert azu.set.item is None
    assert azu.hp > azu.max_hp // 2 - 20
    hp_now = azu.hp
    b.step(Action("pass"), Action("move", 0))
    assert azu.hp < hp_now  # no second berry


def test_unnerve_denies_the_berry():
    holder = mon("Azumarill", ["Aqua Jet"], evs={"hp": 252}, item="Sitrus Berry")
    unnerver = chomp(("Crunch",), ability="Unnerve")
    b = Battle([holder], [unnerver], seed=1)
    azu = b.sides[0].active(0)
    azu.hp = azu.max_hp // 2 + 10
    hp_before = azu.hp
    b.step(Action("pass"), Action("move", 0))
    assert azu.set.item == "Sitrus Berry"  # uneaten
    assert azu.hp < hp_before  # took the hit with no heal


def test_lum_berry_cures_status_instantly():
    holder = mon("Azumarill", ["Aqua Jet"], item="Lum Berry")
    b = Battle([mon("Gholdengo", ["Thunder Wave"])], [holder], seed=1)
    b.step(Action("move", 0), Action("pass"))
    v = b.sides[1].active(0)
    assert v.status is None
    assert v.set.item is None  # consumed


def test_resist_berry_halves_one_super_effective_hit():
    holder = mon("Tyranitar", ["Crunch"], evs={"hp": 252}, item="Chople Berry")
    attacker = chomp(("Close Combat",))
    b = Battle([attacker], [holder], seed=6)
    b.step(Action("move", 0), Action("pass"))
    tar = b.sides[1].active(0)
    first_hit = tar.max_hp - tar.hp
    assert tar.set.item is None  # eaten

    b2 = Battle([attacker], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=6)
    b2.step(Action("move", 0), Action("pass"))
    unberried = b2.sides[1].active(0).max_hp - b2.sides[1].active(0).hp
    assert first_hit < unberried  # the berry blunted it


# ------------------------------------------------------------ destiny bond ---

def test_destiny_bond_takes_the_attacker_down():
    bonder = mon("Gholdengo", ["Destiny Bond"], "Timid", {"spe": 252})
    killer = mon("Azumarill", ["Liquidation"], "Adamant", {"atk": 252})
    b = Battle([bonder], [killer], seed=1)
    b.sides[0].active(0).hp = 1
    b.step(Action("move", 0), Action("move", 0))  # DB first (faster), then KO
    assert b.sides[0].active(0).fainted
    assert b.sides[1].active(0).fainted  # dragged down


def test_destiny_bond_lapses_when_user_moves_again():
    bonder = mon("Gholdengo", ["Destiny Bond", "Recover"], "Timid", {"spe": 252})
    killer = mon("Azumarill", ["Liquidation"], "Adamant", {"atk": 252})
    b = Battle([bonder], [killer], seed=1)
    b.step(Action("move", 0), Action("pass"))  # bond up
    b.step(Action("move", 1), Action("pass"))  # Recover: bond lapses
    b.sides[0].active(0).hp = 1
    b.step(Action("pass"), Action("move", 0))
    assert b.sides[0].active(0).fainted
    assert not b.sides[1].active(0).fainted  # no bond anymore


# --------------------------------------------------------- counter family ---

def test_counter_returns_double_physical():
    countering = mon("Azumarill", ["Counter"], evs={"hp": 252})
    b = Battle([countering], [chomp(("Dragon Claw",))], seed=2)
    b.step(Action("move", 0), Action("move", 0))
    azu = b.sides[0].active(0)
    taken = azu.max_hp - azu.hp
    attacker = b.sides[1].active(0)
    assert attacker.max_hp - attacker.hp == 2 * taken


def test_mirror_coat_needs_special_damage():
    coater = mon("Azumarill", ["Mirror Coat"], evs={"hp": 252})
    b = Battle([coater], [chomp(("Dragon Claw",))], seed=2)
    b.step(Action("move", 0), Action("move", 0))  # physical hit: Coat fails
    assert b.sides[1].active(0).hp == b.sides[1].active(0).max_hp


# ------------------------------------------------------- wish & heal bell ---

def test_wish_heals_the_slot_next_turn_even_after_switch():
    wisher = mon("Politoed", ["Wish"], evs={"hp": 252})
    b = Battle([wisher, chomp()], [mon("Gholdengo", ["Recover"])], seed=1)
    b.sides[0].team[1].hp = 50
    b.step(Action("move", 0), Action("pass"))  # Wish cast
    b.step(Action("switch", 1), Action("pass"))  # Garchomp in; Wish lands
    healed = b.sides[0].active(0)
    assert healed.set.species == "Garchomp"
    assert healed.hp == 50 + b.sides[0].team[0].max_hp // 2
    assert not b.sides[0].wishes  # spent


def test_heal_bell_cures_the_whole_team():
    beller = mon("Politoed", ["Heal Bell"], evs={"hp": 252})
    b = Battle([beller, chomp(), mon("Azumarill", ["Aqua Jet"])], [mon("Gholdengo", ["Recover"])], seed=1)
    b.sides[0].team[0].status = "par"
    b.sides[0].team[1].status = "brn"
    b.sides[0].team[2].status = "tox"
    b.step(Action("move", 0), Action("pass"))
    assert all(p.status is None for p in b.sides[0].team)


# ---------------------------------------------------------------- seeding ---

def test_snapshot_carries_wishes_bond_and_eaten_berries():
    wisher = mon("Politoed", ["Wish"], evs={"hp": 252}, item="Sitrus Berry")
    b = Battle([wisher], [chomp(("Dragon Claw",))], seed=1)
    b.step(Action("move", 0), Action("move", 0))
    b.sides[0].active(0).destiny_bond = True
    b.sides[0].active(0).set.item = None  # pretend the berry got eaten

    snap = b.snapshot()
    b2 = Battle.from_state(snap, seed=3)
    assert b2.sides[0].wishes == b.sides[0].wishes
    assert b2.sides[0].active(0).destiny_bond
    assert b2.sides[0].active(0).set.item is None
