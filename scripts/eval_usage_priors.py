"""Do usage priors actually predict opponents? Held-out check.

Splits the cached Limitless events by date (the newest --holdout fraction is
the test set), rebuilds usage from the older events only, then scores how
well each predictor recovers the held-out players' real sets:

  moves    mean overlap |predicted ∩ actual| / 4 of a 4-move fill, with no
           moves revealed — usage-weighted fill_moves vs the old random
           damaging-move fill (both what rollouts actually do)
  ability  / nature / item  mean log-probability and top-1 accuracy of the
           true value under the usage prior vs uniform over the estimator's
           candidate options (item = the candidate-grid bucket: a modeled
           damage/speed item, or None for everything else)

Needs scripts/usage_cache/ populated by fetch_usage.py (no network).

    python scripts/eval_usage_priors.py [--holdout 0.2]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_usage import CACHE_DIR, Resolver, aggregate_limitless  # noqa: E402

from pokechamp.data.loader import DATA_DIR  # noqa: E402
from pokechamp.estimator.candidates import default_candidates  # noqa: E402
from pokechamp.estimator.usage import SpeciesUsage  # noqa: E402
from pokechamp.sim.determinize import fill_moves  # noqa: E402


def load_teams(events: list[dict]) -> list[dict]:
    teams = []
    for e in events:
        path = CACHE_DIR / "limitless" / f"{e['id']}.json"
        for p in json.loads(path.read_text(encoding="utf-8")):
            if p.get("decklist"):
                teams.append({"tournament": e["id"], "placing": p.get("placing"),
                              "record": p.get("record") or {}, "mons": p["decklist"]})
    return teams


def as_usage(name: str, entry: dict, reg: str) -> SpeciesUsage:
    return SpeciesUsage(
        species=name, regulation=reg, source="limitless", samples=entry["n"],
        usage=entry["usage"], items=entry["items"], abilities=entry["abilities"],
        natures=entry["natures"], moves=entry["moves"], teammates=entry["teammates"],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regulation", default="regulation-m-c")
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    raw = json.loads((DATA_DIR / "champions" / "usage" / f"{args.regulation}.json")
                     .read_text(encoding="utf-8"))
    events = sorted(raw["sources"]["limitless"]["events"], key=lambda e: e["date"])
    cut = int(len(events) * (1 - args.holdout))
    train, test = load_teams(events[:cut]), load_teams(events[cut:])
    print(f"train: {cut} events / {len(train)} teams ({events[0]['date'][:10]}.."
          f"{events[cut - 1]['date'][:10]}); test: {len(events) - cut} events / "
          f"{len(test)} teams ({events[cut]['date'][:10]}..{events[-1]['date'][:10]})")

    res = Resolver()
    usage = {n: as_usage(n, e, args.regulation)
             for n, e in aggregate_limitless(train, res).items()}
    rng = random.Random(args.seed)
    reg = args.regulation

    m_usage = m_random = n_moves = 0
    stats = {f: {"lp_prior": 0.0, "lp_uniform": 0.0, "hit_prior": 0,
                 "hit_uniform": 0, "n": 0} for f in ("ability", "nature", "item")}
    cand_cache: dict[str, list] = {}
    for team in test:
        for mon in team["mons"]:
            name = res.species(mon.get("id", ""), mon.get("name", ""))
            u = usage.get(name)
            if u is None:
                continue
            item = res.item(mon.get("item"))
            # --- moves: a full 4-move fill with nothing revealed ---
            actual = {m for m in (res.move(a) for a in mon.get("attacks") or []) if m}
            if len(actual) == 4:
                pred_u = set(fill_moves(name, rng, weights=u.moves))
                pred_r = set(fill_moves(name, rng))
                m_usage += len(pred_u & actual) / 4
                m_random += len(pred_r & actual) / 4
                n_moves += 1
            # --- ability / nature / item bucket under the prior ---
            if name not in cand_cache:
                cand_cache[name] = default_candidates(
                    name, ruleset="champions", regulation=reg,
                    extra_natures=u.extra_natures(), extra_items=u.extra_items())
            cands = cand_cache[name]
            truth = {
                "ability": res.ability(mon.get("ability")),
                "nature": mon.get("nature"),
                "item": item if item in {c.item for c in cands} else None,
            }
            score = u.prior(cands)
            for f, value in truth.items():
                options = sorted({getattr(c, f) for c in cands}, key=str)
                if len(options) < 2 or value not in options:
                    continue
                # the prior's marginal on this feature
                mass = {}
                for c in cands:
                    mass[getattr(c, f)] = mass.get(getattr(c, f), 0.0) + score(c)
                total = sum(mass.values())
                p = mass[value] / total
                s = stats[f]
                s["lp_prior"] += math.log(p)
                s["lp_uniform"] += math.log(1 / len(options))
                s["hit_prior"] += max(mass, key=mass.get) == value
                s["hit_uniform"] += 1 / len(options)
                s["n"] += 1

    print(f"\nmoves (n={n_moves} held-out sets, 4-move fill, none revealed):")
    print(f"  usage-weighted fill   {m_usage / n_moves:6.1%} of the real moveset")
    print(f"  random damaging fill  {m_random / n_moves:6.1%}")
    for f, s in stats.items():
        n = s["n"]
        print(f"\n{f} (n={n}):")
        print(f"  prior    top-1 {s['hit_prior'] / n:6.1%}   "
              f"mean log-p {s['lp_prior'] / n:7.3f}")
        print(f"  uniform  top-1 {s['hit_uniform'] / n:6.1%}   "
              f"mean log-p {s['lp_uniform'] / n:7.3f}")


if __name__ == "__main__":
    main()
