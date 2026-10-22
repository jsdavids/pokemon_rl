"""What do real Champions spreads look like? Grounding for spread models.

Reads the Pikalytics ladder spreads (fetch_spreads.py) and reports:

  totals     how many of the 66 Stat Points builds actually spend
  per stat   P(0 SP), P(1-4 "leftover"), P(5-31 "benchmark"), P(32 max)
             for each stat — pooled, every species weighted equally
  roles      the same per ROLE, where roles come from what a team sheet
             shows (species + nature), not from the spread itself:
             attack (nature-boosted Atk/SpA, else the higher base),
             dump (the other attacking stat), spe, hp, def, spd —
             split by nature archetype (offense / bulk / Trick Room)
  grid       how much build mass lies within L1 distance d (in SP) of the
             estimator's EV templates — i.e. how often the truth is
             representable by the candidate grid

    python scripts/analyze_spreads.py [--format battledataregmbs3]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from pokechamp.data import load_ruleset
from pokechamp.data.loader import DATA_DIR
from pokechamp.estimator.candidates import EV_TEMPLATES
from pokechamp.estimator.spreads import archetype, stat_roles as roles_for
from pokechamp.stats import ev_to_sp

STATS = ("hp", "atk", "def", "spa", "spd", "spe")
ROLES = ("attack", "spe", "hp", "def", "spd", "dump")
BUCKETS = (("0", 0, 0), ("1-4", 1, 4), ("5-31", 5, 31), ("32", 32, 32))


def bucket_row(dist: dict[int, float]) -> str:
    total = sum(dist.values()) or 1
    cells = []
    for _, lo, hi in BUCKETS:
        p = sum(w for v, w in dist.items() if lo <= v <= hi) / total
        cells.append(f"{p:6.1%}")
    return " ".join(cells)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", default="battledataregmbs3")
    args = ap.parse_args()
    rs = load_ruleset("champions")
    data = json.loads((DATA_DIR / "champions" / "usage" / f"spreads-{args.format}.json")
                      .read_text(encoding="utf-8"))["species"]

    totals = defaultdict(float)
    per_stat = {s: defaultdict(float) for s in STATS}
    per_role = defaultdict(lambda: defaultdict(float))  # (arch, role) -> dist
    arch_weight = defaultdict(float)
    templates = [tuple(ev_to_sp(t.get(s, 0)) for s in STATS) for t in EV_TEMPLATES]
    grid_hits = {d: 0.0 for d in (0, 2, 4, 8)}

    for name, entry in data.items():
        spreads = entry["spreads"]
        mass = sum(s[6] for s in spreads)
        nature = max(entry["natures"], key=entry["natures"].get)
        base = rs.get_species(name).base_stats
        roles = roles_for(base, nature)
        arch = archetype(nature)
        arch_weight[arch] += 1
        for *sp, share in spreads:
            w = share / mass  # each species weighs 1 in total
            totals[sum(sp)] += w
            for stat, v in zip(STATS, sp):
                per_stat[stat][v] += w
                per_role[(arch, roles[stat])][v] += w
            dist = min(sum(abs(a - b) for a, b in zip(sp, t)) for t in templates)
            for d in grid_hits:
                if dist <= d:
                    grid_hits[d] += w

    n = len(data)
    print(f"{n} species, {args.format} (each species weighted equally)\n")
    print("total Stat Points spent:")
    for t, w in sorted(totals.items(), key=lambda kv: -kv[1])[:5]:
        print(f"  {t:3d} SP  {w / n:6.1%}")

    head = " ".join(f"{b[0]:>6}" for b in BUCKETS)
    print(f"\nper stat           {head}")
    for s in STATS:
        print(f"  {s:15} {bucket_row(per_stat[s])}")

    for arch in ("offense", "bulk", "trick room"):
        if not arch_weight[arch]:
            continue
        print(f"\nby role, {arch} natures ({arch_weight[arch]:.0f} species) {head}")
        for role in ROLES:
            print(f"  {role:15} {bucket_row(per_role[(arch, role)])}")

    print("\nbenchmark values (5-31 SP) by role, pooled — most common:")
    for role in ROLES:
        pooled = defaultdict(float)
        for (arch, r), dist in per_role.items():
            if r == role:
                for v, w in dist.items():
                    if 5 <= v <= 31:
                        pooled[v] += w
        top = sorted(pooled.items(), key=lambda kv: -kv[1])[:6]
        print(f"  {role:8} " + ", ".join(f"{v}" for v, _ in top))

    print("\nestimator grid coverage (build mass within L1 distance of a template):")
    for d, w in grid_hits.items():
        print(f"  <= {d:2d} SP  {w / n:6.1%}")
    fold_coverage(data, rs)


def fold_coverage(data: dict, rs, ks=(8, 12, 16, 24), seed: int = 0) -> None:
    """Out-of-sample: candidates built from half the species, scored on the
    other half's real spreads (and vice versa), in stat space through each
    build's nature. The old EV templates are scored the same way."""
    import random
    from pokechamp.estimator.spreads import SpreadCandidates, SpreadModel
    names = sorted(data)
    random.Random(seed).shuffle(names)
    folds = [names[::2], names[1::2]]
    old = [tuple(ev_to_sp(t.get(s, 0)) for s in STATS) for t in EV_TEMPLATES]
    radii = (0, 2, 4, 8)
    rows = {"old templates": {d: 0.0 for d in radii}}
    for k in ks:
        rows[f"ladder K={k}"] = {d: 0.0 for d in radii}
    n = 0
    for train, test in ((folds[0], folds[1]), (folds[1], folds[0])):
        model = SpreadModel({s: data[s] for s in train})
        cands = {k: SpreadCandidates(model, k=k) for k in ks}
        for name in test:
            entry = data[name]
            mass = sum(x[6] for x in entry["spreads"])
            nature = max(entry["natures"], key=entry["natures"].get)
            n += 1
            options = {"old templates": old}
            for k in ks:
                options[f"ladder K={k}"] = [
                    tuple(ev_to_sp(e[s]) for s in STATS)
                    for e, _ in cands[k].evs_for(name, nature, rs)]
            for *sp, share in entry["spreads"]:
                for label, opts in options.items():
                    dist = min(sum(abs(a - b) for a, b in zip(sp, o)) for o in opts)
                    for d in radii:
                        if dist <= d:
                            rows[label][d] += share / mass
    print("\nout-of-sample candidate coverage (2 folds by species; build mass "
          "within L1 distance, per species nature):")
    print("  " + " " * 15 + "".join(f"{'<=' + str(d) + ' SP':>9}" for d in radii))
    for label, cov in rows.items():
        print(f"  {label:15}" + "".join(f"{cov[d] / n:9.1%}" for d in radii))


if __name__ == "__main__":
    main()
