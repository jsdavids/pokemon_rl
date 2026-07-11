"""Candidate set generation for the stat estimator.

v1 is an archetype grid: EV template x nature x item x ability. It
deliberately spans the plausible competitive space rather than every legal
spread — the posterior's job is to kill inconsistent candidates, and
archetypes are what players actually run. Phase 2 completion augments this
with sets derived from usage statistics (which also supply non-uniform
priors).

Rulesets that ship item/ability data (champions) drive the grid:
- items come from the ruleset's item list, optionally filtered to a
  regulation's legal pool (Champions has no Choice Band/Specs or Assault
  Vest at all, and Life Orb only exists from Regulation M-B);
- each of the species' listed abilities becomes a hypothesis (the damage
  calculator models Huge Power, Thick Fat, Multiscale, ...);
- Mega forms get no item variants: their slot is committed to the stone,
  which has no damage or speed effect.

The gen9 sample ruleset has no item/ability data, so it falls back to the
static mainline item list with a single ability hypothesis (None).
"""

from __future__ import annotations

from pokechamp.data import load_ruleset
from pokechamp.schemas import PokemonSet, stat_table

EV_TEMPLATES: list[dict[str, int]] = [
    {"atk": 252, "spe": 252, "hp": 4},   # fast physical
    {"spa": 252, "spe": 252, "hp": 4},   # fast special
    {"hp": 252, "atk": 252, "spe": 4},   # bulky physical
    {"hp": 252, "spa": 252, "spe": 4},   # bulky special
    {"hp": 252, "def": 252, "spd": 4},   # physical wall
    {"hp": 252, "spd": 252, "def": 4},   # special wall
    {"hp": 252, "def": 124, "spd": 128},  # mixed bulk
    {},                                   # uninvested
]

NATURES = [
    "Adamant", "Jolly",        # physical
    "Modest", "Timid",         # special
    "Impish", "Bold",          # physical bulk
    "Careful", "Calm",         # special bulk
    "Serious",                 # neutral
]

# Damage/speed-relevant held items the calculator models. The candidate pool
# is this list intersected with what the ruleset/regulation actually offers.
# (Type boosters like Charcoal are modeled by the calculator but excluded
# here: 18 per-type variants would bloat the grid for a rarely-run choice.)
CANDIDATE_ITEMS: list[str] = [
    "Choice Band",
    "Choice Specs",
    "Choice Scarf",
    "Life Orb",
    "Assault Vest",
    "Expert Belt",
    "Muscle Band",
    "Wise Glasses",
]


def legal_items(ruleset: str, regulation: str | None = None) -> list[str | None]:
    """Candidate items available in `ruleset`, optionally at `regulation`.

    Regulations are cumulative: each one's item list is what it *added*, so
    the pool at regulation R excludes items introduced by later regulations.
    """
    rs = load_ruleset(ruleset)
    if not rs.items:  # ruleset ships no item data (gen9 sample)
        return [None, *CANDIDATE_ITEMS]
    pool = [i for i in CANDIDATE_ITEMS if i in rs.items]
    if regulation is not None:
        if regulation not in rs.regulations:
            raise KeyError(
                f"Unknown regulation {regulation!r}; "
                f"ruleset has {sorted(rs.regulations)}"
            )
        later = [r for r in sorted(rs.regulations) if r > regulation]
        added_later = {i for r in later for i in rs.regulations[r]["items"]}
        pool = [i for i in pool if i not in added_later]
    return [None, *pool]


def default_candidates(
    species: str,
    level: int = 50,
    ruleset: str = "gen9",
    regulation: str | None = None,
) -> list[PokemonSet]:
    """EV templates x natures x legal items x ability hypotheses."""
    rs = load_ruleset(ruleset)
    sp = rs.get_species(species)  # fail loud: species not in this ruleset

    is_mega = sp.base_species is not None and sp.name.startswith("Mega ")
    items = [None] if is_mega else legal_items(ruleset, regulation)
    abilities: list[str | None] = list(sp.abilities) or [None]

    out = []
    for template in EV_TEMPLATES:
        for nature in NATURES:
            for item in items:
                for ability in abilities:
                    out.append(
                        PokemonSet(
                            species=species,
                            level=level,
                            nature=nature,
                            evs={**stat_table(), **template},
                            item=item,
                            ability=ability,
                        )
                    )
    return out
