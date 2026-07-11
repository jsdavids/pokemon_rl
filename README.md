# pokechamp

Decision-support tools for Pokemon Champions players, exposed to a local LLM
that routes natural-language questions ("would his Garchomp survive Ice Punch
if it's bulky?") to the right tool.

| Tool | Package | Status |
|---|---|---|
| Damage calculator | `pokechamp.damage` | complete incl. multi-hit, move flags, weight/HP-scaled moves; 201 golden cases vs @smogon/calc |
| Stat/set estimator | `pokechamp.estimator` | Bayesian damage/speed/priority observations, regulation-legal items, ability inference, censored KOs |
| Battle simulator (rollout engine) | `pokechamp.sim` | seeded singles + doubles engine on champions data, full competitive mechanics (~270/~170 games/s) |
| Move recommender | `pokechamp.recommender` | determinized search (screening + UCB budget); beats MaxDamage 69-75% both formats |
| Self-play RL | `pokechamp.rl` | masked PPO + opponent pool; hybrid (search + learned net) beats pure search 69% |
| Session tools | `pokechamp.session` | versioned team store, opponent scouting book, lead assist, live-battle tracking |
| MCP server + LLM harness | `pokechamp.server`, `pokechamp.llm` | 13 tools over stdio; Ollama chat loop + routing eval |

See [ROADMAP.md](ROADMAP.md) for phase status. Remaining: the model bake-off
(needs the target machine) and ladder validation.

## Setup

```powershell
.\scripts\setup.ps1                    # venv + install + tests
.\scripts\setup.ps1 -Model qwen3:14b   # + pull the model and run the routing eval
```

or by hand: `python -m venv .venv && .venv\Scripts\pip install -e ".[dev,llm]"`.
Extras: `[llm]` (FastMCP + httpx), `[rl]` (PyTorch for the 4b trainer),
`[sim]` (poke-env, deferred cross-check only).

## Using it

```
pokechamp-chat --model qwen3:14b     # terminal assistant (needs Ollama running)
pokechamp-routing-eval --model ...   # tool-choice accuracy over 54 fixtures
pokechamp-mcp                        # bare MCP server on stdio, for any MCP client
```

A session looks like: save your team once ("save this team as ladder: ..."),
scout opponents as you meet them ("their Politoed is Drizzle"), then in-battle
narrate what you see ("my Garchomp is at 62%", "rain is up") and ask "what
should I click?" — the recommender searches determinized worlds sampled from
everything scouted so far. `/help` lists the no-LLM slash commands.

Data persists under `~/.pokechamp/`. Model guidance for a 16GB GPU:
`qwen3:14b` or `gpt-oss:20b` (fully on-GPU), `qwen3:30b-a3b` (split, MoE-fast);
prefer Q5+ quants — tool calling degrades with quantization faster than chat.

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
  `@smogon/calc` (201 golden cases); the team store and scout validate every
  entry against the scraped ruleset — validation once flagged "Alakazam
  can't learn Psychic," which turned out to be a scraper bug (a type-name
  filter eating the one move named after a type), caught precisely because
  entry-time validation surfaces data problems the moment they matter.
- **Latency:** tool-side compute is <1s per recommendation
  (`scripts/benchmark_latency.py`); the ~45s move timer is spent on the local
  LLM's round-trips.
