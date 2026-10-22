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
      multi-hit / effect text, items, per-regulation legality (M-A, M-B,
      M-C). Regulation pages are discovered automatically; `--refresh`
      re-crawls when a new one drops (see README)
- [x] Regulation M-C update (2026-10): 26 newly usable Pokemon incl. the
      first Z Megas (Mega Absol Z Dark/Ghost Sharpness, Mega Garchomp Z
      Dragon Levitate, Mega Lucario Z Aura Guard) + Mega Salamence/
      Golisopod/Baxcalibur, 12 items (Rocky Helmet, Air Balloon, Red Card,
      Eject Button, Binding Band, Normal Gem, Terrain Extender, 4 Seeds,
      Leek), patch rebalances (Slash 80 BP and learned far more widely,
      Meteor Assault 170, Wish/Strength Sap 8 PP, Archaludon lost Metal
      Burst/Mirror Coat). Scraper fixes found on the way: (a) serebii mixes
      UTF-8 and raw cp1252 bytes WITHIN a page — now decoded byte-run-wise
      and cached as raw bytes; (b) forms that differ only by a 'Stats -
      <Form>' table were silently dropped — recovered 9 (Indeedee-F,
      Basculegion-F, Lycanroc Midnight/Dusk, Palafin Hero, Aegislash
      Blade, Gourgeist sizes); 339 forms total. tests/test_item_coverage.py
      now fails on any held item no module has classified — the to-do
      list for the next regulation's item pool. The new learnsets also
      surfaced a determinism bug: fill_moves iterated the learnset
      frozenset, whose order follows the per-process string hash seed, so
      a seeded recommend() varied between processes (a recommender test
      failed ~1 run in 7). Now sorted; verified identical across 20 hash
      seeds
- [ ] Full gen9 data dump (script to convert Showdown data files) — lower
      priority now that champions data exists

## Phase 1 — Damage calculator (2-3 weeks) — DONE (except multi-hit)

- [x] Core Gen 5+ formula: base damage, rolls, STAB, type chart, crit, burn
- [x] Batch API (moves x hypothetical defender builds)
- [x] Stat boost stages (incl. crit interactions)
- [x] Items: Choice Band/Specs, Life Orb, Expert Belt, Assault Vest,
      Muscle Band/Wise Glasses, type boosters; M-C: Normal Gem (1.3x),
      Air Balloon (Ground immunity + ungrounded for terrain); Light Ball
      (Pikachu 2x), Metronome (metronome_uses: +819/4096 per repeat, 2x
      cap), Iron Ball (grounds Flying/Levitate holders; a Flying holder
      takes Ground NEUTRALLY, not its other type's weakness)
- [x] Champions golden suite (2026-10, node on the target machine):
      generate_goldens.mjs gained `champions` (32 damage cases on the
      scraped ruleset — Light Ball, the Metronome chain, Iron Ball, Air
      Balloon, Normal Gem, Aerilate/Pixilate, the M-C Megas — each with a
      smogon `ref` block so data and calc divergences report separately)
      and `champions-species` (all 339 forms' stats/types/weight vs
      smogon's dex). First run found: (a) MY Iron Ball bug — I let Iron
      Ball Skarmory keep its Steel weakness to Ground; Showdown and smogon
      both make a Flying holder take Ground neutrally; (b) Rotom's
      appliance forms carried base Rotom's stats (serebii's shared 'Stats -
      Alternate Forms' table went unread) under names like 'Heat Rotom
      Rotom', Castform's as 'Sunny Form Castform'; (c) 19 regional/alt
      forms inherited the base form's weight (serebii lists them slash-
      separated: '30kg / 21kg') — Hisuian Goodra was a Grass Knot tier
      low; (d) smogon 0.11.0's own Mega Starmie is wrong (Atk 140, a +140
      Mega; serebii/champdex say 100). 6 explained exceptions remain
      (SPECIES_PARITY_EXCEPTIONS). The loader now also accepts Showdown
      spellings ('Rotom-Heat', 'Garchomp-Mega-Z', 'Indeedee-F'), and
      tool/scout/battle inputs are canonicalized so every spelling lands
      on one entry
- [x] Spread modifier, weather (incl. sand/snow stat boosts), terrain, screens
- [x] Abilities: immunities (Levitate etc.), Huge Power, Guts, Technician,
      Adaptability, Thick Fat, Fur Coat, Marvel Scale, Multiscale, Ice Scales,
      Tinted Lens, Filter/Solid Rock; -ate retyping (Aerilate/Pixilate/
      Refrigerate/Galvanize + Champions' Dragonize: type, STAB, immunities,
      terrain and gems all see the new type, 1.2x), and the Champions
      abilities Aura Guard (contact 0.5x), Fire Mane (Fire 1.5x), Mega Sol
      (own moves in sun), Eelevate (floats)
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
- [x] Usage-statistics priors and usage-derived candidate sets (2026-10).
      scripts/fetch_usage.py pulls Limitless open team sheets (no API key;
      50 req/5 min respected, finished events cached forever — 5,136 teams
      from 70 M-C events of 32+ players) as the PRIMARY source (exact
      counts, natures, per-form via the held Mega stone) and Pikalytics'
      crawler-friendly /ai pages as SECONDARY (its M-C format is built from
      the same events; point --pikalytics-format at a ladder format when
      one exists for M-C). estimator/usage.py turns shares into a softened
      prior (uniform floor 5%, more for thin samples) over item bucket x
      ability x nature, applied via apply_prior before any evidence;
      meta-run natures (Naive/Hasty/Brave...) and calc-modeled items
      (Rillaboom's Miracle Seed 72%!) join the candidate grid per species;
      rollout opponents draw moves by usage (sim-modeled moves only).
      Held-out check (scripts/eval_usage_priors.py: train on 56 older
      events, test on the 14 newest): rollout movesets recover 68.6% of
      real moves vs 6.3% for the old random fill; top-1 ability 95% vs 43%
      uniform, nature 69% vs 11%, item bucket 90% vs 16%. Wired through
      the scout (server uses the newest regulation), so opponent_summary
      also returns a 'meta' read. The usage join surfaced scraper gaps:
      slugs with punctuation were dropped (Kommo-o, Sirfetch'd,
      Farfetch'd, Mr. Mime/Rime) and Floette — in the #1 4-core as Mega
      Floette — was missing (serebii only has 'Stats - Eternal Floette');
      345 forms now. Not yet used: teammate co-occurrence (predict the
      unrevealed back line at team preview) and full-set joint
      distributions (moves conditioned on revealed moves)

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
      303 champions forms at the time; 345 now), Heavy Slam/Heat Crash ratios, Eruption/Water
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
- [x] Regulation M-C items in the engine: Air Balloon (pops on any
      damaging hit), Rocky Helmet 1/6 on contact (plus Rough Skin/Iron
      Barbs 1/8, previously unmodeled), Red Card / Eject Button (forced
      switch, random replacement — a real player picks), Normal Gem
      consumption, Binding Band 1/6 trap chip (snapshotted), Terrain
      Extender (8 turns), terrain Seeds (on set and on entry; from_state
      restores items so constructor-time entry can't eat a benched seed);
      Spicy Spray burns attackers
- [x] Full item-pool coverage (2026-10): the last ten unmodeled items —
      Scope Lens (with a real crit-stage ladder: high-crit moves, Super
      Luck, Leek, always-crit moves, Battle/Shell Armor), Quick Claw,
      Bright Powder, King's Rock, Focus Band, Shell Bell, White Herb,
      Metronome (consecutive-use streak, snapshotted), Iron Ball (half
      Speed + grounded, also in the estimator's speed math), Light Ball.
      Found missing on the way and added: Focus Sash + Sturdy (single hit
      from full HP), status-immunity abilities (Limber/Insomnia/Water
      Veil/...), Moxie/Beast Boost. Champions abilities: Piercing Drill
      (contact through Protect at 1/4), Eelevate (+1 highest stat on KO),
      Thermal Exchange (+1 Atk on Fire hits, burn immunity).
      test_item_coverage's allowlist is now EMPTY — any future item is
      unmodeled by definition until classified. Focus Energy (+2) and
      Dragon Cheer (ally +2 if Dragon, else +1) share one crit volatile:
      no stacking, cleared on switch, carried by Baton Pass, snapshotted
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
- [x] Recommender vs real tournament teams (2026-10,
      scripts/eval_recommender_meta.py): 400 random pairings of held-out
      Limitless team sheets (the 14 newest M-C events; usage rebuilt from
      the 56 older ones so no opponent's set feeds its own prior), both
      orientations, doubles at the server's search settings, vs a
      MaxDamage opponent that knows its own team. 3,200 games:
      oracle (true sets) 77.9% / usage-primed inference 63.4% /
      uniform inference (pre-usage behavior) 57.7% / MaxDamage 49.4%.
      Paired: usage - uniform = +5.8 pts [95% CI +2.1, +9.3] — the priors
      close ~29% of the 20-point information gap. The run also found two
      bugs: free-text sheets leaked a lowercase 'relaxed' into a candidate
      grid (natures now canonicalized at fetch + validated), and Dragon
      Tail into an Eject Button holder crashed the deferred switch
      (_force_switch now ignores a Pokemon that's already gone); plus
      determinize() hard-wired the opponent to side 1 (a side-1 search
      resampled its own team). Caveats: the opponent never clicks status
      moves (MaxDamage), team sheets have no EVs (nature-derived spreads),
      Megas start Mega-Evolved, and estimators get NO in-battle damage
      updates here — the next arm to add (the server does update them)
- [x] Champions Stat Points (2026-10): Champions has no EVs — 66 Stat
      Points, max 32 per stat, each exactly +1 at level 50; the HOME rule
      (first SP = 4 EVs, each further = 8) makes SP n == 8n-4 EVs exact
      (tested for every value), so builds stay stored as EVs. Fixed: the
      validator's mainline 508-EV cap rejected the legal 32/32/2 (516 EV-
      equivalents) — now 66 SP total; tools take `stat_points` (and read an
      'evs' dict that fits 0-32/66 as SP, with a note — "32 Atk" used to
      mean +4 stat), get_team reports stat_points
- [x] Ladder spread data + truth-spread sensitivity (2026-10):
      scripts/fetch_spreads.py pulls Pikalytics RANKED spreads (SP, top 30
      per species, 50 species of M-B S3 — no M-C ladder data yet);
      scripts/analyze_spreads.py: 97.3% of builds spend all 66 SP (stats
      are not independent); by role (from species + nature) offense
      natures max attack 61% / Speed 53% and leave the dump stat empty
      94%, bulk natures max HP 73% with Def/SpD benchmarks (14-15, 19-20)
      72-74%, Trick Room natures leave Speed empty 93%. The estimator's 8
      EV templates match only 0.3% of real builds exactly (its 252/252/4
      is 32/32/1 = 65 SP; real is 32/32/2) and 39% sit > 8 SP from every
      template. estimator/spreads.py samples whole spreads: `species`
      (its own ladder spreads) or `role` (a role-space spread from OTHER
      species of the same nature archetype — the data-grounded stress
      test). eval_recommender_meta.py --spreads, same 400 matchups each:
        heuristic: usage - uniform +5.8 [+2.1, +9.3]  (oracle 77.9 / usage 63.4 / uniform 57.7)
        species:   usage - uniform +5.6 [+2.1, +9.2]  (75.1 / 63.9 / 58.3)
        role:      usage - uniform +3.8 [+0.1, +7.4]  (74.4 / 62.1 / 58.2)
      The prior's gain holds under realistic spreads and shrinks (still
      positive) when truth is off-grid by construction. Next: replace the
      EV templates with ladder-grounded candidates — evaluate with `role`
      truth (leave-one-species-out), since `species` truth drawn from the
      same data the candidates come from would grade itself
- [x] Ladder spread candidates + observation-in-the-loop eval (2026-10):
      estimator/spreads.py SpreadCandidates picks K=12 role-space shapes
      per nature archetype by greedy weighted coverage (radius 4 SP),
      maps each onto the species through the candidate's own nature, and
      uses the shape's build mass as the prior (out-of-sample coverage
      within 4 SP: K=12 vs the 8 templates in analyze_spreads.py
      fold_coverage). The sim now has an opt-in event log (battle.events:
      hits with rolled/clean damage, field, boosts; move order by
      priority), and the eval feeds clean single hits + same-priority
      move order to the estimators the way a player would see them
      (their hits as rounded HP-bar %). Fixes found along the way:
      observe_damage ignored spread reduction (`spread=` now passed to
      the calc), and the server's observe_damage dropped the live
      battle's field/boosts (now taken from the BattleSession).
      400 matchups, `role` truth, candidates from the OTHER species fold:
        arm               win%   stat MAE  nature
        oracle            75.8      -        -
        usage             65.1    12.13    67.3%
        usage+obs         65.2     7.02    71.9%
        usage+ladder      67.1     9.15    67.2%
        usage+ladder+obs  68.6     5.32    76.3%
        maxdamage         51.2      -        -
      Paired: usage+ladder+obs − usage+obs MAE −1.69 [−1.84, −1.54],
      win +3.4 pts [+0.0, +6.5]; it closes ~33% of the oracle gap (vs
      ~1% for observations alone). The server's scout now uses ladder
      candidates by default. Observations cut MAE ~40% but barely move
      win rate on their own — most clean hits land after the decision
      they'd have informed
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
      back to the model as text; routing system prompt; 53-fixture
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
      lead_assist stuck at 1/4 through three runs; residual misses are
      casual phrasings ("Queue popped...") and multi-step asks
      (save-a-team-delta), the honest 4B tax. In-battle hot path stayed
      14/15-15/15 across all runs. Run 4 (damage fix + routing-prompt
      few-shot): 72% again but composition shifted — damage 1/6 -> 4/6
      (species-name fix validated; also exposed + fixed a missing
      weather/terrain param on the damage tool), while the few-shot was
      MEASURED NET NEGATIVE: lead_assist fell to 0/4 (the list_teams
      anchor survived a literal "NEVER list_teams" example) and the
      longer prompt caused diffuse no-call stalls elsewhere (-3). Few-
      shot reverted with a NOTE in assistant.py; at 4B, routing fixes
      belong in schemas/descriptions, or grammar-constrained decoding.
      Expected clean-config accuracy ~75-77%; definitive numbers on the
      16GB machine
- [x] Model bake-off on the target machine (RTX 5080 16GB, Ollama
      0.31.2, 16k ctx, seeded runs, results in routing_results.jsonl):
      qwen3:30b-a3b 79% (71% GPU, split) = qwen3:14b-q8_0 79% (75% GPU)
      > qwen3:14b Q4 77% (100% GPU, 7.4s warmup — best operational
      profile; the q8-vs-q4 "quant tax" measured ONE fixture, inside
      noise) >> gpt-oss:20b 64% (100% GPU; 15 of its 19 misses were
      no-tool-call — it KNOWS the tool per its reasoning trace but
      declines and asks a clarifying question when arguments feel
      unconfirmed; a personality mismatch with a move timer, not a
      capability gap). DECISION: deploy qwen3:14b Q4. The remaining
      headroom is not model choice: lead_assist went 0/16 ACROSS ALL
      FOUR models (they ask "which team?" or call list_teams — the
      required team_name argument needs an omit-to-default), "Add a
      Hydreigon to PlayerX's known team" hit save_team 4/4 (scout/
      save_team descriptions need own-team-vs-opponent contrast), and
      casual start_battle phrasings no-call 3-4/4. Fix those schemas
      (per the 4b lesson: schemas, not prompts), re-run, and any of
      these models should approach the ~90% fixture-ambiguity ceiling
      before reaching for grammar-constrained decoding.
- [x] Post-bake-off schema iteration (2026-07, same seed re-runs):
      lead_assist and start_battle arguments became optional —
      team_name defaults to the only/latest-saved team, opponent to the
      live battle's or only-scouted one, with "call it right away
      rather than asking" in the description; scout/save_team got
      own-team-vs-opponent contrast; get_team names "previous
      version/iteration" explicitly. Result: qwen3:14b Q4 77%->91%,
      qwen3:30b-a3b 79%->91% — both AT the ambiguity ceiling, residual
      misses are the genuinely-dual fixtures (switch-in = battle_update
      or scout). gpt-oss:20b 64%->74%: the fixes help but its
      clarify-first hedging survives schema-level nudges
      (recommend_move still no-calls). DEPLOY CONFIRMED: qwen3:14b Q4 —
      ties the 30b at a third the footprint, 100% GPU, 7s load.
      Grammar-constrained decoding not needed at 14B+. Harness prep DONE (2026-07):
      per-model-family sampling presets (qwen3 0.6/0.95, gpt-oss 1.0 —
      hardcoded 0.2 was against every candidate's guidance and near-greedy
      decoding is the documented cause of qwen3 thinking spirals) with
      --temperature/--top-p/--seed/--max-tokens overrides; eval max_tokens
      default raised to 4096 so the cap doesn't score as routing misses;
      preflight before fixture 1 (warmup absorbs the cold load, /api/ps
      confirms 100% GPU vs silent split, warns if context < 8192);
      setup.ps1 persists OLLAMA_CONTEXT_LENGTH=16384 (the 4096 default
      silently front-truncates system prompt + tool schemas) and prints
      the ollama version (Blackwell needs a recent build); --json appends
      full run records incl. misses to routing_results.jsonl for
      cross-model comparison. Seeded runs or 3 runs per model — at 53
      fixtures one flip is ±1.9%
- [x] Newer-model bake-off (2026-10, Ollama 0.35.1, seed 7, 53 fixtures):
      qwen3.8:27b-q4_K_M 94% (50/53) — most accurate router yet, but 18GB
      runs ~75% GPU on 16GB, ~14s/fixture. qwen3:14b re-baselined against
      the current tool schemas: 92% (49/53; latency invalid, a stray server
      held VRAM). Bonsai 2 27B (ternary Qwen3.8, PQ2_0 7.2GB, PrismML
      llama.cpp fork only — Ollama rejects the quant type) 77% thinking-on
      / 83% thinking-off, fully on-GPU at ~3-6s/fixture; most misses are
      list_teams-first anchoring (lead_assist 0/4, start_battle 1/3 with
      thinking off). Sampling presets added for qwen3.8/bonsai (1.0/0.95,
      both model cards); first-match order keeps qwen3.8 off qwen3's 0.6.
      Open: whether Bonsai's list_teams detour still reaches the right
      tool in a multi-step chat (the eval scores only the first call), and
      schema nudges for it if its speed is wanted
- [ ] Ladder validation: use it in real matches; fix what annoys
- [ ] Simple web UI (optional, after the terminal flow proves itself)

## Risks

- Champions mechanics divergence -> swappable ruleset data; re-validate vs
  in-game observations if needed
- RL difficulty (simultaneous-move, imperfect info) -> 4a search baseline
  guarantees a shippable recommender
- Local LLM tool-calling reliability -> few, simple tool schemas; battle_log
  tool keeps context out of the model's head
