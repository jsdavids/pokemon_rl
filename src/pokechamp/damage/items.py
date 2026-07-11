"""Item damage modifiers, expressed in 4096ths exactly as the games chain them.

Only items with a direct damage effect live here. Items whose effect is
battle-flow (Choice lock, Focus Sash, Leftovers, Rocky Helmet) matter to the
estimator/recommender, not to a single damage calculation.

Modifier slots, matching @smogon/calc's pipeline:
- base power mods  -> max(1, pokeRound(bp * chain / 4096))
- attack stat mods -> pokeRound(stat * chain / 4096)
- defense stat mods-> pokeRound(stat * chain / 4096)
- final mods       -> pokeRound(max(1, damage * chain / 4096)), per roll
"""

from __future__ import annotations

from pokechamp.data.loader import norm

# 1.2x (4915/4096) base-power boosters, keyed by the move type they boost.
TYPE_BOOST_ITEMS: dict[str, str] = {
    "silkscarf": "normal",
    "charcoal": "fire",
    "mysticwater": "water",
    "magnet": "electric",
    "miracleseed": "grass",
    "nevermeltice": "ice",
    "blackbelt": "fighting",
    "poisonbarb": "poison",
    "softsand": "ground",
    "sharpbeak": "flying",
    "twistedspoon": "psychic",
    "silverpowder": "bug",
    "hardstone": "rock",
    "spelltag": "ghost",
    "dragonfang": "dragon",
    "blackglasses": "dark",
    "metalcoat": "steel",
    "fairyfeather": "fairy",
}

IMPLEMENTED = set(TYPE_BOOST_ITEMS) | {
    "choiceband",
    "choicespecs",
    "lifeorb",
    "expertbelt",
    "assaultvest",
    "muscleband",
    "wiseglasses",
    "punchingglove",
}

# Known items with no effect on a single damage roll (speed, recovery,
# battle-flow). Correct to ignore here; they matter to the estimator and
# recommender instead.
NO_DAMAGE_EFFECT = {
    "choicescarf",
    "leftovers",
    "focussash",
    "rockyhelmet",
    "boosterenergy",
    "sitrusberry",
    "lumberry",
    "heavydutyboots",
    "safetygoggles",
    "clearamulet",
    "covertcloak",
    "ejectbutton",
    "redcard",
    "mentalherb",
    "widelens",
    "loadeddice",
}


def is_implemented(item: str | None) -> bool:
    # berries are single-use battle-state items handled by the simulator
    # (resist berries halve damage there, not in this stateless calculator)
    return (
        item is None
        or norm(item) in IMPLEMENTED
        or norm(item) in NO_DAMAGE_EFFECT
        or norm(item).endswith("berry")
    )


def base_power_mods(
    item: str | None, move_type: str, category: str, flags: frozenset[str] = frozenset()
) -> list[int]:
    if item is None:
        return []
    i = norm(item)
    mods = []
    if TYPE_BOOST_ITEMS.get(i) == move_type.lower():
        mods.append(4915)
    if i == "muscleband" and category == "Physical":
        mods.append(4505)
    if i == "wiseglasses" and category == "Special":
        mods.append(4505)
    if i == "punchingglove" and "punch" in flags:
        mods.append(4506)  # also negates contact; see abilities.makes_contact
    return mods


def attack_mods(item: str | None, category: str) -> list[int]:
    if item is None:
        return []
    i = norm(item)
    if i == "choiceband" and category == "Physical":
        return [6144]
    if i == "choicespecs" and category == "Special":
        return [6144]
    return []


def defense_mods(item: str | None, category: str) -> list[int]:
    if item is None:
        return []
    if norm(item) == "assaultvest" and category == "Special":
        return [6144]
    return []


def final_mods(item: str | None, type_effectiveness: float) -> list[int]:
    if item is None:
        return []
    i = norm(item)
    mods = []
    if i == "expertbelt" and type_effectiveness > 1:
        mods.append(4915)
    if i == "lifeorb":
        mods.append(5324)
    return mods
