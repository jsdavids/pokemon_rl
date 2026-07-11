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


class OllamaCompleter:
    """POSTs to an OpenAI-compatible chat endpoint (Ollama's by default)."""

    def __init__(
        self,
        model: str = "qwen3:14b",
        base_url: str = "http://localhost:11434/v1",
        temperature: float = 0.2,
        timeout: float = 120.0,
        max_tokens: int | None = 2048,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
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
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        resp = httpx.post(
            f"{self.base_url}/chat/completions", json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]


class Assistant:
    """The chat loop: user text in, tool calls executed, final text out."""

    def __init__(
        self,
        completer: Completer | None = None,
        mcp=None,
        max_steps: int = 8,
        on_tool: Callable[[str, dict, str], None] | None = None,
    ):
        self.completer = completer or OllamaCompleter()
        self.mcp = mcp or pokechamp_server.mcp
        self.max_steps = max_steps
        # UI hook: called as on_tool(name, arguments, result_text) after
        # every executed tool, so the terminal can show what's happening
        self.on_tool = on_tool
        self.tools = tool_schemas(self.mcp)
        self.history: list[dict] = [
            {"role": "system", "content": ROUTING_PROMPT}
        ]

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
        for _ in range(self.max_steps):
            message = self.completer(self.history, self.tools)
            self.history.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                return message.get("content") or ""
            for call in calls:
                fn = call["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args) if args.strip() else {}
                result = asyncio.run(self._call_tool(fn["name"], args))
                if self.on_tool:
                    self.on_tool(fn["name"], args, result)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", fn["name"]),
                    "content": result,
                })
        return "(stopped: too many tool steps without an answer)"


# the terminal UI lives in pokechamp.llm.chat (`pokechamp-chat`)
