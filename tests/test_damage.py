"""Damage calculator checks. Expected rolls hand-computed from the Gen 5+
formula spec; the broader acceptance gate is tests/golden/ (vs @smogon/calc).
"""

import pytest

from pokechamp.damage import calc_damage
from pokechamp.schemas import PokemonSet, stat_table

JOLLY_CHOMP = PokemonSet(
    species="Garchomp",
    nature="Jolly",
    evs={**stat_table(), "atk": 252, "spe": 252, "hp": 4},
    moves=["Earthquake", "Dragon Claw"],
)

UNINVESTED_TUSK = PokemonSet(species="Great Tusk")


def test_eq_vs_great_tusk_rolls():
    # 182 Atk vs 151 Def / 190 HP, STAB, neutral: hand-computed 69-82.
    res = calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Earthquake")
    assert len(res.rolls) == 16
    assert res.min_damage == 69
    assert res.max_damage == 82
    assert res.defender_hp == 190
    assert res.stab == 1.5
    assert res.type_effectiveness == 1.0
    assert not res.possible_ohko


def test_eq_vs_gholdengo_super_effective_ohko():
    res = calc_damage(JOLLY_CHOMP, PokemonSet(species="Gholdengo"), "Earthquake")
    assert res.type_effectiveness == 2.0
    assert res.guaranteed_ohko


def test_eq_vs_dragonite_immune():
    res = calc_damage(JOLLY_CHOMP, PokemonSet(species="Dragonite"), "Earthquake")
    assert res.type_effectiveness == 0.0
    assert res.rolls == [0] * 16


def test_burn_halves_physical():
    burned = PokemonSet(
        species="Garchomp", nature="Jolly",
        evs={**stat_table(), "atk": 252}, status="brn",
    )
    res = calc_damage(burned, UNINVESTED_TUSK, "Earthquake")
    assert res.min_damage == 34  # poke_round(69 * 0.5)
    assert res.max_damage == 41


def test_status_move_deals_nothing():
    res = calc_damage(PokemonSet(species="Amoonguss"), UNINVESTED_TUSK, "Spore")
    assert res.rolls == [0] * 16


def test_rolls_monotonic():
    res = calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Dragon Claw")
    assert res.rolls == sorted(res.rolls)


def test_unknown_move_raises():
    with pytest.raises(KeyError):
        calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Splash")


def test_choice_band():
    # pokeRound(182 * 1.5) = 273 Atk -> hand-computed 102-121
    banded = PokemonSet(
        species="Garchomp", nature="Jolly",
        evs={**stat_table(), "atk": 252}, item="Choice Band",
    )
    res = calc_damage(banded, UNINVESTED_TUSK, "Earthquake")
    assert (res.min_damage, res.max_damage) == (102, 121)


def test_life_orb():
    orbed = PokemonSet(
        species="Garchomp", nature="Jolly",
        evs={**stat_table(), "atk": 252}, item="Life Orb",
    )
    res = calc_damage(orbed, UNINVESTED_TUSK, "Earthquake")
    assert (res.min_damage, res.max_damage) == (90, 107)


def test_plus_two_attack():
    res = calc_damage(
        JOLLY_CHOMP, UNINVESTED_TUSK, "Earthquake", attacker_boosts={"atk": 2}
    )
    assert (res.min_damage, res.max_damage) == (136, 162)


def test_crit_ignores_defender_positive_boosts():
    crit_plain = calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Earthquake", is_crit=True)
    crit_boosted = calc_damage(
        JOLLY_CHOMP, UNINVESTED_TUSK, "Earthquake",
        is_crit=True, defender_boosts={"def": 2},
    )
    assert crit_plain.rolls == crit_boosted.rolls
    # but without the crit, +2 Def matters a lot
    boosted = calc_damage(
        JOLLY_CHOMP, UNINVESTED_TUSK, "Earthquake", defender_boosts={"def": 2}
    )
    assert boosted.max_damage < crit_boosted.min_damage


def test_unimplemented_item_noted_not_fatal():
    holder = PokemonSet(species="Garchomp", item="Eviolite")
    res = calc_damage(holder, UNINVESTED_TUSK, "Earthquake")
    assert any("Eviolite" in n for n in res.notes)


def test_no_damage_effect_item_silent():
    holder = PokemonSet(species="Garchomp", item="Leftovers")
    res = calc_damage(holder, UNINVESTED_TUSK, "Earthquake")
    assert not any("Leftovers" in n for n in res.notes)


def test_reflect_halves_physical():
    from pokechamp.schemas import FieldState, SideState

    field = FieldState(defender_side=SideState(reflect=True))
    res = calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Earthquake", field=field)
    assert (res.min_damage, res.max_damage) == (34, 41)


def test_seismic_toss_fixed_and_ghost_immune():
    res = calc_damage(UNINVESTED_TUSK, PokemonSet(species="Amoonguss"), "Seismic Toss")
    assert res.rolls == [50] * 16
    res = calc_damage(UNINVESTED_TUSK, PokemonSet(species="Gholdengo"), "Seismic Toss")
    assert res.rolls == [0] * 16


def test_levitate_immunity():
    res = calc_damage(
        JOLLY_CHOMP, PokemonSet(species="Tyranitar", ability="Levitate"), "Earthquake"
    )
    assert res.rolls == [0] * 16


def test_multihit_totals_and_distribution():
    cloyster = PokemonSet(
        species="Cloyster", nature="Jolly", evs={**stat_table(), "atk": 252}
    )
    res = calc_damage(cloyster, UNINVESTED_TUSK, "Icicle Spear")  # variable: defaults to 3
    assert res.hits == 3
    assert len(res.rolls) == 16
    assert res.min_damage == 3 * res.rolls[0]
    assert res.max_damage == 3 * res.rolls[-1]
    dist = res.total_distribution()
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    assert min(dist) == res.min_damage and max(dist) == res.max_damage

    res5 = calc_damage(cloyster, UNINVESTED_TUSK, "Icicle Spear", hits=5)
    assert res5.hits == 5 and res5.rolls == res.rolls


def test_fixed_multihit_defaults():
    res = calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Bonemerang")
    assert res.hits == 2


def test_tough_claws_needs_contact():
    clawed = PokemonSet(
        species="Garchomp", nature="Jolly",
        evs={**stat_table(), "atk": 252}, ability="Tough Claws",
    )
    plain_dc = calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Dragon Claw")
    boosted_dc = calc_damage(clawed, UNINVESTED_TUSK, "Dragon Claw")
    assert boosted_dc.max_damage > plain_dc.max_damage
    # Earthquake makes no contact: identical
    assert (
        calc_damage(clawed, UNINVESTED_TUSK, "Earthquake").rolls
        == calc_damage(JOLLY_CHOMP, UNINVESTED_TUSK, "Earthquake").rolls
    )


def test_punching_glove_negates_contact_for_fluffy():
    gloved = PokemonSet(
        species="Garchomp", nature="Jolly",
        evs={**stat_table(), "atk": 252}, item="Punching Glove",
    )
    fluffy_tusk = PokemonSet(species="Great Tusk", ability="Fluffy")
    bare_vs_fluffy = calc_damage(JOLLY_CHOMP, fluffy_tusk, "Ice Punch")
    gloved_vs_fluffy = calc_damage(gloved, fluffy_tusk, "Ice Punch")
    # bare fist: contact, Fluffy halves; gloved: no contact + 1.1x boost
    assert gloved_vs_fluffy.min_damage > 1.5 * bare_vs_fluffy.min_damage


def test_defender_tera_changes_typing():
    res = calc_damage(
        JOLLY_CHOMP, PokemonSet(species="Dragonite", tera_type="Steel"), "Earthquake"
    )
    assert res.type_effectiveness == 2.0
    assert res.max_damage > 0
