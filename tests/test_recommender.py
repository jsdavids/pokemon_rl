"""Phase 4a: determinized search recommender."""

import random

import pytest

from pokechamp.data.loader import DATA_DIR
from pokechamp.estimator import StatEstimator
from pokechamp.recommender import (
    Recommendation,
    SearchAgent,
    determinize,
    joint_actions,
    recommend,
    state_value,
)
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.sim import (
    Action, Battle, EpsilonAgent, MaxDamageAgent, evaluate,
)

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)


def mon(species, moves, nature="Serious", evs=None, item=None, ability=None):
    return PokemonSet(
        species=species, nature=nature, evs={**stat_table(), **(evs or {})},
        moves=list(moves), item=item, ability=ability,
    )


def chomp(moves=("Dragon Claw", "Earthquake"), **kw):
    return mon("Garchomp", moves, "Jolly", {"atk": 252, "spe": 252}, **kw)


# ------------------------------------------------------------- primitives ---

def test_state_value_symmetry_and_terminals():
    b = Battle([chomp()], [mon("Tyranitar", ["Crunch"])], seed=1)
    assert state_value(b, 0) == pytest.approx(-state_value(b, 1))
    b.sides[1].active(0).hp = 0
    assert state_value(b, 0) == 1.0
    assert state_value(b, 1) == -1.0


def test_joint_actions_filter_double_switch_conflicts():
    team = [chomp(), mon("Azumarill", ["Aqua Jet"]),
            mon("Gholdengo", ["Shadow Ball"])]
    b = Battle(team, [t for t in team], format="doubles", seed=1)
    joints = joint_actions(b, 0)
    assert all(len(j) == 2 for j in joints)
    # only one bench mon: no joint may have both slots switching to it
    assert not any(
        j[0].kind == "switch" and j[1].kind == "switch" for j in joints
    )


# ------------------------------------------------------------- recommend ---

def test_recommend_picks_the_super_effective_move():
    # Dragon Claw is neutral into Tyranitar, Earthquake is 2x. depth=0 makes
    # the value the post-turn HP differential, which separates the two moves
    # (deeper search wins with either and ties them at +1).
    b = Battle([chomp()], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=1)
    rec = recommend(b.snapshot(), n_worlds=2, depth=0, seed=0)
    assert isinstance(rec, Recommendation)
    best = rec.best[0]
    assert best.kind == "move"
    assert best.index == 1  # Earthquake
    scores = dict(rec.ranked)
    assert scores[rec.best] >= max(s for _, s in rec.ranked)


def test_recommend_ranks_every_candidate():
    b = Battle(
        [chomp(), mon("Azumarill", ["Play Rough"])],
        [mon("Tyranitar", ["Crunch"])],
        seed=1,
    )
    rec = recommend(b.snapshot(), n_worlds=1, depth=0, seed=0)
    kinds = {(j[0].kind, j[0].index) for j, _ in rec.ranked}
    # both moves and the switch were scored
    assert ("move", 0) in kinds and ("move", 1) in kinds
    assert ("switch", 1) in kinds
    # scores are sorted best-first
    values = [s for _, s in rec.ranked]
    assert values == sorted(values, reverse=True)


def test_recommend_doubles_returns_one_action_per_slot():
    team = [chomp(("Earthquake",)), mon("Azumarill", ["Aqua Jet"]),
            mon("Gholdengo", ["Shadow Ball"])]
    b = Battle(team, [t for t in team], format="doubles", seed=1)
    rec = recommend(b.snapshot(), n_worlds=1, depth=0, max_candidates=8, seed=0)
    assert len(rec.best) == 2
    assert len(rec.ranked) <= 8


def test_budget_mode_concentrates_pulls_on_contenders():
    b = Battle([chomp()], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=1)
    uniform = recommend(b.snapshot(), n_worlds=2, depth=0, seed=0)
    budgeted = recommend(b.snapshot(), depth=0, budget=12, seed=0)
    assert budgeted.best == uniform.best  # same clear-cut answer
    assert sum(budgeted.pulls.values()) == 12
    # every candidate got its floor; the winner got more than the floor
    assert all(n >= 2 for n in budgeted.pulls.values())
    assert budgeted.pulls[budgeted.best] > 2


def test_epsilon_agent_extremes():
    b = Battle([chomp()], [mon("Tyranitar", ["Crunch"], evs={"hp": 252})], seed=1)
    greedy = MaxDamageAgent()
    assert EpsilonAgent(greedy, epsilon=0.0).choose(b, 0) == greedy.choose(b, 0)
    legal = b.legal_actions(0, 0)
    for trial in range(5):
        acts = EpsilonAgent(greedy, epsilon=1.0, seed=trial).choose(b, 0)
        assert all(a in legal for a in acts)


# --------------------------------------------------------- determinization ---

def test_determinize_keeps_observations_and_swaps_the_build():
    opp = mon("Tyranitar", ["Crunch"])
    b = Battle([chomp()], [opp], seed=1)
    tar = b.sides[1].active(0)
    tar.hp = tar.max_hp // 3
    tar.status = "brn"
    tar.boosts["atk"] = 2
    tar.taunt_turns = 2
    tar.choice_lock = 0  # move-index volatile: must not survive a resample
    state = b.snapshot()

    est = StatEstimator("Tyranitar")
    world = determinize(
        state, {0: est}, random.Random(0), revealed_moves={0: ["Crunch"]}
    )
    ps = world.sides[1].pokemon[0]
    assert ps.hp_fraction == pytest.approx(tar.hp / tar.max_hp)
    assert ps.status == "brn"
    assert ps.boosts["atk"] == 2
    assert ps.volatiles.get("taunt_turns") == 2
    assert "choice_lock" not in ps.volatiles
    assert "Crunch" in ps.set.moves and len(ps.set.moves) == 4
    # the sampled build is one of the estimator's candidates
    assert any(
        c.nature == ps.set.nature and c.evs == ps.set.evs
        and c.item == ps.set.item and c.ability == ps.set.ability
        for c in est.candidates
    )
    # the original snapshot was not mutated
    assert state.sides[1].pokemon[0].set.moves == ["Crunch"]


def test_recommend_with_estimator_worlds_runs_end_to_end():
    b = Battle([chomp()], [mon("Tyranitar", ["Crunch"])], seed=1)
    est = StatEstimator("Tyranitar")
    rec = recommend(
        b.snapshot(), estimators={0: est}, revealed_moves={0: ["Crunch"]},
        n_worlds=3, depth=1, seed=0,
    )
    assert rec.best[0].kind == "move"
    assert rec.best[0].index == 1  # Earthquake regardless of the sampled build


# -------------------------------------------------------------- the agent ---

# Mirror teams so only decision quality separates the agents (evaluate
# already alternates sides; identical teams remove matchup luck entirely).
MIRROR_TEAM = [
    chomp(("Dragon Claw", "Earthquake", "Swords Dance")),
    mon("Azumarill", ["Play Rough", "Aqua Jet"], "Adamant",
        {"hp": 252, "atk": 252}),
    mon("Gholdengo", ["Shadow Ball", "Recover"], "Timid",
        {"spa": 252, "spe": 252}),
]


def test_search_agent_beats_max_damage():
    res = evaluate(
        SearchAgent(depth=2, n_worlds=4, seed=7),
        MaxDamageAgent(),
        MIRROR_TEAM, [t for t in MIRROR_TEAM], games=16, seed=100,
    )
    assert res.wins > res.losses, res


def test_search_agent_beats_max_damage_doubles():
    team = MIRROR_TEAM + [
        mon("Tyranitar", ["Crunch", "Stone Edge"], "Adamant",
            {"hp": 252, "atk": 252}),
    ]
    # wide cap + UCB budget: screening admits noisy candidates on ~100-joint
    # turns, and the budget reallocates evaluations away from the junk
    # (uniform averaging over the same cap loses to MaxDamage)
    res = evaluate(
        SearchAgent(depth=1, max_candidates=24, budget=48, seed=7),
        MaxDamageAgent(),
        team, [t for t in team], games=8, seed=0, format="doubles",
    )
    assert res.wins > res.losses, res


def test_determinization_resamples_the_other_side_from_side_1():
    """Searching from side 1, the estimators describe side 0's team; worlds
    used to resample side 1 (our own team) because the opponent index was
    hard-wired."""
    from pokechamp.recommender.search import _Searcher
    b = Battle([chomp()], [mon("Tyranitar", ["Crunch"])], seed=1)
    s = _Searcher(b.snapshot(), 1, {0: StatEstimator("Garchomp")}, None, 0,
                  "champions", MaxDamageAgent(), MaxDamageAgent())
    world, _ = s.world(0)
    assert world.sides[1].pokemon[0].set.species == "Tyranitar"
    assert world.sides[1].pokemon[0].set.moves == ["Crunch"]  # untouched
    assert world.sides[0].pokemon[0].set.species == "Garchomp"
