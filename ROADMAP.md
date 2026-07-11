# Roadmap

Goal: a local-LLM-driven assistant for Pokemon Champions built on three tools —
damage calculator, stat estimator, and an RL/search move recommender.

Architecture decisions (made up front):

1. The tools form a dependency chain: estimator inverts the calculator;
   recommender consumes the estimator's posterior. One shared schema layer.
2. Python stack; tools exposed via MCP (FastMCP) so any tool-calling local
   model works (Ollama + Qwen 3 / Llama 3.3 class).
3. Champions mechanics aren't fully documented — game data lives in swappable
   ruleset JSON, starting from Gen 9 / Showdown data.

## Phase 0 — Foundations (1-2 weeks) — DONE

- [x] Repo, package layout, pytest harness
- [x] Shared schemas (PokemonSet, BattleState, FieldState)
- [x] Ruleset-keyed data layer with sample gen9 data
- [x] Full **champions** ruleset scraped from serebii.net
      (scripts/scrape_serebii.py): all species incl. Mega forms with
      abilities + learnsets, all moves with Champions-rebalanced PP / flags /
      multi-hit / effect text, items, per-regulation legality (M-A, M-B)
- [ ] Full gen9 data dump (script to convert Showdown data files) — lower
      priority now that champions data exists

## Phase 1 — Damage calculator (2-3 weeks) — DONE (except multi-hit)

- [x] Core Gen 5+ formula: base damage, rolls, STAB, type chart, crit, burn
- [x] Batch API (moves x hypothetical defender builds)
- [x] Stat boost stages (incl. crit interactions)
- [x] Items: Choice Band/Specs, Life Orb, Expert Belt, Assault Vest,
      Muscle Band/Wise Glasses, type boosters
- [x] Spread modifier, weather (incl. sand/snow stat boosts), terrain, screens
- [x] Abilities: immunities (Levitate etc.), Huge Power, Guts, Technician,
      Adaptability, Thick Fat, Fur Coat, Marvel Scale, Multiscale, Ice Scales,
      Tinted Lens, Filter/Solid Rock
- [x] Tera STAB (attacker) and Tera defensive typing (defender)
- [x] Move special cases: Body Press, Psyshock, Seismic Toss/Night Shade, Facade
- [x] Move flags (contact/sound/punch/bite/slicing/bullet) with
      flag-conditional effects: Tough Claws, Iron Fist, Strong Jaw, Sharpness,
      Punk Rock, Fluffy, Punching Glove (incl. contact negation)
- [x] Multi-hit moves: per-hit rolls + hits, exact total-damage pmf
      (total_distribution), ko_chance; estimator likelihoods use the pmf
- [x] Golden test suite vs @smogon/calc (192 cases passing; expand alongside
      each new modifier)
- [ ] Per-hit variation in multi-hit (per-hit crits, Multiscale first hit
      only, Loaded Dice, Triple Axel)

## Phase 2 — Stat estimator (3-4 weeks) — IN PROGRESS

- [x] Candidate enumeration (archetype grid: EV template x nature x item x
      ability), driven by the champions ruleset: items limited to what the
      game offers (no Choice Band/Specs/AV in Champions!), regulation-aware
      legality (Life Orb is M-B+), ability hypotheses from species data,
      Mega forms get no item variants (slot committed to the stone)
- [x] Exact damage-roll likelihoods (P(obs | build) = matching rolls / 16),
      Bayesian accumulation across observations
- [x] Speed inference from turn order (Scarf, Tailwind, paralysis, boosts,
      Trick Room; ties scored 0.5)
- [x] Output: ranked PokemonSet candidates with probabilities; feature
      marginals; per-stat summary
- [x] Ability hypotheses in the candidate space (posterior now answers
      "is it Huge Power?" / "is it Thick Fat?" from damage observations)
- [x] Current-HP tracking: the estimator follows the opponent's HP bar,
      accepts observations as remaining-% readings, and treats KOs as
      censored observations (damage >= what was left)
- [x] Priority-bracket inference: observe_move_order reads priorities from
      the ruleset's move data — cross-bracket order carries no speed info,
      contradictions raise (unmodeled Prankster/Gale Wings/Quick Claw)
- [x] apply_prior hook (external reweighting of the candidate space)
- [ ] Usage-statistics priors and usage-derived candidate sets (hook exists;
      no public Champions usage data yet)

## Phase 3 — Simulation infrastructure (3-4 weeks) — IN PROGRESS

Decision (2026-07): rollouts run on a custom engine (`pokechamp.sim`) built
on our damage calc + champions data — Showdown can't represent Champions
(PP rebalance, custom Megas, item pool) and websocket rollouts are ~1000x
too slow for search. Showdown/poke-env is retained as a later mechanics
cross-check, not a rollout path.

- [x] Seeded singles battle engine (~280 games/s with full effects): damaging moves fully
      modeled (accuracy, 4.17% crits, real damage rolls, multi-hit with
      35/35/15/15 counts, priority, immunities, Life Orb recoil), switching,
      Trick Room/Tailwind ordering, turn cap with HP tiebreak
- [x] Baseline agents: random, max-damage, heuristic (switch when walled)
- [x] Determinization: sample_opponent draws posterior builds + fills
      movesets from the champions learnset
- [x] Evaluation harness (seeded matchups, side/team alternation, win rates;
      max-damage reliably beats random)
- [x] Doubles (Champions' primary competitive format, ~215 games/s):
      per-slot actions with explicit targets, canonical move targeting
      scraped from serebii (allAdjacent Earthquake hits your ally;
      adjacentFoes Hyper Voice doesn't), spread reduction only when
      multiple targets are actually hit, retargeting after mid-turn faints,
      per-target accuracy/crit, switch-conflict resolution, empty-slot play
- [x] Status moves via curated effects table (sim/effects.py): setup and
      stat drops, sleep/para/burn/toxic with type + powder immunities,
      recovery, screens, Tailwind, weather/terrain/Trick Room with real
      durations; uncurated status moves remain documented no-ops
- [x] Secondary effects (curated): status riders (Scald burn...), flinch
      (Fake Out first-turn-only), stat drops (Icy Wind), self-drops (Close
      Combat); sleep 1-3 turns, 25% full para, 20% thaw, escalating toxic;
      end-of-turn chip (status, sand) + Leftovers; boosts reset on switch
- [x] Doubles support moves: Protect (1/3^n consecutive), Wide Guard,
      Follow Me/Rage Powder redirection, Helping Hand. Data find: Champions
      marks Rage Powder as NOT a powder move — grass doesn't ignore it here
      (the engine honors whichever flag the data carries)
- [x] BattleState -> Battle seeding: Battle.snapshot() freezes teams/HP/
      status/boosts/actives/conditions; Battle.from_state() reconstructs for
      determinized rollouts (timers restart at a short default)
- [x] Entry hazards: Stealth Rock (scaled by rock weakness), Spikes layers,
      Toxic Spikes (poison-type absorption), Sticky Web; Defog and Rapid
      Spin clearing; hazards apply on every switch-in incl. forced
- [x] Terrain side effects (grounded-only): Grassy 1/16 heal, Electric
      blocks sleep, Misty blocks all status, Psychic blocks priority
- [x] PP tracking from Champions' rebalanced PP data; 0-PP moves illegal;
      Struggle (with 1/4 recoil) when the whole moveset is exhausted
- [x] Trapping: Shadow Tag / Arena Trap (grounded) / Magnet Pull (steel)
      block switches (ghosts exempt); binding moves (Fire Spin...) trap
      4-5 turns with 1/8 chip; snapshots carry pp + hazards + traps
- [x] Substitute: 1/4 HP doll absorbs damage/status/riders/trapping, no
      excess carryover, sound bypasses, dies on switch; snapshotted
- [x] Weather wars: Drizzle/Drought/Sand Stream/Snow Warning + terrain
      Surges trigger on entry in speed order (slower lead wins contested
      setups); weather rocks extend to 8 turns; move + ability setters
      share one pipeline
- [x] Ability redirection & absorption: Lightning Rod/Storm Drain draw
      single-target moves in doubles; absorb abilities apply their real
      on-hit effect (Volt/Water Absorb heal, Sap Sipper/Rod boosts,
      Well-Baked Body +2 Def); Safeguard blocks status for 5 turns
- [x] Imposter/Transform (full form copy at 5 PP, own HP kept, clean revert
      on switch, snapshot-safe), Baton Pass (boosts + Substitute handoff,
      recipient takes hazards), Taunt/Encore locking, Choice Scarf move
      lock, confusion (2-5 turns, 33% typeless 40 BP self-hit)
- [x] Weight/HP-scaled base power in the CALCULATOR with golden parity
      (201 cases): Grass Knot/Low Kick tiers from scraped kg weights (all
      303 champions forms), Heavy Slam/Heat Crash ratios, Eruption/Water
      Spout via integer current HP; Technician sees the computed BP
- [x] Perish Song (3-turn clock, switching escapes, Soundproof deaf to it
      and to Hyper Voice), Disable (4 turns; Disable+Encore = forced
      Struggle), phazing (Whirlwind/Roar; Dragon Tail/Circle Throw damage
      then drag; random replacement takes hazards, boosts erased)
- [x] Berries (all 28 in Champions' item list) + Unnerve denial: Sitrus/
      Oran threshold heals, status-cure berries (Cheri...Lum, Persim for
      confusion), type-resist berries halving one super-effective hit;
      single-use consumption carried through snapshots
- [x] Destiny Bond (lapses on the user's next move), Counter/Mirror Coat
      (2x category-matched return), Wish (slot-targeted delayed heal,
      lands on the switch-in), Heal Bell/Aromatherapy
- [ ] Showdown/poke-env bridge as a mechanics cross-check (deferred)

## Phase 4 — Move recommender (6-10 weeks) — IN PROGRESS

- [x] 4a: determinized search baseline (pokechamp.recommender): joint
      actions (switch-conflict-filtered per-slot product in doubles), 1-ply
      expectation vs a modeled opponent + depth-limited MaxDamage rollouts,
      value = win/loss else team-HP differential; worlds share seeds across
      candidates (common random numbers — chance noise at 1 world was the
      difference between losing to and beating MaxDamage; 4 worlds fixed
      it). With estimators attached, each world redraws opponent builds
      from the posterior (move-index volatiles dropped on resample). Beats
      MaxDamage ~65-70% on mirror teams in singles AND doubles; a full
      doubles recommend() with 8 estimator worlds at depth 2 runs in ~0.9s
- [x] 4a follow-ups: screening (depth-0 pass in one shared world ranks all
      joints; status moves and switches compete on outcome, not a damage
      heuristic) + UCB1 budget mode (flat Monte Carlo: a fixed evaluation
      budget concentrates on the contenders — screening alone lost to
      MaxDamage at 44%, screening+budget wins at 75% in doubles, the best
      result yet); EpsilonAgent opponent model (greedy-with-slips) hedges
      against non-greedy foes
- [x] 4b foundation (pokechamp.rl, torch-cpu via the [rl] extra): fixed
      15-per-slot action space whose masks/decodings derive from
      Battle.legal_actions (every engine rule inherited), ~700-dim
      seat-relative feature encoding, two-headed masked policy/value net,
      PPO with GAE + potential-based HP shaping, scripted-opponent and
      both-seats self-play modes, PolicyAgent wrapper
- [x] 4b self-play infrastructure: frozen-checkpoint opponent pool with
      scripted anchors (guard against self-play cycling), random_team
      factory for varied-team training
- [x] 4b hybrid endpoint (rl/hybrid.py + value_fn hook in the recommender):
      the 4a searcher with the net as leaf value, rollout policy, and
      opponent model. After 150 pool-self-play iterations (~2min CPU,
      models/policy_singles.pt) on the mirror team: raw greedy policy
      plateaus at ~45% vs MaxDamage/Heuristic, but hybrid(depth=0,
      4 worlds) hits 81% vs MaxDamage and 69% vs the pure 4a
      SearchAgent(d2,b32) — search + learned value beats both components
- [x] 4b entropy investigation: added a target-KL early stop to the PPO
      update (safety rail; stays in). Finding: measured KL was ~0.002 the
      whole run, so the fixed-team collapse is genuine convergence on a
      narrow optimal line, not update instability — and varied-team
      training (random_team factory) is the actual fix: entropy holds at
      ~1.3 through 150 iterations
- [x] 4b doubles training run (models/policy_doubles.pt, 100 iters):
      policy 88% vs Random / 38% vs MaxDamage raw; hybrid(budget=48,
      mc=24) hits 69% vs MaxDamage AND 69% vs the best pure doubles
      searcher — same hybrid > both-parents pattern as singles
- [x] 4b specialist/generalist finding (models/policy_varied.pt): a
      varied-team generalist (150 iters) learns (beats Random ~65%
      in-distribution) but its transfer to any SPECIFIC held-out team is
      high-variance run to run (one run 82% vs Random on the mirror team,
      a replication 22%), and its value head guides search no better than
      the plain HP differential. Deployment recipe unchanged and
      reinforced: specialize per matchup — a fixed-team run costs ~2 min
      CPU, cheap enough to train on the actual teams before a session.
      (policy_varied.pt retrained 2026-07 on post-Psychic-fix data; the
      instrumented run confirmed 880 Psychic carriers across 60 species
      flowed through training)
- [ ] 4b data-blocked (revisit when sources exist): usage-informed team
      sampling; imitation warm-start from replays

## Phase 5 — LLM orchestration (2-3 weeks, overlaps Phase 4)

- [x] Session/data-entry library (pokechamp.session, persists under
      ~/.pokechamp/), built server-free so the MCP tools become thin
      wrappers:
      - TeamStore: named + versioned team builds ("what did this team
        look like two edits ago"), validated against the champions
        ruleset at entry time (species/moves/ability/item/EVs)
      - OpponentScout: per-opponent scouting book — revealed facts
        (moves/item/ability) filter the estimator's candidate space as
        hard constraints; damage/turn-order observations are stored as
        replayable records so posteriors rebuild identically across
        restarts; for_recommend() emits the estimators + revealed-moves
        dicts the recommender consumes directly
      - recommend_leads: team-preview assist ranking every (bring, lead)
        choice by win rate over shared sampled opponent scenarios (same
        common-random-numbers discipline as the recommender; accepts
        concrete sets or scouted StatEstimators per opposing slot)
- [x] FastMCP server (pokechamp.server, `pokechamp-mcp` script, stdio;
      fastmcp 3.x via the [llm] extra): 13 flat-schema tools — damage,
      save_team/get_team/list_teams, scout/observe_damage/
      opponent_summary, lead_assist, start_battle/battle_update/
      battle_field/battle_log/recommend_move. All thin wrappers over
      library code; tools stay plain callables so tests hit the functions
      directly plus one end-to-end JSON-RPC round-trip
- [x] Session state across a battle: BattleSession (session/battle.py)
      folds narrated facts (HP bars, statuses, switches, field) together
      with the scouting book into the BattleState recommend() consumes;
      opponent HP lives in the scout's estimators so damage observations
      and recommendations never disagree
- [x] Ollama integration (pokechamp.llm): Assistant chat loop against any
      OpenAI-compatible endpoint (Ollama default — runtime and model are
      config strings, so llama.cpp/vLLM/LM Studio are drop-ins), MCP tool
      schemas converted to OpenAI function definitions, tool calls
      executed against the in-process FastMCP server with errors surfaced
      back to the model as text; routing system prompt; 54-fixture
      routing eval scoring first-tool choice overall and per tool
      (`pokechamp-routing-eval --model qwen3:14b`), `pokechamp-chat`
      terminal REPL. Target hardware (16GB GPU / 32GB RAM): qwen3:14b or
      gpt-oss:20b fully on-GPU, qwen3:30b-a3b split; run the eval as a
      bake-off and prefer Q5+ quants (tool calling degrades with
      quantization faster than chat). If routing accuracy disappoints,
      escalate to llama.cpp grammar-constrained decoding, not a bigger
      model

## Phase 6 — Product (2-4 weeks) — IN PROGRESS

- [x] Terminal chat UI (pokechamp-chat): tool activity shown as it happens
      (trust requires seeing which tool produced the number), no-LLM slash
      commands (/teams /team /opponents /opponent /battle /tools), ANSI
      styling with NO_COLOR/pipe fallback, endpoint errors handled
- [x] Latency budget verified (scripts/benchmark_latency.py, worst-case
      6v6 with all opponents estimated): recommend_move 0.1s singles /
      0.4s doubles, lead_assist 2-7s, everything else <0.2s — so search
      defaults were RAISED (8 worlds singles, budget 96 doubles) and the
      ~45s timer is effectively all LLM round-trip budget; soft 15s
      regression guard in the server tests
- [x] Packaging: scripts/setup.ps1 (venv + extras + tests + optional
      ollama pull + routing eval), console scripts (pokechamp-chat /
      pokechamp-mcp / pokechamp-routing-eval), README rewritten with
      install/usage/model guidance. (A bundled Showdown server is no
      longer part of the plan — rollouts run on our own engine)
- [x] Small-model floor test (qwen3:4b, CPU-forced on the dev box — its
      GTX 960 is Maxwell, below the PTX floor of BOTH CUDA libs Ollama
      ships; failures show as crash-then-silent-CPU-fallback, so force
      OLLAMA_LLM_LIBRARY=cpu for honest numbers): 68% overall (36/53),
      but the in-battle hot path (battle_update/battle_field/battle_log/
      recommend_move) went a PERFECT 15/15. Misses clustered in (a)
      observe_damage 0/5 — the schema demanded a fabricated my_pokemon
      build dict; now accepts a species name resolved from the live
      battle/stored teams, (b) list_teams swallowing lead_assist/get_team
      queries — descriptions sharpened, (c) no-tool-call misses under the
      new 2048-token generation cap (OllamaCompleter default; unbounded
      thinking blew 10+ minutes on ambiguous asks). GPU salvage: Maxwell
      works via Ollama's experimental Vulkan backend (OLLAMA_VULKAN=1 +
      CUDA_VISIBLE_DEVICES=-1; note '-1' hides CUDA, '' is ignored) —
      85% offload, stable, 2-3x CPU speed. CLEAN single-regime run on
      Vulkan after the fixes: 72% (38/53). Fix scoreboard:
      observe_damage 0/5 -> 3/5 and get_team 1/3 -> 2/3 (species-name
      resolution + descriptions worked); damage exposed as 1/6 for the
      same fabricate-two-build-dicts reason -> now also accepts species
      names (battle/store/scout MAP-build/neutral-with-note resolution);
      lead_assist stuck at 1/4 through three runs — escalate to a
      routing-prompt few-shot or grammar-constrained decoding at the
      real bake-off; residual misses are casual phrasings ("Queue
      popped...") and multi-step asks (save-a-team-delta), the honest
      4B tax. In-battle hot path stayed 14/15-15/15 across all runs
- [ ] Model bake-off on the target machine (16GB GPU):
      pokechamp-routing-eval vs qwen3:14b / gpt-oss:20b / qwen3:30b-a3b;
      escalate to llama.cpp grammar-constrained decoding if routing
      accuracy is the bottleneck
- [ ] Ladder validation: use it in real matches; fix what annoys
- [ ] Simple web UI (optional, after the terminal flow proves itself)

## Risks

- Champions mechanics divergence -> swappable ruleset data; re-validate vs
  in-game observations if needed
- RL difficulty (simultaneous-move, imperfect info) -> 4a search baseline
  guarantees a shippable recommender
- Local LLM tool-calling reliability -> few, simple tool schemas; battle_log
  tool keeps context out of the model's head
