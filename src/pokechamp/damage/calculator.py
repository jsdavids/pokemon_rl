"""Damage calculator (Phase 1).

Gen 5+ damage formula with the modifier pipeline applied in cartridge order,
mirroring @smogon/calc's operation order exactly — the integer truncation
points, half-down rounding (poke_round), and 4096ths modifier chains are all
load-bearing. The acceptance gate for this module is the golden test suite
generated from @smogon/calc (tests/golden/).

Pipeline: base power mods (Facade, Technician, terrain, items)
  -> attack (boosts, Huge Power/Guts/Thick Fat, Choice items)
  -> defense (boosts, Fur Coat/Marvel Scale, sand/snow, Assault Vest)
  -> base damage -> spread -> weather -> crit
  -> per roll: random -> STAB (incl. Tera) -> type -> burn
  -> final mods (screens, Multiscale, Ice Scales, Tinted Lens, Filter, items)

Special-cased moves: Body Press (attacks with Def), Psyshock/Psystrike/Secret
Sword (hit Def), Seismic Toss/Night Shade (fixed level damage), Facade.

Multi-hit moves: `rolls` is one hit; DamageResult.hits scales the totals, and
total_distribution() gives the exact pmf of the sum. Flag-conditional effects
(contact, sound, punch, bite, slicing) are driven by Move.flags, with
Long Reach / Punching Glove correctly negating contact.

TODO (known gaps):
- per-hit variation within multi-hit (per-hit crits, Multiscale first hit
  only, Loaded Dice, Triple Axel's escalating base power)
- HP-state-conditional exactness (Multiscale assumed at full HP, noted)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field

from pokechamp.damage import abilities, items
from pokechamp.data import load_ruleset
from pokechamp.data.loader import norm
from pokechamp.schemas import FieldState, PokemonSet
from pokechamp.stats import calc_all_stats

FIXED_LEVEL_DAMAGE_MOVES = {"seismictoss", "nightshade"}
WEIGHT_TARGET_MOVES = {"grassknot", "lowkick"}  # BP from target weight
WEIGHT_RATIO_MOVES = {"heavyslam", "heatcrash"}  # BP from weight ratio
HP_SCALED_MOVES = {"eruption", "waterspout", "dragonenergy"}  # BP from user HP


def weight_tier_bp(kg: float) -> int:
    """Grass Knot / Low Kick base power from the target's weight."""
    for limit, bp in ((10, 20), (25, 40), (50, 60), (100, 80), (200, 100)):
        if kg < limit:
            return bp
    return 120


def weight_ratio_bp(attacker_kg: float, defender_kg: float) -> int:
    """Heavy Slam / Heat Crash base power from the weight ratio."""
    ratio = attacker_kg / max(defender_kg, 0.1)
    for limit, bp in ((5, 120), (4, 100), (3, 80), (2, 60)):
        if ratio >= limit:
            return bp
    return 40
HITS_PHYSICAL_DEFENSE = {"psyshock", "psystrike", "secretsword"}
GRASSY_TERRAIN_HALVED = {"earthquake", "bulldoze", "magnitude"}
TERRAIN_BOOSTED_TYPE = {"electric": "electric", "grassy": "grass", "psychic": "psychic"}


def poke_round(x: float) -> int:
    """Game rounding: round half DOWN (0.5 truncates, >0.5 rounds up)."""
    frac = x - math.floor(x)
    return math.floor(x) if frac <= 0.5 else math.ceil(x)


def chain_mods(mods: list[int]) -> int:
    """Chain 4096ths modifiers the way the games do (rounded at each link)."""
    chained = 4096
    for m in mods:
        chained = (chained * m + 2048) >> 12
    return chained


def boosted_stat(stat: int, stage: int) -> int:
    """Apply a -6..+6 boost stage."""
    if stage >= 0:
        return math.floor(stat * (2 + stage) / 2)
    return math.floor(stat * 2 / (2 - stage))


@dataclass
class DamageResult:
    move: str
    attacker: str
    defender: str
    rolls: list[int]  # 16 values PER HIT, low to high roll
    defender_hp: int
    type_effectiveness: float
    stab: float
    hits: int = 1
    notes: list[str] = dc_field(default_factory=list)

    # min/max/percent/OHKO are TOTALS across all hits; `rolls` is one hit.

    @property
    def min_damage(self) -> int:
        return self.rolls[0] * self.hits

    @property
    def max_damage(self) -> int:
        return self.rolls[-1] * self.hits

    @property
    def min_percent(self) -> float:
        return 100.0 * self.min_damage / self.defender_hp

    @property
    def max_percent(self) -> float:
        return 100.0 * self.max_damage / self.defender_hp

    @property
    def guaranteed_ohko(self) -> bool:
        return self.min_damage >= self.defender_hp

    @property
    def possible_ohko(self) -> bool:
        return self.max_damage >= self.defender_hp

    def total_distribution(self) -> dict[int, float]:
        """Exact pmf of total damage: each hit is an independent uniform draw
        over the 16 rolls, convolved `hits` times. This is what KO-chance and
        the estimator's multi-hit likelihoods are built on."""
        per_hit: dict[int, float] = {}
        for r in self.rolls:
            per_hit[r] = per_hit.get(r, 0.0) + 1.0 / 16.0
        dist = {0: 1.0}
        for _ in range(self.hits):
            nxt: dict[int, float] = {}
            for total, p in dist.items():
                for v, q in per_hit.items():
                    nxt[total + v] = nxt.get(total + v, 0.0) + p * q
            dist = nxt
        return dict(sorted(dist.items()))

    def ko_chance(self) -> float:
        """Probability the total damage KOs a full-HP defender (rolls only;
        crits and accuracy not included)."""
        return sum(p for total, p in self.total_distribution().items() if total >= self.defender_hp)

    def describe(self) -> str:
        hits = f" ({self.hits} hits)" if self.hits > 1 else ""
        return (
            f"{self.attacker} {self.move}{hits} vs {self.defender}: "
            f"{self.min_damage}-{self.max_damage} "
            f"({self.min_percent:.1f}-{self.max_percent:.1f}%)"
        )


def _effective_types(pkm: PokemonSet, species_types: tuple[str, ...]) -> tuple[str, ...]:
    """Terastallizing replaces the Pokemon's defensive typing."""
    return (pkm.tera_type.lower(),) if pkm.tera_type else species_types


def _grounded(pkm: PokemonSet, species_types: tuple[str, ...]) -> bool:
    if items.has_iron_ball(pkm.item):
        return True  # overrides Flying type, Levitate, Air Balloon
    types = _effective_types(pkm, species_types)
    if "flying" in types:
        return False
    if pkm.ability and norm(pkm.ability) in abilities.FLOATING_ABILITIES:
        return False
    if items.has_air_balloon(pkm.item):
        return False
    return True  # TODO: Gravity, Magnet Rise


def _stab_mod(move_type: str, original_types: tuple[str, ...], attacker: PokemonSet) -> int:
    original = move_type in original_types
    tera = attacker.tera_type is not None and attacker.tera_type.lower() == move_type
    adapt = abilities.has_adaptability(attacker.ability)
    if original and tera:
        return 9216 if adapt else 8192
    if original or tera:
        return 8192 if adapt else 6144
    return 4096


def calc_damage(
    attacker: PokemonSet,
    defender: PokemonSet,
    move_name: str,
    field: FieldState | None = None,
    spread: bool = False,
    is_crit: bool = False,
    hits: int | None = None,
    attacker_boosts: dict[str, int] | None = None,
    defender_boosts: dict[str, int] | None = None,
    attacker_hp_fraction: float = 1.0,  # Eruption / Water Spout scaling
    metronome_uses: int = 0,  # prior consecutive uses (Metronome item)
    ruleset: str = "gen9",
) -> DamageResult:
    rs = load_ruleset(ruleset)
    move = rs.get_move(move_name)
    atk_species = rs.get_species(attacker.species)
    def_species = rs.get_species(defender.species)
    field = field or FieldState()
    move_id = norm(move.name)

    def_stats = calc_all_stats(
        def_species.base_stats, defender.level, defender.nature, defender.ivs, defender.evs
    )

    notes: list[str] = []
    for owner, item in (("attacker", attacker.item), ("defender", defender.item)):
        if not items.is_implemented(item):
            notes.append(f"{owner} item {item} not implemented, ignored")

    # Multi-hit: all hits are computed identically (per-hit crits, Multiscale
    # first-hit-only, and Loaded Dice are not modeled yet).
    n_hits = 1
    if move.multihit:
        lo, hi = move.multihit
        n_hits = hits if hits is not None else (lo if lo == hi else 3)
        n_hits = max(lo, min(hi, n_hits))
        notes.append(f"{n_hits} hits")
    elif hits is not None and hits != 1:
        notes.append("hits argument ignored: not a multi-hit move")

    def result(rolls: list[int], eff: float = 1.0, stab: float = 1.0) -> DamageResult:
        return DamageResult(
            move=move.name,
            attacker=atk_species.name,
            defender=def_species.name,
            rolls=rolls,
            defender_hp=def_stats["hp"],
            type_effectiveness=eff,
            stab=stab,
            hits=n_hits if rolls[-1] > 0 else 1,
            notes=notes,
        )

    if move.category == "Status":
        notes.append("status move")
        return result([0] * 16)

    # -ate abilities retype Normal moves before anything reads the type:
    # effectiveness, STAB, immunities, terrain, weather, items all see it
    move_type = move.type
    converted = abilities.ate_type(attacker.ability, move.type)
    if converted:
        move_type = converted
        notes.append(f"{attacker.ability}: {move.name} becomes {converted}-type")

    def_types = _effective_types(defender, def_species.types)
    iron_ball = items.has_iron_ball(defender.item)
    eff = rs.type_effectiveness(move_type, def_types)
    if move_type == "ground" and iron_ball and "flying" in def_types:
        # a grounded Flying type takes Ground hits NEUTRALLY, whatever its
        # other type (Showdown's per-type handler neutralizes every type
        # component; @smogon/calc turns the 0x into 1x) — Iron Ball
        # Skarmory takes 1x, not its Steel weakness
        eff = 1.0
        notes.append(f"{def_species.name}'s Iron Ball grounds it (neutral)")
    if move_type == "ground" and iron_ball:
        pass  # Levitate / Eelevate don't lift an Iron Ball holder
    elif abilities.grants_immunity(defender.ability, move_type):
        eff = 0.0
        notes.append(f"immune ({def_species.name}'s {defender.ability})")
    elif move_type == "ground" and items.has_air_balloon(defender.item):
        eff = 0.0
        notes.append(f"immune ({def_species.name}'s Air Balloon, until it pops)")
    if eff == 0:
        if "immune" not in " ".join(notes):
            notes.append("immune")
        return result([0] * 16, eff=0.0)

    if move_id in FIXED_LEVEL_DAMAGE_MOVES:
        notes.append("fixed damage (attacker's level)")
        return result([attacker.level] * 16, eff=eff)

    if move.base_power == 0 and move_id not in (WEIGHT_TARGET_MOVES | WEIGHT_RATIO_MOVES):
        return result([0] * 16, eff=eff)

    atk_stats = calc_all_stats(
        atk_species.base_stats, attacker.level, attacker.nature, attacker.ivs, attacker.evs
    )
    physical = move.category == "Physical"
    uses_stat = "def" if move_id == "bodypress" else ("atk" if physical else "spa")
    targets_stat = "def" if physical or move_id in HITS_PHYSICAL_DEFENSE else "spd"
    contact = abilities.makes_contact(move.flags, attacker.ability, attacker.item)

    # --- Base power ---
    bp = move.base_power
    if move_id == "facade" and attacker.status:
        bp *= 2
        notes.append("Facade boosted by status")
    elif move_id in WEIGHT_TARGET_MOVES:
        bp = weight_tier_bp(def_species.weight)
        notes.append(f"BP {bp} from {def_species.name}'s {def_species.weight}kg")
    elif move_id in WEIGHT_RATIO_MOVES:
        bp = weight_ratio_bp(atk_species.weight, def_species.weight)
        notes.append(f"BP {bp} from weight ratio")
    elif move_id in HP_SCALED_MOVES and attacker_hp_fraction < 1.0:
        # reference computes via integer current HP, not the raw fraction
        cur_hp = math.floor(atk_stats["hp"] * attacker_hp_fraction)
        bp = max(1, math.floor(bp * cur_hp / atk_stats["hp"]))
        notes.append(f"BP {bp} at {100 * attacker_hp_fraction:.0f}% HP")

    # dynamic BP feeds ability mods (a 40 BP Grass Knot is Technician-boosted)
    bp_mod_list = abilities.bp_mods(
        attacker.ability, bp, move.flags, contact,
        move_type=move_type, ate_converted=converted is not None,
    )
    if field.terrain:
        terrain = field.terrain.lower()
        if (
            TERRAIN_BOOSTED_TYPE.get(terrain) == move_type
            and _grounded(attacker, atk_species.types)
        ):
            bp_mod_list.append(5325)
        if terrain == "misty" and move_type == "dragon" and _grounded(defender, def_species.types):
            bp_mod_list.append(2048)
        if terrain == "grassy" and move_id in GRASSY_TERRAIN_HALVED:
            bp_mod_list.append(2048)
    bp_mod_list += items.base_power_mods(attacker.item, move_type, move.category, move.flags)
    bp_mod = chain_mods(bp_mod_list)
    if bp_mod != 4096:
        bp = max(1, poke_round(bp * bp_mod / 4096))

    # --- Attack (crits ignore the attacker's negative stages) ---
    atk_stage = (attacker_boosts or {}).get(uses_stat, 0)
    if is_crit and atk_stage < 0:
        atk_stage = 0
    a = boosted_stat(atk_stats[uses_stat], atk_stage)
    at_mods = (
        abilities.offensive_stat_mods(attacker.ability, attacker.status, uses_stat == "atk")
        + abilities.defensive_attack_mods(defender.ability, move_type)
        + items.attack_mods(attacker.item, move.category, atk_species.name)
    )
    atk_mod = chain_mods(at_mods)
    if atk_mod != 4096:
        a = poke_round(a * atk_mod / 4096)

    # --- Defense (crits ignore the defender's positive stages) ---
    def_stage = (defender_boosts or {}).get(targets_stat, 0)
    if is_crit and def_stage > 0:
        def_stage = 0
    d = boosted_stat(def_stats[targets_stat], def_stage)
    df_mods = abilities.defense_mods(defender.ability, targets_stat == "def", defender.status)
    weather = (field.weather or "").lower()
    if weather == "sand" and "rock" in def_types and targets_stat == "spd":
        df_mods.append(6144)
    if weather == "snow" and "ice" in def_types and targets_stat == "def":
        df_mods.append(6144)
    df_mods += items.defense_mods(defender.item, move.category)
    def_mod = chain_mods(df_mods)
    if def_mod != 4096:
        d = poke_round(d * def_mod / 4096)

    # --- Base damage ---
    level = attacker.level
    base = math.floor(math.floor(math.floor(2 * level / 5 + 2) * bp * a / d) / 50) + 2

    if spread and move.spread:
        base = poke_round(base * 3072 / 4096)
        notes.append("spread move in doubles")

    # Mega Sol: the holder's own moves act as if the sun were out
    attack_weather = "sun" if abilities.forces_sun(attacker.ability) else weather
    if attack_weather in ("sun", "rain"):
        weather_mod = {
            ("sun", "fire"): 6144,
            ("sun", "water"): 2048,
            ("rain", "water"): 6144,
            ("rain", "fire"): 2048,
        }.get((attack_weather, move_type))
        if weather_mod:
            base = poke_round(base * weather_mod / 4096)

    if is_crit:
        base = math.floor(base * 1.5)
        notes.append("critical hit")

    # --- Per-roll modifiers ---
    stab_mod = _stab_mod(move_type, atk_species.types, attacker)
    burned = (
        physical
        and attacker.status == "brn"
        and not abilities.ignores_burn(attacker.ability)
        and move_id != "facade"
    )
    if burned:
        notes.append("burned attacker")

    final_list: list[int] = []
    if not is_crit:  # screens are ignored by crits
        side = field.defender_side
        if (physical and (side.reflect or side.aurora_veil)) or (
            not physical and (side.light_screen or side.aurora_veil)
        ):
            final_list.append(2048)
    if defender.ability and norm(defender.ability) in ("multiscale", "shadowshield"):
        notes.append(f"assumes full HP ({defender.ability})")
        if n_hits > 1:
            notes.append("Multiscale first-hit-only interaction not modeled")
    final_list += abilities.final_mods(
        attacker.ability, defender.ability, eff, move.category, move_type, move.flags, contact
    )
    final_list += items.final_mods(attacker.item, eff, metronome_uses)
    if metronome_uses and attacker.item and norm(attacker.item) == "metronome":
        notes.append(f"Metronome: use #{metronome_uses + 1} in a row")
    final_mod = chain_mods(final_list)

    rolls: list[int] = []
    for r in range(85, 101):
        dmg = math.floor(base * r / 100)
        if stab_mod != 4096:
            dmg = poke_round(dmg * stab_mod / 4096)
        dmg = math.floor(dmg * eff)
        if burned:
            dmg = math.floor(dmg / 2)
        dmg = poke_round(max(1, dmg * final_mod / 4096))
        rolls.append(dmg)

    return result(rolls, eff=eff, stab=stab_mod / 4096)


def calc_damage_matrix(
    attacker: PokemonSet,
    moves: list[str],
    defenders: list[PokemonSet],
    field: FieldState | None = None,
    ruleset: str = "gen9",
) -> dict[str, list[DamageResult]]:
    """Batch API: every move against every hypothetical defender build.

    This is the shape both the stat estimator (enumerating candidate spreads)
    and the LLM tool layer ("what does Ice Beam do to bulky vs offensive
    Garchomp?") consume.
    """
    return {
        m: [calc_damage(attacker, d, m, field=field, ruleset=ruleset) for d in defenders]
        for m in moves
    }
