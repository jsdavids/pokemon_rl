"""Golden tests: compare our rolls against fixtures generated from
@smogon/calc. This is the acceptance gate for Phase 1.

Generate fixtures with (see tests/golden/README.md):
    node scripts/generate_goldens.mjs > tests/golden/gen9_cases.json
    node scripts/generate_goldens.mjs champions > tests/golden/champions_cases.json
    node scripts/generate_goldens.mjs champions-species > tests/golden/champions_species.json
Each test is skipped until its fixture file exists.
"""

import json
from pathlib import Path

import pytest

from pokechamp.damage import calc_damage
from pokechamp.data.loader import DATA_DIR, load_ruleset, norm
from pokechamp.schemas import FieldState, PokemonSet, SideState

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN = GOLDEN_DIR / "gen9_cases.json"
CHAMPIONS_GOLDEN = GOLDEN_DIR / "champions_cases.json"
CHAMPIONS_SPECIES = GOLDEN_DIR / "champions_species.json"
HAVE_CHAMPIONS = (DATA_DIR / "champions" / "species.json").exists()

# Known, explained differences between our scraped champions data and
# smogon's dex. Anything NOT listed here fails the parity test.
SPECIES_PARITY_EXCEPTIONS = {
    # serebii / champdex / rotomlabs: 60/100/105/130/105/120 (= Starmie
    # +100, the Mega rule); @smogon/calc 0.11.0 has Atk 140 (+140)
    "Mega Starmie": "smogon data error",
    # serebii's own Mega tables list these weights; smogon differs
    "Mega Drampa": "weight: serebii 240.5 vs smogon 185",
    "Mega Eelektross": "weight: serebii 180 vs smogon 160",
    # serebii lists one weight for every size; the sizes do differ
    "Gourgeist (Small Variety)": "weight: serebii lists 12.5 for all sizes",
    "Gourgeist (Large Variety)": "weight: serebii lists 12.5 for all sizes",
    "Gourgeist (Jumbo Variety)": "weight: serebii lists 12.5 for all sizes",
    # serebii lists 42 kg (Galarian Farfetch'd's weight); mainline is 15
    "Farfetch'd": "weight: serebii 42 vs smogon 15",
}


def _run(case: dict, ruleset: str):
    f = case.get("field", {})
    field = FieldState(
        weather=f.get("weather"),
        terrain=f.get("terrain"),
        defender_side=SideState(
            reflect=f.get("reflect", False),
            light_screen=f.get("light_screen", False),
            aurora_veil=f.get("aurora_veil", False),
        ),
    )
    return calc_damage(
        PokemonSet(**case["attacker"]),
        PokemonSet(**case["defender"]),
        case["move"],
        field=field,
        spread=case.get("spread", False),
        is_crit=case.get("is_crit", False),
        hits=case.get("hits"),
        attacker_boosts=case.get("attacker_boosts"),
        defender_boosts=case.get("defender_boosts"),
        attacker_hp_fraction=case.get("attacker_hp_fraction", 1.0),
        metronome_uses=case.get("metronome_uses", 0),
        ruleset=ruleset,
    )


def _describe(case: dict, got) -> str:
    desc = case["move"]
    if case.get("is_crit"):
        desc += " (crit)"
    items = [case["attacker"].get("item"), case["defender"].get("item")]
    extra = " ".join(f"[{i}]" for i in items if i)
    return (
        f"{case['attacker']['species']} {desc} vs "
        f"{case['defender']['species']} {extra}: got {got}, want {case['rolls']}"
    )


@pytest.mark.skipif(
    not GOLDEN.exists(),
    reason="golden fixtures not generated yet - see tests/golden/README.md",
)
def test_matches_smogon_calc():
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    failures = []
    for case in cases:
        res = _run(case, "gen9")
        if res.rolls != case["rolls"] or res.hits != case.get("hits", 1):
            failures.append(_describe(case, res.rolls))
    assert not failures, "\n".join(failures)


@pytest.mark.skipif(
    not (CHAMPIONS_GOLDEN.exists() and HAVE_CHAMPIONS),
    reason="champions golden fixtures or ruleset missing",
)
def test_matches_smogon_calc_on_champions_data():
    """Same gate on the scraped champions ruleset: Light Ball, Metronome,
    Iron Ball, Air Balloon, Normal Gem, -ate abilities, the M-C Megas.
    Each case's `ref` block (smogon's stats / types / move BP) is checked
    first, so a data divergence is reported as data, not as a calc bug."""
    rs = load_ruleset("champions")
    cases = json.loads(CHAMPIONS_GOLDEN.read_text(encoding="utf-8"))
    data_problems, failures = [], []
    for case in cases:
        ref = case["ref"]
        for side in ("attacker", "defender"):
            sp = rs.get_species(case[side]["species"])
            if (dict(sp.base_stats) != ref[side]["base_stats"]
                    or list(sp.types) != ref[side]["types"]):
                data_problems.append(f"{sp.name} != smogon {ref[side]['smogon']}")
        mv = rs.get_move(case["move"])
        if (mv.base_power, mv.type) != (ref["move_bp"], ref["move_type"]):
            data_problems.append(
                f"{mv.name}: ours {mv.base_power} {mv.type}, "
                f"smogon {ref['move_bp']} {ref['move_type']}"
            )
        res = _run(case, "champions")
        if res.rolls != case["rolls"] or res.hits != case.get("hits", 1):
            failures.append(_describe(case, res.rolls))
    assert not data_problems, "data diverges from smogon:\n" + "\n".join(data_problems)
    assert not failures, "\n".join(failures)


@pytest.mark.skipif(
    not (CHAMPIONS_SPECIES.exists() and HAVE_CHAMPIONS),
    reason="champions species parity fixture or ruleset missing",
)
def test_champions_species_match_smogon_dex():
    """Every scraped form vs smogon's dex: base stats, types, weight. This
    is how the Rotom bug (appliance forms carrying base Rotom's stats under
    names like 'Heat Rotom Rotom') and regional forms inheriting the base
    weight were found."""
    rs = load_ruleset("champions")
    ref = json.loads(CHAMPIONS_SPECIES.read_text(encoding="utf-8"))
    unmatched, mismatched = [], []
    for row in ref:
        name = row["species"]
        if row["smogon"] is None:
            unmatched.append(name)
            continue
        sp = rs.get_species(name)
        diffs = []
        if dict(sp.base_stats) != row["base_stats"]:
            diffs.append(f"stats {dict(sp.base_stats)} vs {row['base_stats']}")
        if list(sp.types) != row["types"]:
            diffs.append(f"types {list(sp.types)} vs {row['types']}")
        if sp.weight and abs(sp.weight - row["weight"]) > 0.05:
            diffs.append(f"weight {sp.weight} vs {row['weight']}")
        if diffs and name not in SPECIES_PARITY_EXCEPTIONS:
            mismatched.append(f"{name} ({row['smogon']}): " + "; ".join(diffs))
    assert not unmatched, f"no smogon counterpart for: {unmatched}"
    assert not mismatched, "\n".join(mismatched)
    stale = [n for n in SPECIES_PARITY_EXCEPTIONS if norm(n) not in rs._species]
    assert not stale, f"exceptions for species we no longer have: {stale}"
