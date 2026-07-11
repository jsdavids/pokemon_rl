"""Phase 5: the Ollama harness — schema conversion, chat loop, routing eval
plumbing. A real model is only exercised by the pokechamp-routing-eval CLI;
here the completer is scripted."""

import json

import pytest

pytest.importorskip("fastmcp")

from pokechamp.data.loader import DATA_DIR
from pokechamp import server
from pokechamp.llm import (
    Assistant,
    FIXTURES,
    evaluate_routing,
    tool_schemas,
)

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)


@pytest.fixture()
def app(tmp_path):
    return server.init(tmp_path)


def scripted(*messages):
    """A completer that replays canned assistant messages in order."""
    queue = list(messages)

    def completer(history, tools):
        return queue.pop(0)

    return completer


def tool_call(name, arguments, call_id="c1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }


# ----------------------------------------------------------------- schemas ---

def test_tool_schemas_cover_every_tool():
    schemas = tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {
        "damage", "save_team", "get_team", "list_teams", "scout",
        "observe_damage", "opponent_summary", "lead_assist",
        "start_battle", "battle_update", "battle_field", "battle_log",
        "recommend_move",
    } <= names
    for s in schemas:
        assert s["type"] == "function"
        assert isinstance(s["function"]["parameters"], dict)
        assert "properties" in s["function"]["parameters"]
        assert s["function"]["description"]


# --------------------------------------------------------------- chat loop ---

def test_assistant_executes_tools_and_answers(app):
    completer = scripted(
        tool_call("damage", {
            "attacker": {"species": "Garchomp", "moves": ["Earthquake"],
                         "nature": "Jolly", "evs": {"atk": 252}},
            "defender": {"species": "Tyranitar"},
            "move": "Earthquake",
        }),
        {"role": "assistant",
         "content": "Earthquake does heavy super-effective damage."},
    )
    a = Assistant(completer=completer)
    answer = a.ask("How hard does my Garchomp's EQ hit Tyranitar?")
    assert answer == "Earthquake does heavy super-effective damage."
    # the tool actually ran: its JSON result is in the history
    tool_msg = next(m for m in a.history if m["role"] == "tool")
    payload = json.loads(tool_msg["content"])
    assert payload["type_effectiveness"] == 2.0
    assert tool_msg["tool_call_id"] == "c1"


def test_assistant_surfaces_tool_errors_to_the_model(app):
    completer = scripted(
        tool_call("recommend_move", {}),
        {"role": "assistant",
         "content": "You need to start a battle first."},
    )
    a = Assistant(completer=completer)
    answer = a.ask("what should I click?")
    assert answer == "You need to start a battle first."
    tool_msg = next(m for m in a.history if m["role"] == "tool")
    assert "error" in tool_msg["content"]
    assert "no battle in progress" in tool_msg["content"]


def test_assistant_stops_after_max_steps(app):
    endless = tool_call("list_teams", {})

    def completer(history, tools):
        return dict(endless)

    a = Assistant(completer=completer, max_steps=3)
    answer = a.ask("loop forever")
    assert "too many tool steps" in answer


def test_on_tool_hook_reports_activity(app):
    seen = []
    completer = scripted(
        tool_call("list_teams", {}),
        {"role": "assistant", "content": "You have no teams yet."},
    )
    a = Assistant(completer=completer,
                  on_tool=lambda n, args, r: seen.append((n, args, r)))
    a.ask("what teams do I have?")
    assert seen == [("list_teams", {}, "[]")]


# --------------------------------------------------------------- terminal ---

def test_slash_commands(app):
    from pokechamp.llm.chat import Style, handle_command

    style = Style(enabled=False)
    assert handle_command("not a command", style) is None
    assert "Slash commands" in handle_command("/help", style)
    assert handle_command("/teams", style) == "no teams saved yet"
    assert handle_command("/battle", style) == "no battle in progress"

    from tests.test_server import AZU, CHOMP

    server.save_team("ladder", [CHOMP, AZU])
    server.scout("rival", "Tyranitar", move="Crunch", item="Leftovers")
    assert handle_command("/teams", style) == "ladder"
    team_view = handle_command("/team ladder", style)
    assert "Garchomp: Dragon Claw, Earthquake" in team_view
    opp_view = handle_command("/opponent rival", style)
    assert "Tyranitar (100% hp): Crunch  @ Leftovers" == opp_view
    assert "recommend_move" in handle_command("/tools", style)
    assert "unknown command" in handle_command("/bogus", style)
    with pytest.raises(EOFError):
        handle_command("/quit", style)


# ------------------------------------------------------------ routing eval ---

def test_fixture_set_is_well_formed():
    assert len(FIXTURES) >= 50
    tool_names = {s["function"]["name"] for s in tool_schemas()}
    for query, expect, context in FIXTURES:
        assert expect in tool_names, expect
        assert context in ("idle", "battle")
        assert query.strip()
    # every tool the server exposes is exercised at least once
    assert {expect for _, expect, _ in FIXTURES} == tool_names


def test_evaluate_routing_scores_correctly(app):
    def oracle(history, tools):
        # the fixture's expected tool travels in the user message; find it
        query = history[-1]["content"]
        expect = next(e for q, e, _ in FIXTURES if q == query)
        return tool_call(expect, {})

    report = evaluate_routing(oracle, FIXTURES[:10])
    assert report["accuracy"] == 1.0 and report["n"] == 10

    def mute(history, tools):
        return {"role": "assistant", "content": "hmm", "tool_calls": []}

    report = evaluate_routing(mute, FIXTURES[:10])
    assert report["accuracy"] == 0.0
    assert len(report["misses"]) == 10
    assert all(m["got"] is None for m in report["misses"])

    def flaky(history, tools):
        raise TimeoutError("slow endpoint")

    # one bad call is a miss, not a lost eval run
    report = evaluate_routing(flaky, FIXTURES[:3])
    assert report["accuracy"] == 0.0
    assert all("error" in m["got"] for m in report["misses"])
