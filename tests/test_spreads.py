"""Ladder-grounded Stat Point spread models (estimator/spreads.py)."""

import random

import pytest

from pokechamp.data.loader import DATA_DIR
from pokechamp.estimator.spreads import (
    SpreadModel, archetype, load_spread_model, stat_roles,
)
from pokechamp.stats import SP_MAX_PER_STAT, SP_MAX_TOTAL, ev_to_sp

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)

# a tiny synthetic ladder: two offense species, one bulky, one Trick Room
DATA = {
    "Garchomp": {"spreads": [[2, 32, 0, 0, 0, 32, 0.6], [6, 25, 3, 0, 0, 32, 0.4]],
                 "natures": {"Jolly": 0.6, "Adamant": 0.4}},
    "Gholdengo": {"spreads": [[2, 0, 0, 32, 0, 32, 1.0]],
                  "natures": {"Timid": 1.0}},
    "Incineroar": {"spreads": [[32, 0, 14, 0, 20, 0, 1.0]],
                   "natures": {"Careful": 1.0}},
    "Torkoal": {"spreads": [[32, 0, 0, 32, 2, 0, 1.0]],
                "natures": {"Quiet": 1.0}},
}


def test_roles_follow_the_nature():
    base = {"hp": 108, "atk": 130, "def": 95, "spa": 80, "spd": 85, "spe": 102}
    assert stat_roles(base, "Modest")["spa"] == "attack"  # special Garchomp
    assert stat_roles(base, "Modest")["atk"] == "dump"
    assert stat_roles(base, "Jolly")["atk"] == "attack"  # higher base
    assert archetype("Quiet") == "trick room"
    assert archetype("Careful") == "bulk"
    assert archetype("Timid") == "offense"


def test_species_mode_samples_own_spreads():
    model = SpreadModel(DATA)
    rng = random.Random(0)
    seen = {tuple(model.sample_species("Garchomp", "Jolly", rng).values())
            for _ in range(50)}
    assert seen == {(2, 32, 0, 0, 0, 32), (6, 25, 3, 0, 0, 32)}
    # a Mega shares its base species' ladder spreads
    assert model.sample_species("Mega Garchomp Z", "Modest", rng) is not None
    assert model.sample_species("Rillaboom", "Adamant", rng) is None  # no data


def test_role_mode_maps_other_species_shapes_onto_this_one():
    model = SpreadModel(DATA)
    rng = random.Random(0)
    for _ in range(30):
        # Garchomp's own spreads are excluded: offense donor is Gholdengo
        # (attack 32 / spe 32 / hp 2), mapped onto Garchomp's physical side
        sp = model.sample_role("Garchomp", "Jolly", rng)
        assert sp == {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}
    bulky = model.sample_role("Rillaboom", "Careful", rng)
    assert bulky == {"hp": 32, "atk": 0, "def": 14, "spa": 0, "spd": 20, "spe": 0}
    tr = model.sample_role("Rillaboom", "Brave", rng)  # -Spe: Trick Room donor
    assert tr["spe"] == 0 and tr["atk"] == 32


def test_sample_evs_falls_back_to_role():
    model = SpreadModel(DATA)
    evs, how = model.sample_evs("Rillaboom", "Adamant", random.Random(1))
    assert how == "role"
    assert ev_to_sp(evs["atk"]) == 32


@pytest.mark.skipif(load_spread_model() is None, reason="no ladder spreads fetched")
def test_real_ladder_spreads_are_legal_champions_spreads():
    model = load_spread_model()
    assert len(model.species) >= 40
    for name, entry in model.species.items():
        for *sp, share in entry["spreads"]:
            assert all(0 <= v <= SP_MAX_PER_STAT for v in sp), name
            assert sum(sp) <= SP_MAX_TOTAL, name
            assert 0 < share <= 1
    rng = random.Random(0)
    for mode in ("species", "role"):
        for _ in range(200):
            evs, _ = model.sample_evs("Garchomp", "Jolly", rng, mode)
            assert sum(ev_to_sp(v) for v in evs.values()) <= SP_MAX_TOTAL


# ------------------------------------------------- ladder spread candidates ---

def test_spread_candidates_cover_the_data_and_follow_the_nature():
    from pokechamp.data import load_ruleset
    from pokechamp.estimator.spreads import SpreadCandidates
    from pokechamp.stats import ev_to_sp
    rs = load_ruleset("champions")
    cands = SpreadCandidates(SpreadModel(DATA), k=4)
    # Adamant: attack points land on Atk; Modest: on SpA
    adamant = [{s: ev_to_sp(v) for s, v in evs.items()}
               for evs, _ in cands.evs_for("Garchomp", "Adamant", rs)]
    modest = [{s: ev_to_sp(v) for s, v in evs.items()}
              for evs, _ in cands.evs_for("Garchomp", "Modest", rs)]
    assert {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32} in adamant
    assert {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32} in modest
    # bulky natures get the bulky shape; weights are a distribution
    careful = cands.evs_for("Garchomp", "Careful", rs)
    assert any(ev_to_sp(e["hp"]) == 32 and ev_to_sp(e["spd"]) == 20 for e, _ in careful)
    for nature in ("Adamant", "Careful", "Quiet"):
        assert sum(w for _, w in cands.evs_for("Garchomp", nature, rs)) == pytest.approx(1.0)


def test_ladder_candidates_drive_the_grid_and_prior():
    from pokechamp.data import load_ruleset
    from pokechamp.estimator import StatEstimator
    from pokechamp.estimator.candidates import default_candidates
    from pokechamp.estimator.spreads import SpreadCandidates
    cands = SpreadCandidates(SpreadModel(DATA), k=4)
    grid = default_candidates("Garchomp", ruleset="champions",
                              regulation="regulation-m-c", spreads=cands)
    for c in grid:  # every hypothesis is a legal Champions spread
        assert sum(ev_to_sp(v) for v in c.evs.values()) <= SP_MAX_TOTAL
    est = StatEstimator("Garchomp", candidates=grid)
    est.apply_prior(cands.prior(load_ruleset("champions")), "ladder")
    # offense shapes: 32/32/2 carries Garchomp's 60% + Gholdengo's 100%,
    # 25-attack/6-HP only Garchomp's 40% — the prior keeps that ratio
    shape = est.marginal(lambda c: (ev_to_sp(c.evs["hp"]), ev_to_sp(c.evs["spe"])))
    assert shape[(2, 32)] > 2 * shape[(6, 32)]
    assert min(est.weights) > 0


# ---------------------------------------------------------- sim event log ---

def test_battle_event_log_reports_what_a_player_sees():
    from pokechamp.schemas import PokemonSet, stat_table
    from pokechamp.sim import Action, Battle
    chomp = PokemonSet(species="Garchomp", nature="Jolly", moves=["Earthquake"],
                       evs={**stat_table(), "atk": 252, "spe": 252})
    lax = PokemonSet(species="Snorlax", moves=["Body Slam"], evs=stat_table())
    b = Battle([chomp], [lax], seed=1)
    assert b.events is None  # opt-in
    b.events = []
    b.step(Action("move", 0), Action("move", 0))
    moves = [e for e in b.events if e["kind"] == "move"]
    hits = [e for e in b.events if e["kind"] == "hit"]
    assert [m["side"] for m in moves] == [0, 1]  # Garchomp outspeeds
    first = hits[0]
    assert first["attacker"] == (0, 0) and first["defender"] == (1, 0)
    assert first["move"] == "Earthquake" and first["clean"]
    assert 0 < first["damage"] <= first["hp_before"] == first["max_hp"]
