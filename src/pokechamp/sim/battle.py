"""Battle simulator on the champions ruleset (Phase 3).

Singles and doubles (doubles is Champions' primary competitive format).
This is the recommender's rollout engine: fast turns, exact Champions data,
seeded determinism. A Showdown bridge may later cross-check mechanics.

Modeled:
- damaging moves: accuracy, crits (4.17%), the calculator's 16 rolls,
  multi-hit (35/35/15/15), priority, immunities, items, abilities, Life Orb
  recoil, spread mechanics (allAdjacent hits the ally; reduction only when
  multiple targets are hit), per-target accuracy/crit, retargeting;
- status moves (curated table in effects.py): setup/stat drops, sleep/para/
  burn/toxic with type immunities and powder-vs-grass, recovery, Protect
  (1/3^n consecutive success), Wide Guard, Follow Me/Rage Powder
  redirection, Helping Hand (~1.5x, applied post-roll), screens, Tailwind,
  weather/terrain/Trick Room with real durations;
- secondary effects on damaging moves (curated): status riders, flinch,
  stat drops, self-drops (Close Combat); Fake Out is first-turn-only;
- non-volatile status behavior: burn halves physical (via the calculator),
  para speed cut + 25% full para, sleep 1-3 turns, freeze with 20% thaw,
  poison/toxic end-of-turn chip; boosts and toxic counter reset on switch;
- end of turn: status chip, sand chip, Leftovers, condition timers.

Also modeled: entry hazards (Stealth Rock by weakness, Spikes layers,
Toxic Spikes with poison-type absorption, Sticky Web; Defog/Rapid Spin
clearing), terrain side effects (Grassy healing, Electric/Misty status
blocking, Psychic priority blocking — all grounded-only), PP tracking with
Struggle when exhausted, and trapping (Shadow Tag/Arena Trap/Magnet Pull
with the ghost exemption; binding moves chip 1/8 for 4-5 turns).

Also: Substitute (damage/status/rider absorption, sound bypass), weather
and terrain abilities on switch-in (speed-ordered, so the slower lead wins
a contested weather war; weather rocks extend to 8 turns), Lightning Rod /
Storm Drain redirection with real absorb effects (heals/boosts), Safeguard.

Also: Imposter/Transform (form copy, revert on switch), Baton Pass (boosts
+ Substitute handoff), Taunt/Encore/Choice-Scarf move locking, confusion,
Perish Song + Soundproof, Disable, phazing (Whirlwind/Roar/Dragon Tail),
and weight/HP-scaled base power (Grass Knot, Heavy Slam, Eruption — golden
parity in the calculator, live HP wired through here).

Also: berries (threshold heals, status cures, super-effective halving; all
single-use) with Unnerve denial, Destiny Bond, Counter/Mirror Coat, Wish,
Heal Bell/Aromatherapy.

Regulation M-C items: Air Balloon (ungrounded + Ground immunity until any
damaging hit pops it), Rocky Helmet (1/6 to contact attackers; Rough Skin /
Iron Barbs 1/8 ride the same hook), Red Card / Eject Button (forced switch,
random replacement), Normal Gem (consumed), Binding Band (1/6 trap chip),
Terrain Extender (8-turn terrain) and the four terrain Seeds. Champions
abilities with sim-level effects: Spicy Spray (burns attackers), Eelevate
(floats; +1 highest stat on a KO, like Beast Boost), Piercing Drill
(contact moves through Protect at 1/4), Thermal Exchange (+1 Atk when hit
by Fire, burn-immune).

Every held item in the Champions pool is modeled: crit stages (Gen 7+
ladder over the 1/24 base: high-crit moves, Scope Lens, Super Luck, Leek,
Focus Energy / Dragon Cheer; always-crit moves; Battle/Shell Armor), Quick Claw (20% first in bracket),
Bright Powder (0.9x accuracy), King's Rock (10% flinch), Focus Sash /
Sturdy (single hit from full HP) and Focus Band (10%), Shell Bell (1/8 of
damage dealt), White Herb, Metronome (consecutive-use chain), Iron Ball
(half Speed, grounded), Light Ball (calculator). Status-immunity abilities
(Limber, Insomnia, Water Veil, ...) and KO boosts (Moxie, Beast Boost)
share those hooks.

The engine now covers every mechanic with competitive relevance that we
know of; anything still missing (Metronome randomness, Z-move relics,
Present) is trivia rather than strategy.
"""

from __future__ import annotations

import math
import random
from copy import deepcopy
from dataclasses import dataclass, replace

from pokechamp.damage import calc_damage
from pokechamp.damage.abilities import FLOATING_ABILITIES, ate_type, makes_contact
from pokechamp.damage.calculator import boosted_stat
from pokechamp.damage.items import has_air_balloon, has_iron_ball
from pokechamp.data import load_ruleset
from pokechamp.data.loader import norm
from pokechamp.schemas import (
    BattleState,
    FieldState,
    PokemonSet,
    PokemonSnapshot,
    SideState,
    TeamSnapshot,
)
from pokechamp.sim.effects import (
    ABSORB_ABILITIES,
    ALWAYS_CRIT_MOVES,
    BINDING_BAND_DENOM,
    BRIGHT_POWDER_ACCURACY,
    CONFUSION_CURE_BERRIES,
    CONTACT_CHIP_ABILITIES,
    CRIT_IMMUNE_ABILITIES,
    CRIT_RATES,
    FOCUS_BAND_CHANCE,
    HIGH_CRIT_MOVES,
    KINGS_ROCK_CHANCE,
    KO_BOOST_ABILITIES,
    LEEK_SPECIES,
    PIERCE_PROTECT_FRACTION,
    QUICK_CLAW_CHANCE,
    STATUS_IMMUNE_ABILITIES,
    DURATIONS,
    FIRST_TURN_ONLY,
    HEAL_BERRIES,
    RESIST_BERRIES,
    STATUS_CURE_BERRIES,
    REDIRECT_ABILITIES,
    ROCKY_HELMET_DENOM,
    SECONDARY_EFFECTS,
    STATUS_MOVES,
    STATUS_TYPE_IMMUNITY,
    TERRAIN_ABILITIES,
    TERRAIN_EXTENDER,
    TERRAIN_SEEDS,
    TRAP_CHIP_DENOM,
    TRAP_MOVES,
    TRAPPING_ABILITIES,
    WEATHER_ABILITIES,
    WEATHER_ROCKS,
)
from pokechamp.stats import calc_all_stats

CRIT_RATE = CRIT_RATES[0]  # serebii lists 4.17% as the Champions base rate
MULTIHIT_2_5_WEIGHTS = (35, 35, 15, 15)  # 2, 3, 4, 5 hits
TURN_CAP = 300
BOOST_KEYS = ("atk", "def", "spa", "spd", "spe")


@dataclass(frozen=True)
class Action:
    """One slot's choice for the turn.

    kind="move": index = move slot; target = opposing slot for single-target
    moves (None = auto-pick). Spread/self/field moves ignore target.
    kind="switch": index = bench (team) index.
    """

    kind: str  # "move" | "switch" | "pass"
    index: int = 0
    target: int | None = None

    def __repr__(self) -> str:
        t = f"->{self.target}" if self.target is not None else ""
        return f"{self.kind}:{self.index}{t}"


class BattlePokemon:
    """A PokemonSet plus mutable battle state. The set is deep-copied: the
    sim inflicts status onto it (so the damage calculator sees burns), and
    callers' team lists must survive reuse across games."""

    def __init__(self, pset: PokemonSet, ruleset: str):
        self.set = replace(
            pset,
            moves=list(pset.moves),
            evs=dict(pset.evs),
            ivs=dict(pset.ivs),
        )
        rs = load_ruleset(ruleset)
        self.types = rs.get_species(pset.species).types
        base = rs.get_species(pset.species).base_stats
        self.stats = calc_all_stats(base, pset.level, pset.nature, pset.ivs, pset.evs)
        self.max_hp = self.stats["hp"]
        self.hp = self.max_hp
        self.boosts = {k: 0 for k in BOOST_KEYS}
        self.pp = [rs.get_move(m).pp or 10 for m in pset.moves]
        # volatiles
        self.sleep_turns = 0
        self.tox_counter = 0
        self.flinched = False
        self.protected = False
        self.consecutive_protects = 0
        self.helping_hand = False
        self.turns_on_field = 0
        self.trapped_turns = 0  # partial-trap moves (Fire Spin ...)
        self.trap_denom = TRAP_CHIP_DENOM  # 6 if the binder held Binding Band
        self.sub_hp = 0  # Substitute doll HP (0 = no sub)
        self.taunt_turns = 0
        self.encore_turns = 0
        self.encore_index: int | None = None
        self.confusion_turns = 0
        self.choice_lock: int | None = None  # Choice item move lock
        self.last_move_index: int | None = None
        self.metronome_streak = 0  # prior consecutive uses of the last move
        self.crit_stage = 0  # Focus Energy (+2) / Dragon Cheer (+1 or +2)
        self.perish_count = 0  # 0 = not perishing; faints when it hits 0 from 1
        self.disabled_index: int | None = None
        self.disabled_turns = 0
        self.destiny_bond = False
        # (attacker, damage) of the last hit taken this turn, by category
        self.last_hit_physical: tuple | None = None
        self.last_hit_special: tuple | None = None
        self.transformed = False
        self._orig = None  # pre-Transform state, stashed at transform time

    @property
    def grounded(self) -> bool:
        if has_iron_ball(self.set.item):
            return True  # beats Flying type, Levitate and Air Balloon alike
        if "flying" in self.types:
            return False
        ability = self.set.ability
        if ability and norm(ability) in FLOATING_ABILITIES:
            return False
        return not has_air_balloon(self.set.item)

    @property
    def status(self) -> str | None:
        return self.set.status

    @status.setter
    def status(self, value: str | None) -> None:
        self.set.status = value

    @property
    def fainted(self) -> bool:
        return self.hp <= 0

    @property
    def hp_fraction(self) -> float:
        return max(0.0, self.hp / self.max_hp)

    def reset_on_switch(self) -> None:
        self.boosts = {k: 0 for k in BOOST_KEYS}
        self.tox_counter = 0
        self.flinched = False
        self.protected = False
        self.consecutive_protects = 0
        self.helping_hand = False
        self.turns_on_field = 0
        self.trapped_turns = 0
        self.trap_denom = TRAP_CHIP_DENOM
        self.sub_hp = 0
        self.taunt_turns = 0
        self.encore_turns = 0
        self.encore_index = None
        self.confusion_turns = 0
        self.choice_lock = None
        self.last_move_index = None
        self.metronome_streak = 0
        self.crit_stage = 0
        self.perish_count = 0  # switching escapes the Song
        self.disabled_index = None
        self.disabled_turns = 0
        self.destiny_bond = False
        self.last_hit_physical = None
        self.last_hit_special = None
        if self.transformed:  # revert Transform / Imposter
            self.transformed = False
            types, stats, moves, ability, pp = self._orig
            self.types = tuple(types)
            self.stats = dict(stats)
            self.set.moves = list(moves)
            self.set.ability = ability
            self.pp = list(pp)
            self._orig = None

    def __repr__(self) -> str:
        return f"<{self.set.species} {self.hp}/{self.max_hp}>"


class Side:
    def __init__(self, team: list[PokemonSet], ruleset: str, n_active: int = 1):
        if not team:
            raise ValueError("empty team")
        self.team = [BattlePokemon(p, ruleset) for p in team]
        self.slots: list[int | None] = [
            i if i < len(team) else None for i in range(n_active)
        ]
        self.conditions = SideState()
        # remaining turns for timed conditions
        self.timers = {
            "reflect": 0, "light_screen": 0, "aurora_veil": 0,
            "tailwind": 0, "safeguard": 0,
        }
        # entry hazards on THIS side's half of the field
        self.hazards = {
            "stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False,
        }
        # pending Wishes: slot -> [turns_until_heal, amount]
        self.wishes: dict[int, list] = {}
        # per-turn doubles state
        self.wide_guard = False
        self.redirect_slot: int | None = None
        self.redirect_powder = False

    def sync_conditions(self) -> None:
        self.conditions.reflect = self.timers["reflect"] > 0
        self.conditions.light_screen = self.timers["light_screen"] > 0
        self.conditions.aurora_veil = self.timers["aurora_veil"] > 0
        self.conditions.tailwind = self.timers["tailwind"] > 0
        self.conditions.safeguard = self.timers["safeguard"] > 0

    def active(self, slot: int) -> BattlePokemon | None:
        i = self.slots[slot]
        return self.team[i] if i is not None else None

    def alive_slots(self) -> list[int]:
        return [
            s for s in range(len(self.slots))
            if (mon := self.active(s)) is not None and not mon.fainted
        ]

    @property
    def defeated(self) -> bool:
        return all(p.fainted for p in self.team)

    def healthy_bench(self) -> list[int]:
        return [
            i for i, p in enumerate(self.team)
            if not p.fainted and i not in self.slots
        ]

    # singles-friendly conveniences (slot 0)
    @property
    def active_index(self) -> int | None:
        return self.slots[0]

    @active_index.setter
    def active_index(self, i: int) -> None:
        self.slots[0] = i


class Battle:
    """Simultaneous-choice battle. Each side submits one Action per active
    slot; the engine resolves the turn. Fully seeded: same seed + same
    actions = same battle."""

    def __init__(
        self,
        team0: list[PokemonSet],
        team1: list[PokemonSet],
        ruleset: str = "champions",
        field: FieldState | None = None,
        seed: int | None = None,
        format: str = "singles",
    ):
        if format not in ("singles", "doubles"):
            raise ValueError(f"unknown format {format!r}")
        self.format = format
        self.n_active = 2 if format == "doubles" else 1
        self.ruleset = ruleset
        self.rs = load_ruleset(ruleset)
        self.sides = (
            Side(team0, ruleset, self.n_active),
            Side(team1, ruleset, self.n_active),
        )
        self.field = field or FieldState()
        # timers for field-wide conditions present at start
        self.field_timers = {
            "weather": DURATIONS["weather"] if self.field.weather else 0,
            "terrain": DURATIONS["terrain"] if self.field.terrain else 0,
            "trick_room": DURATIONS["trick_room"] if self.field.trick_room else 0,
        }
        self.rng = random.Random(seed)
        self.turn = 0
        # opt-in observer log (set to [] to enable): what a player would SEE
        # — every damaging hit and every move executed, in order. The eval
        # harness turns it into estimator observations.
        self.events: list[dict] | None = None

        # switch-in abilities for the leads: fastest triggers first, so on a
        # contested lead the SLOWER setter's weather/terrain sticks
        entrants = sorted(
            (
                (self.effective_speed(i, s), i, s)
                for i in (0, 1)
                for s in range(self.n_active)
                if self.sides[i].active(s) is not None
            ),
            reverse=True,
        )
        for _, i, s in entrants:
            self._on_entry(self.sides[i].active(s))

    def _set_weather(self, weather: str, setter: BattlePokemon | None = None) -> None:
        if self.field.weather == weather:
            return
        self.field.weather = weather
        duration = DURATIONS["weather"]
        if (
            setter is not None
            and setter.set.item
            and norm(setter.set.item) == WEATHER_ROCKS.get(weather, "")
        ):
            duration = 8
        self.field_timers["weather"] = duration

    def _set_terrain(self, terrain: str, setter: BattlePokemon | None = None) -> None:
        if self.field.terrain == terrain:
            return
        self.field.terrain = terrain
        duration = DURATIONS["terrain"]
        if setter is not None and setter.set.item and norm(setter.set.item) == TERRAIN_EXTENDER:
            duration = 8
        self.field_timers["terrain"] = duration
        for side in self.sides:
            for s in side.alive_slots():
                self._check_terrain_seed(side.active(s))

    def _check_terrain_seed(self, mon: BattlePokemon) -> None:
        """Electric/Grassy Seed +1 Def, Psychic/Misty Seed +1 SpD, eaten
        when the matching terrain is up."""
        if mon.fainted or not mon.set.item or not self.field.terrain:
            return
        spec = TERRAIN_SEEDS.get(norm(mon.set.item))
        if spec is not None and spec[0] == self.field.terrain:
            self._apply_boosts(mon, {spec[1]: 1})
            mon.set.item = None

    def _transform(self, mon: BattlePokemon, target: BattlePokemon) -> None:
        """Transform / Imposter: copy the target's battle form. Keeps own HP,
        item, and status; copied moves get 5 PP each."""
        if mon.transformed or target.transformed or target.fainted:
            return
        mon._orig = (
            tuple(mon.types), dict(mon.stats),
            list(mon.set.moves), mon.set.ability, list(mon.pp),
        )
        mon.transformed = True
        mon.types = tuple(target.types)
        for k in BOOST_KEYS:
            mon.stats[k] = target.stats[k]
        mon.boosts = dict(target.boosts)
        mon.set.moves = list(target.set.moves)
        mon.set.ability = target.set.ability
        mon.pp = [min(5, self.rs.get_move(m).pp or 5) for m in mon.set.moves]

    def _on_entry(self, mon: BattlePokemon) -> None:
        """Switch-in ability triggers (weather wars and Imposter live here),
        plus terrain seeds entering an already-set terrain."""
        self._check_terrain_seed(mon)
        if mon.fainted or not mon.set.ability:
            return
        ability = norm(mon.set.ability)
        if ability in WEATHER_ABILITIES:
            self._set_weather(WEATHER_ABILITIES[ability], mon)
        if ability in TERRAIN_ABILITIES:
            self._set_terrain(TERRAIN_ABILITIES[ability], mon)
        if ability == "imposter":
            side_idx, slot = self._locate(mon)
            foe = self.sides[1 - side_idx]
            # copy the mon across the field; fall back to any alive foe
            target = foe.active(slot) if slot < len(foe.slots) else None
            if target is None or target.fainted:
                alive = foe.alive_slots()
                target = foe.active(alive[0]) if alive else None
            if target is not None:
                self._transform(mon, target)

    def _locate(self, mon: BattlePokemon) -> tuple[int, int]:
        for i, side in enumerate(self.sides):
            for s in range(self.n_active):
                if side.active(s) is mon:
                    return (i, s)
        raise ValueError("pokemon is not active")

    def _force_switch(self, side: Side, mon: BattlePokemon) -> None:
        """Phazing: drag `mon` out for a random healthy teammate."""
        bench = side.healthy_bench()
        team_idx = side.team.index(mon)
        if not bench or team_idx not in side.slots:
            # already gone: e.g. Dragon Tail dragged out an Eject Button
            # holder before the button's deferred switch ran
            return
        slot = side.slots.index(team_idx)
        mon.reset_on_switch()
        mon.turns_on_field = -1
        side.slots[slot] = self.rng.choice(bench)
        incoming = side.active(slot)
        incoming.reset_on_switch()
        incoming.turns_on_field = -1
        self._entry_hazards(side, incoming)
        self._on_entry(incoming)

    # --- snapshot seeding ---

    def snapshot(self) -> BattleState:
        """Freeze the battle into a BattleState (timers are not snapshotted;
        from_state restarts active conditions with a short default)."""
        sides = tuple(
            TeamSnapshot(
                pokemon=[
                    PokemonSnapshot(
                        set=deepcopy(mon.set),
                        hp_fraction=mon.hp_fraction,
                        status=mon.status,
                        boosts=dict(mon.boosts),
                        pp=list(mon.pp),
                        sub_hp=mon.sub_hp,
                        volatiles=self._snapshot_volatiles(mon),
                    )
                    for mon in side.team
                ],
                active=list(side.slots),
                conditions=replace(side.conditions),
                hazards={**side.hazards,
                         "_wishes": {k: list(w) for k, w in side.wishes.items()}},
            )
            for side in self.sides
        )
        return BattleState(
            sides=sides,
            field=FieldState(
                weather=self.field.weather,
                terrain=self.field.terrain,
                trick_room=self.field.trick_room,
            ),
            format=self.format,
        )

    @staticmethod
    def _snapshot_volatiles(mon: BattlePokemon) -> dict:
        v: dict = {}
        for key in (
            "taunt_turns", "encore_turns", "confusion_turns",
            "trapped_turns", "perish_count", "disabled_turns",
            "metronome_streak", "crit_stage",
        ):
            if getattr(mon, key):
                v[key] = getattr(mon, key)
        if mon.trapped_turns and mon.trap_denom != TRAP_CHIP_DENOM:
            v["trap_denom"] = mon.trap_denom
        if mon.encore_index is not None:
            v["encore_index"] = mon.encore_index
        if mon.disabled_index is not None:
            v["disabled_index"] = mon.disabled_index
        if mon.choice_lock is not None:
            v["choice_lock"] = mon.choice_lock
        if mon.destiny_bond:
            v["destiny_bond"] = True
        if mon.transformed:
            # the copied moves/ability travel in the snapshotted set; stats,
            # types and the pre-transform original ride along here
            v["transformed"] = True
            v["transform_stats"] = {k: mon.stats[k] for k in BOOST_KEYS}
            v["transform_types"] = list(mon.types)
            types, stats, moves, ability, pp = mon._orig
            v["transform_orig"] = {
                "types": list(types), "stats": dict(stats),
                "moves": list(moves), "ability": ability, "pp": list(pp),
            }
        return v

    @classmethod
    def from_state(
        cls,
        state: BattleState,
        ruleset: str = "champions",
        seed: int | None = None,
        condition_turns: int = 3,
    ) -> Battle:
        """Reconstruct a battle from a snapshot for rollouts. Condition
        durations aren't part of the snapshot, so any active timed condition
        restarts with `condition_turns` remaining (a middle-of-the-road
        guess)."""
        b = cls(
            [p.set for p in state.sides[0].pokemon],
            [p.set for p in state.sides[1].pokemon],
            ruleset=ruleset,
            field=FieldState(
                weather=state.field.weather,
                terrain=state.field.terrain,
                trick_room=state.field.trick_room,
            ),
            seed=seed,
            format=state.format,
        )
        # the constructor fired entry abilities for the default leads; undo
        # their effects — the snapshot is the authority on field + form state
        b.field.weather = state.field.weather
        b.field.terrain = state.field.terrain
        b.field.trick_room = state.field.trick_room
        for side in b.sides:
            for mon in side.team:
                if mon.transformed:
                    mon.reset_on_switch()

        for side, snap in zip(b.sides, state.sides):
            side.slots = list(snap.active)
            for mon, ps in zip(side.team, snap.pokemon):
                mon.hp = (
                    0 if ps.hp_fraction <= 0
                    else max(1, round(ps.hp_fraction * mon.max_hp))
                )
                mon.status = ps.status
                mon.set.item = ps.set.item  # undo any constructor-time seed
                if ps.status == "slp":
                    mon.sleep_turns = 1
                mon.boosts = {k: ps.boosts.get(k, 0) for k in BOOST_KEYS}
                if ps.pp is not None:
                    mon.pp = list(ps.pp)
                mon.sub_hp = ps.sub_hp
                v = ps.volatiles
                for key in (
                    "taunt_turns", "encore_turns", "confusion_turns",
                    "trapped_turns", "perish_count", "disabled_turns",
                    "metronome_streak", "crit_stage",
                ):
                    setattr(mon, key, v.get(key, 0))
                mon.trap_denom = v.get("trap_denom", TRAP_CHIP_DENOM)
                mon.encore_index = v.get("encore_index")
                mon.disabled_index = v.get("disabled_index")
                mon.choice_lock = v.get("choice_lock")
                mon.destiny_bond = v.get("destiny_bond", False)
                if v.get("transformed"):
                    mon.transformed = True
                    orig = v["transform_orig"]
                    mon._orig = (
                        tuple(orig["types"]), dict(orig["stats"]),
                        list(orig["moves"]), orig["ability"], list(orig["pp"]),
                    )
                    for k, val in v["transform_stats"].items():
                        mon.stats[k] = val
                    mon.types = tuple(v["transform_types"])
            if snap.hazards:
                hz = dict(snap.hazards)
                wishes = hz.pop("_wishes", {})
                side.hazards.update(hz)
                side.wishes = {int(k): list(w) for k, w in wishes.items()}
            for cond in side.timers:
                if getattr(snap.conditions, cond):
                    side.timers[cond] = condition_turns
            side.sync_conditions()
        for cond, active in (
            ("weather", state.field.weather),
            ("terrain", state.field.terrain),
            ("trick_room", state.field.trick_room),
        ):
            b.field_timers[cond] = condition_turns if active else 0
        return b

    # --- queries ---

    def is_trapped(self, side_idx: int, slot: int) -> bool:
        """Whether this slot's Pokemon may not switch out. Ghosts always can."""
        mon = self.sides[side_idx].active(slot)
        if mon is None or "ghost" in mon.types:
            return False
        if mon.trapped_turns > 0:
            return True
        foe = self.sides[1 - side_idx]
        for s in foe.alive_slots():
            ability = foe.active(s).set.ability
            scope = ability and TRAPPING_ABILITIES.get(norm(ability))
            if scope == "all":
                return True
            if scope == "grounded" and mon.grounded:
                return True
            if scope == "steel" and "steel" in mon.types:
                return True
        return False

    def legal_actions(self, side_idx: int, slot: int = 0) -> list[Action]:
        """Legal choices for one slot: (move, target) pairs — single-target
        moves enumerate each alive opposing slot — plus switches (unless
        trapped). Out of PP on every move = Struggle."""
        side = self.sides[side_idx]
        mon = side.active(slot)
        acts: list[Action] = []
        if mon is not None and not mon.fainted:
            foe_slots = self.sides[1 - side_idx].alive_slots()
            for i, name in enumerate(mon.set.moves):
                if mon.pp[i] <= 0:
                    continue
                move = self.rs.get_move(name)
                if move.name in FIRST_TURN_ONLY and mon.turns_on_field > 0:
                    continue
                if mon.taunt_turns > 0 and move.category == "Status":
                    continue
                if mon.encore_turns > 0 and i != mon.encore_index:
                    continue
                if mon.choice_lock is not None and i != mon.choice_lock:
                    continue
                if mon.disabled_turns > 0 and i == mon.disabled_index:
                    continue
                if move.target in ("normal", "randomNormal") and len(foe_slots) > 1:
                    acts += [Action("move", i, target=t) for t in foe_slots]
                else:
                    acts.append(Action("move", i))
            if not acts and mon.set.moves:
                acts.append(Action("struggle"))
            if not self.is_trapped(side_idx, slot):
                acts += [Action("switch", i) for i in side.healthy_bench()]
        return acts or [Action("pass")]

    def winner(self) -> int | None:
        """0/1 once decided; None while ongoing (or a double-KO draw). At the
        turn cap the side with more total HP fraction wins."""
        d0, d1 = self.sides[0].defeated, self.sides[1].defeated
        if d0 and d1:
            return None
        if d0:
            return 1
        if d1:
            return 0
        if self.turn >= TURN_CAP:
            h0 = sum(p.hp_fraction for p in self.sides[0].team)
            h1 = sum(p.hp_fraction for p in self.sides[1].team)
            if h0 != h1:
                return 0 if h0 > h1 else 1
        return None

    @property
    def is_over(self) -> bool:
        return (
            self.sides[0].defeated
            or self.sides[1].defeated
            or self.turn >= TURN_CAP
        )

    def effective_speed(self, side_idx: int, slot: int = 0) -> int:
        side = self.sides[side_idx]
        mon = side.active(slot)
        if mon is None:
            return 0
        spe = mon.stats["spe"]
        boost = mon.boosts["spe"]
        if boost:
            num, den = (2 + boost, 2) if boost > 0 else (2, 2 - boost)
            spe = math.floor(spe * num / den)
        if mon.set.item and norm(mon.set.item) == "choicescarf":
            spe = math.floor(spe * 1.5)
        if has_iron_ball(mon.set.item):
            spe = math.floor(spe / 2)
        if side.conditions.tailwind:
            spe *= 2
        if mon.status == "par":
            spe = math.floor(spe / 2)
        return spe

    # --- turn resolution ---

    def step(
        self,
        actions0: Action | list[Action],
        actions1: Action | list[Action],
    ) -> None:
        """Resolve one turn. Each side submits one Action per active slot
        (a bare Action is accepted for singles)."""
        if self.is_over:
            raise RuntimeError("battle is over")
        self.turn += 1

        chosen: dict[tuple[int, int], Action] = {}
        for side_idx, acts in ((0, actions0), (1, actions1)):
            if isinstance(acts, Action):
                acts = [acts]
            for slot in range(self.n_active):
                chosen[(side_idx, slot)] = (
                    acts[slot] if slot < len(acts) else Action("pass")
                )

        # per-turn state resets
        for side in self.sides:
            side.wide_guard = False
            side.redirect_slot = None
            for mon in side.team:
                mon.protected = False
                mon.helping_hand = False
                mon.last_hit_physical = None
                mon.last_hit_special = None

        # switches resolve before moves; a bench mon can only be claimed once
        claimed: set[tuple[int, int]] = set()
        for key in self._order(chosen, switches=True):
            side_idx, slot = key
            act = chosen[key]
            if (side_idx, act.index) in claimed:
                continue
            side = self.sides[side_idx]
            if act.index in side.healthy_bench() and not self.is_trapped(side_idx, slot):
                out = side.active(slot)
                if out is not None:
                    out.reset_on_switch()
                    out.turns_on_field = -1  # incremented back to 0 at end of turn
                side.slots[slot] = act.index
                side.team[act.index].reset_on_switch()
                side.team[act.index].turns_on_field = -1
                claimed.add((side_idx, act.index))
                self._entry_hazards(side, side.team[act.index])
                self._on_entry(side.team[act.index])

        for key in self._order(chosen, switches=False):
            act = chosen[key]
            if act.kind in ("move", "struggle"):
                self._do_move(key[0], key[1], act)

        self._end_of_turn()

        # forced replacements after faints (auto: first healthy teammate)
        for side in self.sides:
            for slot in range(self.n_active):
                mon = side.active(slot)
                if (mon is None or mon.fainted) and side.healthy_bench():
                    side.slots[slot] = side.healthy_bench()[0]
                    side.active(slot).reset_on_switch()
                    self._entry_hazards(side, side.active(slot))
                    self._on_entry(side.active(slot))

    def _entry_hazards(self, side: Side, mon: BattlePokemon) -> None:
        hz = side.hazards
        if hz["stealth_rock"]:
            eff = self.rs.type_effectiveness("rock", mon.types)
            mon.hp = max(0, mon.hp - math.floor(mon.max_hp * eff / 8))
        if not mon.grounded or mon.fainted:
            return
        if hz["spikes"]:
            denom = {1: 8, 2: 6, 3: 4}[hz["spikes"]]
            mon.hp = max(0, mon.hp - math.floor(mon.max_hp / denom))
        if hz["toxic_spikes"] and not mon.fainted:
            if "poison" in mon.types:
                hz["toxic_spikes"] = 0  # absorbed
            else:
                self._try_status(
                    mon, "psn" if hz["toxic_spikes"] == 1 else "tox", side
                )
        if hz["sticky_web"] and not mon.fainted:
            self._apply_boosts(mon, {"spe": -1})
        self._check_heal_berry(mon)  # hazard damage can trigger Sitrus

    def _order(
        self, chosen: dict[tuple[int, int], Action], switches: bool
    ) -> list[tuple[int, int]]:
        """Action order: switches first (by speed), then moves by
        (priority, effective speed, coin flip); Trick Room flips speed."""
        keys = [
            k for k, a in chosen.items()
            if (a.kind == "switch") == switches
        ]

        def sort_key(key: tuple[int, int]):
            side_idx, slot = key
            prio = 0
            act = chosen[key]
            if not switches and act.kind == "move":
                mon = self.sides[side_idx].active(slot)
                if mon is not None and act.index < len(mon.set.moves):
                    prio = self.rs.get_move(mon.set.moves[act.index]).priority
            spe = self.effective_speed(side_idx, slot)
            if self.field.trick_room:
                spe = -spe
            # Quick Claw: 20% to act first within its priority bracket
            quick = 0
            if not switches and act.kind == "move":
                mon = self.sides[side_idx].active(slot)
                if (
                    mon is not None and mon.set.item
                    and norm(mon.set.item) == "quickclaw"
                    and self.rng.random() < QUICK_CLAW_CHANCE
                ):
                    quick = 1
            return (prio, quick, spe, self.rng.random())

        return sorted(keys, key=sort_key, reverse=True)

    # --- acting ---

    def _can_act(self, mon: BattlePokemon) -> bool:
        if mon.flinched:
            return False
        if mon.status == "slp":
            mon.sleep_turns -= 1
            if mon.sleep_turns <= 0:
                mon.status = None  # woke up, acts this turn
                return True
            return False
        if mon.status == "frz":
            if self.rng.random() < 0.20:
                mon.status = None
                return True
            return False
        if mon.status == "par" and self.rng.random() < 0.25:
            return False
        if mon.confusion_turns > 0:
            mon.confusion_turns -= 1
            if mon.confusion_turns > 0 and self.rng.random() < 1 / 3:
                # confusion self-hit: 40 BP typeless physical vs own Defense,
                # no STAB/crit/modifiers, standard 85-100 roll
                atk = boosted_stat(mon.stats["atk"], mon.boosts["atk"])
                dfn = boosted_stat(mon.stats["def"], mon.boosts["def"])
                base = (
                    math.floor(
                        math.floor(math.floor(2 * mon.set.level / 5 + 2) * 40 * atk / dfn) / 50
                    )
                    + 2
                )
                dmg = math.floor(base * self.rng.randint(85, 100) / 100)
                mon.hp = max(0, mon.hp - dmg)
                return False
        return True

    def _absorbed(self, defender: BattlePokemon, move_type: str) -> tuple | None:
        """(ability_spec) if the defender's ability eats this move type."""
        ability = defender.set.ability
        if not ability:
            return None
        spec = ABSORB_ABILITIES.get(norm(ability))
        if spec is not None and spec[0] == move_type:
            return spec
        return None

    def _apply_absorb(self, defender: BattlePokemon, spec: tuple) -> None:
        effect = spec[1]
        if effect is None:
            return
        kind, val = effect
        if kind == "boost":
            self._apply_boosts(defender, val)
        else:  # heal 1/val of max HP
            defender.hp = min(defender.max_hp, defender.hp + defender.max_hp // val)

    def _can_eat_berries(self, mon: BattlePokemon) -> bool:
        """Unnerve on the opposing side shuts down berry consumption."""
        try:
            side_idx, _ = self._locate(mon)
        except ValueError:
            return True  # benched (Heal Bell targets): nothing unnerving them
        foe = self.sides[1 - side_idx]
        return not any(
            (a := foe.active(s).set.ability) and norm(a) == "unnerve"
            for s in foe.alive_slots()
        )

    def _check_heal_berry(self, mon: BattlePokemon) -> None:
        """Sitrus / Oran fire the moment HP crosses their threshold."""
        if mon.fainted or not mon.set.item:
            return
        spec = HEAL_BERRIES.get(norm(mon.set.item))
        if spec is None:
            return
        threshold, heal = spec
        if mon.hp <= mon.max_hp * threshold and self._can_eat_berries(mon):
            amount = math.floor(mon.max_hp * heal) if heal <= 1 else int(heal)
            mon.hp = min(mon.max_hp, mon.hp + amount)
            mon.set.item = None

    def _check_cure_berry(self, mon: BattlePokemon) -> None:
        if mon.fainted or not mon.set.item:
            return
        item = norm(mon.set.item)
        if (
            mon.status in STATUS_CURE_BERRIES.get(item, ())
            and self._can_eat_berries(mon)
        ):
            mon.status = None
            mon.sleep_turns = 0
            mon.tox_counter = 0
            mon.set.item = None

    def _try_status(
        self, target: BattlePokemon, status: str, side: Side | None = None
    ) -> bool:
        if target.status is not None or target.fainted:
            return False
        if side is not None and side.timers.get("safeguard", 0) > 0:
            return False
        if status in STATUS_IMMUNE_ABILITIES.get(norm(target.set.ability or ""), ()):
            return False  # Thermal Exchange, Water Veil, Limber, Insomnia...
        if any(
            t in STATUS_TYPE_IMMUNITY.get(status, ())
            for t in target.types
        ):
            return False
        # terrain protects grounded Pokemon: Electric blocks sleep, Misty
        # blocks every non-volatile status
        if target.grounded:
            if self.field.terrain == "misty":
                return False
            if self.field.terrain == "electric" and status == "slp":
                return False
        target.status = status
        if status == "slp":
            target.sleep_turns = self.rng.randint(1, 3)
        if status == "tox":
            target.tox_counter = 0
        self._check_cure_berry(target)  # Cheri/Chesto/.../Lum eat it instantly
        return True

    def _apply_boosts(self, mon: BattlePokemon, deltas: dict[str, int]) -> None:
        for stat, d in deltas.items():
            mon.boosts[stat] = max(-6, min(6, mon.boosts[stat] + d))
        # White Herb: wipes every lowered stage once, then it's gone
        if (
            mon.set.item and norm(mon.set.item) == "whiteherb"
            and any(v < 0 for v in mon.boosts.values())
        ):
            mon.boosts = {k: max(0, v) for k, v in mon.boosts.items()}
            mon.set.item = None

    def _do_move(self, side_idx: int, slot: int, act: Action) -> None:
        attacker = self.sides[side_idx].active(slot)
        if attacker is None or attacker.fainted:
            return
        if not self._can_act(attacker):
            return
        attacker.destiny_bond = False  # lapses when the user moves again
        if act.kind == "struggle":
            attacker.consecutive_protects = 0
            dealt = self._do_damaging_move(
                side_idx, slot, attacker, Action("move", 0), self.rs.get_move("Struggle")
            )
            if dealt:  # Struggle recoil: 1/4 of the user's max HP
                attacker.hp = max(0, attacker.hp - math.floor(attacker.max_hp / 4))
            return
        if act.index >= len(attacker.set.moves):
            return
        if attacker.pp[act.index] <= 0:
            return
        move_name = attacker.set.moves[act.index]
        move = self.rs.get_move(move_name)
        if attacker.taunt_turns > 0 and move.category == "Status":
            return
        if self.events is not None:
            self.events.append({
                "kind": "move", "turn": self.turn, "move": move.name,
                "side": side_idx, "mon": self.sides[side_idx].team.index(attacker),
                "priority": move.priority,
            })
        attacker.pp[act.index] -= 1
        attacker.metronome_streak = (
            attacker.metronome_streak + 1
            if attacker.last_move_index == act.index else 0
        )
        attacker.last_move_index = act.index
        if (
            attacker.choice_lock is None
            and attacker.set.item
            and norm(attacker.set.item) == "choicescarf"
        ):
            attacker.choice_lock = act.index
        if move.name in FIRST_TURN_ONLY and attacker.turns_on_field > 0:
            return

        if move.name in ("Counter", "Mirror Coat"):
            hit = (
                attacker.last_hit_physical
                if move.name == "Counter"
                else attacker.last_hit_special
            )
            if hit is not None:
                striker, dmg = hit
                if not striker.fainted:
                    payback = 2 * dmg
                    if striker.sub_hp > 0:
                        striker.sub_hp = max(0, striker.sub_hp - payback)
                    else:
                        striker.hp = max(0, striker.hp - payback)
                        self._check_heal_berry(striker)
            return

        if move.category == "Status" or move.base_power == 0:
            self._do_status_move(side_idx, slot, attacker, move.name)
            return

        attacker.consecutive_protects = 0
        self._do_damaging_move(side_idx, slot, attacker, act, move)

    def _do_status_move(
        self, side_idx: int, slot: int, attacker: BattlePokemon, move_name: str
    ) -> None:
        move = self.rs.get_move(move_name)
        eff = STATUS_MOVES.get(move.name)
        if eff is None:
            attacker.consecutive_protects = 0
            return  # unmodeled status move: no-op

        if eff.protect:
            success = 1.0 / (3 ** attacker.consecutive_protects)
            if self.rng.random() < success:
                attacker.protected = True
                attacker.consecutive_protects += 1
            else:
                attacker.consecutive_protects = 0
            return
        attacker.consecutive_protects = 0

        side = self.sides[side_idx]
        foe_side = self.sides[1 - side_idx]
        if eff.hazard:
            hz = foe_side.hazards
            if eff.hazard == "spikes":
                hz["spikes"] = min(3, hz["spikes"] + 1)
            elif eff.hazard == "toxic_spikes":
                hz["toxic_spikes"] = min(2, hz["toxic_spikes"] + 1)
            else:
                hz[eff.hazard] = True
            return
        if eff.clear_hazards:  # Defog: both sides' hazards + foe screens
            for s in self.sides:
                s.hazards = {
                    "stealth_rock": False, "spikes": 0,
                    "toxic_spikes": 0, "sticky_web": False,
                }
            for cond in ("reflect", "light_screen", "aurora_veil"):
                foe_side.timers[cond] = 0
            foe_side.sync_conditions()
            return
        if eff.wide_guard:
            side.wide_guard = True
            return
        if eff.redirect:
            side.redirect_slot = slot
            side.redirect_powder = "powder" in move.flags
            return
        if eff.helping_hand:
            ally_slots = [s for s in side.alive_slots() if s != slot]
            if ally_slots:
                side.active(ally_slots[0]).helping_hand = True
            return
        if eff.crit_boost:  # Focus Energy fails if already pumped
            if attacker.crit_stage == 0:
                attacker.crit_stage = eff.crit_boost
            return
        if eff.crit_boost_ally:  # Dragon Cheer: no ally (singles) = fails
            ally_slots = [s for s in side.alive_slots() if s != slot]
            if ally_slots:
                ally = side.active(ally_slots[0])
                if ally.crit_stage == 0:
                    ally.crit_stage = 2 if "dragon" in ally.types else 1
            return

        if eff.substitute:
            cost = attacker.max_hp // 4
            if attacker.sub_hp <= 0 and attacker.hp > cost:
                attacker.hp -= cost
                attacker.sub_hp = cost
            return

        if eff.baton_pass:
            bench = side.healthy_bench()
            if bench:
                passed_boosts = dict(attacker.boosts)
                passed_sub = attacker.sub_hp
                passed_crit = attacker.crit_stage
                attacker.reset_on_switch()
                attacker.turns_on_field = -1
                side.slots[slot] = bench[0]
                incoming = side.active(slot)
                incoming.reset_on_switch()
                incoming.turns_on_field = -1
                incoming.boosts = passed_boosts
                incoming.sub_hp = passed_sub
                incoming.crit_stage = passed_crit  # Baton Pass carries it
                self._entry_hazards(side, incoming)
                self._on_entry(incoming)
            return

        if eff.transform:
            targets = self._targets_for(side_idx, slot, move.target, None, move.type)
            targets = [t for t in targets if not t.fainted]
            if targets:
                self._transform(attacker, targets[0])
            return

        if eff.destiny_bond:
            attacker.destiny_bond = True
            return

        if eff.wish:
            side.wishes[slot] = [2, math.floor(attacker.max_hp / 2)]
            return

        if eff.heal_team_status:
            for member in side.team:
                if not member.fainted:
                    member.status = None
                    member.sleep_turns = 0
                    member.tox_counter = 0
            return

        if eff.perish_song:
            for s_idx, s in enumerate(self.sides):
                for sl in s.alive_slots():
                    hearer = s.active(sl)
                    ability = hearer.set.ability
                    if ability and norm(ability) == "soundproof":
                        continue
                    if hearer.perish_count == 0:
                        hearer.perish_count = 4  # ticks to 3 this turn's end
            return

        if eff.phaze:
            targets = self._targets_for(side_idx, slot, move.target, None, move.type)
            targets = [t for t in targets if not t.fainted]
            if targets:
                target = targets[0]
                if (
                    "sound" in move.flags  # Roar vs Soundproof
                    and target.set.ability
                    and norm(target.set.ability) == "soundproof"
                ):
                    return
                self._force_switch(self.sides[1 - side_idx], target)
            return

        if eff.boosts_self:
            self._apply_boosts(attacker, eff.boosts_self)
        if eff.heal_fraction:
            attacker.hp = min(
                attacker.max_hp,
                attacker.hp + math.floor(attacker.max_hp * eff.heal_fraction),
            )
        if eff.side_condition:
            if side.timers[eff.side_condition] <= 0:
                side.timers[eff.side_condition] = DURATIONS[eff.side_condition]
                side.sync_conditions()
        if eff.weather:
            self._set_weather(eff.weather, attacker)
        if eff.terrain:
            self._set_terrain(eff.terrain, attacker)
        if eff.trick_room:
            if self.field.trick_room:
                self.field.trick_room = False
                self.field_timers["trick_room"] = 0
            else:
                self.field.trick_room = True
                self.field_timers["trick_room"] = DURATIONS["trick_room"]

        if (
            eff.boosts_target or eff.status_target or eff.taunt
            or eff.encore or eff.confuse_target or eff.disable
        ):
            targets = self._targets_for(side_idx, slot, move.target, None, move.type)
            targets = [t for t in targets if not t.fainted]
            for target in targets:
                if target.protected:
                    continue
                if target.sub_hp > 0 and "sound" not in move.flags:
                    continue  # the doll eats targeted status moves
                if self._absorbed(target, move.type):
                    continue  # e.g. Lightning Rod eats Thunder Wave
                if move.accuracy is not None and self.rng.random() * 100 >= move.accuracy:
                    continue
                # type immunity applies to typed status moves (Thunder Wave
                # vs Ground) and powder moves fail vs Grass (Spore)
                if eff.status_target:
                    if self.rs.type_effectiveness(move.type, target.types) == 0:
                        continue
                    if "powder" in move.flags and "grass" in target.types:
                        continue
                    self._try_status(target, eff.status_target, foe_side)
                if eff.boosts_target:
                    self._apply_boosts(target, eff.boosts_target)
                if eff.taunt:
                    target.taunt_turns = 3
                if eff.encore and target.last_move_index is not None:
                    target.encore_turns = 3
                    target.encore_index = target.last_move_index
                if eff.confuse_target and target.confusion_turns == 0:
                    target.confusion_turns = self.rng.randint(2, 5)
                    if (
                        target.set.item
                        and norm(target.set.item) in CONFUSION_CURE_BERRIES
                        and self._can_eat_berries(target)
                    ):
                        target.confusion_turns = 0
                        target.set.item = None
                if eff.disable and target.last_move_index is not None:
                    target.disabled_index = target.last_move_index
                    target.disabled_turns = 4

    def _targets_for(
        self,
        side_idx: int,
        slot: int,
        target_kind: str,
        chosen_target: int | None,
        move_type: str | None = None,
    ) -> list[BattlePokemon]:
        """Resolve a move's targets: Follow Me / Rage Powder first, then
        Lightning Rod / Storm Drain draws, then retargeting when the chosen
        slot is gone."""
        foe = self.sides[1 - side_idx]
        foes = [foe.active(s) for s in foe.alive_slots()]
        if target_kind == "allAdjacent":
            ally = [
                m for s in self.sides[side_idx].alive_slots()
                if s != slot and (m := self.sides[side_idx].active(s)) is not None
            ]
            return foes + ally
        if target_kind == "adjacentFoes":
            return foes

        # single-target: Follow Me / Rage Powder on the defending side wins
        if foe.redirect_slot is not None:
            redirector = foe.active(foe.redirect_slot)
            if redirector is not None and not redirector.fainted:
                attacker = self.sides[side_idx].active(slot)
                powder_immune = (
                    foe.redirect_powder
                    and attacker is not None
                    and "grass" in attacker.types
                )
                if not powder_immune:
                    return [redirector]
        # Lightning Rod / Storm Drain draw matching single-target moves
        if move_type is not None:
            for s in foe.alive_slots():
                m = foe.active(s)
                ability = m.set.ability
                if ability and REDIRECT_ABILITIES.get(norm(ability)) == move_type:
                    return [m]
        if chosen_target is not None:
            mon = foe.active(chosen_target) if chosen_target < len(foe.slots) else None
            if mon is not None and not mon.fainted:
                return [mon]
        if target_kind == "randomNormal" and foes:
            return [self.rng.choice(foes)]
        return foes[:1]  # auto-target / redirect to first alive foe

    def _do_damaging_move(
        self,
        side_idx: int,
        slot: int,
        attacker: BattlePokemon,
        act: Action,
        move,
    ) -> bool:
        targets = self._targets_for(side_idx, slot, move.target, act.target, move.type)
        targets = [t for t in targets if not t.fainted]
        if not targets:
            return False
        is_spread = len(targets) >= 2
        foe_side = self.sides[1 - side_idx]
        secondary = SECONDARY_EFFECTS.get(move.name)
        contact = makes_contact(move.flags, attacker.set.ability, attacker.set.item)
        # the type the hit actually lands as (Aerilate & co. retype Normal)
        landed_type = ate_type(attacker.set.ability, move.type) or move.type

        a_ability = norm(attacker.set.ability or "")
        dealt_any = False
        dealt_total = 0  # Shell Bell
        red_carded = False
        ejected: list[BattlePokemon] = []
        for defender in targets:
            # Piercing Drill (Mega Excadrill): contact goes through Protect
            # at 1/4 damage
            pierced = False
            if defender.protected:
                if a_ability == "piercingdrill" and contact:
                    pierced = True
                else:
                    continue
            if foe_side.wide_guard and is_spread and defender in [
                foe_side.active(s) for s in foe_side.alive_slots()
            ]:
                continue
            # Psychic Terrain shields grounded targets from priority moves
            if (
                self.field.terrain == "psychic"
                and move.priority > 0
                and defender.grounded
            ):
                continue
            # type-absorbing ability: no damage, on-hit boost/heal instead
            spec = self._absorbed(defender, move.type)
            if spec is not None:
                self._apply_absorb(defender, spec)
                continue
            if (
                "sound" in move.flags
                and defender.set.ability
                and norm(defender.set.ability) == "soundproof"
            ):
                continue
            if move.accuracy is not None:
                accuracy = move.accuracy
                if defender.set.item and norm(defender.set.item) == "brightpowder":
                    accuracy *= BRIGHT_POWDER_ACCURACY
                if self.rng.random() * 100 >= accuracy:
                    continue

            n_hits = 1
            if move.multihit:
                lo, hi = move.multihit
                if (lo, hi) == (2, 5):
                    n_hits = self.rng.choices((2, 3, 4, 5), MULTIHIT_2_5_WEIGHTS)[0]
                else:
                    n_hits = self.rng.randint(lo, hi)

            is_crit = self._rolls_crit(attacker, defender, move)
            res = calc_damage(
                attacker.set, defender.set, move.name,
                field=self._field_for(side_idx),
                spread=is_spread,
                is_crit=is_crit,
                hits=n_hits,
                attacker_boosts=attacker.boosts,
                defender_boosts=defender.boosts,
                attacker_hp_fraction=attacker.hp_fraction,  # Eruption-class
                metronome_uses=attacker.metronome_streak,
                ruleset=self.ruleset,
            )
            damage = sum(self.rng.choice(res.rolls) for _ in range(res.hits))
            if damage == 0:
                continue  # immune
            rolled = damage  # what the calculator alone explains
            if attacker.helping_hand:
                damage = int(damage * 1.5)  # sim-level approximation
            if pierced:
                damage = max(1, math.floor(damage * PIERCE_PROTECT_FRACTION))

            # a Substitute eats the hit (and its riders) unless sound bypasses
            if defender.sub_hp > 0 and "sound" not in move.flags:
                defender.sub_hp = max(0, defender.sub_hp - damage)
                dealt_any = True
                continue

            # resist berry: halves a super-effective hit (Chilan: any Normal)
            if defender.set.item and RESIST_BERRIES.get(norm(defender.set.item)) == move.type:
                if (
                    (res.type_effectiveness > 1 or move.type == "normal")
                    and self._can_eat_berries(defender)
                ):
                    damage //= 2
                    defender.set.item = None

            if damage >= defender.hp:
                damage = self._survive_check(defender, damage, res.hits)

            if self.events is not None:
                d_idx = side_idx if defender in self.sides[side_idx].team else 1 - side_idx
                self.events.append({
                    "kind": "hit", "turn": self.turn, "move": move.name,
                    "attacker": (side_idx, self.sides[side_idx].team.index(attacker)),
                    "defender": (d_idx, self.sides[d_idx].team.index(defender)),
                    "damage": min(damage, defender.hp), "hp_before": defender.hp,
                    "max_hp": defender.max_hp, "ko": damage >= defender.hp,
                    "crit": is_crit, "hits": res.hits, "spread": is_spread,
                    # clean = the calculator's roll alone explains the damage
                    # (no Helping Hand / Piercing Drill / berry / Sash / Sturdy)
                    "clean": damage == rolled,
                    "field": self._field_for(side_idx),
                    "attacker_boosts": dict(attacker.boosts),
                    "defender_boosts": dict(defender.boosts),
                    "attacker_status": attacker.status,
                })

            defender.hp = max(0, defender.hp - damage)
            dealt_any = True
            dealt_total += damage
            if has_air_balloon(defender.set.item):
                defender.set.item = None  # any damaging hit pops it
            if move.category == "Physical":
                defender.last_hit_physical = (attacker, damage)
            else:
                defender.last_hit_special = (attacker, damage)
            if defender.fainted and defender.destiny_bond:
                attacker.hp = 0  # the Bond claims its due
            self._check_heal_berry(defender)

            # secondary effects hit opposing targets only
            friendly = defender in [
                self.sides[side_idx].active(s)
                for s in self.sides[side_idx].alive_slots()
            ]
            self._on_hit_reactions(attacker, defender, contact, side_idx)
            # Thermal Exchange: a Fire hit raises the holder's Attack
            if (
                landed_type == "fire" and not defender.fainted
                and norm(defender.set.ability or "") == "thermalexchange"
            ):
                self._apply_boosts(defender, {"atk": 1})
            # Moxie / Beast Boost / Eelevate: a KO feeds the attacker
            if (
                defender.fainted and not attacker.fainted
                and a_ability in KO_BOOST_ABILITIES
            ):
                stat = KO_BOOST_ABILITIES[a_ability] or max(
                    BOOST_KEYS,
                    key=lambda k: (attacker.stats[k], -BOOST_KEYS.index(k)),
                )
                self._apply_boosts(attacker, {stat: 1})
            # King's Rock: 10% flinch on moves without a flinch rider
            if (
                not friendly and not defender.fainted
                and attacker.set.item and norm(attacker.set.item) == "kingsrock"
                and not (secondary and secondary.flinch)
                and self.rng.random() < KINGS_ROCK_CHANCE
            ):
                defender.flinched = True
            d_item = norm(defender.set.item or "")
            if not friendly and not defender.fainted and d_item == "ejectbutton":
                defender.set.item = None
                ejected.append(defender)
            elif (
                not friendly and not defender.fainted
                and d_item == "redcard" and not red_carded
            ):
                defender.set.item = None
                red_carded = True
            if secondary and not friendly and not defender.fainted:
                if self.rng.random() * 100 < secondary.chance:
                    if secondary.status:
                        self._try_status(defender, secondary.status, foe_side)
                    if secondary.flinch:
                        defender.flinched = True
                    if secondary.boosts_target:
                        self._apply_boosts(defender, secondary.boosts_target)
                    if secondary.phaze:
                        self._force_switch(foe_side, defender)
            if (
                move.name in TRAP_MOVES
                and not friendly
                and not defender.fainted
                and "ghost" not in defender.types
            ):
                defender.trapped_turns = max(
                    defender.trapped_turns, self.rng.randint(4, 5)
                )
                if norm(attacker.set.item or "") == "bindingband":
                    defender.trap_denom = BINDING_BAND_DENOM

        if dealt_any:
            if secondary and secondary.boosts_self:
                self._apply_boosts(attacker, secondary.boosts_self)
            if move.name == "Rapid Spin":
                self.sides[side_idx].hazards = {
                    "stealth_rock": False, "spikes": 0,
                    "toxic_spikes": 0, "sticky_web": False,
                }
            a_item = norm(attacker.set.item or "")
            if a_item == "lifeorb":
                attacker.hp = max(0, attacker.hp - math.floor(attacker.max_hp / 10))
            elif a_item == "normalgem" and landed_type == "normal":
                attacker.set.item = None  # spent on this move
            elif a_item == "shellbell" and dealt_total and not attacker.fainted:
                attacker.hp = min(
                    attacker.max_hp, attacker.hp + math.floor(dealt_total / 8)
                )
        # Eject Button: the holder bails out (to a random healthy teammate —
        # a real player picks; rollouts treat it like a phaze). Red Card:
        # the attacker is dragged out instead.
        for mon in ejected:
            if not mon.fainted:
                self._force_switch(foe_side, mon)
        if red_carded and not attacker.fainted:
            self._force_switch(self.sides[side_idx], attacker)
        return dealt_any

    def _rolls_crit(
        self, attacker: BattlePokemon, defender: BattlePokemon, move
    ) -> bool:
        """Crit stages: high-crit moves, Scope Lens, Super Luck +1 each;
        Leek +2 (Farfetch'd line); Focus Energy +2 / Dragon Cheer +1-2
        (volatile); always-crit moves skip the ladder.
        Battle Armor / Shell Armor block crits outright."""
        if norm(defender.set.ability or "") in CRIT_IMMUNE_ABILITIES:
            return False
        if move.name in ALWAYS_CRIT_MOVES:
            return True
        stage = attacker.crit_stage + (1 if move.name in HIGH_CRIT_MOVES else 0)
        item = norm(attacker.set.item or "")
        if item == "scopelens":
            stage += 1
        elif item == "leek" and norm(attacker.set.species).startswith(LEEK_SPECIES):
            stage += 2
        if norm(attacker.set.ability or "") == "superluck":
            stage += 1
        rate = CRIT_RATES[min(stage, len(CRIT_RATES) - 1)]
        return rate >= 1.0 or self.rng.random() < rate

    def _survive_check(
        self, defender: BattlePokemon, damage: int, hits: int
    ) -> int:
        """A would-be KO: Focus Sash (consumed) and Sturdy hold at 1 HP from
        full HP against a single hit (a multi-hit's later hits break
        through); Focus Band holds 10% of the time, at any HP."""
        if defender.hp <= 1:
            return damage
        item = norm(defender.set.item or "")
        ability = norm(defender.set.ability or "")
        if defender.hp == defender.max_hp and hits == 1:
            if item == "focussash":
                defender.set.item = None
                return defender.hp - 1
            if ability == "sturdy":
                return defender.hp - 1
        if item == "focusband" and self.rng.random() < FOCUS_BAND_CHANCE:
            return defender.hp - 1
        return damage

    def _on_hit_reactions(
        self, attacker: BattlePokemon, defender: BattlePokemon,
        contact: bool, side_idx: int,
    ) -> None:
        """The defender's ability/item striking back at the attacker:
        Rough Skin / Iron Barbs / Rocky Helmet on contact (these trigger even
        if the holder fainted), Spicy Spray (Mega Scovillain) burns on any
        damaging hit."""
        if attacker.fainted:
            return
        d_ability = norm(defender.set.ability or "")
        if contact:
            denom = CONTACT_CHIP_ABILITIES.get(d_ability)
            if denom:
                attacker.hp = max(0, attacker.hp - math.floor(attacker.max_hp / denom))
            if norm(defender.set.item or "") == "rockyhelmet":
                attacker.hp = max(
                    0, attacker.hp - math.floor(attacker.max_hp / ROCKY_HELMET_DENOM)
                )
        if d_ability == "spicyspray" and not attacker.fainted:
            self._try_status(attacker, "brn", self.sides[side_idx])

    # --- end of turn ---

    def _end_of_turn(self) -> None:
        for side in self.sides:
            for s in side.alive_slots():
                mon = side.active(s)
                # status / weather chip, item healing
                if mon.status == "brn":
                    mon.hp = max(0, mon.hp - math.floor(mon.max_hp / 16))
                elif mon.status == "psn":
                    mon.hp = max(0, mon.hp - math.floor(mon.max_hp / 8))
                elif mon.status == "tox":
                    mon.tox_counter += 1
                    mon.hp = max(
                        0, mon.hp - math.floor(mon.max_hp * mon.tox_counter / 16)
                    )
                if (
                    self.field.weather == "sand"
                    and not any(t in ("rock", "ground", "steel") for t in mon.types)
                ):
                    mon.hp = max(0, mon.hp - math.floor(mon.max_hp / 16))
                if mon.trapped_turns > 0 and not mon.fainted:
                    mon.hp = max(0, mon.hp - math.floor(mon.max_hp / mon.trap_denom))
                    mon.trapped_turns -= 1
                if not mon.fainted:
                    if self.field.terrain == "grassy" and mon.grounded:
                        mon.hp = min(mon.max_hp, mon.hp + math.floor(mon.max_hp / 16))
                    if mon.set.item and norm(mon.set.item) == "leftovers":
                        mon.hp = min(mon.max_hp, mon.hp + math.floor(mon.max_hp / 16))
                mon.flinched = False
                if mon.taunt_turns > 0:
                    mon.taunt_turns -= 1
                if mon.encore_turns > 0:
                    mon.encore_turns -= 1
                    if mon.encore_turns == 0:
                        mon.encore_index = None
                if mon.disabled_turns > 0:
                    mon.disabled_turns -= 1
                    if mon.disabled_turns == 0:
                        mon.disabled_index = None
                if mon.perish_count > 0:
                    mon.perish_count -= 1
                    if mon.perish_count == 0:
                        mon.hp = 0  # the Song claims its listener
                self._check_heal_berry(mon)  # chip may cross the threshold
                mon.turns_on_field += 1

            # pending Wishes land on whoever now occupies the slot
            for wslot in list(side.wishes):
                side.wishes[wslot][0] -= 1
                if side.wishes[wslot][0] <= 0:
                    amount = side.wishes.pop(wslot)[1]
                    recipient = side.active(wslot) if wslot < len(side.slots) else None
                    if recipient is not None and not recipient.fainted:
                        recipient.hp = min(recipient.max_hp, recipient.hp + amount)

            for cond in side.timers:
                if side.timers[cond] > 0:
                    side.timers[cond] -= 1
            side.sync_conditions()

        for cond in self.field_timers:
            if self.field_timers[cond] > 0:
                self.field_timers[cond] -= 1
                if self.field_timers[cond] == 0:
                    if cond == "weather":
                        self.field.weather = None
                    elif cond == "terrain":
                        self.field.terrain = None
                    else:
                        self.field.trick_room = False

    def _field_for(self, attacker_idx: int) -> FieldState:
        """FieldState oriented so attacker_side/defender_side match the
        acting side."""
        return FieldState(
            weather=self.field.weather,
            terrain=self.field.terrain,
            trick_room=self.field.trick_room,
            attacker_side=self.sides[attacker_idx].conditions,
            defender_side=self.sides[1 - attacker_idx].conditions,
        )
