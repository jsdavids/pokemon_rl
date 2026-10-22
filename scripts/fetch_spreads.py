"""Fetch Champions Stat Point spread distributions from Pikalytics.

Tournament team sheets (Limitless, Pikalytics' tournament formats) carry no
spreads. Pikalytics' RANKED-LADDER formats do: per species, the most common
Stat Point spreads (HP/Atk/Def/SpA/SpD/Spe, 0-32 each, 66 total) with their
shares, plus a nature table (reported separately — spreads and natures are
not joined). The newest ladder format with data is M-B season 3
(battledataregmbs3); it stands in for M-C until Pikalytics publishes M-C
ladder data, so species new in M-C have no entry.

The AI Markdown pages only give the single top spread, so this reads the
spreads/natures tables out of the standard species page (cached as the
extracted tables, not the ~1 MB page).

Output: src/pokechamp/data/rulesets/champions/usage/spreads-<format>.json
    {"format", "generated", "species": {our name: {"spreads": [[hp, atk,
     def, spa, spd, spe, share], ...], "natures": {name: share}}}}

    python scripts/fetch_spreads.py [--format battledataregmbs3]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_usage import CACHE_DIR, OUT_DIR, PIKALYTICS, PIKALYTICS_DELAY_S, Resolver, _get  # noqa: E402

_SPREAD = re.compile(
    r"(\d+)/\s*\|\s*(\d+)/\s*\|\s*(\d+)/\s*\|\s*(\d+)/\s*\|\s*(\d+)/\s*\|\s*(\d+)"
    r"\s*\|\s*([\d.]+)%"
)
_NATURE = re.compile(r"\|\s*([A-Z][a-z]+)\s*\|\s*([\d.]+)%")


def _flat(html: str) -> str:
    text = re.sub(r"<[^>]+>", " | ", html)
    return re.sub(r"(\s*\|\s*)+", " | ", text)


def parse_species_page(html: str) -> dict:
    """The spreads + natures tables of one Pikalytics species page."""
    i = html.find('id="dex_spreads_wrapper"')
    j = html.find('id="dex_natures_wrapper"')
    if i == -1 or j == -1:
        return {"spreads": [], "natures": {}}
    spreads = [
        [*(int(v) for v in m.groups()[:6]), round(float(m[7]) / 100, 4)]
        for m in _SPREAD.finditer(_flat(html[i:j]))
    ]
    k = html.find("Type Matchups", j)
    natures = {
        n: round(float(p) / 100, 4)
        for n, p in _NATURE.findall(_flat(html[j:k if k != -1 else j + 4000]))
    }
    return {"spreads": spreads, "natures": natures}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", default="battledataregmbs3")
    args = ap.parse_args()
    fmt = args.format
    now = datetime.now(timezone.utc)
    res = Resolver()

    index = _get(f"{PIKALYTICS}/ai/pokedex/{fmt}", PIKALYTICS_DELAY_S,
                 CACHE_DIR / "pikalytics" / now.strftime("%Y-%m-%d") / f"{fmt}.md")
    names = re.findall(r"^\| \d+ \| \*\*(.+?)\*\* \|", index, re.M)
    out = {}
    for name in names:
        cache = CACHE_DIR / "pikalytics_spreads" / fmt / f"{name}.json"
        if cache.exists():
            tables = json.loads(cache.read_text(encoding="utf-8"))
        else:
            html = _get(f"{PIKALYTICS}/pokedex/{fmt}/{urllib.parse.quote(name)}",
                        PIKALYTICS_DELAY_S, None)
            tables = parse_species_page(html)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(tables), encoding="utf-8")
        ours = res.species(name)
        if ours is None or not tables["spreads"]:
            print(f"  ! {name}: {'unknown species' if ours is None else 'no spreads'}")
            continue
        out[ours] = tables
        covered = sum(s[6] for s in tables["spreads"])
        print(f"  {name} -> {ours}: {len(tables['spreads'])} spreads "
              f"({covered:.0%} of builds), top {tables['spreads'][0][:6]}")

    path = OUT_DIR / f"spreads-{fmt}.json"
    path.write_text(json.dumps({
        "format": fmt, "generated": now.isoformat(timespec="seconds"),
        "species": out,
    }, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path.name}: {len(out)} species")


if __name__ == "__main__":
    main()
