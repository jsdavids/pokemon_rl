"""Phase 4a: determinized search recommender.

Imperfect information is handled by determinization: sample concrete worlds
(opponent builds drawn from StatEstimator posteriors), then treat each world
as perfect-information and search it. A candidate action's value is its mean
outcome across the worlds it was evaluated in.

Search is 1-ply expectation + rollout: for each candidate joint action (one
Action per active slot), step one turn against a modeled opponent, play
`depth` further turns with rollout policies, and score the resulting state
(+1 win / -1 loss, else team-HP differential). World k is shared by every
candidate's k-th evaluation (common random numbers), so candidates are
compared on identical opponent builds and chance outcomes — at 1 world
without CRN the recommender lost to MaxDamage on crit noise alone.

Candidate control on wide turns (doubles products run ~100 joints):
- screening: a depth-0 pass in one world keeps the `max_candidates` best
- budget mode: instead of evaluating every survivor in every world, UCB1
  spends a fixed number of pulls where the value estimates want them
  (flat Monte Carlo search; 4b will grow this into guided MCTS).
"""

from __future__ import annotations

import math
import random
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product

from pokechamp.estimator import StatEstimator
from pokechamp.schemas import BattleState, PokemonSnapshot
from pokechamp.sim.agents import Agent, MaxDamageAgent
from pokechamp.sim.battle import Action, Battle
from pokechamp.sim.determinize import sample_opponent

# Volatiles that survive a moveset resample. Index-based ones (choice_lock,
# encore/disabled index) point into a moveset we just replaced; transform
# state carries a whole copied build. Both die with the old set.
_INDEX_FREE_VOLATILES = (
    "taunt_turns", "confusion_turns", "trapped_turns", "trap_denom",
    "crit_stage",
    "perish_count", "destiny_bond",
)

UCB_MIN_PULLS = 2  # every candidate gets this many worlds before UCB kicks in
UCB_C = 0.7  # exploration constant, scaled to values living in [-1, 1]


def state_value(battle: Battle, side_idx: int) -> float:
    """Position score in [-1, 1] from `side_idx`'s seat: decided games are
    +/-1, everything else the team-HP-fraction differential."""
    w = battle.winner()
    if w is not None:
        return 1.0 if w == side_idx else -1.0
    if battle.is_over:
        return 0.0

    def health(side) -> float:
        return sum(p.hp_fraction for p in side.team) / len(side.team)

    return health(battle.sides[side_idx]) - health(battle.sides[1 - side_idx])


def joint_actions(battle: Battle, side_idx: int) -> list[tuple[Action, ...]]:
    """Every legal per-slot combination (the cartesian product in doubles),
    minus combos where two slots claim the same bench Pokemon."""
    per_slot = [
        battle.legal_actions(side_idx, slot) for slot in range(battle.n_active)
    ]
    joints = []
    for combo in product(*per_slot):
        switched = [a.index for a in combo if a.kind == "switch"]
        if len(switched) != len(set(switched)):
            continue
        joints.append(combo)
    return joints


def determinize(
    state: BattleState,
    estimators: dict[int, StatEstimator],
    rng: random.Random,
    revealed_moves: dict[int, list[str]] | None = None,
    opponent: int = 1,
) -> BattleState:
    """One concrete world: the opponent snapshots named in `estimators`
    (keyed by team index) get their sets replaced by a posterior sample with
    a plausible moveset. Observed battle state (HP bar, status, boosts,
    substitute) is kept; move-index volatiles and PP are reset because they
    referred to the old moveset."""
    state = deepcopy(state)
    team = state.sides[opponent].pokemon
    for idx, est in estimators.items():
        old = team[idx]
        build = sample_opponent(
            est, rng, (revealed_moves or {}).get(idx)
        )
        team[idx] = PokemonSnapshot(
            set=build,
            hp_fraction=old.hp_fraction,
            status=old.status,
            boosts=dict(old.boosts),
            pp=None,
            sub_hp=old.sub_hp,
            volatiles={
                k: v for k, v in old.volatiles.items()
                if k in _INDEX_FREE_VOLATILES
            },
        )
    return state


@dataclass
class Recommendation:
    """Ranked joint actions (one Action per slot), best first. `pulls` is
    how many worlds each joint was evaluated in (uniform unless a budget
    concentrated them)."""

    ranked: list[tuple[tuple[Action, ...], float]]
    pulls: dict[tuple[Action, ...], int] = field(default_factory=dict)

    @property
    def best(self) -> tuple[Action, ...]:
        return self.ranked[0][0]

    @property
    def score(self) -> float:
        return self.ranked[0][1]

    def __repr__(self) -> str:
        top = ", ".join(f"{list(j)}={s:+.3f}" for j, s in self.ranked[:3])
        return f"<Recommendation {top}>"


class _Searcher:
    """One recommend() call's working state: the lazily-grown world list and
    the pull bookkeeping shared by screening, uniform, and budget modes."""

    def __init__(
        self,
        state: BattleState,
        side_idx: int,
        estimators: dict[int, StatEstimator] | None,
        revealed_moves: dict[int, list[str]] | None,
        seed: int,
        ruleset: str,
        opp_model: Agent,
        rollout: Agent,
        value_fn=None,
    ):
        self.state = state
        self.side_idx = side_idx
        self.estimators = estimators
        self.revealed_moves = revealed_moves
        self.seed = seed
        self.ruleset = ruleset
        self.opp_model = opp_model
        self.rollout = rollout
        # non-terminal leaf evaluator (battle, side_idx) -> float; decided
        # games always score +/-1 regardless (a learned net plugs in here)
        self.value_fn = value_fn or state_value
        self.rng = random.Random(seed)
        self._worlds: list[tuple[BattleState, int]] = []

    def world(self, k: int) -> tuple[BattleState, int]:
        while len(self._worlds) <= k:
            i = len(self._worlds)
            ws = (
                # opponent = the OTHER side (was hard-wired to side 1, which
                # resampled our own team when searching from side 1)
                determinize(self.state, self.estimators, self.rng,
                            self.revealed_moves, opponent=1 - self.side_idx)
                if self.estimators
                else self.state
            )
            self._worlds.append((ws, self.seed + i))
        return self._worlds[k]

    def pull(self, joint: tuple[Action, ...], k: int, depth: int) -> float:
        """Evaluate one candidate in world k."""
        world_state, seed = self.world(k)
        b = Battle.from_state(world_state, ruleset=self.ruleset, seed=seed)
        if b.is_over:
            return state_value(b, self.side_idx)
        opp = self.opp_model.choose(b, 1 - self.side_idx)
        ours = list(joint)
        b.step(*((ours, opp) if self.side_idx == 0 else (opp, ours)))
        for _ in range(depth):
            if b.is_over:
                break
            b.step(self.rollout.choose(b, 0), self.rollout.choose(b, 1))
        if b.is_over:
            return state_value(b, self.side_idx)
        return self.value_fn(b, self.side_idx)

    def screen(
        self, joints: list[tuple[Action, ...]], cap: int
    ) -> list[tuple[Action, ...]]:
        """Depth-0 pass in one shared world; keep the `cap` best. One real
        (if shallow) simulation per joint, so status moves and switches
        compete on outcome rather than on a damage heuristic."""
        if len(joints) <= cap:
            return joints
        scored = sorted(
            joints, key=lambda j: self.pull(j, 0, depth=0), reverse=True
        )
        return scored[:cap]

    def score(
        self,
        candidates: list[tuple[Action, ...]],
        n_worlds: int,
        depth: int,
        budget: int | None,
    ) -> Recommendation:
        counts = {j: 0 for j in candidates}
        sums = {j: 0.0 for j in candidates}

        def do_pull(j: tuple[Action, ...]) -> None:
            sums[j] += self.pull(j, counts[j], depth)
            counts[j] += 1

        if budget is None or len(candidates) <= 1:
            for _ in range(n_worlds):
                for j in candidates:
                    do_pull(j)
        else:
            budget = max(budget, UCB_MIN_PULLS * len(candidates))
            total = 0
            while total < budget:
                fresh = [j for j in candidates if counts[j] < UCB_MIN_PULLS]
                if fresh:
                    j = fresh[0]
                else:
                    j = max(
                        candidates,
                        key=lambda c: sums[c] / counts[c]
                        + UCB_C * math.sqrt(math.log(total) / counts[c]),
                    )
                do_pull(j)
                total += 1

        ranked = sorted(
            ((j, sums[j] / counts[j]) for j in candidates),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return Recommendation(ranked, pulls=dict(counts))


def recommend(
    state: BattleState,
    estimators: dict[int, StatEstimator] | None = None,
    revealed_moves: dict[int, list[str]] | None = None,
    side: int = 0,
    n_worlds: int = 4,
    depth: int = 2,
    max_candidates: int = 16,
    budget: int | None = None,
    seed: int = 0,
    ruleset: str = "champions",
    opp_model: Agent | None = None,
    rollout: Agent | None = None,
    value_fn=None,
) -> Recommendation:
    """Rank `side`'s joint actions for the current turn.

    With `estimators` (opponent team index -> StatEstimator), each world
    draws that Pokemon's build from the posterior; without them the snapshot
    is taken at face value and worlds differ only in chance outcomes.

    `budget=None` evaluates every candidate in every world; an int spends
    that many total evaluations UCB1-style, concentrating on the contenders
    (worth it once screening lets many candidates through).

    `value_fn(battle, side) -> float` replaces the HP-differential score at
    non-terminal leaves (e.g. the 4b value net); decided games stay +/-1.
    """
    searcher = _Searcher(
        state, side, estimators, revealed_moves, seed, ruleset,
        opp_model or MaxDamageAgent(), rollout or MaxDamageAgent(),
        value_fn=value_fn,
    )
    probe = Battle.from_state(state, ruleset=ruleset, seed=seed)
    candidates = searcher.screen(joint_actions(probe, side), max_candidates)
    return searcher.score(candidates, n_worlds, depth, budget)


class SearchAgent(Agent):
    """The recommender wearing an Agent interface, for the eval harness.

    Perfect information (it snapshots the real battle, no estimators), so
    this measures the search itself: worlds are chance determinizations and
    the opponent is modeled by `opp_model` (MaxDamage by default)."""

    def __init__(
        self,
        depth: int = 2,
        n_worlds: int = 4,
        max_candidates: int = 16,
        budget: int | None = None,
        seed: int = 0,
        opp_model: Agent | None = None,
        rollout: Agent | None = None,
        value_fn=None,
    ):
        self.depth = depth
        self.n_worlds = n_worlds
        self.max_candidates = max_candidates
        self.budget = budget
        self.rng = random.Random(seed)
        self.opp_model = opp_model or MaxDamageAgent()
        self.rollout = rollout or MaxDamageAgent()
        self.value_fn = value_fn

    def choose(self, battle: Battle, side_idx: int) -> list[Action]:
        rec = recommend(
            battle.snapshot(),
            side=side_idx,
            n_worlds=self.n_worlds,
            depth=self.depth,
            max_candidates=self.max_candidates,
            budget=self.budget,
            seed=self.rng.randrange(1 << 30),
            ruleset=battle.ruleset,
            opp_model=self.opp_model,
            rollout=self.rollout,
            value_fn=self.value_fn,
        )
        return list(rec.best)
