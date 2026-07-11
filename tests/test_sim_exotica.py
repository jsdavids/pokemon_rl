"""Substitute, weather/terrain abilities (weather wars), ability
redirection/absorption, and Safeguard."""

import pytest

from pokechamp.data import load_ruleset
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


# ------------------------------------------------------------- substitute ---

def test_substitute_costs_quarter_and_blocks_damage():
    subber = mon("Gholdengo", ["Substitute"], evs={"hp": 252})
    b = Battle([subber], [chomp(("Dragon Claw",))], seed=1)
    user = b.sides[0].active(0)
    b.step(Action("move", 0), Action("pass"))
    assert user.sub_hp == user.max_hp // 4
    assert user.hp == user.max_hp - user.max_hp // 4

    hp_behind_sub = user.hp
    b.step(Action("pass"), Action("move", 0))  # DC deals ~36-42 to the doll
    assert user.hp == hp_behind_sub  # the mon took nothing
    assert user.sub_hp < user.max_hp // 4  # the doll took it


def test_substitute_breaks_without_carryover():
    subber = mon("Gholdengo", ["Substitute"], evs={"hp": 252})
    b = Battle([subber], [chomp(("Earthquake",))], seed=1)
    user = b.sides[0].active(0)
    b.step(Action("move", 0), Action("pass"))
    hp_behind_sub = user.hp
    b.step(Action("pass"), Action("move", 0))  # 2x EQ obliterates the doll
    assert user.sub_hp == 0
    assert user.hp == hp_behind_sub  # excess damage does not carry over


def test_substitute_fails_at_low_hp():
    subber = mon("Gholdengo", ["Substitute"], evs={"hp": 252})
    b = Battle([subber], [chomp()], seed=1)
    user = b.sides[0].active(0)
    user.hp = user.max_hp // 4  # exactly the cost: not strictly greater
    b.step(Action("move", 0), Action("pass"))
    assert user.sub_hp == 0
    assert user.hp == user.max_hp // 4  # no cost paid


def test_substitute_blocks_status_and_riders():
    subber = mon("Gholdengo", ["Substitute"], evs={"hp": 252})
    b = Battle([subber], [mon("Azumarill", ["Scald", "Icy Wind"])], seed=1)
    b.step(Action("move", 0), Action("pass"))
    user = b.sides[0].active(0)
    b.step(Action("pass"), Action("move", 1))  # Icy Wind: 100% spe drop
    assert user.boosts["spe"] == 0  # rider blocked by the doll

    waver = Battle([subber], [mon("Azumarill", ["Thunder Wave"])], seed=1)
    waver.step(Action("move", 0), Action("pass"))
    waver.step(Action("pass"), Action("move", 0))
    assert waver.sides[0].active(0).status is None  # TWave blocked


def test_substitute_blocks_trapping():
    subber = mon("Gholdengo", ["Substitute"], evs={"hp": 252})
    b = Battle([subber], [mon("Azumarill", ["Fire Spin"])], seed=1)
    b.step(Action("move", 0), Action("pass"))
    b.step(Action("pass"), Action("move", 0))
    assert b.sides[0].active(0).trapped_turns == 0


def test_sound_moves_bypass_substitute():
    rs = load_ruleset("champions")
    assert "sound" in rs.get_move("Hyper Voice").flags  # test premise
    subber = mon("Tyranitar", ["Substitute"], evs={"hp": 252})
    b = Battle([subber], [mon("Gholdengo", ["Hyper Voice"], "Modest", {"spa": 252})], seed=1)
    b.step(Action("move", 0), Action("pass"))
    user = b.sides[0].active(0)
    hp_behind_sub = user.hp
    b.step(Action("pass"), Action("move", 0))
    assert user.hp < hp_behind_sub  # sound went through the doll
    assert user.sub_hp == user.max_hp // 4  # doll untouched


def test_setup_works_behind_substitute_and_sub_dies_on_switch():
    subber = mon("Garchomp", ["Substitute", "Swords Dance"], "Jolly", {"spe": 252})
    b = Battle([subber, azu()], [azu(("Aqua Jet",))], seed=1)
    b.step(Action("move", 0), Action("pass"))
    b.step(Action("move", 1), Action("pass"))
    assert b.sides[0].active(0).boosts["atk"] == 2
    b.step(Action("switch", 1), Action("pass"))
    assert b.sides[0].team[0].sub_hp == 0  # sub doesn't survive switching


# ------------------------------------------------------- weather abilities ---

def test_sand_stream_sets_sand_on_lead():
    ttar = mon("Tyranitar", ["Crunch"], ability="Sand Stream")
    b = Battle([ttar], [azu()], seed=1)
    assert b.field.weather == "sand"
    assert b.field_timers["weather"] == 5


def test_weather_war_slower_lead_wins():
    # Politoed (base 70 spe) outspeeds Tyranitar (61): Drizzle fires first,
    # Sand Stream fires last -> sand
    toed = mon("Politoed", ["Scald"], ability="Drizzle")
    ttar = mon("Tyranitar", ["Crunch"], ability="Sand Stream")
    b = Battle([toed], [ttar], seed=1)
    assert b.field.weather == "sand"

    # give Tyranitar max speed: now it triggers first and rain sticks
    fast_ttar = mon(
        "Tyranitar", ["Crunch"], "Jolly", {"spe": 252}, ability="Sand Stream"
    )
    b2 = Battle([toed], [fast_ttar], seed=1)
    assert b2.field.weather == "rain"


def test_weather_rock_extends_duration():
    ttar = mon("Tyranitar", ["Crunch"], item="Smooth Rock", ability="Sand Stream")
    b = Battle([ttar], [azu()], seed=1)
    assert b.field_timers["weather"] == 8


def test_drizzle_on_mid_battle_switch_in():
    toed = mon("Politoed", ["Scald"], ability="Drizzle")
    b = Battle([azu(), toed], [chomp()], seed=1)
    assert b.field.weather is None
    b.step(Action("switch", 1), Action("pass"))
    assert b.field.weather == "rain"


def test_terrain_ability_on_entry():
    # Champions gave Mega Raichu X Electric Surge — but any set works
    surger = mon("Gholdengo", ["Shadow Ball"], ability="Electric Surge")
    b = Battle([surger], [azu()], seed=1)
    assert b.field.terrain == "electric"


# --------------------------------------------- redirection and absorption ---

def test_lightning_rod_draws_and_boosts_in_doubles():
    rod = mon("Gholdengo", ["Recover"], ability="Lightning Rod")
    frail = mon("Azumarill", ["Aqua Jet"], evs={"hp": 252})
    bolt = mon("Politoed", ["Thunderbolt"], "Modest", {"spa": 252})
    b = Battle([frail, rod], [bolt, azu()], seed=1, format="doubles")
    b.step(
        [Action("pass"), Action("pass")],
        [Action("move", 0, target=0), Action("pass")],  # bolt aimed at Azumarill
    )
    assert b.sides[0].active(0).hp == b.sides[0].active(0).max_hp  # drawn away
    assert b.sides[0].active(1).hp == b.sides[0].active(1).max_hp  # and absorbed
    assert b.sides[0].active(1).boosts["spa"] == 1


def test_water_absorb_heals():
    sponge = mon("Politoed", ["Scald"], ability="Water Absorb")
    b = Battle([sponge], [azu(("Liquidation",))], seed=1)
    victim = b.sides[0].active(0)
    victim.hp = 50
    b.step(Action("pass"), Action("move", 0))
    assert victim.hp == 50 + victim.max_hp // 4


def test_sap_sipper_boosts_attack():
    sipper = mon("Azumarill", ["Aqua Jet"], ability="Sap Sipper")
    b = Battle([sipper], [mon("Venusaur", ["Energy Ball"], "Modest", {"spa": 252})], seed=1)
    b.step(Action("pass"), Action("move", 0))
    target = b.sides[0].active(0)
    assert target.hp == target.max_hp
    assert target.boosts["atk"] == 1


# ---------------------------------------------------------------- safeguard ---

def test_safeguard_blocks_status_until_it_expires():
    guarded = mon("Azumarill", ["Safeguard"])
    b = Battle([guarded], [mon("Gholdengo", ["Thunder Wave"])], seed=1)
    b.step(Action("move", 0), Action("pass"))  # guard goes up uncontested
    for _ in range(4):  # timer 5 decrements at each turn's end: 4 blocked waves
        b.step(Action("pass"), Action("move", 0))
        assert b.sides[0].active(0).status is None
    b.step(Action("pass"), Action("move", 0))  # timer expired: this one lands
    assert b.sides[0].active(0).status == "par"


# ---------------------------------------------------------------- seeding ---

def test_snapshot_preserves_substitute():
    subber = mon("Gholdengo", ["Substitute"], evs={"hp": 252})
    b = Battle([subber], [chomp()], seed=1)
    b.step(Action("move", 0), Action("pass"))
    snap = b.snapshot()
    b2 = Battle.from_state(snap, seed=4)
    assert b2.sides[0].active(0).sub_hp == b.sides[0].active(0).sub_hp > 0