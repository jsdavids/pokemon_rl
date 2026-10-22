"""Ability damage modifiers, in 4096ths, mirroring @smogon/calc's pipeline.

Only damage-relevant abilities are modeled. Unknown abilities are ignored
silently — unlike items, the vast majority of abilities have no effect on a
single damage roll, so warning on every Intimidate/Prankster would be noise.

Conditional abilities we can't verify from a lone calculation (Multiscale's
full-HP requirement) are assumed active; the calculator notes the assumption.
"""

from __future__ import annotations

from pokechamp.data.loader import norm

# defender ability -> attacking type it nullifies
TYPE_IMMUNITIES: dict[str, str] = {
    "levitate": "ground",
    "eartheater": "ground",
    "flashfire": "fire",
    "wellbakedbody": "fire",
    "waterabsorb": "water",
    "stormdrain": "water",
    "dryskin": "water",
    "voltabsorb": "electric",
    "lightningrod": "electric",
    "motordrive": "electric",
    "sapsipper": "grass",
    "eelevate": "ground",  # Champions: Mega Eelektross floats like Levitate
}

# Normal-type moves become this type with a 1.2x power boost. Dragonize is
# Champions' own (Mega Feraligatr); Aerilate returns with Mega Salamence (M-C).
ATE_ABILITIES: dict[str, str] = {
    "aerilate": "flying",
    "pixilate": "fairy",
    "refrigerate": "ice",
    "galvanize": "electric",
    "dragonize": "dragon",
}

# abilities that keep the holder off the ground (terrain, Spikes, Ground)
FLOATING_ABILITIES = {"levitate", "eelevate"}


def grants_immunity(ability: str | None, move_type: str) -> bool:
    return ability is not None and TYPE_IMMUNITIES.get(norm(ability)) == move_type.lower()


def ate_type(ability: str | None, move_type: str) -> str | None:
    """The type an -ate ability turns this move into, or None if it doesn't."""
    if ability is None or move_type.lower() != "normal":
        return None
    return ATE_ABILITIES.get(norm(ability))


def forces_sun(ability: str | None) -> bool:
    """Mega Sol (Mega Meganium): its moves act as if the sun were out."""
    return ability is not None and norm(ability) == "megasol"


def makes_contact(
    flags: frozenset[str], attacker_ability: str | None, attacker_item: str | None
) -> bool:
    """Whether the move actually makes contact once Long Reach / Punching
    Glove are accounted for. Gates Tough Claws, Fluffy, Rocky Helmet, etc."""
    if "contact" not in flags:
        return False
    if attacker_ability and norm(attacker_ability) == "longreach":
        return False
    if attacker_item and norm(attacker_item) == "punchingglove":
        return False
    return True


def bp_mods(
    ability: str | None,
    base_power: int,
    flags: frozenset[str],
    contact: bool,
    move_type: str = "",
    ate_converted: bool = False,
) -> list[int]:
    if ability is None:
        return []
    a = norm(ability)
    mods = []
    if ate_converted:  # Aerilate/Pixilate/Dragonize/...: 1.2x on converted moves
        mods.append(4915)
    if a == "firemane" and move_type.lower() == "fire":  # Mega Pyroar
        mods.append(6144)
    if a == "technician" and base_power <= 60:
        mods.append(6144)
    if a == "toughclaws" and contact:
        mods.append(5325)
    if a == "punkrock" and "sound" in flags:
        mods.append(5325)
    if a == "ironfist" and "punch" in flags:
        mods.append(4915)
    if a == "strongjaw" and "bite" in flags:
        mods.append(6144)
    if a == "sharpness" and "slicing" in flags:
        mods.append(6144)
    return mods


def offensive_stat_mods(ability: str | None, status: str | None, uses_atk: bool) -> list[int]:
    """Mods on the attacker's offensive stat. uses_atk: the move draws from
    Attack (so not Body Press's Defense, not a special move's SpA)."""
    if ability is None:
        return []
    a = norm(ability)
    mods = []
    if a in ("hugepower", "purepower") and uses_atk:
        mods.append(8192)
    if a == "guts" and status and uses_atk:
        mods.append(6144)
    return mods


def defensive_attack_mods(ability: str | None, move_type: str) -> list[int]:
    """Mods the *defender's* ability applies to the attacker's offensive stat."""
    if ability is None:
        return []
    if norm(ability) == "thickfat" and move_type.lower() in ("fire", "ice"):
        return [2048]
    return []


def defense_mods(ability: str | None, targets_def: bool, defender_status: str | None) -> list[int]:
    if ability is None:
        return []
    a = norm(ability)
    mods = []
    if a == "furcoat" and targets_def:
        mods.append(8192)
    if a == "marvelscale" and targets_def and defender_status:
        mods.append(6144)
    return mods


def has_adaptability(ability: str | None) -> bool:
    return ability is not None and norm(ability) == "adaptability"


def ignores_burn(ability: str | None) -> bool:
    return ability is not None and norm(ability) == "guts"


def final_mods(
    attacker_ability: str | None,
    defender_ability: str | None,
    eff: float,
    category: str,
    move_type: str,
    flags: frozenset[str],
    contact: bool,
) -> list[int]:
    """Combined attacker+defender ability final mods, in @smogon/calc's chain
    order (which matters because the chain rounds at each link):
    Multiscale -> Fluffy contact -> Punk Rock/Ice Scales -> Tinted Lens ->
    Fluffy fire -> Filter/Solid Rock/Prism Armor.
    Aura Guard (Champions, Mega Lucario Z) halves contact damage exactly like
    Fluffy's contact half, so it shares that link of the chain.
    """
    atk = norm(attacker_ability) if attacker_ability else ""
    dfn = norm(defender_ability) if defender_ability else ""
    mods = []
    if dfn in ("multiscale", "shadowshield"):
        mods.append(2048)  # assumes full HP; calculator adds a note
    if dfn in ("fluffy", "auraguard") and contact:
        mods.append(2048)
    if (dfn == "punkrock" and "sound" in flags) or (dfn == "icescales" and category == "Special"):
        mods.append(2048)
    if atk == "tintedlens" and eff < 1:
        mods.append(8192)
    if dfn == "fluffy" and move_type.lower() == "fire":
        mods.append(8192)
    if dfn in ("filter", "solidrock", "prismarmor") and eff > 1:
        mods.append(3072)
    return mods
