"""Named, versioned storage for the user's own teams.

Every save appends a new version under the team's name (builds iterate
constantly during ladder play; "what did this team look like two edits
ago" should always be answerable). Saves are validated against the
champions ruleset by default so typos and illegal builds are caught at
entry time, not mid-battle.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pokechamp.data import load_ruleset
from pokechamp.data.loader import norm
from pokechamp.schemas import PokemonSet, STAT_KEYS
from pokechamp.session.serialize import set_from_dict, set_to_dict

DEFAULT_DIR = Path.home() / ".pokechamp"
EV_MAX_PER_STAT = 252
EV_MAX_TOTAL = 508


def validate_set(pset: PokemonSet, ruleset: str = "champions") -> list[str]:
    """Every rule violation in one build (empty list = legal)."""
    rs = load_ruleset(ruleset)
    problems = []
    try:
        sp = rs.get_species(pset.species)
    except KeyError:
        return [f"unknown species {pset.species!r}"]

    learnset = set(sp.learnset)
    for move in pset.moves:
        try:
            rs.get_move(move)
        except KeyError:
            problems.append(f"{pset.species}: unknown move {move!r}")
            continue
        if norm(move) not in learnset:
            problems.append(f"{pset.species} can't learn {move!r}")
    if len(pset.moves) > 4:
        problems.append(f"{pset.species}: more than 4 moves")

    if pset.ability is not None and norm(pset.ability) not in {
        norm(a) for a in sp.abilities
    }:
        problems.append(
            f"{pset.species}: ability {pset.ability!r} not in {sp.abilities}"
        )
    if pset.item is not None and pset.item not in rs.items:
        problems.append(f"{pset.species}: item {pset.item!r} not in champions")

    for k in STAT_KEYS:
        if not 0 <= pset.evs.get(k, 0) <= EV_MAX_PER_STAT:
            problems.append(f"{pset.species}: {k} EVs outside 0-{EV_MAX_PER_STAT}")
        if not 0 <= pset.ivs.get(k, 31) <= 31:
            problems.append(f"{pset.species}: {k} IVs outside 0-31")
    if sum(pset.evs.values()) > EV_MAX_TOTAL:
        problems.append(f"{pset.species}: EV total over {EV_MAX_TOTAL}")
    return problems


class TeamStore:
    """teams.json: {"teams": {name: [version, ...]}} where each version is
    {"saved_at": iso8601, "team": [set-dict, ...]}."""

    def __init__(self, path: str | Path | None = None, ruleset: str = "champions"):
        self.path = Path(path) if path else DEFAULT_DIR / "teams.json"
        self.ruleset = ruleset
        self._data = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {"teams": {}}
        )

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=1), encoding="utf-8"
        )

    def save(
        self, name: str, team: list[PokemonSet], validate: bool = True
    ) -> int:
        """Append a version; returns its index. Raises ValueError listing
        every problem when validation fails."""
        if not 1 <= len(team) <= 6:
            raise ValueError(f"team must have 1-6 Pokemon, got {len(team)}")
        if validate:
            problems = [
                p for pset in team for p in validate_set(pset, self.ruleset)
            ]
            if problems:
                raise ValueError("; ".join(problems))
        versions = self._data["teams"].setdefault(name, [])
        versions.append({
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "team": [set_to_dict(p) for p in team],
        })
        self._write()
        return len(versions) - 1

    def get(self, name: str, version: int | None = None) -> list[PokemonSet]:
        """A stored team (latest version unless one is named)."""
        versions = self._data["teams"].get(name)
        if not versions:
            raise KeyError(f"no team named {name!r}")
        v = versions[-1 if version is None else version]
        return [set_from_dict(d) for d in v["team"]]

    def names(self) -> list[str]:
        return sorted(self._data["teams"])

    def versions(self, name: str) -> list[dict]:
        """Version metadata (saved_at + species list), oldest first."""
        if name not in self._data["teams"]:
            raise KeyError(f"no team named {name!r}")
        return [
            {
                "version": i,
                "saved_at": v["saved_at"],
                "species": [d["species"] for d in v["team"]],
            }
            for i, v in enumerate(self._data["teams"][name])
        ]

    def delete(self, name: str) -> None:
        self._data["teams"].pop(name, None)
        self._write()
