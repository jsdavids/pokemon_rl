"""Status moves, secondary effects, doubles support moves, and battle
seeding (snapshot round-trip)."""

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
    """random() pinned to a constant; everything else follows the seed."""

    def __init__(self, seed=0, value=0.5):
        super().__init__(seed)
        self.value = value

    def random(self):
        return self.value


def mon(species, moves, nature="Serious", evs=None, item=None):
    return PokemonSet(
        species=species, nature=nature,
        evs={**stat_table(), **(evs or {})}, moves=list(moves), item=item,
    )


def chomp(moves=("Earthquake", "Dragon Claw")):
    return mon("Garchomp", moves, "Jolly", {"atk": 252, "spe": 252})


def azu(moves=("Play Rough", "Aqua Jet", "Liquidation")):
    return mon("Azumarill", moves, "Adamant", {"hp": 252, "atk": 252})


def pinned(b, value=0.5, seed=0):
    b.rng = PinnedRandom(seed, value)
    return b


# ---------------------------------------------------------------- status ---

def test_swords_dance_boosts_and_amplifies():
    b = pinned(Battle([chomp(("Swords Dance", "Dragon Claw"))], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=3))
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[0].active(0).boosts["atk"] == 2

    tar0 = b.sides[1].active(0)
    hp_before = tar0.hp
    b.step(Action("move", 1), Action("pass"))
    boosted_hit = hp_before - tar0.hp

    b2 = pinned(Battle([chomp(("Swords Dance", "Dragon Claw"))], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=3))
    b2.step(Action("move", 1), Action("pass"))
    plain_hit = b2.sides[1].active(0).max_hp - b2.sides[1].active(0).hp
    assert boosted_hit > 1.8 * plain_hit  # +2 atk ~doubles damage


def test_spore_sleeps_and_grass_is_immune():
    sporer = mon("Azumarill", ["Spore"])
    b = Battle([sporer], [chomp()], seed=1)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).status == "slp"

    b2 = Battle([sporer], [mon("Venusaur", ["Tackle"])], seed=1)
    b2.step(Action("move", 0), Action("pass"))
    assert b2.sides[1].active(0).status is None  # powder vs grass fails


def test_sleeping_pokemon_wakes_within_three_turns():
    b = Battle([mon("Azumarill", ["Spore"])], [chomp()], seed=5)
    b.step(Action("move", 0), Action("pass"))
    victim = b.sides[1].active(0)
    assert victim.status == "slp"
    for _ in range(3):
        b.step(Action("pass"), Action("move", 0))
    assert victim.status is None  # slept at most 3 turns


def test_thunder_wave_paralyzes_but_not_ground():
    waver = mon("Gholdengo", ["Thunder Wave"])
    b = Battle([waver], [azu()], seed=1)
    spe_before = b.effective_speed(1)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).status == "par"
    assert b.effective_speed(1) == spe_before // 2

    b2 = Battle([waver], [chomp()], seed=1)
    b2.step(Action("move", 0), Action("pass"))
    assert b2.sides[1].active(0).status is None  # Ground blanks Thunder Wave


def test_burn_applies_and_chips():
    wisper = mon("Gholdengo", ["Will-O-Wisp"], evs={"hp": 252})
    b = pinned(Battle([wisper], [chomp()], seed=2), value=0.4)
    b.step(Action("move", 0), Action("pass"))
    victim = b.sides[1].active(0)
    assert victim.status == "brn"
    # end-of-turn chip: 1/16 max HP (damage halving is calculator-tested)
    assert victim.hp == victim.max_hp - victim.max_hp // 16


def test_toxic_chip_escalates():
    b = Battle([mon("Gholdengo", ["Toxic"])], [azu()], seed=1)
    victim = b.sides[1].active(0)
    b.step(Action("move", 0), Action("pass"))
    assert victim.status == "tox"
    hp1 = victim.hp
    chip1 = victim.max_hp - hp1
    b.step(Action("pass"), Action("pass"))
    chip2 = hp1 - victim.hp
    assert chip1 == victim.max_hp // 16  # 1/16, then 2/16, ...
    assert chip2 == (2 * victim.max_hp) // 16


def test_recover_heals_half():
    b = Battle([mon("Gholdengo", ["Recover"])], [chomp()], seed=1)
    healer = b.sides[0].active(0)
    healer.hp = 10
    b.step(Action("move", 0), Action("pass"))
    assert healer.hp - (healer.max_hp - healer.max_hp // 16) <= 10 + healer.max_hp // 2
    assert healer.hp >= 10 + healer.max_hp // 2 - healer.max_hp // 16


def test_protect_blocks_then_fails_when_spammed():
    # pinned random()=0.5: first Protect succeeds (p=1.0), second fails (p=1/3)
    b = pinned(Battle([mon("Gholdengo", ["Protect"])], [chomp(("Dragon Claw",))], seed=4))
    protector = b.sides[0].active(0)
    b.step(Action("move", 0), Action("move", 0))
    assert protector.hp == protector.max_hp  # blocked
    b.step(Action("move", 0), Action("move", 0))
    assert protector.hp < protector.max_hp  # consecutive protect failed


# ------------------------------------------------------------ secondaries ---

def test_icy_wind_drops_speed():
    b = pinned(Battle([mon("Gholdengo", ["Icy Wind"])], [azu()], seed=1))
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).boosts["spe"] == -1


def test_fake_out_flinches_and_is_first_turn_only():
    fast = mon("Garchomp", ["Fake Out", "Dragon Claw"], "Jolly", {"spe": 252})
    b = pinned(Battle([fast], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=1))
    b.step(Action("move", 0), Action("move", 0))
    tar0 = b.sides[1].active(0)
    assert tar0.hp < tar0.max_hp  # Fake Out landed
    assert b.sides[0].active(0).hp == b.sides[0].active(0).max_hp  # foe flinched
    # after the first turn on field, Fake Out is no longer offered
    acts = b.legal_actions(0, 0)
    assert all(not (a.kind == "move" and a.index == 0) for a in acts)


def test_close_combat_self_drops():
    b = pinned(Battle([mon("Garchomp", ["Close Combat"], "Jolly", {"atk": 252})], [azu()], seed=1))
    b.step(Action("move", 0), Action("pass"))
    user = b.sides[0].active(0)
    assert user.boosts["def"] == -1 and user.boosts["spd"] == -1


def test_scald_can_burn():
    b = pinned(Battle([mon("Azumarill", ["Scald"])], [chomp()], seed=1), value=0.1)
    b.step(Action("move", 0), Action("pass"))  # 0.1*100 < 30% rate -> burns
    assert b.sides[1].active(0).status == "brn"


# ------------------------------------------------- field & side conditions ---

def test_tailwind_doubles_speed_and_expires():
    b = Battle([azu(("Tailwind", "Aqua Jet"))], [chomp()], seed=1)
    base = b.effective_speed(0)
    b.step(Action("move", 0), Action("pass"))
    assert b.effective_speed(0) == base * 2
    for _ in range(3):  # 4-turn duration, one turn already elapsed
        b.step(Action("pass"), Action("pass"))
    assert b.effective_speed(0) == base


def test_trick_room_set_by_move_and_expires():
    b = Battle([azu(("Trick Room",))], [chomp()], seed=1)
    b.step(Action("move", 0), Action("pass"))
    assert b.field.trick_room
    for _ in range(4):
        b.step(Action("pass"), Action("pass"))
    assert not b.field.trick_room


def test_reflect_halves_physical():
    # set the screen on turn 1, take the hit on turn 2 (the faster attacker
    # would outpace a same-turn Reflect)
    screener = mon("Gholdengo", ["Reflect"], evs={"hp": 252})
    b = pinned(Battle([screener], [chomp(("Dragon Claw",))], seed=9))
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[0].conditions.reflect
    b.step(Action("pass"), Action("move", 0))
    behind_screen = b.sides[0].active(0).max_hp - b.sides[0].active(0).hp

    b2 = pinned(Battle([mon("Gholdengo", ["Swords Dance"], evs={"hp": 252})], [chomp(("Dragon Claw",))], seed=9))
    b2.step(Action("move", 0), Action("pass"))
    b2.step(Action("pass"), Action("move", 0))
    no_screen = b2.sides[0].active(0).max_hp - b2.sides[0].active(0).hp
    assert 0 < behind_screen < no_screen


def test_sandstorm_chips_non_immune():
    b = Battle([mon("Tyranitar", ["Sandstorm"])], [azu()], seed=1)
    b.step(Action("move", 0), Action("pass"))
    azu0 = b.sides[1].active(0)
    assert b.field.weather == "sand"
    assert azu0.hp == azu0.max_hp - azu0.max_hp // 16  # chipped
    tar0 = b.sides[0].active(0)
    assert tar0.hp == tar0.max_hp  # Rock-type immune to chip


def test_leftovers_heal():
    holder = mon("Azumarill", ["Aqua Jet"], item="Leftovers")
    b = Battle([holder], [chomp()], seed=1)
    lefty = b.sides[0].active(0)
    lefty.hp = 50
    b.step(Action("pass"), Action("pass"))
    assert lefty.hp == 50 + lefty.max_hp // 16


# ----------------------------------------------------- doubles mechanics ---

def doubles(t0, t1, seed=1):
    return Battle(t0, t1, seed=seed, format="doubles")


def test_follow_me_redirects():
    redirector = mon("Azumarill", ["Follow Me"], evs={"hp": 252})
    frail = mon("Gholdengo", ["Recover"])
    # attacker uses Crunch: neutral into the Azumarill redirector (Dragon
    # moves would be blanked by its Fairy typing)
    b = doubles([frail, redirector], [mon("Tyranitar", ["Crunch"]), azu()])
    b.step(
        [Action("move", 0), Action("move", 0)],
        [Action("move", 0, target=0), Action("pass")],
    )
    assert b.sides[0].active(0).hp == b.sides[0].active(0).max_hp
    assert b.sides[0].active(1).hp < b.sides[0].active(1).max_hp


def test_rage_powder_redirects_in_champions():
    # Champions data marks Rage Powder as NOT a powder move (serebii's flag
    # grid) — so unlike mainline, grass types don't ignore it here
    redirector = mon("Azumarill", ["Rage Powder"], evs={"hp": 252})
    frail = mon("Tyranitar", ["Crunch"])
    grass_attacker = mon("Venusaur", ["Energy Ball"], "Modest", {"spa": 252})
    b = doubles([frail, redirector], [grass_attacker, azu()])
    b.step(
        [Action("move", 0), Action("move", 0)],
        [Action("move", 0, target=0), Action("pass")],
    )
    assert b.sides[0].active(0).hp == b.sides[0].active(0).max_hp  # redirected
    assert b.sides[0].active(1).hp < b.sides[0].active(1).max_hp


def test_powder_redirection_ignored_by_grass_attackers():
    # the mechanism itself, exercised directly (no champions move currently
    # carries both redirect + powder flag)
    grass_attacker = mon("Venusaur", ["Energy Ball"], "Modest", {"spa": 252})
    b = doubles([mon("Tyranitar", ["Crunch"]), azu()], [grass_attacker, azu()])
    b.sides[0].redirect_slot = 1
    b.sides[0].redirect_powder = True
    targets = b._targets_for(1, 0, "normal", 0)
    assert targets == [b.sides[0].active(0)]  # not redirected
    b.sides[0].redirect_powder = False
    assert b._targets_for(1, 0, "normal", 0) == [b.sides[0].active(1)]


def test_wide_guard_blocks_spread_only():
    guard = mon("Azumarill", ["Wide Guard"], evs={"hp": 252})
    ally = mon("Gholdengo", ["Recover"])
    b = doubles([guard, ally], [chomp(("Earthquake", "Dragon Claw")), azu()])
    b.step(
        [Action("move", 0), Action("move", 0)],
        [Action("move", 0), Action("pass")],  # spread EQ into Wide Guard
    )
    assert b.sides[0].active(0).hp == b.sides[0].active(0).max_hp
    assert b.sides[0].active(1).hp == b.sides[0].active(1).max_hp


def test_helping_hand_amplifies_ally():
    helper = mon("Azumarill", ["Helping Hand"])
    hitter = chomp(("Dragon Claw",))
    target = mon("Tyranitar", ["Crunch"], evs={"hp": 252})

    b = doubles([hitter, helper], [target, azu()], seed=6)
    pinned(b)
    b.step(
        [Action("move", 0, target=0), Action("move", 0)],
        [Action("pass"), Action("pass")],
    )
    helped = b.sides[1].active(0).max_hp - b.sides[1].active(0).hp

    b2 = doubles([hitter, mon("Azumarill", ["Aqua Jet"])], [target, azu()], seed=6)
    pinned(b2)
    b2.step(
        [Action("move", 0, target=0), Action("pass")],
        [Action("pass"), Action("pass")],
    )
    plain = b2.sides[1].active(0).max_hp - b2.sides[1].active(0).hp
    assert helped > 1.3 * plain


# ---------------------------------------------------------------- seeding ---

def test_snapshot_round_trip():
    b = Battle([chomp(), azu()], [azu(), mon("Tyranitar", ["Crunch"])], seed=1)
    b.sides[0].active(0).hp = 77
    b.sides[1].active(0).status = "brn"
    b.sides[1].active(0).boosts["atk"] = 2
    b.sides[0].timers["tailwind"] = 2
    b.sides[0].sync_conditions()
    b.field.trick_room = True

    snap = b.snapshot()
    b2 = Battle.from_state(snap, seed=42)
    assert b2.sides[0].active(0).hp == round(77 / b.sides[0].active(0).max_hp * b2.sides[0].active(0).max_hp)
    assert b2.sides[1].active(0).status == "brn"
    assert b2.sides[1].active(0).boosts["atk"] == 2
    assert b2.sides[0].conditions.tailwind
    assert b2.field.trick_room
    assert not b2.is_over
    b2.step(Action("move", 0), Action("move", 0))  # playable


def test_snapshot_is_isolated_from_live_battle():
    b = Battle([chomp()], [azu()], seed=1)
    snap = b.snapshot()
    b.step(Action("move", 0), Action("move", 0))
    assert snap.sides[1].pokemon[0].hp_fraction == 1.0  # frozen in time


def test_sim_does_not_mutate_caller_teams():
    team0 = [mon("Azumarill", ["Scald"])]
    team1 = [chomp()]
    b = pinned(Battle(team0, team1, seed=1), value=0.1)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).status == "brn"
    assert team1[0].status is None  # caller's set untouched
