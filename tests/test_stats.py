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
