"""Stat formula checks against well-known competitive benchmark values."""

from pokechamp.stats import calc_all_stats, calc_stat
from pokechamp.data import load_ruleset

GARCHOMP = load_ruleset("gen9").get_species("Garchomp").base_stats


def test_garchomp_hp_level_50():
    assert calc_stat("hp", GARCHOMP["hp"], 31, 0, 50) == 183


def test_garchomp_jolly_252_atk_is_neutral():
    assert calc_stat("atk", GARCHOMP["atk"], 31, 252, 50, "Jolly") == 182


def test_garchomp_adamant_252_atk():
    assert calc_stat("atk", GARCHOMP["atk"], 31, 252, 50, "Adamant") == 200


def test_garchomp_jolly_252_spe():
    assert calc_stat("spe", GARCHOMP["spe"], 31, 252, 50, "Jolly") == 169


def test_gholdengo_modest_252_spa():
    base = load_ruleset("gen9").get_species("Gholdengo").base_stats
    assert calc_stat("spa", base["spa"], 31, 252, 50, "Modest") == 203


def test_calc_all_stats_defaults():
    stats = calc_all_stats(GARCHOMP)  # level 50, neutral, 31 IVs, 0 EVs
    assert stats == {"hp": 183, "atk": 150, "def": 115, "spa": 100, "spd": 105, "spe": 122}


def test_stat_points_reproduce_the_level_50_stat():
    """Champions Stat Points: each SP is exactly +1 at level 50, and the
    HOME rule (first SP = 4 EVs, each further SP = 8) round-trips."""
    from pokechamp.stats import calc_stat, ev_to_sp, sp_to_ev
    for base in (45, 80, 100, 130):
        for stat in ("atk", "hp"):
            zero = calc_stat(stat, base, 31, 0, 50)
            for sp in range(0, 33):
                assert calc_stat(stat, base, 31, sp_to_ev(sp), 50) == zero + sp
                assert ev_to_sp(sp_to_ev(sp)) == sp
    assert (sp_to_ev(32), sp_to_ev(1), sp_to_ev(2)) == (252, 4, 12)
    # every EV value maps to the SP that gives the same stat
    for ev in range(0, 253):
        gain = calc_stat("atk", 100, 31, ev, 50) - calc_stat("atk", 100, 31, 0, 50)
        assert ev_to_sp(ev) == gain
