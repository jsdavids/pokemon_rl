"""Imposter/Transform, Baton Pass, Taunt/Encore/Choice locking, confusion."""

import random

import pytest

from pokechamp.data.loader import DATA_DIR
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.sim import Action, Battle

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)


class PinnedRandom(random.Random):
    def __init__(self, seed=0, value=0.5):
        super().__init__(seed)
        self.value = value

    def random(self):
        return self.value


def mon(species, moves, nature="Serious", evs=None, item=None, ability=None):
    return PokemonSet(
        species=species, nature=nature, evs={**stat_table(), **(evs or {})},
        moves=list(moves), item=item, ability=ability,
    )


def chomp(moves=("Earthquake", "Dragon Claw")):
    return mon("Garchomp", moves, "Jolly", {"atk": 252, "spe": 252})


def azu(moves=("Play Rough", "Aqua Jet", "Liquidation")):
    return mon("Azumarill", moves, "Adamant", {"hp": 252, "atk": 252})


# ------------------------------------------------------ imposter/transform ---

def test_imposter_copies_form_but_not_hp():
    ditto = mon("Ditto", ["Transform"], evs={"hp": 252}, ability="Imposter")
    b = Battle([ditto], [chomp()], seed=1)
    d = b.sides[0].active(0)
    target = b.sides[1].active(0)
    assert d.transformed
    assert d.types == target.types
    assert d.stats["atk"] == target.stats["atk"]
    assert d.set.moves == target.set.moves
    assert d.pp == [5] * len(target.set.moves)
    assert d.max_hp != target.max_hp  # HP stays Ditto's own
    # copied Earthquake actually works
    b.step(Action("move", 0), Action("pass"))
    assert target.hp < target.max_hp


def test_transform_reverts_on_switch():
    ditto = mon("Ditto", ["Transform"], evs={"hp": 252}, ability="Imposter")
    b = Battle([ditto, azu()], [chomp()], seed=1)
    d = b.sides[0].team[0]
    assert d.transformed
    copied_atk = d.stats["atk"]
    b.step(Action("switch", 1), Action("pass"))
    assert not d.transformed
    assert d.stats["atk"] != copied_atk
    assert d.set.moves == ["Transform"]
    assert d.set.ability == "Imposter"


def test_transform_move_copies_target():
    changer = mon("Azumarill", ["Transform"], evs={"hp": 252})
    b = Battle([changer], [chomp()], seed=1)
    user = b.sides[0].active(0)
    assert not user.transformed
    b.step(Action("move", 0), Action("pass"))
    assert user.transformed
    assert user.types == b.sides[1].active(0).types


# -------------------------------------------------------------- baton pass ---

def test_baton_pass_hands_over_boosts_and_sub():
    passer = mon("Gholdengo", ["Swords Dance", "Substitute", "Baton Pass"], evs={"hp": 252})
    b = Battle([passer, chomp()], [azu(("Aqua Jet",))], seed=1)
    b.step(Action("move", 0), Action("pass"))  # +2 atk
    b.step(Action("move", 1), Action("pass"))  # sub up
    sub_before = b.sides[0].team[0].sub_hp
    assert sub_before > 0
    b.step(Action("move", 2), Action("pass"))  # pass!
    incoming = b.sides[0].active(0)
    assert incoming.set.species == "Garchomp"
    assert incoming.boosts["atk"] == 2
    assert incoming.sub_hp == sub_before
    # the passer lost its state
    assert b.sides[0].team[0].boosts["atk"] == 0
    assert b.sides[0].team[0].sub_hp == 0


def test_baton_pass_recipient_takes_hazards():
    passer = mon("Gholdengo", ["Baton Pass"], evs={"hp": 252})
    b = Battle([passer, chomp()], [azu()], seed=1)
    b.sides[0].hazards["stealth_rock"] = True
    b.step(Action("move", 0), Action("pass"))
    incoming = b.sides[0].active(0)
    assert incoming.hp < incoming.max_hp  # rocks bit the recipient


# ------------------------------------------------------------------- locks ---

def test_taunt_blocks_status_moves_for_three_turns():
    taunter = mon("Gholdengo", ["Taunt"], evs={"spa": 252}, nature="Timid")
    victim = mon("Azumarill", ["Recover", "Liquidation"])
    b = Battle([taunter], [victim], seed=1)
    b.step(Action("move", 0), Action("pass"))
    v = b.sides[1].active(0)
    assert v.taunt_turns == 2  # 3 applied, 1 elapsed at end of turn
    legal = [a for a in b.legal_actions(1, 0) if a.kind == "move"]
    assert [a.index for a in legal] == [1]  # only the attack
    # Recover also fails if forced through the engine
    v.hp = 50
    b.step(Action("pass"), Action("move", 0))
    assert v.hp == 50
    b.step(Action("pass"), Action("pass"))
    assert v.taunt_turns == 0  # expired
    assert any(a.kind == "move" and a.index == 0 for a in b.legal_actions(1, 0))


def test_encore_locks_last_move():
    encorer = mon("Azumarill", ["Encore"])
    victim = mon("Garchomp", ["Swords Dance", "Earthquake"], "Jolly", {"spe": 252})
    b = Battle([encorer], [victim], seed=1)
    # victim (faster) uses Swords Dance, then the Encore lands
    b.step(Action("move", 0), Action("move", 0))
    v = b.sides[1].active(0)
    assert v.encore_turns == 2 and v.encore_index == 0
    legal = [a for a in b.legal_actions(1, 0) if a.kind == "move"]
    assert [a.index for a in legal] == [0]  # locked into Swords Dance


def test_choice_scarf_locks_until_switch():
    scarfer = mon(
        "Garchomp", ["Earthquake", "Dragon Claw"], "Jolly", {"spe": 252},
        item="Choice Scarf",
    )
    b = Battle([scarfer, azu()], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=1)
    b.step(Action("move", 1), Action("pass"))
    user = b.sides[0].active(0)
    assert user.choice_lock == 1
    legal = [a for a in b.legal_actions(0, 0) if a.kind == "move"]
    assert [a.index for a in legal] == [1]
    b.step(Action("switch", 1), Action("pass"))
    assert b.sides[0].team[0].choice_lock is None  # unlocked by switching


# --------------------------------------------------------------- confusion ---

def test_confuse_ray_and_self_hit():
    rayer = mon("Gholdengo", ["Confuse Ray"], nature="Timid", evs={"spe": 252})
    victim = mon("Azumarill", ["Liquidation"], evs={"hp": 252})
    b = Battle([rayer], [victim], seed=1)
    b.step(Action("move", 0), Action("pass"))
    v = b.sides[1].active(0)
    assert v.confusion_turns >= 1
    # pin rng below 1/3: the next action becomes a self-hit
    b.rng = PinnedRandom(0, value=0.2)
    v.confusion_turns = 3
    attacker_side_hp = b.sides[0].active(0).hp
    b.step(Action("pass"), Action("move", 0))
    assert v.hp < v.max_hp  # hurt itself
    assert b.sides[0].active(0).hp == attacker_side_hp  # move never came out


def test_confusion_wears_off():
    rayer = mon("Gholdengo", ["Confuse Ray"])
    victim = mon("Azumarill", ["Recover"])
    b = Battle([rayer], [victim], seed=2)
    b.rng = PinnedRandom(2, value=0.9)  # never self-hits, always acts
    b.step(Action("move", 0), Action("pass"))
    v = b.sides[1].active(0)
    start = v.confusion_turns
    assert start >= 1
    for _ in range(start):
        b.step(Action("pass"), Action("move", 0))
    assert v.confusion_turns == 0


def test_swagger_boosts_and_confuses():
    swaggerer = mon("Gholdengo", ["Swagger"])
    b = Battle([swaggerer], [azu()], seed=1)
    b.rng = PinnedRandom(1, value=0.5)  # 50 < 85 acc: hits
    b.step(Action("move", 0), Action("pass"))
    v = b.sides[1].active(0)
    assert v.boosts["atk"] == 2
    assert v.confusion_turns > 0


# ---------------------------------------------------------------- seeding ---

def test_snapshot_preserves_volatiles_and_transform():
    ditto = mon("Ditto", ["Transform"], evs={"hp": 252}, ability="Imposter")
    b = Battle([ditto], [chomp()], seed=1)
    d = b.sides[0].active(0)
    b.sides[1].active(0).taunt_turns = 2
    b.sides[1].active(0).confusion_turns = 3

    snap = b.snapshot()
    b2 = Battle.from_state(snap, seed=7)
    d2 = b2.sides[0].active(0)
    assert d2.transformed
    assert d2.stats["atk"] == d.stats["atk"]
    assert d2.types == d.types
    assert d2.set.moves == d.set.moves
    assert b2.sides[1].active(0).taunt_turns == 2
    assert b2.sides[1].active(0).confusion_turns == 3
    # and the transformed copy still reverts cleanly on switch in rollouts
    d2.reset_on_switch()
    assert d2.set.moves == ["Transform"]
