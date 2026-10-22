# Golden fixtures

Fixtures pinning our damage output (and our scraped data) to the reference
implementation, [@smogon/calc](https://github.com/smogon/damage-calc) — the
acceptance gate for Phase 1. Every modifier we add (items, abilities,
weather, screens, ...) should come with golden cases exercising it.

| File | Mode | What it pins |
|---|---|---|
| `gen9_cases.json` | `gen9` | 201 damage cases on the small gen9 sample ruleset |
| `champions_cases.json` | `champions` | damage cases on the scraped champions ruleset: Light Ball, Metronome, Iron Ball, Air Balloon, Normal Gem, -ate abilities, the M-C Megas |
| `champions_species.json` | `champions-species` | smogon's base stats / types / weight for every scraped form (data parity) |

## Generating

```
cd scripts
npm ci                      # @smogon/calc pinned by package-lock.json
node generate_goldens.mjs > ../tests/golden/gen9_cases.json
node generate_goldens.mjs champions > ../tests/golden/champions_cases.json
node generate_goldens.mjs champions-species > ../tests/golden/champions_species.json
```

(A shell opened before Node was installed won't have it on PATH; use
`"C:\Program Files\nodejs\node.exe"` or open a new terminal.) Regenerate
the champions files after every re-scrape. `tests/test_damage_golden.py`
picks the files up automatically (each test is skipped while its file is
absent).

## Champions mode notes

- Cases use OUR species names; the generator maps them to smogon's
  (`Mega Salamence` -> `Salamence-Mega`, `Heat Rotom` -> `Rotom-Heat`).
  `pokechamp.data.loader.showdown_name` is the Python mirror of that map —
  keep the two in sync.
- Each case carries a `ref` block (smogon's stats/types/move BP). The test
  checks it first, so a data divergence is reported as data, not as a
  calculator bug.
- Unnamed abilities are pinned to an inert `No Ability`: smogon otherwise
  defaults to the species' first ability (Mega Lucario Z -> Adaptability,
  since it lacks Champions' Aura Guard).
- smogon has no Champions-only abilities (Dragonize, Aura Guard, Fire Mane,
  Mega Sol, Eelevate, Piercing Drill, Spicy Spray) — those stay covered by
  unit tests only.
- Known data differences live in `SPECIES_PARITY_EXCEPTIONS` in
  `test_damage_golden.py`, each with its reason (e.g. smogon 0.11.0's Mega
  Starmie has Atk 140; serebii and the +100 Mega rule say 100).

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

`attacker`/`defender` objects are `PokemonSet` kwargs. Optional keys:
`hits`, `attacker_boosts`, `defender_boosts`, `is_crit`, `spread`,
`attacker_hp_fraction`, `metronome_uses`, `field`, and (champions) `ref`.
