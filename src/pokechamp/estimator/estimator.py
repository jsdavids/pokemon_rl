"""Bayesian stat/set estimator (Phase 2).

Inverts the damage calculator: maintain a posterior over candidate builds
(nature, EVs, item) for one opponent Pokemon, updated by observations.

The damage-roll likelihood is exact, not a heuristic: in-game damage is a
uniform draw over the calculator's 16 rolls, so
P(observed % | candidate) = (# rolls within tolerance of observed %) / 16.
Speed observations are hard constraints (0/1), with ties scored 0.5 since
tie order is a coin flip.

Implemented observations: damage dealt by the opponent, damage taken by the
opponent, and turn-order (speed) within the same priority bracket.

Candidate enumeration defaults to the champions ruleset: items are limited
to what the game/regulation actually offers, and each of the species' real
abilities is a hypothesis (so a Huge Power hit or a Thick Fat non-hit shifts
the ability marginal). The gen9 sample ruleset remains available for tests.

The estimator tracks the opponent's HP bar (percent) across observations, so
damage can be reported as "the bar went to X%" and KOs become censored
observations: P(damage >= what was left). Move-order observations look up
priority from the ruleset's move data and only extract speed information when
both moves share a bracket. `apply_prior` reweights candidates by any external
score (usage statistics, team-archetype knowledge).

TODO (Phase 2 completion):
- usage-statistics priors and usage-derived candidate sets (the apply_prior
  hook exists; Champions usage data doesn't yet)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pokechamp.damage import calc_damage
from pokechamp.damage.calculator import boosted_stat
from pokechamp.data import load_ruleset
from pokechamp.data.loader import norm
from pokechamp.estimator.candidates import default_candidates
from pokechamp.schemas import STAT_KEYS, FieldState, PokemonSet
from pokechamp.stats import calc_all_stats

EPS = 1e-12


class InconsistentObservation(ValueError):
    """No candidate can explain the observation. Usually the tolerance is too
    tight, the candidate grid too coarse, or an unmodeled effect (ability,
    unimplemented item) is in play."""


def effective_speed(
    pkm: PokemonSet,
    boost: int = 0,
    tailwind: bool = False,
    paralyzed: bool = False,
    ruleset: str = "champions",
) -> int:
    rs = load_ruleset(ruleset)
    base = rs.get_species(pkm.species).base_stats
    spe = calc_all_stats(base, pkm.level, pkm.nature, pkm.ivs, pkm.evs)["spe"]
    spe = boosted_stat(spe, boost)
    if pkm.item and norm(pkm.item) == "choicescarf":
        spe = math.floor(spe * 1.5)
    if tailwind:
        spe *= 2
    if paralyzed:
        spe = math.floor(spe / 2)
    return spe


@dataclass
class Observation:
    description: str
    survivors: int  # candidates with nonzero weight after this observation


class StatEstimator:
    """Posterior over builds for a single opponent Pokemon.

    >>> est = StatEstimator("Garchomp")
    >>> est.observe_damage_dealt("Earthquake", my_pokemon, percent=58.4)
    >>> est.observe_speed(opponent_moved_first=True, reference_speed=150)
    >>> est.top_candidates(5)
    """

    def __init__(
        self,
        species: str,
        level: int = 50,
        ruleset: str = "champions",
        regulation: str | None = None,
        candidates: list[PokemonSet] | None = None,
    ):
        self.species = species
        self.level = level
        self.ruleset = ruleset
        self.candidates = (
            candidates
            if candidates is not None
            else default_candidates(species, level, ruleset=ruleset, regulation=regulation)
        )
        n = len(self.candidates)
        self.weights = [1.0 / n] * n
        self.log: list[Observation] = []
        # the opponent's HP bar, as shown in-game (percent of its max HP)
        self.hp_percent = 100.0

    # --- observations ---

    def _update(self, likelihoods: list[float], description: str) -> None:
        new = [w * l for w, l in zip(self.weights, likelihoods)]
        total = sum(new)
        if total <= EPS:
            raise InconsistentObservation(
                f"No candidate consistent with: {description}. "
                "Widen the tolerance or candidate set, or suspect an "
                "unmodeled ability/item."
            )
        self.weights = [w / total for w in new]
        self.log.append(
            Observation(description, sum(1 for w in self.weights if w > EPS))
        )

    @staticmethod
    def _roll_likelihood(res, percent: float, tolerance: float) -> float:
        """P(observed % | candidate). Single hit: matching rolls / 16.
        Multi-hit: exact probability mass of matching totals."""
        if res.hits == 1:
            return (
                sum(
                    1
                    for r in res.rolls
                    if abs(100.0 * r / res.defender_hp - percent) <= tolerance
                )
                / 16.0
            )
        return sum(
            p
            for total, p in res.total_distribution().items()
            if abs(100.0 * total / res.defender_hp - percent) <= tolerance
        )

    @staticmethod
    def _ko_likelihood(res, remaining: float, tolerance: float) -> float:
        """P(damage >= the target's remaining HP) — a KO is a censored
        observation: we only learn the damage was at least what was left."""
        thr = remaining - tolerance
        if res.hits == 1:
            return (
                sum(1 for r in res.rolls if 100.0 * r / res.defender_hp >= thr)
                / 16.0
            )
        return sum(
            p
            for total, p in res.total_distribution().items()
            if 100.0 * total / res.defender_hp >= thr
        )

    def observe_damage_dealt(
        self,
        move: str,
        target: PokemonSet,
        percent: float,
        tolerance: float = 1.0,
        ko: bool = False,
        field: FieldState | None = None,
        is_crit: bool = False,
        attacker_boosts: dict[str, int] | None = None,
        defender_boosts: dict[str, int] | None = None,
    ) -> None:
        """The opponent's `move` hit my `target` for `percent` of its max HP.

        ko=True: the hit KO'd my target, and `percent` is the HP it had left —
        we only learn the damage was at least that (censored).
        """
        lik = []
        for cand in self.candidates:
            res = calc_damage(
                cand, target, move,
                field=field, is_crit=is_crit,
                attacker_boosts=attacker_boosts, defender_boosts=defender_boosts,
                ruleset=self.ruleset,
            )
            if ko:
                lik.append(self._ko_likelihood(res, percent, tolerance))
            else:
                lik.append(self._roll_likelihood(res, percent, tolerance))
        what = f"KO'd {target.species} from {percent:.1f}%" if ko else \
            f"dealt {percent:.1f}% to {target.species}"
        self._update(lik, f"{self.species}'s {move} {what}")

    def observe_damage_taken(
        self,
        attacker: PokemonSet,
        move: str,
        percent: float | None = None,
        tolerance: float = 1.0,
        remaining_percent: float | None = None,
        ko: bool = False,
        field: FieldState | None = None,
        is_crit: bool = False,
        attacker_boosts: dict[str, int] | None = None,
        defender_boosts: dict[str, int] | None = None,
    ) -> None:
        """My `attacker` hit the opponent with `move`. Report the hit as one of:

        - percent: damage as % of the opponent's max HP;
        - remaining_percent: where its HP bar ended up (the estimator tracks
          the bar across observations and derives the damage);
        - ko=True: the hit KO'd it — censored, damage >= what was left.
        """
        if ko:
            threshold = self.hp_percent
        elif remaining_percent is not None:
            percent = self.hp_percent - remaining_percent
        elif percent is None:
            raise ValueError("pass percent=, remaining_percent=, or ko=True")

        lik = []
        for cand in self.candidates:
            res = calc_damage(
                attacker, cand, move,
                field=field, is_crit=is_crit,
                attacker_boosts=attacker_boosts, defender_boosts=defender_boosts,
                ruleset=self.ruleset,
            )
            if ko:
                lik.append(self._ko_likelihood(res, threshold, tolerance))
            else:
                lik.append(self._roll_likelihood(res, percent, tolerance))

        if ko:
            desc = f"{attacker.species}'s {move} KO'd {self.species} (from {threshold:.1f}%)"
        else:
            desc = f"{attacker.species}'s {move} dealt {percent:.1f}% to {self.species}"
        self._update(lik, desc)  # raises on inconsistency before we touch the bar
        self.hp_percent = 0.0 if ko else max(0.0, self.hp_percent - percent)

    def set_hp(self, percent: float) -> None:
        """Resync the tracked HP bar (heals, hazards, weather chip, switch-in
        of a previously damaged Pokemon)."""
        self.hp_percent = percent

    def observe_speed(
        self,
        opponent_moved_first: bool,
        reference_speed: int,
        trick_room: bool = False,
        opponent_tailwind: bool = False,
        opponent_paralyzed: bool = False,
        opponent_boost: int = 0,
    ) -> None:
        """Turn-order observation within the same priority bracket.

        reference_speed: my Pokemon's effective speed at the time (after its
        own boosts/item/paralysis — caller-computed for now).
        """
        lik = []
        for cand in self.candidates:
            spe = effective_speed(
                cand,
                boost=opponent_boost,
                tailwind=opponent_tailwind,
                paralyzed=opponent_paralyzed,
                ruleset=self.ruleset,
            )
            if spe == reference_speed:
                lik.append(0.5)  # speed tie: order is a coin flip
            else:
                faster = spe > reference_speed
                if trick_room:
                    faster = not faster
                lik.append(1.0 if faster == opponent_moved_first else 0.0)
        order = "before" if opponent_moved_first else "after"
        self._update(
            lik,
            f"{self.species} moved {order} a reference speed of {reference_speed}"
            f"{' (Trick Room)' if trick_room else ''}",
        )

    def observe_move_order(
        self,
        my_move: str,
        opponent_move: str,
        opponent_moved_first: bool,
        reference_speed: int,
        trick_room: bool = False,
        opponent_tailwind: bool = False,
        opponent_paralyzed: bool = False,
        opponent_boost: int = 0,
    ) -> None:
        """Turn-order observation given both moves used. Priorities come from
        the ruleset's move data: different brackets fully explain the order
        (no speed information — an Aqua Jet going first says nothing about
        Speed); the same bracket delegates to observe_speed.
        """
        rs = load_ruleset(self.ruleset)
        my_prio = rs.get_move(my_move).priority
        opp_prio = rs.get_move(opponent_move).priority
        if opp_prio != my_prio:
            # priority brackets always resolve before speed, even in Trick Room
            if (opp_prio > my_prio) != opponent_moved_first:
                raise InconsistentObservation(
                    f"{opponent_move} (priority {opp_prio}) moved "
                    f"{'before' if opponent_moved_first else 'after'} "
                    f"{my_move} (priority {my_prio}) — order contradicts "
                    "priority; suspect an unmodeled priority effect "
                    "(Prankster, Gale Wings, Quick Claw...)"
                )
            self.log.append(
                Observation(
                    f"{opponent_move} vs {my_move}: order explained by "
                    f"priority ({opp_prio} vs {my_prio}), no speed info",
                    sum(1 for w in self.weights if w > EPS),
                )
            )
            return
        self.observe_speed(
            opponent_moved_first,
            reference_speed,
            trick_room=trick_room,
            opponent_tailwind=opponent_tailwind,
            opponent_paralyzed=opponent_paralyzed,
            opponent_boost=opponent_boost,
        )

    # --- priors ---

    def apply_prior(self, score, description: str = "external prior") -> None:
        """Reweight candidates by `score(candidate) -> float >= 0`. The hook
        for usage-statistics priors and ad-hoc knowledge ('this team is
        Trick Room, discount fast builds')."""
        self._update([max(0.0, score(c)) for c in self.candidates], description)

    # --- queries ---

    def posterior(self) -> list[tuple[PokemonSet, float]]:
        pairs = [(c, w) for c, w in zip(self.candidates, self.weights) if w > EPS]
        return sorted(pairs, key=lambda p: p[1], reverse=True)

    def top_candidates(self, n: int = 10) -> list[tuple[PokemonSet, float]]:
        return self.posterior()[:n]

    def marginal(self, key) -> dict:
        """Posterior marginal of any candidate feature. `key` maps a
        PokemonSet to a hashable value, e.g. lambda c: c.item."""
        out: dict = {}
        for c, w in zip(self.candidates, self.weights):
            k = key(c)
            out[k] = out.get(k, 0.0) + w
        return {k: v for k, v in sorted(out.items(), key=lambda kv: -kv[1]) if v > EPS}

    def stat_summary(self) -> dict:
        """Weighted mean and surviving range of each actual stat, plus the
        item marginal — the 'what are we dealing with' answer."""
        rs = load_ruleset(self.ruleset)
        base = rs.get_species(self.species).base_stats
        summary: dict = {}
        for stat in STAT_KEYS:
            vals = [
                (calc_all_stats(base, c.level, c.nature, c.ivs, c.evs)[stat], w)
                for c, w in zip(self.candidates, self.weights)
                if w > EPS
            ]
            summary[stat] = {
                "mean": sum(v * w for v, w in vals) / sum(w for _, w in vals),
                "min": min(v for v, _ in vals),
                "max": max(v for v, _ in vals),
            }
        summary["item"] = self.marginal(lambda c: c.item)
        summary["ability"] = self.marginal(lambda c: c.ability)
        return summary
