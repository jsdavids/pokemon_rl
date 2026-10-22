"""Phase 5: the MCP server. `pokechamp-mcp` runs it over stdio.

Every tool is a thin wrapper over library code (damage calc, TeamStore,
OpponentScout, BattleSession, recommend/recommend_leads); the wrappers
only parse JSON-friendly arguments and render results as compact dicts.
Tool count is deliberately small and the schemas flat — the client is a
local tool-calling model, and routing reliability beats expressiveness.

State: one TeamStore + OpponentScout (persistent, ~/.pokechamp/) and at
most one live BattleSession (in-memory), rebuilt by start_battle.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from pokechamp.damage import calc_damage as _calc
from pokechamp.data import load_ruleset
from pokechamp.data.loader import canonical_species
from pokechamp.estimator.spreads import load_spread_candidates
from pokechamp.recommender import recommend as _recommend
from pokechamp.schemas import FieldState, PokemonSet, stat_table
from pokechamp.stats import SP_MAX_PER_STAT, SP_MAX_TOTAL, evs_to_sp, sp_to_ev
from pokechamp.session import (
    OpponentScout,
    TeamStore,
    recommend_leads as _leads,
    set_to_dict,
)
from pokechamp.session.battle import BattleSession
from pokechamp.sim.battle import Action

RULESET = "champions"
# the newest regulation in the scraped ruleset: item legality + usage priors
REGULATION = sorted(load_ruleset(RULESET).regulations)[-1]

mcp = FastMCP("pokechamp")


class AppState:
    def __init__(self, data_dir: str | Path | None = None):
        base = Path(data_dir) if data_dir else None
        self.store = TeamStore(
            base / "teams.json" if base else None, ruleset=RULESET
        )
        self.scout = OpponentScout(
            base / "scouting.json" if base else None, ruleset=RULESET,
            regulation=REGULATION,
            # ladder spread hypotheses: best estimator in the held-out
            # simulation (stat MAE 5.3 vs 7.0 with the EV templates)
            spreads=load_spread_candidates(ruleset=RULESET),
        )
        self.battle: BattleSession | None = None


STATE = AppState()


def init(data_dir: str | Path | None = None) -> AppState:
    """Reset server state (tests point this at a temp dir)."""
    global STATE
    STATE = AppState(data_dir)
    return STATE


def _looks_like_stat_points(spread: dict) -> bool:
    """An 'evs' dict that's really Champions Stat Points: every value fits
    the 0-32 SP range and the total fits 66. No real EV spread looks like
    that (32 EVs is +4 stat), and Champions players think in SP."""
    vals = [v for v in spread.values() if isinstance(v, (int, float))]
    return (
        bool(vals) and sum(vals) > 0
        and all(0 <= v <= SP_MAX_PER_STAT for v in vals)
        and sum(vals) <= SP_MAX_TOTAL
    )


def parse_set(d: dict, notes: list[str] | None = None) -> PokemonSet:
    """A build from tool input; missing EV/IV keys are filled in. Spreads
    come as `stat_points` (Champions' unit) or `evs`; an `evs` dict that
    is clearly Stat Points is converted (and noted)."""
    d = dict(d)
    if "species" in d:
        d["species"] = canonical_species(d["species"], RULESET)
    sp = d.pop("stat_points", None) or d.pop("sp", None)
    if sp is None and _looks_like_stat_points(d.get("evs") or {}):
        sp = d.pop("evs")
        if notes is not None:
            notes.append(f"{d.get('species')}: read the spread as stat points")
    if sp is not None:
        d["evs"] = {k: sp_to_ev(int(v)) for k, v in sp.items()}
    d["evs"] = {**stat_table(), **d.get("evs", {})}
    d["ivs"] = {**stat_table(31), **d.get("ivs", {})}
    return PokemonSet(**d)


def _status(value: str | None) -> str | None:
    """None = unchanged is handled by callers; 'none'/'healthy' clears."""
    return None if value in ("none", "healthy") else value


def _battle() -> BattleSession:
    if STATE.battle is None:
        raise ValueError("no battle in progress — call start_battle first")
    return STATE.battle


def _action_text(act: Action, session: BattleSession, slot: int) -> str:
    if act.kind == "switch":
        return f"switch to {session.my_team[act.index].species}"
    if act.kind in ("move", "struggle"):
        mon = session.my_team[session.my_active[slot]]
        if act.kind == "struggle":
            return f"{mon.species}: Struggle"
        name = mon.moves[act.index]
        target = (
            f" -> {session.opp_active[act.target]}"
            if act.target is not None and act.target < len(session.opp_active)
            else ""
        )
        return f"{mon.species}: {name}{target}"
    return "pass"


# ------------------------------------------------------------- calculator ---

def _resolve_build(spec: dict | str, notes: list[str]) -> PokemonSet:
    """A build for the damage calc from whatever the model has: a full
    dict, or a species name resolved from the live battle team, stored
    teams, a scouted opponent's most likely build, or (last resort) a
    neutral default — always answerable, with the assumption noted."""
    if isinstance(spec, dict):
        return parse_set(spec, notes)
    name = canonical_species(str(spec), RULESET).lower()
    if STATE.battle is not None:
        for p in STATE.battle.my_team:
            if p.species.lower() == name:
                return p
    for team_name in reversed(STATE.store.names()):
        for p in STATE.store.get(team_name):
            if p.species.lower() == name:
                return p
    for opp in STATE.scout.opponents():
        for sp in STATE.scout.team(opp):
            if sp.lower() == name:
                build = STATE.scout.estimator(opp, sp).posterior()[0][0]
                notes.append(
                    f"{sp}: using the most likely scouted build for {opp}"
                )
                return build
    notes.append(f"{spec}: no known build, assumed neutral (0 EVs)")
    return parse_set({"species": str(spec)})


@mcp.tool
def damage(
    attacker: dict | str,
    defender: dict | str,
    move: str,
    is_crit: bool = False,
    spread: bool = False,
    weather: str | None = None,
    terrain: str | None = None,
    attacker_boosts: dict | None = None,
    defender_boosts: dict | None = None,
) -> dict:
    """Exact damage of one move. attacker/defender can be just species
    names (builds are looked up from the battle, stored teams, or
    scouting; unknown ones assume neutral stats) or full build objects
    {species, moves?, level?, nature?, stat_points?, item?, ability?,
    status?} for hypotheticals (stat_points: Champions SP per stat,
    0-32 each, 66 total; e.g. {"atk": 32, "spe": 32, "hp": 2}). weather (sun/rain/sand/snow) and terrain
    (electric/grassy/psychic/misty) apply their damage effects. Returns
    damage range, % of defender HP, and KO chance."""
    notes: list[str] = []
    field = FieldState(weather=weather, terrain=terrain)
    res = _calc(
        _resolve_build(attacker, notes), _resolve_build(defender, notes),
        move,
        field=field,
        is_crit=is_crit, spread=spread,
        attacker_boosts=attacker_boosts, defender_boosts=defender_boosts,
        ruleset=RULESET,
    )
    return {
        "summary": res.describe(),
        "damage": [res.min_damage, res.max_damage],
        "percent": [round(res.min_percent, 1), round(res.max_percent, 1)],
        "ko_chance": round(res.ko_chance(), 4),
        "type_effectiveness": res.type_effectiveness,
        "notes": res.notes + notes,
    }


# ------------------------------------------------------------------ teams ---

def _default_team_name() -> str:
    """The team to assume when the user doesn't name one: their only
    team, else the most recently saved one."""
    names = STATE.store.names()
    if not names:
        raise ValueError("no teams saved yet — save_team first")
    if len(names) == 1:
        return names[0]
    return max(
        names, key=lambda n: STATE.store.versions(n)[-1]["saved_at"]
    )


def _default_opponent() -> str:
    """The opponent to assume when the user doesn't name one: the live
    battle's, else the only scouted one."""
    if STATE.battle is not None:
        return STATE.battle.opponent
    opps = STATE.scout.opponents()
    if len(opps) == 1:
        return opps[0]
    if not opps:
        raise ValueError("no opponent known — ask who they're facing")
    raise ValueError(
        f"several opponents scouted ({', '.join(opps)}) — say which one"
    )


@mcp.tool
def save_team(name: str, team: list[dict]) -> dict:
    """Store MY OWN team build (1-6 Pokemon) under a name — never an
    opponent's Pokemon (recording what an opponent has is `scout`).
    Every save adds a new version; the build is validated against the
    Champions ruleset and rejected with a list of problems if illegal.
    Each Pokemon: {species, moves, nature?, stat_points?, item?, ability?}
    with stat_points as in-game (0-32 per stat, 66 total)."""
    notes: list[str] = []
    version = STATE.store.save(name, [parse_set(d, notes) for d in team])
    out = {"name": name, "version": version}
    if notes:
        out["notes"] = notes
    return out


def _set_view(pset: PokemonSet) -> dict:
    """A stored build as the user thinks of it: Stat Points alongside the
    EVs the engine stores."""
    d = set_to_dict(pset)
    d["stat_points"] = evs_to_sp(pset.evs)
    return d


@mcp.tool
def get_team(name: str, version: int | None = None) -> dict:
    """Read one stored team's full build by name — use whenever the user
    asks to see/show/pull up their team, or any earlier version/
    iteration of it (version counts from 0; omit for current — the
    response lists all versions for follow-ups like "the previous one").
    (For opponent info use opponent_summary; for advice use lead_assist
    or recommend_move.)"""
    team = STATE.store.get(name, version)
    return {
        "name": name,
        "team": [_set_view(p) for p in team],
        "versions": STATE.store.versions(name),
    }


@mcp.tool
def list_teams() -> list[str]:
    """ONLY the names of stored teams (no contents). Not for showing a
    team's build (get_team), and not for battle or matchup questions
    (lead_assist / recommend_move)."""
    return STATE.store.names()


# --------------------------------------------------------------- scouting ---

@mcp.tool
def scout(
    opponent: str,
    species: str,
    move: str | None = None,
    item: str | None = None,
    ability: str | None = None,
) -> dict:
    """Record hard facts about an OPPONENT'S Pokemon: register the
    species on THEIR team (e.g. "add X to PlayerY's team", "they have
    a Z") and optionally a revealed move / item / ability. Opponents'
    Pokemon are never saved with save_team — that's for my own teams."""
    STATE.scout.add_pokemon(opponent, species)
    if move:
        STATE.scout.reveal_move(opponent, species, move)
    if item:
        STATE.scout.reveal_item(opponent, species, item)
    if ability:
        STATE.scout.reveal_ability(opponent, species, ability)
    return opponent_summary(opponent, species)


def _resolve_my_pokemon(spec: dict | str) -> PokemonSet:
    """A build dict, or just a species name resolved from the live battle
    team (small models shouldn't have to fabricate EV spreads)."""
    if isinstance(spec, dict):
        return parse_set(spec)
    wanted = canonical_species(str(spec), RULESET).lower()
    if STATE.battle is not None:
        for p in STATE.battle.my_team:
            if canonical_species(p.species, RULESET).lower() == wanted:
                return p
    for name in reversed(STATE.store.names()):
        for p in STATE.store.get(name):
            if canonical_species(p.species, RULESET).lower() == wanted:
                return p
    raise ValueError(
        f"can't resolve {spec!r} to a build — start a battle, save a team "
        "containing it, or pass the full build as an object"
    )


@mcp.tool
def observe_damage(
    opponent: str,
    species: str,
    move: str,
    my_pokemon: dict | str,
    percent: float | None = None,
    remaining_percent: float | None = None,
    ko: bool = False,
    opponent_attacked: bool = False,
    is_crit: bool = False,
    spread: bool = False,
) -> dict:
    """Feed a damage observation into the opponent's build estimate.
    my_pokemon is just my Pokemon's species name (its build is looked up
    from the current battle/stored teams); pass a full build object only
    for hypotheticals. opponent_attacked=False: MY pokemon hit THEIRS
    with `move` (give percent damage, or remaining_percent of their bar,
    or ko=True). opponent_attacked=True: THEIR `species` hit MY pokemon
    with `move` for `percent` of my max HP (ko=True with percent = HP I
    had left). spread=True when a spread move (Earthquake, Rock Slide,
    Hyper Voice...) hit more than one target. During a battle, weather/
    terrain/screens and both Pokemon's stat boosts are taken from it."""
    mine = _resolve_my_pokemon(my_pokemon)
    context = _battle_context(mine, species, opponent_attacked)
    if opponent_attacked:
        STATE.scout.observe(
            opponent, species, "damage_dealt",
            move=move, target=mine, percent=percent or 0.0, ko=ko,
            is_crit=is_crit, spread=spread, **context,
        )
    else:
        STATE.scout.observe(
            opponent, species, "damage_taken",
            attacker=mine, move=move, percent=percent,
            remaining_percent=remaining_percent, ko=ko, is_crit=is_crit,
            spread=spread, **context,
        )
    return opponent_summary(opponent, species)


def _battle_context(mine: PokemonSet, species: str, opponent_attacked: bool) -> dict:
    """Field + boosts for a damage observation, from the live battle (the
    hit happened under them: sun-boosted fire must not read as a stronger
    attacker, damage through Reflect as a bulkier target)."""
    b = STATE.battle
    if b is None:
        return {}
    my_boosts = next(
        (dict(s["boosts"]) for p, s in zip(b.my_team, b.my_state)
         if canonical_species(p.species, RULESET) == mine.species),
        {},
    )
    opp = canonical_species(species, RULESET)
    opp_boosts = dict(b.opp_state.get(opp, {}).get("boosts", {}))
    attacker_side, defender_side = (
        (b.opp_conditions, b.my_conditions) if opponent_attacked
        else (b.my_conditions, b.opp_conditions)
    )
    field = FieldState(
        weather=b.field.weather, terrain=b.field.terrain,
        trick_room=b.field.trick_room,
        attacker_side=attacker_side, defender_side=defender_side,
    )
    return {
        "field": field,
        "attacker_boosts": opp_boosts if opponent_attacked else my_boosts,
        "defender_boosts": my_boosts if opponent_attacked else opp_boosts,
    }


@mcp.tool
def opponent_summary(opponent: str, species: str | None = None) -> dict:
    """What we know about an opponent: their team, and for one species the
    revealed facts plus estimated stats/item/ability."""
    if species is None:
        return {"opponent": opponent, "team": STATE.scout.team(opponent)}
    s = STATE.scout.summary(opponent, species)
    for stat in ("hp", "atk", "def", "spa", "spd", "spe"):
        s[stat] = {k: round(v, 1) for k, v in s[stat].items()}
    s["item"] = {str(k): round(v, 3) for k, v in list(s["item"].items())[:5]}
    s["ability"] = {
        str(k): round(v, 3) for k, v in list(s["ability"].items())[:5]
    }
    return s


# ------------------------------------------------------------ lead assist ---

@mcp.tool
def lead_assist(
    team_name: str | None = None,
    opponent: str | None = None,
    format: str = "singles",
    games: int = 8,
) -> dict:
    """Answer "who should I lead / which Pokemon should I bring" for a
    matchup: ranks every bring/lead choice of a stored team against a
    scouted opponent by simulated win rate. Use this whenever the user
    asks about leads, picks, or what to bring — even mid-conversation
    about their team. Every argument is optional: omit team_name to use
    the user's only/latest team and omit opponent to use the current or
    only scouted one — call it right away rather than asking which."""
    team_name = team_name or _default_team_name()
    opponent = opponent or _default_opponent()
    team = STATE.store.get(team_name)
    ests, revealed = STATE.scout.for_recommend(opponent)
    plan = _leads(
        team, [ests[i] for i in sorted(ests)], format=format,
        games=games, ruleset=RULESET, opp_revealed_moves=revealed,
    )
    return {
        "options": [
            {
                "lead": o.species[: len(o.leads)],
                "bring": o.species,
                "win_rate": round(o.win_rate, 3),
            }
            for o in plan.ranked[:5]
        ],
        "games_per_option": plan.games_per_option,
    }


# ------------------------------------------------------------ live battle ---

@mcp.tool
def start_battle(
    team_name: str | None = None,
    opponent: str = "",
    format: str = "singles",
) -> dict:
    """Begin tracking a battle: my stored team vs an opponent. Call it
    the moment the user says a match is starting, in any phrasing —
    "starting a match", "queue popped", "I'm playing X now". Omit
    team_name to use the user's only/latest team; format defaults to
    singles. The opponent doesn't need to be scouted first."""
    team_name = team_name or _default_team_name()
    opponent = opponent or _default_opponent()
    STATE.battle = BattleSession(
        STATE.store.get(team_name), opponent, STATE.scout,
        format=format, ruleset=RULESET,
    )
    return {
        "my_team": [p.species for p in STATE.battle.my_team],
        "opponent_known": STATE.battle.opp_species,
        "format": format,
    }


@mcp.tool
def battle_update(
    side: str,
    pokemon: str,
    hp_percent: float | None = None,
    status: str | None = None,
    boosts: dict | None = None,
    now_active: bool = False,
    turn: int | None = None,
) -> dict:
    """Update one Pokemon's condition mid-battle. side is "mine" or
    "opponent"; pokemon is a species name; status "none" clears; set
    now_active when it switched in (singles)."""
    b = _battle()
    if turn is not None:
        b.turn = turn
    pokemon = canonical_species(pokemon, RULESET)
    if side == "mine":
        idx = next(
            i for i, p in enumerate(b.my_team)
            if canonical_species(p.species, RULESET) == pokemon
        )
        b.update_mine(
            idx, hp_percent=hp_percent,
            status=_status(status) if status is not None else "unchanged",
            boosts=boosts,
        )
        if now_active:
            b.set_actives(mine=[idx])
    else:
        b.update_opponent(
            pokemon, hp_percent=hp_percent,
            status=_status(status) if status is not None else "unchanged",
            boosts=boosts,
        )
        if now_active:
            b.set_actives(opponent=[pokemon])
    b.note(f"{side} {pokemon}: hp={hp_percent} status={status}")
    return {"ok": True, "turn": b.turn}


@mcp.tool
def battle_field(
    weather: str | None = None,
    terrain: str | None = None,
    trick_room: bool | None = None,
    my_hazards: dict | None = None,
    opponent_hazards: dict | None = None,
) -> dict:
    """Update field conditions: weather sun/rain/sand/snow or "none",
    terrain electric/grassy/psychic/misty or "none", hazards like
    {"stealth_rock": true, "spikes": 2}."""
    b = _battle()
    if weather is not None:
        b.field.weather = None if weather == "none" else weather
    if terrain is not None:
        b.field.terrain = None if terrain == "none" else terrain
    if trick_room is not None:
        b.field.trick_room = trick_room
    if my_hazards is not None:
        b.my_hazards.update(my_hazards)
    if opponent_hazards is not None:
        b.opp_hazards.update(opponent_hazards)
    return {"ok": True}


@mcp.tool
def battle_log(entry: str | None = None, last: int = 20) -> list[dict]:
    """Append a note to the battle log (entry given) or read the most
    recent entries back."""
    b = _battle()
    if entry:
        b.note(entry)
    return b.log[-last:]


@mcp.tool
def recommend_move(top: int = 3) -> dict:
    """Rank my legal actions for this turn using the battle state and
    everything scouted so far (opponent builds sampled from their
    estimated posteriors)."""
    b = _battle()
    state, ests, revealed = b.for_recommend()
    # tool-side latency is <1s at these settings (scripts/
    # benchmark_latency.py) — the 45s timer is spent on LLM round-trips,
    # so search gets generous world/budget counts
    kwargs = (
        {"n_worlds": 8, "depth": 2}
        if b.format == "singles"
        else {"depth": 1, "max_candidates": 24, "budget": 96}
    )
    rec = _recommend(
        state, estimators=ests, revealed_moves=revealed,
        ruleset=RULESET, **kwargs,
    )
    return {
        "best": [
            _action_text(a, b, slot) for slot, a in enumerate(rec.best)
        ],
        "options": [
            {
                "actions": [
                    _action_text(a, b, slot) for slot, a in enumerate(joint)
                ],
                "score": round(score, 3),
            }
            for joint, score in rec.ranked[:top]
        ],
    }


def main() -> None:  # pragma: no cover
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
