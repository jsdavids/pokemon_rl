"""Scouting book: everything known about an opponent's Pokemon, persisted.

Two kinds of knowledge, kept separate on purpose:
- revealed facts (moves seen, item/ability confirmed) — hard constraints
  that filter the estimator's candidate space
- observations (damage rolls, turn order, HP readings) — soft evidence,
  stored as replayable records so the Bayesian posterior can be rebuilt
  identically after a restart (the estimator itself is never pickled)

`for_recommend` hands the recommender exactly what it wants: estimators
keyed by opponent team index plus the revealed-move lists that seed each
determinized world's moveset fill.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from pokechamp.data import load_ruleset
from pokechamp.data.loader import canonical_species, norm
from pokechamp.estimator import StatEstimator
from pokechamp.estimator.candidates import default_candidates
from pokechamp.estimator.usage import usage_for
from pokechamp.session.serialize import (
    field_from_dict,
    field_to_dict,
    set_from_dict,
    set_to_dict,
)
from pokechamp.session.teams import DEFAULT_DIR

# scout.observe kinds -> StatEstimator methods
_OBSERVERS = {
    "damage_dealt": "observe_damage_dealt",
    "damage_taken": "observe_damage_taken",
    "move_order": "observe_move_order",
    "speed": "observe_speed",
    "set_hp": "set_hp",
}
_SET_KEYS = ("target", "attacker")


class OpponentScout:
    """scouting.json: {"opponents": {name: {"pokemon": {species: record}}}}
    where a record holds revealed_moves / item / ability / observations."""

    def __init__(
        self,
        path: str | Path | None = None,
        ruleset: str = "champions",
        regulation: str | None = None,
        level: int = 50,
        use_usage: bool = True,
        spreads=None,
    ):
        self.path = Path(path) if path else DEFAULT_DIR / "scouting.json"
        self.ruleset = ruleset
        self.regulation = regulation
        # usage priors need a regulation with fetched usage data
        # (scripts/fetch_usage.py); without one the estimator stays uniform
        self.use_usage = use_usage
        # SpreadCandidates (estimator/spreads.py): ladder-grounded spread
        # hypotheses + their prior instead of the hand-written EV templates
        self.spreads = spreads
        self.level = level
        self._data = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {"opponents": {}}
        )
        self._cache: dict[tuple[str, str], StatEstimator] = {}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1), encoding="utf-8")

    def _mon(self, opponent: str, species: str) -> dict:
        species = canonical_species(species, self.ruleset)
        team = self._data["opponents"].setdefault(opponent, {"pokemon": {}})
        return team["pokemon"].setdefault(species, {
            "revealed_moves": [], "item": None, "ability": None,
            "observations": [],
        })

    # --- data entry ---

    def add_pokemon(self, opponent: str, species: str) -> None:
        """Register a species on the opponent's team (validates the name)."""
        load_ruleset(self.ruleset).get_species(species)  # validates the name
        self._mon(opponent, species)
        self._write()

    def reveal_move(self, opponent: str, species: str, move: str) -> None:
        rs = load_ruleset(self.ruleset)
        canonical = rs.get_move(move).name
        if norm(move) not in set(rs.get_species(species).learnset):
            raise ValueError(f"{species} can't learn {move!r}")
        rec = self._mon(opponent, species)
        if canonical not in rec["revealed_moves"]:
            rec["revealed_moves"].append(canonical)
        self._write()

    def reveal_item(self, opponent: str, species: str, item: str) -> None:
        species = canonical_species(species, self.ruleset)
        rs = load_ruleset(self.ruleset)
        if item not in rs.items:
            raise ValueError(f"unknown item {item!r}")
        self._mon(opponent, species)["item"] = item
        self._cache.pop((opponent, species), None)  # candidate space changed
        self._write()

    def reveal_ability(self, opponent: str, species: str, ability: str) -> None:
        species = canonical_species(species, self.ruleset)
        rs = load_ruleset(self.ruleset)
        if norm(ability) not in {
            norm(a) for a in rs.get_species(species).abilities
        }:
            raise ValueError(f"{species} can't have ability {ability!r}")
        self._mon(opponent, species)["ability"] = ability
        self._cache.pop((opponent, species), None)
        self._write()

    def observe(self, opponent: str, species: str, kind: str, **kwargs) -> None:
        """Feed one observation to the estimator AND record it for replay.
        `kind` is one of damage_dealt / damage_taken / move_order / speed /
        set_hp with that estimator method's keyword arguments."""
        if kind not in _OBSERVERS:
            raise ValueError(f"unknown observation kind {kind!r}")
        # applying first means a contradictory entry raises before it is saved
        getattr(self.estimator(opponent, species), _OBSERVERS[kind])(**kwargs)
        stored = dict(kwargs)
        for key in _SET_KEYS:
            if key in stored:
                stored[key] = set_to_dict(stored[key])
        if stored.get("field") is not None:
            stored["field"] = field_to_dict(stored["field"])
        self._mon(opponent, species)["observations"].append(
            {"kind": kind, "kwargs": stored}
        )
        self._write()

    # --- estimator reconstruction ---

    def _candidates(self, rec: dict, species: str):
        """Default candidate grid, cut down by revealed hard facts. A
        revealed item outside the usual archetypes replaces the item on
        every survivor instead (deduplicated)."""
        usage = self._usage(species)
        cands = default_candidates(
            species, self.level, ruleset=self.ruleset,
            regulation=self.regulation,
            extra_natures=usage.extra_natures() if usage else (),
            extra_items=usage.extra_items() if usage else (),
            spreads=self.spreads,
        )
        if rec["item"] is not None:
            match = [c for c in cands if norm(c.item or "") == norm(rec["item"])]
            if not match:
                seen, match = set(), []
                for c in cands:
                    forced = replace(c, item=rec["item"])
                    key = (forced.nature, tuple(sorted(forced.evs.items())),
                           forced.ability)
                    if key not in seen:
                        seen.add(key)
                        match.append(forced)
            cands = match
        if rec["ability"] is not None:
            cands = [
                c for c in cands
                if norm(c.ability or "") == norm(rec["ability"])
            ]
        return cands

    def _usage(self, species: str):
        if not self.use_usage:
            return None
        return usage_for(
            canonical_species(species, self.ruleset), self.ruleset,
            self.regulation,
        )

    def estimator(self, opponent: str, species: str) -> StatEstimator:
        """The live posterior for one opposing Pokemon: candidates filtered
        by revealed facts, every stored observation replayed."""
        species = canonical_species(species, self.ruleset)
        key = (opponent, species)
        if key not in self._cache:
            rec = self._mon(opponent, species)
            est = StatEstimator(
                species, level=self.level, ruleset=self.ruleset,
                candidates=self._candidates(rec, species),
            )
            usage = self._usage(species)
            if usage is not None:  # priors first, then the evidence
                est.usage = usage
                est.apply_prior(usage.prior(est.candidates), usage.description)
            if self.spreads is not None:
                est.apply_prior(self.spreads.prior(load_ruleset(self.ruleset)),
                                "ladder spread prior")
            for ob in rec["observations"]:
                kwargs = dict(ob["kwargs"])
                for k in _SET_KEYS:
                    if k in kwargs:
                        kwargs[k] = set_from_dict(kwargs[k])
                if kwargs.get("field") is not None:
                    kwargs["field"] = field_from_dict(kwargs["field"])
                getattr(est, _OBSERVERS[ob["kind"]])(**kwargs)
            self._cache[key] = est
        return self._cache[key]

    # --- queries ---

    def opponents(self) -> list[str]:
        return sorted(self._data["opponents"])

    def team(self, opponent: str) -> list[str]:
        """Known species, in the order they were registered."""
        return list(
            self._data["opponents"].get(opponent, {"pokemon": {}})["pokemon"]
        )

    def summary(self, opponent: str, species: str) -> dict:
        """Revealed facts + the posterior's stat/item/ability read. The
        stat_summary's "item"/"ability" keys are posterior marginals;
        confirmed facts live under revealed_*."""
        rec = self._mon(opponent, species)
        est = self.estimator(opponent, species)
        return {
            "species": canonical_species(species, self.ruleset),
            "revealed_moves": list(rec["revealed_moves"]),
            "revealed_item": rec["item"],
            "revealed_ability": rec["ability"],
            "observations": len(rec["observations"]),
            "hp_percent": est.hp_percent,
            **est.stat_summary(),
            **({"meta": est.usage.summary()} if est.usage is not None else {}),
        }

    def for_recommend(
        self, opponent: str, team_order: list[str] | None = None
    ) -> tuple[dict[int, StatEstimator], dict[int, list[str]]]:
        """(estimators, revealed_moves) keyed by opponent team index, ready
        to pass straight into pokechamp.recommender.recommend()."""
        order = team_order or self.team(opponent)
        ests, revealed = {}, {}
        for i, species in enumerate(order):
            rec = self._mon(opponent, species)
            ests[i] = self.estimator(opponent, species)
            if rec["revealed_moves"]:
                revealed[i] = list(rec["revealed_moves"])
        return ests, revealed
