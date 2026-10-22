"""Scrape serebii.net's Pokemon Champions dex into a pokechamp ruleset.

Sources:
  /pokedex-champions/           index -> ~209 species pages (incl. Mega forms)
  /attackdex-champions/         index -> ~900 move pages (stats, flags, effects)
  /pokemonchampions/items.shtml         all items with descriptions
  /pokemonchampions/rankedbattle/regulationm-{a,b,c,...}.shtml  per-regulation
      legality, walked until the first 404 (new regulations need no code change)

Output: src/pokechamp/data/rulesets/champions/{species,moves,items,regulations}.json
(typechart.json is copied from gen9 — Champions uses the standard chart).

Pages are cached in scripts/serebii_cache/ so re-runs never re-hit the site;
network fetches are rate-limited. Run with --limit N for a smoke test.

Usage:
    python scripts/scrape_serebii.py            # fetch (cached) + parse + write
    python scripts/scrape_serebii.py --limit 5  # smoke test on 5 species/moves
    python scripts/scrape_serebii.py --only species,items
    python scripts/scrape_serebii.py --refresh  # new regulation: re-fetch all
"""

from __future__ import annotations

import argparse
import codecs
import html as htmllib
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.serebii.net"
SCRIPTS_DIR = Path(__file__).parent
CACHE_DIR = SCRIPTS_DIR / "serebii_cache"
OUT_DIR = SCRIPTS_DIR.parent / "src" / "pokechamp" / "data" / "rulesets" / "champions"
GEN9_DIR = SCRIPTS_DIR.parent / "src" / "pokechamp" / "data" / "rulesets" / "gen9"

FETCH_DELAY_S = 0.5
UA = "pokechamp-scraper/0.1 (personal project; one-time cached scrape)"

TYPE_NAMES = {
    "normal", "fire", "water", "electric", "grass", "ice", "fighting",
    "poison", "ground", "flying", "psychic", "bug", "rock", "ghost",
    "dragon", "dark", "steel", "fairy",
}

_last_fetch = 0.0
REFRESH = False  # --refresh: ignore cached pages (a new regulation dropped)


def _cp1252_bytes(err: UnicodeDecodeError) -> tuple[str, int]:
    bad = err.object[err.start : err.end]
    return bad.decode("cp1252", errors="replace"), err.end


codecs.register_error("cp1252-bytes", _cp1252_bytes)


def decode(raw: bytes) -> str:
    """Serebii pages mix encodings WITHIN a page: UTF-8 text (move effects'
    em dashes) next to raw cp1252 bytes (0xE9, the é in the 'Pokémon' nav
    links). Decode as UTF-8 and reinterpret only the invalid bytes as
    cp1252 — all-or-nothing decoding either turned every cp1252 é into
    U+FFFD (utf-8, errors='replace') or double-mangled the UTF-8 runs
    (cp1252 for the whole page)."""
    return raw.decode("utf-8", errors="cp1252-bytes")


def fetch(path: str, missing_ok: bool = False) -> str | None:
    """Fetch a serebii path with local file cache + rate limit. With
    missing_ok, a 404 returns None (probing for regulations not out yet)."""
    global _last_fetch
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / (re.sub(r"[^A-Za-z0-9.-]+", "_", path.strip("/")) + ".html")
    if cache_file.exists() and not REFRESH:
        # the cache holds the server's raw bytes (older caches hold UTF-8
        # text, which decodes identically)
        return decode(cache_file.read_bytes())
    wait = _last_fetch + FETCH_DELAY_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    url = BASE + urllib.parse.quote(path, safe="/")  # sirfetch'd, mr.mime
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        _last_fetch = time.monotonic()
        if missing_ok and e.code == 404:
            return None
        raise
    _last_fetch = time.monotonic()
    cache_file.write_bytes(raw)
    return decode(raw)


# serebii sometimes serves UTF-8 bytes mis-labeled as cp1252 (mojibake)
MOJIBAKE = {"â€”": "—", "â€™": "'",
            "â€œ": '"', "â€": '"',
            "Ã©": "é"}


def clean_text(s: str) -> str:
    s = htmllib.unescape(s)
    for bad, good in MOJIBAKE.items():
        s = s.replace(bad, good)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- species ---

def species_slugs(index_html: str) -> list[str]:
    # [^"/]+, not [a-z0-9]+: that silently dropped farfetch'd, sirfetch'd,
    # mr.mime, mr.rime and kommo-o
    slugs = re.findall(r'<option value="/pokedex-champions/([^"/]+)/"', index_html)
    return sorted(set(slugs))


def _types_in(fragment: str) -> list[str]:
    types = []
    for t in re.findall(r"type/(\w+)\.gif", fragment):
        if t in TYPE_NAMES and t not in types:
            types.append(t)
    return types


def _first_word(s: str) -> str:
    parts = re.split(r"[\s(]+", s.strip())
    return parts[0].lower() if parts and parts[0] else ""


def _ability_groups(section: str) -> list[tuple[str, list[str]]]:
    """The Abilities cell lists link groups, optionally tagged '(Form)':
    'Static - Lightning Rod (Raichu) - Surge Surfer (Alola Form)'."""
    ma = re.search(r"<b>Abilities</b>:(.*?)</td>", section, re.S)
    if not ma:
        return []
    tokens = re.findall(
        r'href="/abilitydex/[^"]+"><b>([^<]+)</b>|\(([^)]+)\)', ma.group(1)
    )
    groups: list[tuple[str, list[str]]] = []
    cur: list[str] = []
    for ability, label in tokens:
        if ability:
            if (a := clean_text(ability)) not in cur:
                cur.append(a)
        else:
            groups.append((clean_text(label), cur))
            cur = []
    if cur:
        groups.append(("", cur))
    return groups


def parse_species_page(page: str) -> list[dict]:
    """One page can hold several forms. Megas open a fresh identity table
    (with the precise name in a header cell above it); regional forms share
    the base identity table via labeled type rows ('Normal' / 'Alolan' / ...)
    plus their own 'Stats - <Full Form Name>' table and a '(Form)'-tagged
    ability group."""
    header_re = re.compile(r'<td class="fooevo">Name</td>')
    starts = [m.start() for m in header_re.finditer(page)]
    if not starts:
        return []

    # learnset is page-wide (shared by all forms on the page)
    learnset = []
    mv_start = page.find("Standard Moves")
    if mv_start != -1:
        mv_end = page.find("<h2>Stats</h2>", mv_start)
        region = page[mv_start : mv_end if mv_end != -1 else None]
        for slug, name in re.findall(
            r'<a href="/attackdex-champions/([^"]+)\.shtml">([^<]+)</a>', region
        ):
            # No type-name filter here: the regex already requires text
            # content, and type-icon links wrap an <img> (their slugs are
            # "<type>t.shtml" besides). Filtering slugs against TYPE_NAMES
            # silently dropped the MOVE Psychic (slug "psychic") from every
            # learnset — the one move whose name is also a type.
            name = clean_text(name)
            if name and name not in learnset:
                learnset.append(name)

    stats_row_re = re.compile(
        r"Base Stats[^<]*</td>\s*"
        + r"\s*".join(r'<td[^>]*class="fooinfo">(\d+)</td>' for _ in range(6))
    )

    forms = []
    for i, start in enumerate(starts):
        section = page[start : starts[i + 1] if i + 1 < len(starts) else len(page)]

        m = re.search(r'<td class="fooinfo">\s*([^<]+?)\s*</td>', section)
        if not m:
            continue
        ident_name = clean_text(m.group(1))
        # alt forms carry the precise name (Mega Charizard X) in a fooevo
        # header cell just above the identity table; the Name cell may not
        heads = re.findall(
            r'<td[^>]*class="fooevo"[^>]*>(.*?)</td>', page[max(0, start - 1500) : start], re.S
        )
        heads = [clean_text(re.sub(r"<[^>]+>", "", h)) for h in heads]
        heads = [h for h in heads if h and h != "Picture"]
        if heads and ident_name in heads[-1]:
            ident_name = heads[-1]

        num = None
        mnum = re.search(r"National</b>:\s*</td>\s*<td[^>]*>#?(\d+)", section)
        if mnum:
            num = int(mnum.group(1))

        # kg weights (Grass Knot / Heavy Slam tiers). Forms sharing an
        # identity table list theirs slash-separated in form order
        # ('30kg / 21kg' = Raichu / Alolan Raichu); a form past the end of
        # the list (Gourgeist sizes: one weight given) gets the first.
        weights: list[float] = []
        wi = section.find("Weight</td>")
        if wi != -1:
            mw = re.search(r"[\d.]+\s*kg(?:\s*/\s*[\d.]+\s*kg)*", section[wi : wi + 600])
            if mw:
                weights = [float(w) for w in re.findall(r"([\d.]+)\s*kg", mw.group(0))]

        def weight_for(i: int) -> float | None:
            if not weights:
                return None
            return weights[i] if i < len(weights) else weights[0]

        # types live in the identity table, before the Classification row;
        # bound the search so learnset move-type icons don't leak in.
        # Regional forms appear as labeled rows inside the Type cell.
        ident = section[: max(section.find("Classification"), 0) or 2500]
        type_rows = re.findall(
            r'<td width="50%">\s*([^<]*?)\s*</td>\s*<td width="50%">(.*?)</td>',
            ident, re.S,
        )
        labeled = [(clean_text(lbl), _types_in(cell)) for lbl, cell in type_rows]
        labeled = [(lbl, t) for lbl, t in labeled if t]
        if not labeled:
            labeled = [("", _types_in(ident))]

        # every stats table in the section: '' label = first/base form,
        # 'Alolan Raichu' etc. = regional form full name
        stats_tables: list[tuple[str, dict]] = []
        for mh in re.finditer(r"<h2>Stats(?:\s*-\s*([^<]*))?</h2>", section):
            ms = stats_row_re.search(section[mh.end() : mh.end() + 4000])
            if ms:
                vals = [int(v) for v in ms.groups()]
                stats_tables.append(
                    (clean_text(mh.group(1) or ""),
                     dict(zip(("hp", "atk", "def", "spa", "spd", "spe"), vals)))
                )
        anon_stats = next((s for lbl, s in stats_tables if not lbl), None)
        named_stats = [(lbl, s) for lbl, s in stats_tables if lbl]

        groups = _ability_groups(section)

        def abilities_for(form_label: str) -> list[str]:
            if not groups:
                return []
            if len(groups) == 1:
                return groups[0][1]
            key = _first_word(form_label)[:4]
            base_key = _first_word(ident_name)
            for lbl, abs_ in groups:
                gw = _first_word(lbl)
                if form_label in ("", "Normal"):
                    if not lbl or gw == base_key:
                        return abs_
                elif gw[:4] == key:
                    return abs_
            return groups[0][1]

        last_regional: tuple[str, dict, list[str]] | None = None
        used_stats: set[str] = set()
        section_start = len(forms)
        # one table shared by every non-base form (Rotom's appliances)
        alt_label, alt_stats = next(
            ((l, s) for l, s in named_stats if l.lower().startswith("alternate form")),
            (None, None),
        )
        for lbl, types in labeled:
            if lbl in ("", "Normal") or lbl == ident_name:
                name, stats, abilities = ident_name, anon_stats, abilities_for("")
                if stats is None and named_stats:
                    # no plain 'Stats' table: the game only has a named form
                    # (Floette exists in Champions only as 'Eternal Floette')
                    name, stats = named_stats[0]
                    used_stats.add(name)
            else:
                match = next(
                    ((full, s) for full, s in named_stats
                     if _first_word(lbl)[:4] in full.lower()),
                    None,
                )
                if match:
                    name, stats = match
                    used_stats.add(name)
                    abilities = abilities_for(lbl)
                    last_regional = (name, stats, abilities)
                elif last_regional:  # e.g. Paldean Tauros breed rows
                    name = f"{last_regional[0]} ({lbl})"
                    stats, abilities = last_regional[1], last_regional[2]
                else:
                    # 'Heat Rotom' already names the form; 'Sunny Form'
                    # becomes 'Castform (Sunny Form)'. Prepending the label
                    # used to produce 'Heat Rotom Rotom'.
                    name = lbl if ident_name in lbl else f"{ident_name} ({lbl})"
                    stats = alt_stats or anon_stats
                    if alt_stats is not None:
                        used_stats.add(alt_label)
                    abilities = abilities_for(lbl)
            if stats is None:
                continue  # decorative table (gender differences etc.)
            form = {
                "name": name,
                "num": num,
                "types": types,
                "abilities": abilities,
                "base_stats": stats,
                "moves": learnset,
            }
            weight = weight_for(len(forms) - section_start)
            if weight is not None:
                form["weight"] = weight
            forms.append(form)

        # forms that differ only in stats (Indeedee's 'Stats - Female')
        # have no labeled type row; give each leftover named stats table
        # its own form with the base typing and its '(Label)' ability group
        if not forms or forms[0]["name"] != ident_name:
            continue
        base_form = forms[0]
        for lbl, stats in named_stats:
            if lbl in used_stats or ident_name.lower() in lbl.lower():
                continue  # regional/mega tables carry the full form name
            key = _first_word(lbl)[:4]
            abilities = next(
                (abs_ for glbl, abs_ in groups if _first_word(glbl)[:4] == key),
                base_form["abilities"],
            )
            form = {
                **base_form,
                "name": f"{ident_name} ({lbl})",
                "abilities": abilities,
                "base_stats": stats,
            }
            weight = weight_for(len(forms) - section_start)
            if weight is not None:
                form["weight"] = weight
            forms.append(form)
    return forms


def scrape_species(limit: int | None) -> dict:
    index = fetch("/pokedex-champions/")
    slugs = species_slugs(index)
    if limit:
        slugs = slugs[:limit]
    out: dict[str, dict] = {}
    base_name = None
    for n, slug in enumerate(slugs, 1):
        try:
            forms = parse_species_page(fetch(f"/pokedex-champions/{slug}/"))
        except Exception as e:  # noqa: BLE001 - log and continue the crawl
            print(f"  ! {slug}: {e}", file=sys.stderr)
            continue
        base_name = forms[0]["name"] if forms else None
        for form in forms:
            entry = dict(form)
            name = entry.pop("name")
            if name != base_name:
                entry["base"] = base_name
            key = name
            while key in out:  # same-named regional forms: suffix and warn
                key += "*"
            if key != name:
                print(f"  ! duplicate form name {name!r} ({slug}) -> {key!r}", file=sys.stderr)
            out[key] = entry
        if n % 25 == 0:
            print(f"  species pages: {n}/{len(slugs)}")
    return out


# ------------------------------------------------------------------ moves ---

MULTIHIT_PATTERNS: list[tuple[str, tuple[int, int] | None]] = [
    (r"(\d+) to (\d+) times", None),  # groups give the range
    (r"attacks? (\d+) times", None),  # single group: fixed count
    (r"two to five times", (2, 5)),
    (r"attacks? twice|hits twice|hits the target twice|strikes twice", (2, 2)),
    (r"three times in a row|hits three times|attacks? three times", (3, 3)),
    (r"ten times in a row", (10, 10)),
]

# Serebii's Champions attackdex has empty Battle Effect text for some moves;
# fall back to the mainline multi-hit table for those.
KNOWN_MULTIHIT: dict[str, list[int]] = {
    "Arm Thrust": [2, 5], "Barrage": [2, 5], "Bone Rush": [2, 5],
    "Bonemerang": [2, 2], "Bullet Seed": [2, 5], "Comet Punch": [2, 5],
    "Double Hit": [2, 2], "Double Iron Bash": [2, 2], "Double Kick": [2, 2],
    "Double Slap": [2, 5], "Dragon Darts": [2, 2], "Dual Chop": [2, 2],
    "Dual Wingbeat": [2, 2], "Fury Attack": [2, 5], "Fury Swipes": [2, 5],
    "Gear Grind": [2, 2], "Icicle Spear": [2, 5], "Pin Missile": [2, 5],
    "Population Bomb": [1, 10], "Rock Blast": [2, 5], "Scale Shot": [2, 5],
    "Spike Cannon": [2, 5], "Surging Strikes": [3, 3], "Tail Slap": [2, 5],
    "Triple Axel": [3, 3], "Triple Dive": [3, 3], "Triple Kick": [3, 3],
    "Twineedle": [2, 2], "Water Shuriken": [2, 5],
}


def canonical_target(raw: str) -> str:
    """Serebii's 'Pokemon Hit in Battle' strings -> canonical target ids
    (doubles targeting depends on these: Earthquake hits the ally, Hyper
    Voice doesn't)."""
    t = raw.lower()
    if "all adjacent pok" in t:
        return "allAdjacent"
    if "all adjacent foes" in t or "all adjacent opponents" in t or t == "all opponents":
        return "adjacentFoes"
    if t == "self":
        return "self"
    if "ally" in t or "allies" in t or t == "team":
        return "allies"
    if "side" in t:
        return "side"
    if t in ("field", "all"):
        return "field"
    if "random" in t:
        return "randomNormal"
    return "normal"  # Selected Target / Opponent / misc single-target


def parse_move_page(page: str, name: str) -> dict | None:
    start = page.find("Attack Name")
    if start == -1:
        return None
    body = page[start:]

    mt = re.search(r"type/(\w+)\.gif", body)
    mc = re.search(r"type/(physical|special|other)\.png", body)
    if not mt or not mc:
        return None
    category = {"physical": "Physical", "special": "Special", "other": "Status"}[mc.group(1)]

    def cen_values(after: str, count: int) -> list[str]:
        i = body.find(after)
        if i == -1:
            return []
        vals = re.findall(r'<td class="cen">\s*(.*?)\s*</td>', body[i:], re.S)
        return [clean_text(re.sub(r"<[^>]+>", " ", v)) for v in vals[:count]]

    nums = cen_values("Power Points", 3)  # PP, base power, accuracy
    if len(nums) < 3:
        return None

    def to_int(v: str) -> int | None:
        m = re.search(r"\d+", v)
        return int(m.group(0)) if m else None

    pp, base_power, accuracy = (to_int(v) for v in nums)
    if accuracy is not None and accuracy > 100:  # serebii's 101 = never misses
        accuracy = None

    prio_target = cen_values("Speed Priority", 3)  # crit rate, priority, target
    priority = 0
    target = "normal"
    if len(prio_target) == 3:
        priority = to_int(prio_target[1].replace("--", "0")) or 0
        if prio_target[1].strip().startswith("-"):
            priority = -priority
        target = canonical_target(prio_target[2])
    spread = target in ("adjacentFoes", "allAdjacent")

    # flag grid: two label rows of 5, each followed by 5 Yes/No cells
    flags = []
    fi = body.find("Physical Contact")
    if fi != -1:
        yn = re.findall(r'<td class="cen">(Yes|No)</td>', body[fi:])[:10]
        flag_order = ["contact", "sound", "punch", "bite", None,
                      "slicing", "bullet", "wind", "powder", None]
        flags = [f for f, v in zip(flag_order, yn) if f and v == "Yes"]

    effect = ""
    me = re.search(r"Battle Effect:(.*?)(?:In-Depth Effect|Secondary Effect)", body, re.S)
    if me:
        effect = clean_text(re.sub(r"<[^>]+>", " ", me.group(1)))

    multihit = None
    for pat, val in MULTIHIT_PATTERNS:
        m = re.search(pat, effect, re.I)
        if m:
            groups = [g for g in m.groups() if g]
            if len(groups) == 2:
                multihit = [int(groups[0]), int(groups[1])]
            elif len(groups) == 1:
                multihit = [int(groups[0])] * 2
            else:
                multihit = list(val)
            break
    if multihit is None and name in KNOWN_MULTIHIT:
        multihit = KNOWN_MULTIHIT[name]

    entry: dict = {
        "type": mt.group(1),
        "category": category,
        "base_power": base_power or 0,
        "accuracy": accuracy,
        "pp": pp,
    }
    if priority:
        entry["priority"] = priority
    if target != "normal":
        entry["target"] = target
    if spread:
        entry["spread"] = True
    if flags:
        entry["flags"] = flags
    if multihit:
        entry["multihit"] = multihit
    if effect and effect != "--":
        entry["effect"] = effect
    return entry


def scrape_moves(limit: int | None) -> dict:
    index = fetch("/attackdex-champions/")
    options = re.findall(
        r'<option value="/attackdex-champions/([^"]+)\.shtml"\s*>([^<]+)</option>', index
    )
    seen: dict[str, str] = {}
    for slug, name in options:
        seen.setdefault(slug, clean_text(name))
    items = sorted(seen.items())
    if limit:
        items = items[:limit]
    out: dict[str, dict] = {}
    for n, (slug, name) in enumerate(items, 1):
        try:
            entry = parse_move_page(fetch(f"/attackdex-champions/{slug}.shtml"), name)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {slug}: {e}", file=sys.stderr)
            continue
        if entry is None:
            print(f"  ! {slug}: unparseable, skipped", file=sys.stderr)
            continue
        out[name] = entry
        if n % 100 == 0:
            print(f"  move pages: {n}/{len(items)}")
    return out


# ------------------------------------------------------------------ items ---

def scrape_items() -> dict:
    page = fetch("/pokemonchampions/items.shtml")
    main = page[page.find("<main"):] if "<main" in page else page
    out: dict[str, dict] = {}
    row_re = re.compile(
        r'href="/itemdex/[^"]+"[^>]*>([^<]+)</a></td>\s*'
        r'<td class="fooinfo">(.*?)</td>\s*<td class="fooinfo">(.*?)</td>',
        re.S,
    )
    for name, desc, loc in row_re.findall(main):
        name = clean_text(name)
        desc = clean_text(re.sub(r"<[^>]+>", " ", desc))
        loc = clean_text(re.sub(r"<[^>]+>", " ", loc))
        mega_stone = "Mega Stones" in desc
        out[name] = {
            "description": desc,
            "location": loc,
            "held": mega_stone
            or desc.startswith(("An item to be held", "An item for a Pok")),
        }
        if mega_stone:
            out[name]["mega_stone"] = True
    return out


# ------------------------------------------------------------ regulations ---

def parse_regulation(page: str) -> dict:
    dates = ""
    md = re.search(r"Regulation Date(?: Date)?</h3>\s*(?:<br\s*/?>\s*)*([^<]+)", page)
    if md:
        dates = clean_text(md.group(1))

    pokemon = []
    pi = page.find("Newly Useable Pok")
    if pi != -1:
        region = page[pi : page.find("Newly Added Items") if "Newly Added Items" in page else len(page)]
        for name in re.findall(
            r'href="/pokedex-champions/[a-z0-9]+/">\s*([^<]+?)\s*<br', region
        ):
            name = clean_text(name)
            if name and name not in pokemon:
                pokemon.append(name)

    items = []
    ii = page.find("Newly Added Items")
    if ii != -1:
        for name in re.findall(r'href="/itemdex/[^"]+"[^>]*>([^<]+)</a>', page[ii:]):
            name = clean_text(name)
            if name and name not in items:
                items.append(name)

    return {"dates": dates, "pokemon": pokemon, "items": items}


def scrape_regulations() -> dict:
    """Walk regulationm-a, -b, -c, ... until serebii has no page yet, so a
    new regulation is picked up by a --refresh re-run with no code change."""
    out = {}
    for reg in "abcdefghijklmnopqrstuvwxyz":
        page = fetch(f"/pokemonchampions/rankedbattle/regulationm-{reg}.shtml",
                     missing_ok=True)
        if page is None:
            break
        out[f"regulation-m-{reg}"] = parse_regulation(page)
    return out


# ------------------------------------------------------------------- main ---

def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {path.relative_to(SCRIPTS_DIR.parent)} ({len(data)} entries)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="species,moves,items,regulations",
                    help="comma list of: species,moves,items,regulations")
    ap.add_argument("--limit", type=int, default=None,
                    help="smoke test: first N species/move pages only")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch pages even if cached (new regulation out)")
    args = ap.parse_args()
    parts = set(args.only.split(","))
    global REFRESH
    REFRESH = args.refresh

    if "species" in parts:
        print("scraping species...")
        write_json(OUT_DIR / "species.json", scrape_species(args.limit))
    if "moves" in parts:
        print("scraping moves...")
        write_json(OUT_DIR / "moves.json", scrape_moves(args.limit))
    if "items" in parts:
        print("scraping items...")
        write_json(OUT_DIR / "items.json", scrape_items())
    if "regulations" in parts:
        print("scraping regulations...")
        write_json(OUT_DIR / "regulations.json", scrape_regulations())

    typechart = OUT_DIR / "typechart.json"
    if not typechart.exists() and (GEN9_DIR / "typechart.json").exists():
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(GEN9_DIR / "typechart.json", typechart)
        print("copied gen9 typechart.json (standard chart)")


if __name__ == "__main__":
    main()
