# Golden fixtures

Fixtures pinning our damage output to the reference implementation,
[@smogon/calc](https://github.com/smogon/damage-calc) — the acceptance gate
for Phase 1. Every modifier we add (items, abilities, weather, screens, ...)
should come with golden cases exercising it.

## Generating

```
cd scripts
npm install @smogon/calc
node generate_goldens.mjs > ../tests/golden/gen9_cases.json
```

`tests/test_damage_golden.py` picks the file up automatically (it is skipped
while the file is absent).

## Fixture schema

```json
[
  {
    "attacker": {"species": "Garchomp", "level": 50, "nature": "Jolly", "evs": {"atk": 252}},
    "defender": {"species": "Great Tusk", "level": 50},
    "move": "Earthquake",
    "rolls": [69, "...", 82]
  }
]
```

`attacker`/`defender` objects are `PokemonSet` kwargs.
