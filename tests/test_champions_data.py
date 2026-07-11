"""Integrity checks for the scraped Champions ruleset.

Skipped entirely if the champions ruleset hasn't been generated yet
(scripts/scrape_serebii.py builds it; the JSON is committed once scraped).
"""

import pytest

from pokechamp.damage import calc_damage
from pokechamp.data.loader import DATA_DIR, load_ruleset, norm
from pokechamp.schemas import PokemonSet, stat_table

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)


@pytest.fixture(scope="module")
def rs():
    return load_ruleset("champions")


def test_known_species(rs):
    chomp = rs.get_species("Garchomp")
    assert chomp.types == ("dragon", "ground")
    assert chomp.base_stats == {
        "hp": 108, "atk": 130, "def": 95, "spa": 80, "spd": 85, "spe": 102,
    }
    assert "Rough Skin" in chomp.abilities
    assert norm("Earthquake") in chomp.learnset


def test_mega_forms_present(rs):
    mega = rs.get_species("Mega Garchomp")
    assert mega.base_species == "Garchomp"
    assert mega.base_stats["atk"] == 170
    # Champions-exclusive megas made it in via the regulation pages' roster
    for name in ("Mega Charizard X", "Mega Charizard Y"):
        assert rs.get_species(name).base_species == "Charizard"


def test_known_moves(rs):
    eq = rs.get_move("Earthquake")
    assert (eq.type, eq.category, eq.base_power) == ("ground", "Physical", 100)
    assert eq.spread
    assert eq.pp == 12  # Champions rebalanced PP (mainline: 10)

    spear = rs.get_move("Icicle Spear")
    assert spear.multihit == (2, 5)

    punch = rs.get_move("Ice Punch")
    assert {"contact", "punch"} <= punch.flags


def test_psychic_survives_the_type_name_collision(rs):
    """Psychic is the one move whose name is also a type; a scraper filter
    against type-name slugs once silently deleted it from all 83 learners
    (found when the team validator rejected 'Alakazam can't learn
    Psychic')."""
    learners = [
        sp.name for sp in rs.all_species() if "psychic" in sp.learnset
    ]
    assert "Alakazam" in learners
    assert len(learners) >= 80


def test_learnsets_reference_known_moves(rs):
    """Every move in every learnset must resolve in moves.json."""
    missing = set()
    for key in list(rs._species):
        sp = rs._species[key]
        for mv in sp.learnset:
            if mv not in rs._moves:
                missing.add(mv)
    assert not missing, f"learnset moves absent from moves.json: {sorted(missing)[:20]}"


def test_all_species_have_types_stats_and_moves(rs):
    for sp in rs._species.values():
        assert sp.types, sp.name
        assert set(sp.base_stats) == set(stat_table()), sp.name
        assert sp.learnset, sp.name


def test_items_and_regulations_loaded(rs):
    assert rs.items["Life Orb"]["held"]
    assert rs.items["Garchompite"]["mega_stone"]
    rega = rs.regulations["regulation-m-a"]
    assert "Garchomp" in rega["pokemon"]
    assert rs.regulations["regulation-m-b"]["items"]


def test_end_to_end_damage_calc(rs):
    chomp = PokemonSet(
        species="Garchomp", nature="Jolly", evs={**stat_table(), "atk": 252}
    )
    res = calc_damage(chomp, PokemonSet(species="Dragonite"), "Dragon Claw",
                      ruleset="champions")
    assert res.type_effectiveness == 2.0
    assert res.max_damage > 0
