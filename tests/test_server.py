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
