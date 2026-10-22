# pokechamp

Decision-support tools for Pokemon Champions players, exposed to a local LLM
that routes natural-language questions ("would his Garchomp survive Ice Punch
if it's bulky?") to the right tool.

| Tool | Package | Status |
|---|---|---|
| Damage calculator | `pokechamp.damage` | complete incl. multi-hit, move flags, weight/HP-scaled moves; 233 golden cases vs @smogon/calc (201 gen9 + 32 on champions data) plus a 345-form data-parity check |
| Stat/set estimator | `pokechamp.estimator` | Bayesian damage/speed/priority observations, usage-based priors from 5k+ tournament team sheets, ladder-grounded Stat Point spread hypotheses, regulation-legal items, ability inference, censored KOs; in simulated battles on held-out teams, stat error 5.3 points vs 12.1 with usage priors alone |
| Battle simulator (rollout engine) | `pokechamp.sim` | seeded singles + doubles engine on champions data, full competitive mechanics (~270/~170 games/s) |
| Move recommender | `pokechamp.recommender` | determinized search (screening + UCB budget); beats MaxDamage 69-75% both formats; on real M-C tournament teams under imperfect info, 63% with usage priors vs 58% without (78% with perfect info); with realistic spreads, 69% with ladder spread hypotheses + in-battle observations vs 65% usage alone (76% perfect info) |
| Self-play RL | `pokechamp.rl` | masked PPO + opponent pool; hybrid (search + learned net) beats pure search 69% |
| Session tools | `pokechamp.session` | versioned team store, opponent scouting book, lead assist, live-battle tracking |
| MCP server + LLM harness | `pokechamp.server`, `pokechamp.llm` | 13 tools over stdio; Ollama chat loop + routing eval |

See [ROADMAP.md](ROADMAP.md) for phase status. Game data is current through
**Regulation M-C** (Sept 9 - Dec 2, 2026). Remaining: ladder validation.

## Setup

```powershell
.\scripts\setup.ps1                      # venv + install + tests
.\scripts\setup.ps1 -Model qwen3:14b     # + pull the model and run the routing eval
```

or by hand: `python -m venv .venv && .venv\Scripts\pip install -e ".[dev,llm]"`.
Extras: `[llm]` (FastMCP + httpx), `[rl]` (PyTorch for the 4b trainer),
`[sim]` (poke-env, deferred cross-check only).

## Using it

```
pokechamp-chat --model qwen3:14b     # terminal assistant (needs Ollama running)
pokechamp-routing-eval --model ...   # tool-choice accuracy over 53 fixtures
pokechamp-mcp                        # bare MCP server on stdio, for any MCP client
```

A session looks like: save your team once ("save this team as ladder: ..."),
scout opponents as you meet them ("their Politoed is Drizzle"), then in-battle
narrate what you see ("my Garchomp is at 62%", "rain is up") and ask "what
should I click?" — the recommender searches determinized worlds sampled from
everything scouted so far. `/help` lists the no-LLM slash commands.

Data persists under `~/.pokechamp/`. Model guidance for a 16GB GPU — pin
quant tags explicitly, since tool calling degrades with quantization faster
than chat and bare tags are Q4. Ollama's qwen3 offers no Q5: the choices
are `qwen3:14b` (Q4_K_M, fully on-GPU) vs `qwen3:14b-q8_0` (16GB, splits
slightly — eval both to measure the quant tax), `gpt-oss:20b` (MXFP4-only,
fully on-GPU), `qwen3:30b-a3b` (Q4, split, MoE-fast), `qwen3.8:27b-q4_K_M`
(18GB, ~75% GPU: most accurate router, 94%, but slowest), and Bonsai 2 27B
(ternary Qwen3.8, 7GB, fully on-GPU and fast, but 77-83%: it tends to
call `list_teams` first). Bonsai won't load in Ollama — run it with
PrismML's llama.cpp fork (`llama-server --jinja`, port 8080) and pass
`--url http://localhost:8080/v1 --model bonsai-2-27b`. Ollama
prerequisites (setup.ps1 handles both; the eval preflight verifies them):
a recent build (Blackwell/RTX 50xx support; old builds fall back to CPU
silently) and `OLLAMA_CONTEXT_LENGTH=16384` — the 4096 default silently
front-truncates the system prompt + tool schemas. Sampling defaults come
from per-model-family presets (qwen3 0.6/0.95, gpt-oss 1.0 — near-greedy
decoding makes thinking models loop); the eval takes
`--temperature/--top-p/--seed/--max-tokens/--json` for controlled runs.

## When a new regulation drops

```powershell
.venv\Scripts\python scripts\scrape_serebii.py --refresh   # re-fetch everything (~20 min, rate-limited)
.venv\Scripts\python scripts\fetch_usage.py                # usage priors for the newest regulation (~10 min)
.venv\Scripts\python scripts\fetch_spreads.py --format <ladder format>   # Stat Point spreads, once Pikalytics has ladder data
.venv\Scripts\python -m pytest
```

Re-run `fetch_usage.py` weekly or so during a regulation: it reads the last
28 days of Limitless tournaments (`--days`, `--min-players`) and caches
finished ones, so refreshes only fetch what's new.

Regulation pages are discovered automatically (`regulationm-a`, `-b`, `-c`,
... until serebii 404s), so the scrape itself needs no code change. New
*mechanics* do: `tests/test_item_coverage.py` fails on any held item that
neither the calculator nor the sim has classified, which is the to-do list
for a new item pool. New abilities go in `damage/abilities.py`
(damage-roll effects) or `sim/` (battle-flow effects).

## Quick taste (library)

```python
from pokechamp import PokemonSet, calc_damage
from pokechamp.schemas import stat_table

chomp = PokemonSet("Garchomp", nature="Jolly", evs={**stat_table(), "atk": 252, "spe": 252})
print(calc_damage(chomp, PokemonSet("Tyranitar"), "Earthquake", ruleset="champions").describe())
```

## Layout

```
src/pokechamp/
  schemas.py        shared contract: PokemonSet, BattleState, FieldState
  stats.py          stat formulas + natures
  data/             ruleset-keyed JSON (gen9 sample + real scraped champions data)
  damage/           Phase 1: damage calculator
  estimator/        Phase 2: Bayesian stat estimator
  sim/              Phase 3: battle engine, agents, determinization, eval
  recommender/      Phase 4a: determinized search
  rl/               Phase 4b: PPO self-play + hybrid search
  session/          Phase 5: team store, scouting book, lead assist, battle session
  server.py         Phase 5: FastMCP server (13 tools)
  llm/              Phase 5-6: Ollama chat loop, routing eval, terminal UI
models/             trained policy checkpoints
tests/golden/       fixtures generated from @smogon/calc (acceptance gate)
scripts/            serebii scraper, golden generator, setup.ps1, latency benchmark
```

## Design notes

- **The tools form a dependency chain.** The estimator inverts the damage
  calculator; the recommender samples opponent builds from the estimator's
  posterior before searching; the scouting book persists those posteriors as
  replayable observation logs. Everything speaks `schemas.PokemonSet`.
- **Rollouts run on our own engine**, not Showdown — Champions' PP rebalance,
  exclusive Megas, and item pool aren't representable there, and search needs
  thousands of seeded games per decision.
- **Correctness anchors:** the calculator has exact rounding parity with
  `@smogon/calc` (233 golden cases, plus every scraped form's stats/types/
  weight checked against smogon's dex); the team store and scout validate every
  entry against the scraped ruleset — validation once flagged "Alakazam
  can't learn Psychic," which turned out to be a scraper bug (a type-name
  filter eating the one move named after a type), caught precisely because
  entry-time validation surfaces data problems the moment they matter.
- **Latency:** tool-side compute is <1s per recommendation
  (`scripts/benchmark_latency.py`); the ~45s move timer is spent on the local
  LLM's round-trips.
