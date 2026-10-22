"""Phase 5: the Ollama harness — MCP tools driven by a local LLM.

Ollama doesn't speak MCP natively, so this module is the bridge:
- MCP tool definitions -> OpenAI-style function schemas (Ollama's
  /v1/chat/completions endpoint accepts those, as do llama.cpp, vLLM and
  LM Studio — the runtime is a config string, per the bake-off plan)
- a chat loop that executes the model's tool calls against the in-process
  FastMCP server and feeds results back until the model answers in text

The completer is injectable (`completer(messages, tools) -> assistant
message dict`), so tests script the model and the routing eval measures a
real one.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Callable

import httpx
from fastmcp import Client

from pokechamp import server as pokechamp_server

ROUTING_PROMPT = """\
You are the Pokemon Champions battle assistant. You have tools for exact
damage math, storing the user's teams, scouting opponents, team-preview
lead advice, and in-battle move recommendations. Rules:

- Use a tool for anything factual; never guess damage numbers, stats, or
  what an opponent's build might be.
- Data entry: `scout` records an opponent's revealed species/move/item/
  ability. `observe_damage` records how hard a hit landed (this sharpens
  the opponent's estimated build). `battle_update` records HP/status/
  boosts/switches during a battle; `battle_field` records weather,
  terrain, Trick Room, and hazards; `battle_log` stores free-form notes.
- Before `recommend_move` works, a battle must be started with
  `start_battle` (team name + opponent name).
- Copy species, move, and item names exactly as the user gives them.
- Percentages are 0-100, statuses are brn/par/slp/frz/psn/tox ("none"
  clears one).
- Answer concisely: give the recommendation or number first, then one
  sentence of reasoning.
"""
# NOTE: a tool-choice few-shot block was tried here and measured NET
# NEGATIVE at 4B (run 4 of the routing eval): it failed to fix the
# lead_assist->list_teams anchor and the longer prompt caused diffuse
# no-tool-call stalls under the generation cap. Routing fixes belong in
# tool schemas/descriptions or grammar constraints, not prompt examples.

Completer = Callable[[list[dict], list[dict]], dict]


def tool_schemas(mcp=None) -> list[dict]:
    """Every MCP tool as an OpenAI-style function definition."""
    tools = asyncio.run((mcp or pokechamp_server.mcp).list_tools())
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


# Model-family sampling presets. One temperature does not fit all: qwen3's
# thinking mode loops endlessly under near-greedy decoding (Qwen's own
# guidance is 0.6/0.95 — the "10+ minute thinking spirals" above were
# consistent with this), and gpt-oss is tuned for temperature 1.0.
# Qwen3.8 (and Bonsai 2, its ternary build) are tuned for 1.0/0.95; top_k
# and min_p come from the server (Ollama's params / llama-server flags).
# First substring match wins, so qwen3.8 must precede qwen3.
# Unrecognized families keep the conservative low-temperature default.
SAMPLING_PRESETS: list[tuple[str, dict]] = [
    ("qwen3.8", {"temperature": 1.0, "top_p": 0.95}),
    ("bonsai", {"temperature": 1.0, "top_p": 0.95}),
    ("qwen3", {"temperature": 0.6, "top_p": 0.95}),
    ("gpt-oss", {"temperature": 1.0, "top_p": 1.0}),
]
DEFAULT_SAMPLING = {"temperature": 0.2, "top_p": None}


def sampling_preset(model: str) -> dict:
    """Recommended sampling for a model tag (substring family match)."""
    name = model.lower()
    for family, preset in SAMPLING_PRESETS:
        if family in name:
            return dict(preset)
    return dict(DEFAULT_SAMPLING)


class OllamaCompleter:
    """POSTs to an OpenAI-compatible chat endpoint (Ollama's by default).

    temperature/top_p default to the model family's preset; pass explicit
    values to override. seed fixes sampling for reproducible eval runs.
    """

    def __init__(
        self,
        model: str = "qwen3:14b",
        base_url: str = "http://localhost:11434/v1",
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        timeout: float = 120.0,
        max_tokens: int | None = 2048,
    ):
        preset = sampling_preset(model)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = (
            preset["temperature"] if temperature is None else temperature
        )
        self.top_p = preset["top_p"] if top_p is None else top_p
        self.seed = seed
        self.timeout = timeout
        # generation cap (thinking tokens included): reasoning models can
        # spiral for 10+ minutes on ambiguous asks, and a truncated answer
        # the user can rephrase beats one that blows the move timer
        self.max_tokens = max_tokens

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "temperature": self.temperature,
        }
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.seed is not None:
            body["seed"] = self.seed
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        resp = httpx.post(
            f"{self.base_url}/chat/completions", json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]


# Some endpoint/model combinations leak the reasoning block into content
# as <think>...</think> instead of a separate field. An unclosed block
# (the generation cap landed mid-think) swallows to end of text.
_THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL)


def strip_think(text: str) -> str:
    """Drop leaked <think> blocks — they'd print as walls of reasoning
    and, kept in history, get re-sent on every later turn."""
    return _THINK_RE.sub("", text).strip()


class Assistant:
    """The chat loop: user text in, tool calls executed, final text out."""

    def __init__(
        self,
        completer: Completer | None = None,
        mcp=None,
        max_steps: int = 8,
        on_tool: Callable[[str, dict, str], None] | None = None,
        max_history: int = 60,
    ):
        self.completer = completer or OllamaCompleter()
        self.mcp = mcp or pokechamp_server.mcp
        self.max_steps = max_steps
        # UI hook: called as on_tool(name, arguments, result_text) after
        # every executed tool, so the terminal can show what's happening
        self.on_tool = on_tool
        # message-count cap on history: Ollama silently front-truncates
        # an over-length prompt, and the front is the system prompt +
        # tool schemas — better to drop oldest turns ourselves
        self.max_history = max_history
        self.tools = tool_schemas(self.mcp)
        self.history: list[dict] = [
            {"role": "system", "content": ROUTING_PROMPT}
        ]

    def _trim_history(self) -> None:
        """Keep the system prompt + the most recent messages, cutting on
        a user message so no tool response is orphaned from the assistant
        message that called it (some servers 400 on that)."""
        if len(self.history) <= self.max_history:
            return
        tail = self.history[-(self.max_history - 1):]
        while tail and tail[0].get("role") != "user":
            tail.pop(0)
        self.history[:] = [self.history[0]] + tail

    async def _call_tool(self, name: str, arguments: dict) -> str:
        """Execute one tool; errors come back as text so the model can
        correct itself (wrong species name, no battle started, ...)."""
        try:
            async with Client(self.mcp) as client:
                result = await client.call_tool(name, arguments)
            if result.data is not None:
                return json.dumps(result.data, default=str)
            return "\n".join(
                getattr(c, "text", "") for c in result.content
            ) or "ok"
        except Exception as e:  # noqa: BLE001 — surfaced to the model
            return f"error: {e}"

    def ask(self, text: str) -> str:
        self.history.append({"role": "user", "content": text})
        self._trim_history()
        for _ in range(self.max_steps):
            message = self.completer(self.history, self.tools)
            if isinstance(message.get("content"), str):
                message["content"] = strip_think(message["content"])
            self.history.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                return message.get("content") or ""
            for call in calls:
                fn = call["function"]
                args, error = fn.get("arguments") or {}, None
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except ValueError as e:
                        # a raised exception here would leave the
                        # assistant's tool_calls dangling in history with
                        # no tool response; answer it with an error the
                        # model can correct instead
                        error = (f"error: tool arguments are not valid "
                                 f"JSON ({e}) — retry with valid JSON")
                        args = {}
                result = error or asyncio.run(
                    self._call_tool(fn["name"], args)
                )
                if self.on_tool:
                    self.on_tool(fn["name"], args, result)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", fn["name"]),
                    "content": result,
                })
        return "(stopped: too many tool steps without an answer)"


# the terminal UI lives in pokechamp.llm.chat (`pokechamp-chat`)
