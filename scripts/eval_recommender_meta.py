"""The recommender vs real tournament teams, in simulation.

Held-out Limitless team sheets (the newest events; usage priors are rebuilt
from the OLDER events only, so no opponent's set leaks into its own prior)
are paired into doubles matchups. Every matchup is played by each arm with
the same teams, bring/leads and battle seed, both orientations (each team
gets a turn as "ours"), against a MaxDamage opponent that knows its team.

Arms (--arms, comma list):
  oracle       recommender with perfect information (sees true sets)
  maxdamage    greedy baseline, no search
  <inference>  the recommender inferring the opponent from species (team
               preview) + every move it clicks, as '+'-joined features:
                 usage    usage priors + usage-weighted moveset fill
                          (without it: uniform estimators, random fill)
                 ladder   ladder-grounded spread candidates + spread prior
                          (estimator/spreads.py) instead of the 8 EV templates
                 obs      the estimator updates from what a player sees
                          during the game: damage both ways (HP bar to the
                          nearest %, exact for our own side), crits, spread,
                          field, boosts, and same-priority move order
               e.g. usage, usage+obs, usage+ladder+obs, obs

Team sheets carry no spreads, so true spreads come from --spreads:
  heuristic  from the nature (+Atk/SpA: 252 there + 252 Spe if base Spe >=
             80 else HP; +Spe: 252 attack + Spe; +Def/SpD: 252 HP + that)
  species    the species' own Pikalytics LADDER spreads, sampled by share;
             species without ladder data fall back to `role`
  role       a real spread in role space from OTHER ladder species of the
             same nature archetype. The ladder species are split into two
             folds; each opponent Pokemon draws its truth from one fold and
             its `ladder` candidates come from the OTHER fold, so the
             candidates never contain the spread they're graded against.

Besides win rate, inference arms report estimator accuracy at game end, per
opponent Pokemon: mean absolute error (stat points) of the posterior-mean
stats vs the true stats, and top-1 nature accuracy.

The sim has no mid-battle Mega Evolution: each side's first brought stone
holder starts in its Mega form. Search settings match the server's doubles
defaults (depth 1, 24 candidates, budget 96).

    python scripts/eval_recommender_meta.py --matchups 400 --spreads role \\
        --arms oracle,usage,usage+obs,usage+ladder+obs
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_usage_priors import as_usage, load_teams  # noqa: E402
from fetch_usage import Resolver, aggregate_limitless  # noqa: E402

from pokechamp.data import load_ruleset  # noqa: E402
from pokechamp.data.loader import DATA_DIR  # noqa: E402
from pokechamp.estimator import InconsistentObservation, StatEstimator  # noqa: E402
from pokechamp.estimator.candidates import default_candidates  # noqa: E402
from pokechamp.estimator.spreads import (  # noqa: E402
    SpreadCandidates, SpreadModel, load_spread_model,
)
from pokechamp.recommender.search import SearchAgent, recommend  # noqa: E402
from pokechamp.schemas import STAT_KEYS, PokemonSet, stat_table  # noqa: E402
from pokechamp.sim.agents import Agent, MaxDamageAgent  # noqa: E402
from pokechamp.sim.battle import Action, Battle  # noqa: E402
from pokechamp.stats import NATURES  # noqa: E402

DEFAULT_ARMS = "oracle,usage,usage+obs,usage+ladder+obs,maxdamage"
SEARCH = {"depth": 1, "max_candidates": 24, "budget": 96}  # server doubles
REG = "regulation-m-c"


# ------------------------------------------------------- team-sheet -> sets ---

def sheet_to_team(mons: list[dict], res: Resolver) -> list[PokemonSet] | None:
    """A Limitless team sheet as concrete sets, or None if any Pokemon
    can't be resolved. The first stone holder is converted to its Mega."""
    rs = res.rs
    team, mega_used = [], False
    for mon in mons:
        base = res.species(mon.get("id", ""), mon.get("name", ""))
        if base is None:
            return None
        item = res.item(mon.get("item"))
        mega = res.mega_for(base, item)
        species = mega if (mega and not mega_used) else base
        mega_used |= species != base
        sp = rs.get_species(species)
        moves = list(dict.fromkeys(
            m for m in (res.move(a) for a in mon.get("attacks") or []) if m
        ))[:4]
        if not moves:
            return None
        ability = res.ability(mon.get("ability"))
        if ability not in sp.abilities:
            ability = sp.abilities[0] if sp.abilities else None
        nature = res.nature(mon.get("nature")) or "Serious"
        team.append(PokemonSet(
            species=species, moves=moves, nature=nature,
            evs=spread_for(sp, nature, moves, rs),
            item=item if item in rs.items else None, ability=ability,
        ))
    return team


def spread_for(sp, nature: str, moves: list[str], rs) -> dict:
    plus, _ = NATURES[nature]
    phys = sum(rs.get_move(m).category == "Physical" for m in moves)
    spec = sum(rs.get_move(m).category == "Special" for m in moves)
    off = "atk" if phys > spec or (phys == spec and
                                   sp.base_stats["atk"] >= sp.base_stats["spa"]) else "spa"
    if plus in ("atk", "spa"):
        evs = {plus: 252, ("spe" if sp.base_stats["spe"] >= 80 else "hp"): 252}
    elif plus == "spe":
        evs = {off: 252, "spe": 252}
    elif plus in ("def", "spd"):
        evs = {"hp": 252, plus: 252}
    else:
        evs = {off: 252, "hp": 252}
    if "hp" not in evs:
        evs["hp"] = 4  # the leftover 4
    return {**stat_table(), **evs}


# ------------------------------------------------------------- the agents ---

class InferenceSearchAgent(Agent):
    """The recommender under imperfect information: estimators built from
    species alone (team preview), optionally usage-primed, optionally on
    ladder spread candidates, optionally updated from in-game observations;
    revealed moves accumulate as the opponent clicks them."""

    def __init__(self, features: set[str], usage: dict, seed: int,
                 spreads: list[SpreadCandidates | None] | None = None):
        self.features = features
        self.usage = usage if "usage" in features else None
        self.rng = random.Random(seed)
        self.spreads = spreads  # per opponent team index (fold-aware)
        self.estimators: dict[int, StatEstimator] = {}
        self.revealed: dict[int, list[str]] = {}
        self.stats = {"observed": 0, "inconsistent": 0, "skipped": 0}
        self._speeds: dict = {}

    def start(self, battle: Battle, side: int) -> None:
        rs = battle.rs
        opp = battle.sides[1 - side]
        for i, mon in enumerate(opp.team):
            species = mon.set.species
            u = self.usage.get(species) if self.usage else None
            spreads = (self.spreads[i] if self.spreads and "ladder" in self.features
                       else None)
            cands = default_candidates(
                species, ruleset="champions", regulation=REG,
                extra_natures=u.extra_natures() if u else (),
                extra_items=u.extra_items() if u else (),
                spreads=spreads,
            )
            est = StatEstimator(species, candidates=cands)
            if u is not None:
                est.usage = u
                est.apply_prior(u.prior(est.candidates), u.description)
            if spreads is not None:
                est.apply_prior(spreads.prior(rs), "ladder spread prior")
            self.estimators[i] = est
        if "obs" in self.features:
            battle.events = []

    # --- what a player sees, turned into estimator evidence ---

    def before_step(self, battle: Battle, side: int) -> None:
        """Speeds and conditions at the start of the turn (for move order)."""
        if "obs" not in self.features:
            return
        opp = 1 - side
        mine = {}
        for slot in battle.sides[side].alive_slots():
            mine[battle.sides[side].slots[slot]] = battle.effective_speed(side, slot)
        theirs = {}
        for slot in battle.sides[opp].alive_slots():
            mon = battle.sides[opp].active(slot)
            theirs[battle.sides[opp].slots[slot]] = {
                "boost": mon.boosts["spe"], "par": mon.status == "par",
                "tailwind": battle.sides[opp].conditions.tailwind,
            }
        self._speeds = {"mine": mine, "theirs": theirs,
                        "trick_room": battle.field.trick_room}

    def _try(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
            self.stats["observed"] += 1
        except InconsistentObservation:
            self.stats["inconsistent"] += 1  # unmodeled effect; evidence dropped

    def after_step(self, battle: Battle, side: int) -> None:
        if "obs" not in self.features or battle.events is None:
            return
        events, battle.events = battle.events, []
        opp = 1 - side
        for e in events:
            if e["kind"] != "hit":
                continue
            (a_side, a_idx), (d_side, d_idx) = e["attacker"], e["defender"]
            if not e["clean"] or e["hits"] > 1 or a_side == d_side:
                self.stats["skipped"] += 1
                continue
            common = dict(field=e["field"], is_crit=e["crit"], spread=e["spread"],
                          attacker_boosts=e["attacker_boosts"],
                          defender_boosts=e["defender_boosts"])
            if a_side == side:  # we hit them: their bar, to the nearest %
                est = self.estimators[d_idx]
                attacker = replace(battle.sides[side].team[a_idx].set,
                                   status=e["attacker_status"])
                before = round(100 * e["hp_before"] / e["max_hp"])
                after = round(100 * (e["hp_before"] - e["damage"]) / e["max_hp"])
                est.set_hp(before)
                if e["ko"]:
                    self._try(est.observe_damage_taken, attacker, e["move"],
                              ko=True, tolerance=1.0, **common)
                else:
                    self._try(est.observe_damage_taken, attacker, e["move"],
                              percent=before - after, tolerance=1.5, **common)
            else:  # they hit us: our own HP is exact
                if e["attacker_status"] == "brn":
                    self.stats["skipped"] += 1  # candidates carry no status
                    continue
                est = self.estimators[a_idx]
                target = battle.sides[side].team[d_idx].set
                if e["ko"]:
                    self._try(est.observe_damage_dealt, e["move"], target,
                              100 * e["hp_before"] / e["max_hp"], ko=True,
                              tolerance=0.5, **common)
                else:
                    self._try(est.observe_damage_dealt, e["move"], target,
                              100 * e["damage"] / e["max_hp"], tolerance=0.5,
                              **common)
        # turn order within a priority bracket -> speed evidence
        order = [e for e in events if e["kind"] == "move"]
        for i, mine in enumerate(order):
            if mine["side"] != side or mine["mon"] not in self._speeds.get("mine", {}):
                continue
            for j, theirs in enumerate(order):
                if (theirs["side"] != opp or theirs["priority"] != mine["priority"]
                        or theirs["mon"] not in self._speeds["theirs"]):
                    continue
                cond = self._speeds["theirs"][theirs["mon"]]
                self._try(
                    self.estimators[theirs["mon"]].observe_speed,
                    opponent_moved_first=j < i,
                    reference_speed=self._speeds["mine"][mine["mon"]],
                    trick_room=self._speeds["trick_room"],
                    opponent_tailwind=cond["tailwind"],
                    opponent_paralyzed=cond["par"],
                    opponent_boost=cond["boost"],
                )

    def observe(self, battle: Battle, opp_side: int, actions: list[Action]) -> None:
        side = battle.sides[opp_side]
        for slot, act in enumerate(actions):
            mon_idx = side.slots[slot] if slot < len(side.slots) else None
            if act.kind != "move" or mon_idx is None:
                continue
            mon = side.team[mon_idx]
            if act.index < len(mon.set.moves):
                seen = self.revealed.setdefault(mon_idx, [])
                name = mon.set.moves[act.index]
                if name not in seen:
                    seen.append(name)

    def choose(self, battle: Battle, side: int) -> list[Action]:
        rec = recommend(
            battle.snapshot(), estimators=self.estimators,
            revealed_moves=self.revealed, side=side,
            seed=self.rng.randrange(1 << 30), **SEARCH,
        )
        return list(rec.best)

    def accuracy(self, battle: Battle, side: int) -> dict:
        """Posterior vs truth for every opponent Pokemon: stat MAE (posterior
        mean, in stat points) and top-1 nature."""
        maes, natures = [], []
        for idx, est in self.estimators.items():
            truth = battle.sides[1 - side].team[idx]
            summary = est.stat_summary()
            maes.append(sum(abs(summary[s]["mean"] - truth.stats[s])
                            for s in STAT_KEYS) / len(STAT_KEYS))
            nat = est.marginal(lambda c: c.nature)
            natures.append(max(nat, key=nat.get) == truth.set.nature)
        return {"stat_mae": sum(maes) / len(maes),
                "nature_acc": sum(natures) / len(natures), **self.stats}


def make_agent(arm: str, usage: dict, seed: int, spreads) -> Agent:
    if arm == "oracle":
        return SearchAgent(seed=seed, **SEARCH)
    if arm == "maxdamage":
        return MaxDamageAgent()
    return InferenceSearchAgent(set(arm.split("+")), usage, seed, spreads)


def play(arm: str, usage: dict, ours: list, theirs: list, seed: int,
         spreads) -> dict:
    agent, opp = make_agent(arm, usage, seed, spreads), MaxDamageAgent()
    b = Battle(ours, theirs, seed=seed, format="doubles")
    inference = isinstance(agent, InferenceSearchAgent)
    if inference:
        agent.start(b, 0)
    while not b.is_over:
        a0, a1 = agent.choose(b, 0), opp.choose(b, 1)
        if inference:
            agent.observe(b, 1, a1)
            agent.before_step(b, 0)
        b.step(a0, a1)
        if inference:
            agent.after_step(b, 0)
    w = b.winner()
    out = {"win": None if w is None else w == 0, "turns": b.turn}
    if inference:
        out.update(agent.accuracy(b, 0))
    return out


# --------------------------------------------------------------- workers ---

_USAGE: dict = {}
_FOLD_SPREADS: list[SpreadCandidates | None] = [None, None]


def _init(usage: dict, folds: list[list[str]] | None) -> None:
    """Per worker: usage, and ladder candidates built from each fold."""
    global _USAGE, _FOLD_SPREADS
    _USAGE = usage
    model = load_spread_model()
    if model is None:
        _FOLD_SPREADS = [None, None]
    elif folds is None:  # no fold split: candidates from all ladder data
        full = SpreadCandidates(model)
        _FOLD_SPREADS = [full, full]
    else:
        _FOLD_SPREADS = [
            SpreadCandidates(SpreadModel({s: model.species[s] for s in fold}))
            for fold in folds
        ]


def _job(job: dict) -> dict:
    t0 = time.perf_counter()
    # an opponent Pokemon whose truth came from fold f gets candidates
    # built from the OTHER fold
    spreads = [_FOLD_SPREADS[1 - f] for f in job["theirs_folds"]]
    try:
        out = play(job["arm"], _USAGE, job["ours"], job["theirs"], job["seed"],
                   spreads)
    except Exception as e:  # surface WHICH game broke, with the worker's trace
        import traceback
        sets = [f"{p.species} @ {p.item} [{p.ability}] {p.moves}"
                for p in job["ours"] + job["theirs"]]
        header = (f"{job['arm']} matchup {job['matchup']}/{job['orient']} "
                  f"seed {job['seed']}")
        raise RuntimeError(
            "\n".join([header, *sets, traceback.format_exc()])
        ) from e
    return {**{k: job[k] for k in ("matchup", "orient", "arm", "seed")},
            **out, "secs": round(time.perf_counter() - t0, 2)}


# ------------------------------------------------------------------ main ---

def paired_diff(results: list[dict], a: str, b: str, rng: random.Random,
                key=None):
    """Mean difference a - b over shared (matchup, orientation) trials, with
    a 95% bootstrap CI over matchups. Default metric: win (draw = half)."""
    key = key or (lambda r: 0.5 if r["win"] is None else float(r["win"]))
    by = {}
    for r in results:
        by.setdefault((r["matchup"], r["orient"]), {})[r["arm"]] = key(r)
    pairs: dict[int, list[float]] = {}
    for (m, _), arms in by.items():
        if a in arms and b in arms:
            pairs.setdefault(m, []).append(arms[a] - arms[b])
    keys = list(pairs)
    if not keys:
        return None
    flat = [d for k in keys for d in pairs[k]]
    mean = sum(flat) / len(flat)
    boots = []
    for _ in range(2000):
        sample = [d for k in rng.choices(keys, k=len(keys)) for d in pairs[k]]
        boots.append(sum(sample) / len(sample))
    boots.sort()
    return mean, boots[50], boots[1949], len(flat)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matchups", type=int, default=100)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--arms", default=DEFAULT_ARMS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None, help="JSON-lines results file")
    ap.add_argument("--spreads", choices=("heuristic", "species", "role"),
                    default="role", help="how true sets get spreads")
    args = ap.parse_args()
    arms = args.arms.split(",")

    raw = json.loads((DATA_DIR / "champions" / "usage" / f"{REG}.json")
                     .read_text(encoding="utf-8"))
    events = sorted(raw["sources"]["limitless"]["events"], key=lambda e: e["date"])
    cut = int(len(events) * (1 - args.holdout))
    res = Resolver()
    usage = {n: as_usage(n, e, REG)
             for n, e in aggregate_limitless(load_teams(events[:cut]), res).items()}
    pool = [t for t in (sheet_to_team(x["mons"], res) for x in load_teams(events[cut:]))
            if t and len(t) == 6]
    print(f"usage from {cut} older events; {len(pool)} usable held-out teams "
          f"from {len(events) - cut} newest events")

    # truth spreads; `role` draws each Pokemon's from one ladder fold and
    # tags it so its ladder candidates come from the other fold
    folds_of = [[0] * 6 for _ in pool]
    fold_names = None
    if args.spreads != "heuristic":
        model = load_spread_model()
        if model is None:
            sys.exit("no ladder spreads: run scripts/fetch_spreads.py")
        srng = random.Random(args.seed + 1)
        names = sorted(model.species)
        random.Random(args.seed + 2).shuffle(names)
        fold_names = [names[::2], names[1::2]]
        fold_models = [SpreadModel({s: model.species[s] for s in f})
                       for f in fold_names]
        how = {"species": 0, "role": 0}
        for t, team in enumerate(pool):
            new_team = []
            for m, p in enumerate(team):
                if args.spreads == "role":
                    f = srng.randrange(2)
                    folds_of[t][m] = f
                    evs, source = fold_models[f].sample_evs(
                        p.species, p.nature, srng, "role")
                else:
                    evs, source = model.sample_evs(p.species, p.nature, srng,
                                                   args.spreads)
                how[source] += 1
                new_team.append(replace(p, evs=evs))
            pool[t] = new_team
        print(f"spreads: {args.spreads} — {how['species']} from the species' "
              f"own ladder data, {how['role']} from the role model")
        if args.spreads == "species" and any("ladder" in a for a in arms):
            print("  ! `species` truth and `ladder` candidates share the ladder "
                  "data: optimistic for ladder arms (use --spreads role)")
        if args.spreads != "role":
            fold_names = None

    rng = random.Random(args.seed)
    jobs = []
    for m in range(args.matchups):
        ia, ib = rng.sample(range(len(pool)), 2)
        sa, sb = rng.sample(range(6), 4), rng.sample(range(6), 4)  # first 2 lead
        bring_a = ([pool[ia][i] for i in sa], [folds_of[ia][i] for i in sa])
        bring_b = ([pool[ib][i] for i in sb], [folds_of[ib][i] for i in sb])
        for orient, (ours, theirs) in enumerate(((bring_a, bring_b),
                                                  (bring_b, bring_a))):
            seed = rng.randrange(1 << 30)
            for arm in arms:
                jobs.append({"matchup": m, "orient": orient, "arm": arm,
                             "ours": ours[0], "theirs": theirs[0],
                             "theirs_folds": theirs[1], "seed": seed})

    out_path = Path(args.out) if args.out else None
    results, t0 = [], time.perf_counter()
    with ProcessPoolExecutor(args.workers, initializer=_init,
                             initargs=(usage, fold_names)) as ex:
        for i, r in enumerate(ex.map(_job, jobs, chunksize=1), 1):
            results.append(r)
            if out_path:
                with out_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(r) + "\n")
            if i % 100 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} games  ({time.perf_counter() - t0:.0f}s)",
                      flush=True)

    print(f"\n{len(results)} games, {args.matchups} matchups x 2 orientations, "
          f"vs MaxDamage (knows its own team); truth spreads: {args.spreads}\n")
    print(f"{'arm':20} {'win%':>6} {'W-L-D':>12} {'stat MAE':>9} {'nature':>7} "
          f"{'obs/game':>9} {'drop%':>6} {'s/game':>7}")
    for arm in arms:
        rs_ = [r for r in results if r["arm"] == arm]
        w = sum(r["win"] is True for r in rs_)
        l = sum(r["win"] is False for r in rs_)
        d = len(rs_) - w - l
        line = (f"{arm:20} {100 * (w + d / 2) / len(rs_):5.1f}% "
                f"{f'{w}-{l}-{d}':>12}")
        if "stat_mae" in rs_[0]:
            obs = sum(r["observed"] for r in rs_)
            bad = sum(r["inconsistent"] for r in rs_)
            line += (f" {sum(r['stat_mae'] for r in rs_) / len(rs_):9.2f}"
                     f" {sum(r['nature_acc'] for r in rs_) / len(rs_):6.1%}"
                     f" {obs / len(rs_):9.1f}"
                     f" {100 * bad / max(1, obs + bad):5.1f}%")
        else:
            line += f" {'-':>9} {'-':>7} {'-':>9} {'-':>6}"
        line += f" {sum(r['secs'] for r in rs_) / len(rs_):7.2f}"
        print(line)

    brng = random.Random(1)
    print("\npaired win-rate differences (95% bootstrap CI over matchups):")
    for i, a in enumerate(arms):
        for b in arms[i + 1:]:
            if "maxdamage" in (a, b) and "oracle" in (a, b):
                continue
            r = paired_diff(results, a, b, brng)
            if r:
                mean, lo, hi, n = r
                print(f"  {a:>18} - {b:<18} {100 * mean:+6.1f} pts  "
                      f"[{100 * lo:+.1f}, {100 * hi:+.1f}]")
    inf = [a for a in arms if a not in ("oracle", "maxdamage")]
    if len(inf) > 1:
        print("\npaired stat-MAE differences (negative = more accurate):")
        for i, a in enumerate(inf):
            for b in inf[i + 1:]:
                mean, lo, hi, n = paired_diff(
                    [r for r in results if "stat_mae" in r], a, b, brng,
                    key=lambda r: r["stat_mae"])
                print(f"  {a:>18} - {b:<18} {mean:+6.2f}  [{lo:+.2f}, {hi:+.2f}]")


if __name__ == "__main__":
    main()
