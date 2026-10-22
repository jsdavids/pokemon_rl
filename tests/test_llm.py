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


def test_strip_think():
    from pokechamp.llm.assistant import strip_think

    assert strip_think("<think>hmm, EQ is SE...</think>Use Earthquake.") \
        == "Use Earthquake."
    # generation cap landed mid-think: the whole tail is reasoning
    assert strip_think("<think>truncated mid-thought") == ""
    assert strip_think("plain answer") == "plain answer"


def test_assistant_strips_leaked_thinking(app):
    completer = scripted(
        {"role": "assistant",
         "content": "<think>lots of reasoning</think>Click Earthquake."},
    )
    a = Assistant(completer=completer)
    assert a.ask("best move?") == "Click Earthquake."
    # and the reasoning is not kept in history to be re-sent every turn
    assert a.history[-1]["content"] == "Click Earthquake."


def test_assistant_recovers_from_malformed_arguments(app):
    completer = scripted(
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "list_teams",
                                      "arguments": '{"broken'}}]},
        {"role": "assistant", "content": "Let me try that again."},
    )
    a = Assistant(completer=completer)
    assert a.ask("teams?") == "Let me try that again."
    # the bad call got a tool-role error response — no dangling
    # tool_calls in history (some servers 400 on that)
    tool_msg = next(m for m in a.history if m["role"] == "tool")
    assert "not valid JSON" in tool_msg["content"]
    assert tool_msg["tool_call_id"] == "c1"


def test_history_trimming_preserves_structure(app):
    def completer(history, tools):
        # every other turn calls a tool, so history holds mixed
        # user / assistant+tool_calls / tool / assistant exchanges
        n_user = sum(1 for m in history if m["role"] == "user")
        if history[-1]["role"] == "user" and n_user % 2 == 0:
            return dict(tool_call("list_teams", {}))
        return {"role": "assistant", "content": f"answer {n_user}"}

    a = Assistant(completer=completer, max_history=10)
    for i in range(12):
        a.ask(f"question {i}")
    # bounded: at most max_history at turn start + this turn's messages
    assert len(a.history) <= 10 + 4
    # the system prompt survives, and the cut lands on a user message
    assert a.history[0]["role"] == "system"
    assert a.history[1]["role"] == "user"
    # no orphaned tool responses: every tool message directly follows
    # an assistant message that made tool calls
    for prev, m in zip(a.history, a.history[1:]):
        if m["role"] == "tool":
            assert prev["role"] in ("assistant", "tool")
            assert any(x.get("tool_calls")
                       for x in a.history[:a.history.index(m)]
                       if x["role"] == "assistant")
    # the latest turn is intact
    assert a.history[-1]["content"].startswith("answer")


def test_eval_sends_one_merged_system_message():
    from pokechamp.llm.routing import first_tool_choice

    seen = {}

    def completer(messages, tools):
        seen["messages"] = messages
        return tool_call("damage", {})

    got = first_tool_choice(
        completer, "How much damage?", "battle", tools=[]
    )
    assert got == "damage"
    system = [m for m in seen["messages"] if m["role"] == "system"]
    assert len(system) == 1
    assert "battle against PlayerX" in system[0]["content"]
    assert "battle assistant" in system[0]["content"]


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


# ---------------------------------------------------------------- sampling ---

def test_sampling_presets_by_model_family():
    from pokechamp.llm.assistant import OllamaCompleter, sampling_preset

    assert sampling_preset("qwen3:14b-q5_K_M") == {
        "temperature": 0.6, "top_p": 0.95,
    }
    assert sampling_preset("gpt-oss:20b") == {
        "temperature": 1.0, "top_p": 1.0,
    }
    assert sampling_preset("llama3.3:70b")["temperature"] == 0.2
    # qwen3.8 is not qwen3: first match wins
    assert sampling_preset("qwen3.8:27b-q4_K_M")["temperature"] == 1.0
    assert sampling_preset("bonsai-2-27b")["temperature"] == 1.0

    c = OllamaCompleter(model="qwen3:30b-a3b")
    assert (c.temperature, c.top_p) == (0.6, 0.95)
    # explicit arguments beat the family preset
    c = OllamaCompleter(model="qwen3:14b", temperature=0.0, top_p=1.0,
                        seed=7)
    assert (c.temperature, c.top_p, c.seed) == (0.0, 1.0, 7)


def test_completer_request_body_carries_sampling(monkeypatch):
    from pokechamp.llm import assistant as mod

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "role": "assistant", "content": "ok",
            }}]}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    c = mod.OllamaCompleter(model="gpt-oss:20b", seed=42, max_tokens=4096)
    msg = c([{"role": "user", "content": "hi"}], [])
    assert msg["content"] == "ok"
    body = captured["body"]
    assert body["temperature"] == 1.0 and body["top_p"] == 1.0
    assert body["seed"] == 42 and body["max_tokens"] == 4096


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
