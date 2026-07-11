"""Golden tests: compare our rolls against fixtures generated from
@smogon/calc. This is the acceptance gate for Phase 1.

Generate fixtures with:  node scripts/generate_goldens.mjs > tests/golden/gen9_cases.json
(see tests/golden/README.md). Skipped until the fixture file exists.
"""

import json
from pathlib import Path

import pytest

from pokechamp.damage import calc_damage
from pokechamp.schemas import FieldState, PokemonSet, SideState

GOLDEN = Path(__file__).parent / "golden" / "gen9_cases.json"


@pytest.mark.skipif(
    not GOLDEN.exists(),
    reason="golden fixtures not generated yet - see tests/golden/README.md",
)
def test_matches_smogon_calc():
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    failures = []
    for case in cases:
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
        res = calc_damage(
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
        )
        if res.rolls != case["rolls"] or res.hits != case.get("hits", 1):
            desc = case["move"]
            if case.get("is_crit"):
                desc += " (crit)"
            failures.append(
                f"{case['attacker']['species']} {desc} vs "
                f"{case['defender']['species']}: got {res.rolls}, want {case['rolls']}"
            )
    assert not failures, "\n".join(failures)
