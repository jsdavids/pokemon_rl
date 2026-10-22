"""Phase 5: the MCP server's tool functions (fastmcp leaves them callable)."""

import asyncio

import pytest

fastmcp = pytest.importorskip("fastmcp")

from pokechamp.data.loader import DATA_DIR
from pokechamp import server

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)

CHOMP = {
    "species": "Garchomp", "moves": ["Dragon Claw", "Earthquake"],
    "nature": "Jolly", "evs": {"atk": 252, "spe": 252},
}
AZU = {
    "species": "Azumarill", "moves": ["Play Rough", "Aqua Jet"],
    "nature": "Adamant", "evs": {"hp": 252, "atk": 252},
}
GHOLD = {
    "species": "Gholdengo", "moves": ["Shadow Ball", "Recover"],
    "nature": "Timid", "evs": {"spa": 252, "spe": 252},
}
TTAR = {
    "species": "Tyranitar", "moves": ["Crunch", "Stone Edge"],
    "nature": "Adamant", "evs": {"hp": 252, "atk": 252},
}


@pytest.fixture()
def app(tmp_path):
    return server.init(tmp_path)


def test_all_tools_registered():
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {
        "damage", "save_team", "get_team", "list_teams", "scout",
        "observe_damage", "opponent_summary", "lead_assist",
        "start_battle", "battle_update", "battle_field", "battle_log",
        "recommend_move",
    } <= tools


def test_damage_tool(app):
    out = server.damage(CHOMP, TTAR, "Earthquake")
    assert out["type_effectiveness"] == 2.0
    assert 0.0 <= out["ko_chance"] <= 1.0
    assert out["percent"][0] < out["percent"][1]
    assert "Garchomp" in out["summary"]


def test_damage_tool_resolves_species_names(app):
    server.save_team("ladder", [CHOMP, AZU])
    server.scout("rival", "Tyranitar", item="Choice Scarf")

    # attacker from my stored team, defender from the scouting book
    out = server.damage("Garchomp", "Tyranitar", "Earthquake")
    assert out["type_effectiveness"] == 2.0
    assert any("scouted build" in n for n in out["notes"])

    # unknown species: neutral default, disclosed
    out = server.damage("Garchomp", "Dragonite", "Dragon Claw")
    assert any("neutral" in n for n in out["notes"])
    assert out["damage"][1] > 0


def test_damage_tool_applies_weather(app):
    calm = server.damage(
        {"species": "Politoed", "moves": ["Surf"], "nature": "Modest",
         "evs": {"spa": 252}},
        TTAR, "Surf",
    )
    rain = server.damage(
        {"species": "Politoed", "moves": ["Surf"], "nature": "Modest",
         "evs": {"spa": 252}},
        TTAR, "Surf", weather="rain",
    )
    assert rain["damage"][1] > calm["damage"][1]  # 1.5x water in rain


def test_team_tools(app):
    v = server.save_team("ladder", [CHOMP, AZU, GHOLD])
    assert v == {"name": "ladder", "version": 0}
    got = server.get_team("ladder")
    assert [p["species"] for p in got["team"]] == [
        "Garchomp", "Azumarill", "Gholdengo",
    ]
    assert got["team"][0]["evs"]["atk"] == 252  # parse_set filled the rest
    assert server.list_teams() == ["ladder"]
    with pytest.raises(ValueError, match="can't learn"):
        server.save_team("bad", [{"species": "Azumarill",
                                  "moves": ["Earthquake"]}])


def test_scouting_tools(app):
    out = server.scout("rival", "Tyranitar", move="Crunch",
                       ability="Sand Stream")
    assert out["revealed_moves"] == ["Crunch"]
    assert out["ability"] == {"Sand Stream": 1.0}

    # my Garchomp's Earthquake left it at 35%: consistent with bulky builds
    # only (the candidate range is 56-107%), so the posterior narrows
    out = server.observe_damage(
        "rival", "Tyranitar", "Earthquake", CHOMP, remaining_percent=35.0,
    )
    assert out["observations"] == 1
    assert out["hp_percent"] == 35.0

    team = server.opponent_summary("rival")
    assert team == {"opponent": "rival", "team": ["Tyranitar"]}


def test_observe_damage_resolves_species_names(app):
    server.save_team("ladder", [CHOMP, AZU, GHOLD])
    server.scout("rival", "Tyranitar")
    server.start_battle("ladder", "rival")
    # just a species name: the build comes from the live battle team
    out = server.observe_damage(
        "rival", "Tyranitar", "Earthquake", "Garchomp",
        remaining_percent=35.0,
    )
    assert out["observations"] == 1

    server.STATE.battle = None  # no battle: falls back to stored teams
    out = server.observe_damage(
        "rival", "Tyranitar", "Earthquake", "garchomp",  # case-insensitive
        ko=True,  # censored: consistent with the 35% bar it had left
    )
    assert out["observations"] == 2

    with pytest.raises(ValueError, match="can't resolve"):
        server.observe_damage("rival", "Tyranitar", "Surf", "Politoed",
                              percent=30.0)


def test_lead_assist_tool(app):
    server.save_team("ladder", [CHOMP, AZU, GHOLD])
    server.scout("rival", "Tyranitar")
    server.scout("rival", "Alakazam")
    out = server.lead_assist("ladder", "rival", games=4)
    assert out["games_per_option"] == 4
    assert len(out["options"]) >= 3
    top = out["options"][0]
    assert len(top["lead"]) == 1 and len(top["bring"]) == 3
    assert 0.0 <= top["win_rate"] <= 1.0


def test_lead_assist_defaults_resolve(app):
    # the only team + only scouted opponent are assumed when omitted
    server.save_team("ladder", [CHOMP, AZU])
    server.scout("rival", "Tyranitar")
    out = server.lead_assist(games=2)
    assert out["options"]

    # several teams: the most recently saved one wins
    server.save_team("aaa-older", [GHOLD, TTAR])
    STATE = server.STATE
    STATE.store._data["teams"]["aaa-older"][-1]["saved_at"] = \
        "2020-01-01T00:00:00+00:00"
    assert server._default_team_name() == "ladder"

    # several opponents and no battle: ambiguous, told to specify
    server.scout("other", "Politoed")
    with pytest.raises(ValueError, match="say which one"):
        server.lead_assist(games=2)
    # ...but a live battle resolves it
    server.start_battle("ladder", "rival")
    assert server._default_opponent() == "rival"


def test_default_helpers_error_when_empty(app):
    with pytest.raises(ValueError, match="no teams saved"):
        server._default_team_name()
    with pytest.raises(ValueError, match="no opponent known"):
        server._default_opponent()


def test_start_battle_defaults(app):
    server.save_team("ladder", [CHOMP, AZU])
    out = server.start_battle(opponent="somebody-new")
    assert out["my_team"] == ["Garchomp", "Azumarill"]
    assert out["opponent_known"] == []


def test_battle_flow(app):
    server.save_team("ladder", [CHOMP, AZU, GHOLD])
    server.scout("rival", "Tyranitar", move="Crunch")

    out = server.start_battle("ladder", "rival")
    assert out["my_team"] == ["Garchomp", "Azumarill", "Gholdengo"]
    assert out["opponent_known"] == ["Tyranitar"]

    server.battle_update("mine", "Garchomp", hp_percent=70.0, turn=2)
    server.battle_update("opponent", "Tyranitar", hp_percent=80.0,
                         status="par", now_active=True)
    # a species not scouted yet gets registered on the fly
    server.battle_update("opponent", "Politoed", hp_percent=100.0)
    server.battle_field(weather="rain",
                        opponent_hazards={"stealth_rock": True})

    log = server.battle_log("they clicked Crunch into the switch")
    assert log[-1]["text"] == "they clicked Crunch into the switch"
    assert log[-1]["turn"] == 2

    import time
    t0 = time.perf_counter()
    out = server.recommend_move(top=3)
    # soft latency guard: the move timer is ~45s and LLM round-trips need
    # most of it; tool-side compute regressing past this is a bug
    assert time.perf_counter() - t0 < 15.0
    assert out["best"] and out["options"]
    assert out["options"][0]["actions"] == out["best"]
    # actions are rendered readably for the LLM
    assert any(
        "Garchomp:" in a or "switch to" in a for a in out["best"]
    )
    scores = [o["score"] for o in out["options"]]
    assert scores == sorted(scores, reverse=True)

    # the session's snapshot reflects the entered facts
    state = server.STATE.battle.state()
    assert state.sides[0].pokemon[0].hp_fraction == pytest.approx(0.7)
    assert state.sides[1].pokemon[0].status == "par"
    assert state.field.weather == "rain"
    assert state.sides[1].hazards["stealth_rock"] is True
    assert len(state.sides[1].pokemon) == 2  # Politoed joined mid-battle


def test_recommend_move_requires_battle(app):
    with pytest.raises(ValueError, match="no battle in progress"):
        server.recommend_move()


def test_tool_call_through_the_mcp_protocol(app):
    """One call end-to-end through JSON-RPC: schemas must serialize."""
    from fastmcp import Client

    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(
                "damage",
                {"attacker": CHOMP, "defender": TTAR, "move": "Earthquake"},
            )
    result = asyncio.run(run())
    assert result.data["type_effectiveness"] == 2.0
    assert "Garchomp" in result.data["summary"]


def test_species_spellings_are_canonicalized(app):
    """'Rotom-Heat' and 'heat rotom' must land on the same scouting entry,
    the same team slot, and the same battle Pokemon."""
    server.save_team("ladder", [
        {"species": "Rotom-Wash", "moves": ["Hydro Pump"]}, CHOMP,
    ])
    assert server.get_team("ladder")["team"][0]["species"] == "Wash Rotom"
    server.scout("rival", "Rotom-Heat")
    server.scout("rival", "heat rotom", move="Overheat")
    assert server.opponent_summary("rival")["team"] == ["Heat Rotom"]
    server.start_battle("ladder", "rival")
    server.battle_update("mine", "wash rotom", hp_percent=50.0)
    server.battle_update("opponent", "Rotom-Heat", hp_percent=40.0)
    state = server.STATE.battle.state()
    assert state.sides[0].pokemon[0].hp_fraction == pytest.approx(0.5)
    assert len(state.sides[1].pokemon) == 1


def test_stat_points_in_and_out(app):
    """Players think in Champions Stat Points; '32 Atk' must not be read as
    32 EVs (+4 stat)."""
    out = server.save_team("sp", [
        {"species": "Garchomp", "moves": ["Earthquake"], "nature": "Jolly",
         "stat_points": {"atk": 32, "spe": 32, "hp": 2}},
        {"species": "Azumarill", "moves": ["Aqua Jet"],
         "evs": {"hp": 32, "atk": 32, "def": 2}},  # SP written as 'evs'
    ])
    assert any("stat points" in n for n in out["notes"])
    team = server.get_team("sp")["team"]
    assert team[0]["evs"]["atk"] == 252 and team[0]["evs"]["hp"] == 12
    assert team[0]["stat_points"] == {"hp": 2, "atk": 32, "def": 0,
                                      "spa": 0, "spd": 0, "spe": 32}
    assert team[1]["stat_points"]["hp"] == 32
    # a real EV spread is left alone
    server.save_team("ev", [{"species": "Garchomp", "moves": ["Earthquake"],
                             "evs": {"atk": 252, "spe": 252, "hp": 4}}])
    assert server.get_team("ev")["team"][0]["stat_points"]["hp"] == 1
    # damage from an SP-spec'd hypothetical matches the EV-spec'd one
    sp = server.damage({"species": "Garchomp", "nature": "Jolly",
                        "stat_points": {"atk": 32}}, TTAR, "Earthquake")
    ev = server.damage({"species": "Garchomp", "nature": "Jolly",
                        "evs": {"atk": 252}}, TTAR, "Earthquake")
    assert sp["damage"] == ev["damage"]


def test_observe_damage_uses_the_live_battle_context(app):
    """Damage seen under sun / with boosts must be scored under them."""
    from pokechamp.damage import calc_damage
    from pokechamp.schemas import PokemonSet
    server.save_team("ladder", [CHOMP, AZU])
    server.scout("rival", "Charizard")
    server.start_battle("ladder", "rival")
    server.battle_field(weather="sun")
    server.battle_update("mine", "Garchomp", boosts={"def": 1})
    res = calc_damage(server.parse_set(CHOMP), PokemonSet(species="Charizard"),
                      "Dragon Claw", ruleset="champions")
    pct = 100 * res.rolls[8] / res.defender_hp
    server.observe_damage("rival", "Charizard", "Dragon Claw", "Garchomp",
                          percent=pct)
    obs = server.STATE.scout._mon("rival", "Charizard")["observations"][-1]
    assert obs["kwargs"]["field"]["weather"] == "sun"
    assert obs["kwargs"]["attacker_boosts"]["def"] == 1  # my Garchomp's
    from pokechamp.schemas import FieldState
    hit = calc_damage(PokemonSet(species="Charizard"), server.parse_set(CHOMP),
                      "Flamethrower", field=FieldState(weather="sun"),
                      ruleset="champions")
    server.observe_damage("rival", "Charizard", "Flamethrower", "Garchomp",
                          percent=100 * hit.rolls[8] / hit.defender_hp,
                          opponent_attacked=True)
    obs = server.STATE.scout._mon("rival", "Charizard")["observations"][-1]
    assert obs["kwargs"]["field"]["weather"] == "sun"
    assert obs["kwargs"]["defender_boosts"]["def"] == 1
