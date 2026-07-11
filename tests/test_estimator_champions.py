"""Estimator candidate enumeration against the real Champions ruleset:
regulation-legal items, data-derived ability hypotheses, mega handling."""

import pytest

from pokechamp.damage import calc_damage
from pokechamp.data.loader import DATA_DIR
from pokechamp.estimator import StatEstimator
from pokechamp.estimator.candidates import default_candidates, legal_items
from pokechamp.schemas import PokemonSet, stat_table

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)

MY_CHOMP = PokemonSet(
    species="Garchomp", nature="Jolly", evs={**stat_table(), "atk": 252, "spe": 252}
)


def test_champions_item_pool_lacks_choice_band():
    # Champions simply doesn't have Choice Band/Specs or Assault Vest
    pool = legal_items("champions")
    assert "Choice Band" not in pool
    assert "Choice Specs" not in pool
    assert "Assault Vest" not in pool
    assert {"Choice Scarf", "Life Orb", "Muscle Band", "Wise Glasses"} <= set(pool)


def test_regulation_gates_item_pool():
    # Life Orb & friends only entered the game in Regulation M-B
    ma = legal_items("champions", "regulation-m-a")
    mb = legal_items("champions", "regulation-m-b")
    assert set(ma) == {None, "Choice Scarf"}
    assert "Life Orb" in mb and "Expert Belt" in mb
    with pytest.raises(KeyError):
        legal_items("champions", "regulation-z")


def test_ability_hypotheses_from_species_data():
    cands = default_candidates("Azumarill", ruleset="champions")
    assert {c.ability for c in cands} == {"Thick Fat", "Huge Power", "Sap Sipper"}


def test_mega_candidates_have_no_item_variants():
    cands = default_candidates("Mega Garchomp", ruleset="champions")
    assert {c.item for c in cands} == {None}
    assert {c.ability for c in cands} == {"Sand Force"}


def test_unknown_species_fails_loud():
    with pytest.raises(KeyError):
        default_candidates("Great Tusk", ruleset="champions")  # not in Champions


def test_huge_power_identified_from_damage():
    hidden = PokemonSet(
        species="Azumarill", nature="Adamant",
        evs={**stat_table(), "atk": 252, "hp": 252}, item="Life Orb",
        ability="Huge Power",
    )
    res = calc_damage(hidden, MY_CHOMP, "Play Rough", ruleset="champions")
    pct = 100.0 * res.rolls[8] / res.defender_hp

    est = StatEstimator("Azumarill", regulation="regulation-m-b")
    est.observe_damage_dealt("Play Rough", MY_CHOMP, pct, tolerance=0.5)
    # doubled Attack puts this damage far beyond any non-Huge Power build
    assert est.marginal(lambda c: c.ability).keys() == {"Huge Power"}
    survivors = [c for c, _ in est.posterior()]
    assert any(
        c.nature == "Adamant" and c.item == "Life Orb" and c.evs["atk"] == 252
        for c in survivors
    )


def test_thick_fat_shifts_ability_marginal():
    hidden = PokemonSet(
        species="Azumarill", nature="Calm",
        evs={**stat_table(), "hp": 252, "spd": 252}, ability="Thick Fat",
    )
    res = calc_damage(MY_CHOMP, hidden, "Fire Fang", ruleset="champions")
    pct = 100.0 * res.rolls[8] / res.defender_hp

    est = StatEstimator("Azumarill", regulation="regulation-m-b")
    est.observe_damage_taken(MY_CHOMP, "Fire Fang", pct, tolerance=0.3)
    marg = est.marginal(lambda c: c.ability)
    assert max(marg, key=marg.get) == "Thick Fat"


def test_regulation_constrains_inference():
    # in Regulation M-A the only damage-relevant item question is Scarf-or-not
    est = StatEstimator("Azumarill", regulation="regulation-m-a")
    assert set(est.marginal(lambda c: c.item)) <= {None, "Choice Scarf"}


def test_hp_bar_tracking_and_remaining_percent():
    hidden = PokemonSet(
        species="Azumarill", nature="Calm",
        evs={**stat_table(), "hp": 252, "spd": 252}, ability="Thick Fat",
    )
    res = calc_damage(MY_CHOMP, hidden, "Liquidation", ruleset="champions")
    dealt = 100.0 * res.rolls[8] / res.defender_hp

    # report the same event two ways; posteriors must match
    est_pct = StatEstimator("Azumarill")
    est_pct.observe_damage_taken(MY_CHOMP, "Liquidation", percent=dealt, tolerance=0.4)

    est_bar = StatEstimator("Azumarill")
    est_bar.observe_damage_taken(
        MY_CHOMP, "Liquidation", remaining_percent=100.0 - dealt, tolerance=0.4
    )
    assert est_bar.weights == est_pct.weights
    assert abs(est_bar.hp_percent - (100.0 - dealt)) < 1e-9


def test_ko_is_censored_not_exact():
    est = StatEstimator("Alakazam")
    # a hit KO'd it from 30%: any build the move can hit for >=30% survives
    # the update — the KO only bounds the damage from below
    est.set_hp(30.0)
    est.observe_damage_taken(MY_CHOMP, "Earthquake", ko=True, tolerance=0.4)
    assert est.hp_percent == 0.0
    for cand, _ in est.posterior():
        res = calc_damage(MY_CHOMP, cand, "Earthquake", ruleset="champions")
        assert res.max_percent >= 30.0 - 0.4


def test_ko_from_full_excludes_survivable_builds():
    est = StatEstimator("Alakazam")
    est.observe_damage_taken(MY_CHOMP, "Earthquake", ko=True, tolerance=0.4)
    # every surviving candidate can actually be OHKO'd by the move
    for cand, _ in est.posterior():
        res = calc_damage(MY_CHOMP, cand, "Earthquake", ruleset="champions")
        assert res.possible_ohko
    # and builds the move cannot OHKO (max-bulk spreads) were eliminated
    dead = [
        c for c, w in zip(est.candidates, est.weights)
        if w == 0.0
        and not calc_damage(MY_CHOMP, c, "Earthquake", ruleset="champions").possible_ohko
    ]
    assert dead


def test_priority_move_order_gives_no_speed_info():
    est = StatEstimator("Azumarill")
    before = list(est.weights)
    # Aqua Jet (+1) going before my Earthquake (0) says nothing about Speed
    est.observe_move_order("Earthquake", "Aqua Jet", True, reference_speed=100)
    assert est.weights == before
    assert "no speed info" in est.log[-1].description
    # same bracket: order becomes a real speed observation (Azumarill tops
    # out above 100 only with Scarf or +Spe investment, so this eliminates)
    est.observe_move_order("Earthquake", "Play Rough", False, reference_speed=100)
    assert est.weights != before


def test_priority_contradiction_raises():
    from pokechamp.estimator import InconsistentObservation

    est = StatEstimator("Azumarill")
    with pytest.raises(InconsistentObservation):
        # Aqua Jet moving *after* a priority-0 move contradicts brackets
        est.observe_move_order("Earthquake", "Aqua Jet", False, reference_speed=169)


def test_apply_prior_reweights():
    est = StatEstimator("Azumarill")
    est.apply_prior(
        lambda c: 0.0 if c.item == "Choice Scarf" else 1.0, "team looks Trick Room"
    )
    assert "Choice Scarf" not in est.marginal(lambda c: c.item)
