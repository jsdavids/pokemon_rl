"""Routing fixtures + eval: does a local model pick the right tool?

Each fixture is one natural user utterance and the tool a competent
assistant would reach for first. The eval sends each query single-turn
(with a one-line situation note, since real usage has conversation
context) and scores the model's FIRST tool choice — arguments aren't
validated here; this measures routing, the thing small local models get
wrong most. Run against a live endpoint:

    python -m pokechamp.llm.routing --model qwen3:14b

and compare models by overall + per-tool accuracy. A few fixtures are
genuinely ambiguous (a revealed move mid-battle is both scoutable and
loggable); a strong model should still clear ~90%.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from pokechamp.llm.assistant import ROUTING_PROMPT, Completer, tool_schemas

CONTEXT_NOTES = {
    "idle": "Situation: no battle is currently in progress.",
    "battle": (
        "Situation: a battle against PlayerX is in progress, tracked with "
        "the user's 'ladder' team."
    ),
}

# (query, expected_tool, context)
FIXTURES: list[tuple[str, str, str]] = [
    # --- damage math ---
    ("How much does my Adamant max-attack Azumarill's Play Rough do to a standard Garchomp?", "damage", "idle"),
    ("Would Earthquake from my Garchomp OHKO a Gholdengo?", "damage", "idle"),
    ("What's the damage range on Gholdengo's Shadow Ball into Politoed?", "damage", "idle"),
    ("If it crits, how much does Tyranitar's Crunch do to my Dragapult?", "damage", "battle"),
    ("Does my Alakazam's Psychic 2HKO a bulky Conkeldurr?", "damage", "idle"),
    ("How hard does a rain-boosted Surf from Politoed hit my Tyranitar?", "damage", "battle"),
    # --- team storage ---
    ("Save this team as 'ladder': Garchomp with Dragon Claw and Earthquake, Azumarill with Play Rough and Aqua Jet, Gholdengo with Shadow Ball and Recover.", "save_team", "idle"),
    ("Store my new draft as 'rain team': Politoed with Surf and Ice Beam, plus Azumarill with Liquidation.", "save_team", "idle"),
    ("I swapped Recover for Thunderbolt on Gholdengo — save the updated ladder team.", "save_team", "idle"),
    ("Show me my ladder team.", "get_team", "idle"),
    ("What did version 0 of my rain team look like?", "get_team", "idle"),
    ("Pull up the previous iteration of ladder.", "get_team", "idle"),
    ("Which teams do I have saved?", "list_teams", "idle"),
    ("List all my stored builds.", "list_teams", "idle"),
    # --- scouting: hard facts ---
    ("I'm about to face PlayerX — they brought a Tyranitar to team preview.", "scout", "idle"),
    ("PlayerX's Garchomp revealed Swords Dance last game.", "scout", "idle"),
    ("Their Azumarill is holding a Sitrus Berry.", "scout", "battle"),
    ("Their Politoed's ability is Drizzle.", "scout", "battle"),
    ("PlayerX has a Dragonite with Extreme Speed.", "scout", "idle"),
    ("Mark down that PlayerX's Gholdengo knows Make It Rain.", "scout", "idle"),
    ("Turns out that Tyranitar has Unnerve, not Sand Stream.", "scout", "battle"),
    ("Add a Hydreigon to PlayerX's known team.", "scout", "idle"),
    # --- scouting: damage observations ---
    ("My Garchomp's Earthquake took their Tyranitar from full to 40%.", "observe_damage", "battle"),
    ("Their Tyranitar's Crunch did 55% to my Gholdengo.", "observe_damage", "battle"),
    ("Their Tyranitar KO'd my Alakazam with Crunch — I was at 80%.", "observe_damage", "battle"),
    ("I hit the opposing Azumarill with Thunderbolt and its bar dropped to a quarter.", "observe_damage", "battle"),
    ("Record that their Dragapult's Shadow Ball chunked my Azumarill for 38%.", "observe_damage", "battle"),
    # --- scouting: reading it back ---
    ("What do we know about PlayerX's Tyranitar so far?", "opponent_summary", "idle"),
    ("What's the estimated speed on their Garchomp?", "opponent_summary", "battle"),
    ("Show the scouting report on PlayerX.", "opponent_summary", "idle"),
    ("Is their Azumarill more likely Huge Power or Thick Fat?", "opponent_summary", "battle"),
    # --- lead assist ---
    ("Who should I lead against PlayerX?", "lead_assist", "idle"),
    ("Which three of my ladder team should I bring into this matchup?", "lead_assist", "idle"),
    ("Best lead for my ladder team versus PlayerX's squad?", "lead_assist", "idle"),
    ("Help me pick my doubles leads against PlayerX.", "lead_assist", "idle"),
    # --- battle lifecycle ---
    ("Starting a match against PlayerX with my ladder team.", "start_battle", "idle"),
    ("New battle: PlayerX, singles, use the ladder team.", "start_battle", "idle"),
    ("Queue popped — I'm playing PlayerX now, track it with ladder.", "start_battle", "idle"),
    ("My Garchomp is down to 62%.", "battle_update", "battle"),
    ("Their Tyranitar got burned.", "battle_update", "battle"),
    ("They switched Politoed in.", "battle_update", "battle"),
    ("My Azumarill took Toxic — it's badly poisoned now.", "battle_update", "battle"),
    ("Their Garchomp is at +2 attack now.", "battle_update", "battle"),
    ("Rain is up now.", "battle_field", "battle"),
    ("They set Stealth Rock on my side.", "battle_field", "battle"),
    ("Trick Room went up this turn.", "battle_field", "battle"),
    ("Note for later: this player always Protects on the mega turn.", "battle_log", "battle"),
    ("What happened over the last few turns?", "battle_log", "battle"),
    ("Log this: they sacked the Politoed to keep Tyranitar healthy.", "battle_log", "battle"),
    # --- the payoff ---
    ("What should I click this turn?", "recommend_move", "battle"),
    ("Best move here?", "recommend_move", "battle"),
    ("Should I stay in or switch out?", "recommend_move", "battle"),
    ("Rank my options for this turn.", "recommend_move", "battle"),
]


def first_tool_choice(
    completer: Completer, query: str, context: str, tools: list[dict]
) -> str | None:
    # one merged system message: chat templates differ in how they render
    # a second system message (harmony remaps system prompts entirely),
    # and that would be a per-model confound in the numbers
    messages = [
        {"role": "system",
         "content": f"{ROUTING_PROMPT}\n{CONTEXT_NOTES[context]}"},
        {"role": "user", "content": query},
    ]
    message = completer(messages, tools)
    calls = message.get("tool_calls") or []
    return calls[0]["function"]["name"] if calls else None


def evaluate_routing(
    completer: Completer,
    fixtures: list[tuple[str, str, str]] | None = None,
    verbose: bool = False,
) -> dict:
    """Routing accuracy of a completer over the fixture set: overall,
    per expected tool, and the misses (query, expected, got)."""
    fixtures = fixtures if fixtures is not None else FIXTURES
    tools = tool_schemas()
    hits, per_tool, misses = 0, defaultdict(lambda: [0, 0]), []
    for query, expect, context in fixtures:
        try:
            got = first_tool_choice(completer, query, context, tools)
        except Exception as e:  # noqa: BLE001 — one bad call is a miss,
            got = f"error: {type(e).__name__}"  # not a lost eval run
        per_tool[expect][1] += 1
        if got == expect:
            hits += 1
            per_tool[expect][0] += 1
        else:
            misses.append({"query": query, "expected": expect, "got": got})
        if verbose:
            mark = "ok " if got == expect else "MISS"
            print(f"  {mark} [{expect:>16}] got={got}: {query[:60]}")
    return {
        "accuracy": hits / len(fixtures) if fixtures else 0.0,
        "n": len(fixtures),
        "per_tool": {
            k: {"correct": v[0], "total": v[1]} for k, v in per_tool.items()
        },
        "misses": misses,
    }


def preflight(completer, base_url: str) -> dict:  # pragma: no cover — live
    """Warm the model up and verify where it landed, so run 1 fixture 1
    doesn't eat the cold load and a silent partial CPU offload doesn't
    quietly corrupt a bake-off. Returns what it learned for the report."""
    import time

    import httpx

    root = base_url.rstrip("/")
    root = root[: -len("/v1")] if root.endswith("/v1") else root
    info: dict = {}
    try:
        info["ollama_version"] = httpx.get(
            f"{root}/api/version", timeout=5.0
        ).json().get("version")
        print(f"ollama {info['ollama_version']} at {root}")
    except Exception:
        print(f"note: {root}/api/version unreachable — not Ollama? "
              "skipping GPU checks")
    t0 = time.perf_counter()
    try:
        completer(
            [{"role": "user", "content": "Reply with the word: ready"}],
            tool_schemas(),
        )
    except Exception as e:  # noqa: BLE001 — fail the run loudly up front
        raise SystemExit(
            f"warmup call failed ({type(e).__name__}: {e}) — is the "
            f"endpoint up and the model pulled? "
            f"(ollama pull {completer.model})"
        ) from e
    info["warmup_s"] = round(time.perf_counter() - t0, 1)
    print(f"warmup (model load + one completion): {info['warmup_s']}s")
    if "ollama_version" not in info:
        return info
    try:
        loaded = httpx.get(f"{root}/api/ps", timeout=5.0).json()["models"]
    except Exception:
        return info
    for m in loaded:
        if completer.model not in (m.get("name"), m.get("model")):
            continue
        vram, size = m.get("size_vram", 0), m.get("size") or 1
        info["gpu_offload"] = round(vram / size, 3)
        ctx = m.get("context_length")
        if ctx:
            info["context_length"] = ctx
        where = (
            "100% GPU" if vram >= size
            else f"{vram / size:.0%} GPU — split/CPU; latency and even "
                 "accuracy comparisons are not like-for-like"
        )
        print(f"{completer.model}: {where}"
              + (f", context {ctx}" if ctx else ""))
        if ctx and ctx < 8192:
            print(f"  WARNING: context {ctx} < 8192 — the tool schemas + "
                  "system prompt risk silent front-truncation. Set "
                  "OLLAMA_CONTEXT_LENGTH=16384 and restart Ollama.")
    return info


def main() -> None:  # pragma: no cover — needs a live endpoint
    import json
    from datetime import datetime, timezone

    from pokechamp.llm.assistant import OllamaCompleter

    ap = argparse.ArgumentParser(description="routing accuracy eval")
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--url", default="http://localhost:11434/v1")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip", type=int, default=0,
                    help="resume an interrupted run at fixture N")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="per-call seconds; raise for CPU inference")
    ap.add_argument("--temperature", type=float, default=None,
                    help="override the model family's sampling preset")
    ap.add_argument("--top-p", type=float, default=None,
                    help="override the model family's sampling preset")
    ap.add_argument("--seed", type=int, default=None,
                    help="fix sampling for reproducible runs")
    ap.add_argument("--max-tokens", type=int, default=4096,
                    help="generation cap incl. thinking tokens (chat "
                         "defaults to 2048; the eval allows longer "
                         "thinking so the cap doesn't score as routing)")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="append the full run report to this file as one "
                         "JSON line (misses included) for bake-off "
                         "comparisons")
    args = ap.parse_args()
    completer = OllamaCompleter(
        model=args.model, base_url=args.url, timeout=args.timeout,
        temperature=args.temperature, top_p=args.top_p, seed=args.seed,
        max_tokens=args.max_tokens,
    )
    print(f"sampling: temperature={completer.temperature} "
          f"top_p={completer.top_p} seed={completer.seed} "
          f"max_tokens={completer.max_tokens}")
    env = preflight(completer, args.url)
    fixtures = FIXTURES[args.skip:]
    if args.limit:
        fixtures = fixtures[: args.limit]
    report = evaluate_routing(completer, fixtures, verbose=True)
    print(f"\n{args.model}: {report['accuracy']:.0%} "
          f"({report['n'] - len(report['misses'])}/{report['n']})")
    for tool, r in sorted(report["per_tool"].items()):
        print(f"  {tool:>16}: {r['correct']}/{r['total']}")
    if report["misses"]:
        print("misses:")
        for m in report["misses"]:
            print(f"  [{m['expected']:>16}] got={m['got']}: "
                  f"{m['query'][:60]}")
    if args.json_out:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "model": args.model,
            "url": args.url,
            "sampling": {
                "temperature": completer.temperature,
                "top_p": completer.top_p,
                "seed": completer.seed,
                "max_tokens": completer.max_tokens,
            },
            "env": env,
            "skip": args.skip,
            "limit": args.limit,
            **report,
        }
        with open(args.json_out, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        print(f"report appended to {args.json_out}")


if __name__ == "__main__":  # pragma: no cover
    main()
