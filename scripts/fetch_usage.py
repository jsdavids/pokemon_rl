"""Fetch Pokemon Champions usage data for the estimator's priors.

Sources:
  Limitless  play.limitlesstcg.com/api — no key needed. Completed tournaments
             of one format (M-C) above a size threshold; every player's open
             team list carries species, item, ability, NATURE and all four
             moves, plus placing/record. Rate limit is 50 requests / 5 min,
             so requests are spaced 6.5 s apart. Standings of finished
             tournaments never change and are cached forever.
  Pikalytics /ai/pokedex/<format>[/<species>] (Markdown, crawler-friendly
             per robots.txt / llms.txt). Per-species usage, move/item/ability
             shares, teammates. No natures. Its tournament formats are built
             from the same Limitless events, so it is the SECONDARY source;
             its ranked-ladder formats (e.g. battledataregmbs3) are a
             different population — point --pikalytics-format at one when
             Pikalytics publishes ladder data for the current regulation.

Output: src/pokechamp/data/rulesets/champions/usage/<regulation>.json, keyed
by OUR species names (Mega forms resolved from the held stone: Salamence @
Salamencite -> Mega Salamence). Raw responses are cached under
scripts/usage_cache/ (gitignored).

Usage:
    python scripts/fetch_usage.py                         # current regulation
    python scripts/fetch_usage.py --days 14 --min-players 64
    python scripts/fetch_usage.py --no-pikalytics
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pokechamp.data import load_ruleset
from pokechamp.data.loader import norm
from pokechamp.stats import NATURES

SCRIPTS_DIR = Path(__file__).parent
CACHE_DIR = SCRIPTS_DIR / "usage_cache"
OUT_DIR = SCRIPTS_DIR.parent / "src" / "pokechamp" / "data" / "rulesets" / "champions" / "usage"
UA = "pokechamp-usage/0.1 (personal project; cached fetch)"

LIMITLESS = "https://play.limitlesstcg.com/api"
LIMITLESS_DELAY_S = 6.5  # 50 requests / 5 min
PIKALYTICS = "https://www.pikalytics.com"
PIKALYTICS_DELAY_S = 1.0

TOP_MOVES, TOP_ITEMS, TOP_TEAMMATES, TOP_SETS = 24, 16, 12, 5

_last: dict[str, float] = {}


def _get(url: str, delay: float, cache: Path | None) -> str:
    """GET with a per-host delay and an optional permanent cache file."""
    if cache is not None and cache.exists():
        return cache.read_text(encoding="utf-8")
    host = urllib.parse.urlsplit(url).netloc
    wait = _last.get(host, 0.0) + delay - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    finally:
        _last[host] = time.monotonic()
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return text


# -------------------------------------------------------------- resolving ---

class Resolver:
    """Source names -> our ruleset's canonical names (None if unknown)."""

    def __init__(self, ruleset: str = "champions"):
        self.rs = load_ruleset(ruleset)
        self.unknown: Counter = Counter()
        # team sheets are free text: 'Rough skin', ' Intimidate' -> canonical
        self.abilities = {
            norm(a): a for sp in self.rs.all_species() for a in sp.abilities
        }
        # base species -> its Mega forms
        self.megas: dict[str, list[str]] = defaultdict(list)
        for sp in self.rs.all_species():
            if sp.base_species and sp.name.startswith("Mega "):
                self.megas[sp.base_species].append(sp.name)

    def species(self, *names: str) -> str | None:
        for name in names:
            if not name:
                continue
            try:
                return self.rs.get_species(name).name
            except KeyError:
                continue
        self.unknown[("species", names[0])] += 1
        return None

    def ability(self, name: str | None) -> str | None:
        if not name:
            return None
        return self.abilities.get(norm(name), name.strip())

    def nature(self, name: str | None) -> str | None:
        """'relaxed ' -> 'Relaxed'; anything not a nature -> None."""
        if not name:
            return None
        canon = name.strip().capitalize()
        if canon in NATURES:
            return canon
        self.unknown[("nature", name)] += 1
        return None

    def move(self, name: str) -> str | None:
        try:
            return self.rs.get_move(name).name
        except KeyError:
            self.unknown[("move", name)] += 1
            return None

    def item(self, name: str | None) -> str | None:
        if not name:
            return None
        if name in self.rs.items:
            return name
        for known in self.rs.items:
            if norm(known) == norm(name):
                return known
        self.unknown[("item", name)] += 1
        return name  # keep it: usage may legitimately list unscraped items

    def mega_for(self, base: str, item: str | None) -> str | None:
        """The Mega form a held stone turns `base` into (suffix letter of
        the stone picks X / Y / Z when the base has several)."""
        if not item or not self.rs.items.get(item, {}).get("mega_stone"):
            return None
        forms = self.megas.get(base, [])
        m = re.search(r" ([XYZ])$", item)
        for form in forms:
            if m and form.endswith(" " + m[1]):
                return form
            if not m and not re.search(r" [XYZ]$", form):
                return form
        return None


# -------------------------------------------------------------- Limitless ---

def limitless_tournaments(fmt: str, since: datetime, until: datetime,
                          min_players: int) -> list[dict]:
    out, page = [], 1
    while True:
        url = (f"{LIMITLESS}/tournaments?game=VGC&format={urllib.parse.quote(fmt)}"
               f"&limit=100&page={page}")
        rows = json.loads(_get(url, LIMITLESS_DELAY_S, None))
        if not rows:
            break
        out += rows
        page += 1
        if min(datetime.fromisoformat(r["date"].replace("Z", "+00:00")) for r in rows) < since:
            break
    keep = []
    for r in out:
        when = datetime.fromisoformat(r["date"].replace("Z", "+00:00"))
        if since <= when <= until and r["players"] >= min_players:
            keep.append(r)
    return sorted(keep, key=lambda r: r["date"])


def limitless_teams(tournaments: list[dict]) -> list[dict]:
    """Every submitted team: {"tournament", "placing", "record", "mons"}."""
    teams = []
    for n, t in enumerate(tournaments, 1):
        cache = CACHE_DIR / "limitless" / f"{t['id']}.json"
        fresh = not cache.exists()
        standings = json.loads(_get(
            f"{LIMITLESS}/tournaments/{t['id']}/standings",
            LIMITLESS_DELAY_S, cache,
        ))
        for p in standings:
            if p.get("decklist"):
                teams.append({
                    "tournament": t["id"],
                    "placing": p.get("placing"),
                    "record": p.get("record") or {},
                    "mons": p["decklist"],
                })
        print(f"  [{n}/{len(tournaments)}] {t['name'][:50]} "
              f"({t['players']}p){' (cached)' if not fresh else ''}")
    return teams


def aggregate_limitless(teams: list[dict], res: Resolver) -> dict:
    """Per-species counts. A stone holder counts toward BOTH its base form
    (that's what you see at team preview / before it Mega Evolves) and its
    Mega form (what it fights as afterwards)."""
    agg: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "items": Counter(), "abilities": Counter(), "natures": Counter(),
        "moves": Counter(), "teammates": Counter(), "sets": Counter(),
        "wins": 0, "games": 0,
    })
    for team in teams:
        names = []
        for mon in team["mons"]:
            # Limitless ids are Showdown-style ('indeedee-f'); names are
            # display strings ('Indeedee ♀')
            base = res.species(mon.get("id", ""), mon.get("name", ""))
            if base is None:
                continue
            item = res.item(mon.get("item"))
            ability = res.ability(mon.get("ability"))
            nature = res.nature(mon.get("nature"))
            mega = res.mega_for(base, item)
            names.append(base)
            # dict.fromkeys: dedupe (a sheet listing Protect twice pushed a
            # share past 100%)
            moves = list(dict.fromkeys(
                m for m in (res.move(a) for a in mon.get("attacks") or []) if m
            ))
            rec = team["record"]
            for key in (base, mega):
                if key is None:
                    continue
                a = agg[key]
                a["n"] += 1
                a["wins"] += rec.get("wins", 0)
                a["games"] += rec.get("wins", 0) + rec.get("losses", 0) + rec.get("ties", 0)
                if item:
                    a["items"][item] += 1
                if ability and key == base:  # a Mega's ability is fixed
                    a["abilities"][ability] += 1
                if nature:
                    a["natures"][nature] += 1
                for m in moves:
                    a["moves"][m] += 1
                a["sets"][(item, ability, nature,
                           tuple(sorted(moves)))] += 1
        for name in names:
            for mate in names:
                if mate != name:
                    agg[name]["teammates"][mate] += 1

    out = {}
    for name, a in agg.items():
        n = a["n"]
        share = lambda c, top=None: {k: round(v / n, 4) for k, v in c.most_common(top)}
        out[name] = {
            "n": n,
            "usage": round(n / len(teams), 4),
            "win_rate": round(a["wins"] / a["games"], 4) if a["games"] else None,
            "items": share(a["items"], TOP_ITEMS),
            "abilities": share(a["abilities"]),
            "natures": share(a["natures"]),
            "moves": share(a["moves"], TOP_MOVES),
            "teammates": share(a["teammates"], TOP_TEAMMATES),
            "sets": [
                {"item": s[0], "ability": s[1], "nature": s[2], "moves": list(s[3]),
                 "share": round(c / n, 4)}
                for s, c in a["sets"].most_common(TOP_SETS)
            ],
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))


# ------------------------------------------------------------- Pikalytics ---

_PCT = re.compile(r"^- \*\*(.+?)\*\*: ([\d.]+)%", re.M)


def _section(md: str, title: str) -> dict[str, float]:
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    if not m:
        return {}
    return {k: round(float(v) / 100, 4) for k, v in _PCT.findall(m[1])}


def pikalytics(fmt: str, res: Resolver, today: str) -> dict:
    index = _get(f"{PIKALYTICS}/ai/pokedex/{fmt}", PIKALYTICS_DELAY_S,
                 CACHE_DIR / "pikalytics" / today / f"{fmt}.md")
    rows = re.findall(r"^\| \d+ \| \*\*(.+?)\*\* \| ([\d.]+)%", index, re.M)
    out = {}
    for name, usage in rows:
        slug = urllib.parse.quote(name)
        md = _get(f"{PIKALYTICS}/ai/pokedex/{fmt}/{slug}", PIKALYTICS_DELAY_S,
                  CACHE_DIR / "pikalytics" / today / f"{fmt}_{norm(name)}.md")
        ours = res.species(name)
        if ours is None:
            continue
        rec = re.search(r"\*\*Record\*\* \| (\d+)-(\d+)", md)
        moves = {}
        for mv, pct in _section(md, "Common Moves").items():
            canon = res.move(mv)
            if canon:
                moves[canon] = pct
        mates = {}
        for mate, pct in _section(md, "Common Teammates").items():
            canon = res.species(mate)
            if canon:
                mates[canon] = pct
        out[ours] = {
            "usage": round(float(usage) / 100, 4),
            "games": int(rec[1]) + int(rec[2]) if rec else None,
            "items": {res.item(k): v for k, v in _section(md, "Common Items").items()},
            "abilities": _section(md, "Common Abilities"),
            "moves": moves,
            "teammates": mates,
        }
        print(f"  pikalytics {name} -> {ours}")
    return out


# ------------------------------------------------------------------- main ---

def main() -> None:
    rs = load_ruleset("champions")
    current = sorted(rs.regulations)[-1]
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regulation", default=current,
                    help=f"ruleset regulation key (default: {current})")
    ap.add_argument("--days", type=int, default=28,
                    help="tournament window, days back from now (default 28)")
    ap.add_argument("--min-players", type=int, default=32)
    ap.add_argument("--settle-hours", type=int, default=24,
                    help="skip tournaments newer than this (may still be running)")
    ap.add_argument("--pikalytics-format", default=None,
                    help="Pikalytics format code (default: "
                         "gen9championsvgc2026reg<letters>)")
    ap.add_argument("--no-pikalytics", action="store_true")
    args = ap.parse_args()

    letter = args.regulation.rsplit("-", 2)[-2:]  # ['m', 'c']
    fmt_limitless = "-".join(letter).upper()        # 'M-C'
    fmt_pika = args.pikalytics_format or f"gen9championsvgc2026reg{''.join(letter)}"
    now = datetime.now(timezone.utc)
    res = Resolver()

    print(f"Limitless {fmt_limitless}: last {args.days} days, "
          f">= {args.min_players} players")
    tournaments = limitless_tournaments(
        fmt_limitless, now - timedelta(days=args.days),
        now - timedelta(hours=args.settle_hours), args.min_players,
    )
    teams = limitless_teams(tournaments)
    species_l = aggregate_limitless(teams, res) if teams else {}

    species_p = {}
    if not args.no_pikalytics:
        print(f"Pikalytics {fmt_pika}")
        try:
            species_p = pikalytics(fmt_pika, res, now.strftime("%Y-%m-%d"))
        except urllib.error.HTTPError as e:
            print(f"  ! pikalytics {fmt_pika}: {e}", file=sys.stderr)

    data = {
        "regulation": args.regulation,
        "generated": now.isoformat(timespec="seconds"),
        "sources": {
            "limitless": {
                "format": fmt_limitless,
                "tournaments": len(tournaments),
                "teams": len(teams),
                "window": [tournaments[0]["date"] if tournaments else None,
                           tournaments[-1]["date"] if tournaments else None],
                "min_players": args.min_players,
                "events": [
                    {"id": t["id"], "date": t["date"], "players": t["players"]}
                    for t in tournaments
                ],
            },
            "pikalytics": {"format": fmt_pika, "species": len(species_p)}
            if species_p else None,
        },
        "species": {
            name: {k: v for k, v in (
                ("limitless", species_l.get(name)),
                ("pikalytics", species_p.get(name)),
            ) if v}
            for name in sorted(set(species_l) | set(species_p),
                               key=lambda n: -(species_l.get(n, {}).get("n", 0)))
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{args.regulation}.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"wrote {path.relative_to(SCRIPTS_DIR.parent)}: {len(teams)} teams from "
          f"{len(tournaments)} tournaments, {len(data['species'])} species")
    if res.unknown:
        print("unresolved names (not in the champions ruleset):")
        for (kind, name), c in res.unknown.most_common(25):
            print(f"  {kind:8} {name!r} x{c}")


if __name__ == "__main__":
    main()
