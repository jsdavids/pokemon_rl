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
from pokechamp.recommender import recommend as _recommend
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.session import (
    OpponentScout,
    TeamStore,
    recommend_leads as _leads,
    set_to_dict,
)
from pokechamp.session.battle import BattleSession
from pokechamp.sim.battle import Action

RULESET = "champions"

mcp = FastMCP("pokechamp")


class AppState:
    def __init__(self, data_dir: str | Path | None = None):
        base = Path(data_dir) if data_dir else None
        self.store = TeamStore(
            base / "teams.json" if base else None, ruleset=RULESET
        )
        self.scout = OpponentScout(
            base / "scouting.json" if base else None, ruleset=RULESET
        )
        self.battle: BattleSession | None = None


STATE = AppState()


def init(data_dir: str | Path | None = None) -> AppState:
    """Reset server state (tests point this at a temp dir)."""
    global STATE
    STATE = AppState(data_dir)
    return STATE


def parse_set(d: dict) -> PokemonSet:
    """A build from tool input; missing EV/IV keys are filled in."""
    d = dict(d)
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
        return parse_set(spec)
    name = str(spec).lower()
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
    attacker_boosts: dict | None = None,
    defender_boosts: dict | None = None,
) -> dict:
    """Exact damage of one move. attacker/defender can be just species
    names (builds are looked up from the battle, stored teams, or
    scouting; unknown ones assume neutral stats) or full build objects
    {species, moves?, level?, nature?, evs?, ivs?, item?, ability?,
    status?} for hypotheticals. Returns damage range, % of defender HP,
    and KO chance."""
    notes: list[str] = []
    res = _calc(
        _resolve_build(attacker, notes), _resolve_build(defender, notes),
        move,
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

@mcp.tool
def save_team(name: str, team: list[dict]) -> dict:
    """Store a team build (1-6 Pokemon) under a name. Every save adds a
    new version; the build is validated against the Champions ruleset and
    rejected with a list of problems if illegal."""
    version = STATE.store.save(name, [parse_set(d) for d in team])
    return {"name": name, "version": version}


@mcp.tool
def get_team(name: str, version: int | None = None) -> dict:
    """Read one stored team's full build by name — use whenever the user
    asks to see/show/pull up their team or an earlier version of it.
    (For opponent info use opponent_summary; for advice use lead_assist
    or recommend_move.)"""
    team = STATE.store.get(name, version)
    return {
        "name": name,
        "team": [set_to_dict(p) for p in team],
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
    """Record hard facts about an opponent's Pokemon: register the species
    on their team and optionally a revealed move / item / ability."""
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
    if STATE.battle is not None:
        for p in STATE.battle.my_team:
            if p.species.lower() == str(spec).lower():
                return p
    for name in reversed(STATE.store.names()):
        for p in STATE.store.get(name):
            if p.species.lower() == str(spec).lower():
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
) -> dict:
    """Feed a damage observation into the opponent's build estimate.
    my_pokemon is just my Pokemon's species name (its build is looked up
    from the current battle/stored teams); pass a full build object only
    for hypotheticals. opponent_attacked=False: MY pokemon hit THEIRS
    with `move` (give percent damage, or remaining_percent of their bar,
    or ko=True). opponent_attacked=True: THEIR `species` hit MY pokemon
    with `move` for `percent` of my max HP (ko=True with percent = HP I
    had left)."""
    mine = _resolve_my_pokemon(my_pokemon)
    if opponent_attacked:
        STATE.scout.observe(
            opponent, species, "damage_dealt",
            move=move, target=mine, percent=percent or 0.0, ko=ko,
            is_crit=is_crit,
        )
    else:
        STATE.scout.observe(
            opponent, species, "damage_taken",
            attacker=mine, move=move, percent=percent,
            remaining_percent=remaining_percent, ko=ko, is_crit=is_crit,
        )
    return opponent_summary(opponent, species)


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
    team_name: str,
    opponent: str,
    format: str = "singles",
    games: int = 8,
) -> dict:
    """Answer "who should I lead / which Pokemon should I bring" for a
    matchup: ranks every bring/lead choice of a stored team against a
    scouted opponent by simulated win rate. Use this whenever the user
    asks about leads, picks, or what to bring — even mid-conversation
    about their team."""
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
    team_name: str, opponent: str, format: str = "singles"
) -> dict:
    """Begin tracking a battle: my stored team vs a scouted opponent."""
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
    if side == "mine":
        idx = next(
            i for i, p in enumerate(b.my_team) if p.species == pokemon
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
