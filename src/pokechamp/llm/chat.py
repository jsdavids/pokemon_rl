"""Phase 6: the terminal chat UI (`pokechamp-chat`).

A readable REPL over the Assistant: tool activity is shown as it happens
(the whole point of the app is trust in the numbers — the user should see
which tool produced them), slash commands cover the common lookups
without spending an LLM round-trip, and everything degrades to plain
text when ANSI isn't available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pokechamp import server as pokechamp_server
from pokechamp.llm.assistant import Assistant, OllamaCompleter


def _ansi_ok() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        os.system("")  # enables VT processing in the Windows console
    return sys.stdout.isatty()


class Style:
    def __init__(self, enabled: bool):
        c = (lambda code: (lambda s: f"\x1b[{code}m{s}\x1b[0m")) if enabled \
            else (lambda code: (lambda s: s))
        self.dim = c("2")
        self.bold = c("1")
        self.cyan = c("36")
        self.yellow = c("33")
        self.red = c("31")


HELP = """\
Ask anything — damage math, scouting entries, "what should I click".
Slash commands (no LLM round-trip):
  /teams              stored team names
  /team <name>        one team's current build
  /opponents          scouted opponent names
  /opponent <name>    what's known about their team
  /battle             the live battle log (last 15 entries)
  /tools              tool names the model can call
  /help               this text
  /quit               exit (blank line also works)"""


def handle_command(text: str, style: Style) -> str | None:
    """Slash commands answered from local state; None = not a command."""
    if not text.startswith("/"):
        return None
    cmd, _, arg = text[1:].partition(" ")
    arg = arg.strip()
    state = pokechamp_server.STATE
    try:
        if cmd == "help":
            return HELP
        if cmd == "teams":
            names = state.store.names()
            return "\n".join(names) if names else "no teams saved yet"
        if cmd == "team" and arg:
            team = state.store.get(arg)
            return "\n".join(
                f"{p.species}: {', '.join(p.moves)}"
                f"{f'  @ {p.item}' if p.item else ''}"
                for p in team
            )
        if cmd == "opponents":
            names = state.scout.opponents()
            return "\n".join(names) if names else "nobody scouted yet"
        if cmd == "opponent" and arg:
            team = state.scout.team(arg)
            if not team:
                return f"nothing known about {arg!r}"
            lines = []
            for sp in team:
                s = state.scout.summary(arg, sp)
                moves = ", ".join(s["revealed_moves"]) or "no moves seen"
                item = f"  @ {s['revealed_item']}" if s["revealed_item"] else ""
                lines.append(f"{sp} ({s['hp_percent']:.0f}% hp): {moves}{item}")
            return "\n".join(lines)
        if cmd == "battle":
            if state.battle is None:
                return "no battle in progress"
            entries = state.battle.log[-15:]
            return "\n".join(
                f"t{e['turn']}: {e['text']}" for e in entries
            ) or "battle log is empty"
        if cmd == "tools":
            import asyncio
            tools = asyncio.run(pokechamp_server.mcp.list_tools())
            return "\n".join(t.name for t in tools)
        if cmd in ("quit", "exit", "q"):
            raise EOFError
        return style.dim(f"unknown command /{cmd} — try /help")
    except EOFError:
        raise
    except Exception as e:  # noqa: BLE001 — REPL keeps running
        return style.red(f"error: {e}")


def _tool_line(style: Style):
    def on_tool(name: str, args: dict, result: str) -> None:
        brief = json.dumps(args)
        if len(brief) > 70:
            brief = brief[:67] + "..."
        note = ""
        if result.startswith("error:"):
            note = " " + style.red(result[:80])
        print(style.dim(f"  · {name}{brief}") + note)

    return on_tool


def main() -> None:  # pragma: no cover — interactive
    ap = argparse.ArgumentParser(description="pokechamp terminal assistant")
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--url", default="http://localhost:11434/v1")
    ap.add_argument("--data-dir", default=None,
                    help="override ~/.pokechamp for teams/scouting")
    args = ap.parse_args()
    if args.data_dir:
        pokechamp_server.init(args.data_dir)

    style = Style(_ansi_ok())
    assistant = Assistant(
        OllamaCompleter(model=args.model, base_url=args.url),
        on_tool=_tool_line(style),
    )
    print(style.bold(f"pokechamp ({args.model})") +
          style.dim("  /help for commands, blank line to quit"))
    while True:
        try:
            text = input(style.cyan("> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        try:
            answer = handle_command(text, style)
        except EOFError:
            break
        if answer is None:
            try:
                answer = assistant.ask(text)
            except Exception as e:  # noqa: BLE001 — endpoint hiccups
                answer = style.red(
                    f"model error: {e} — is Ollama running at {args.url}?"
                )
        print(answer)


if __name__ == "__main__":  # pragma: no cover
    main()
