"""Session tools: TeamStore, OpponentScout, lead assist."""

import pytest

from pokechamp.data.loader import DATA_DIR
from pokechamp.estimator import StatEstimator
from pokechamp.recommender import recommend
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.session import (
    OpponentScout,
    TeamStore,
    recommend_leads,
    validate_set,
)
from pokechamp.sim import Battle

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)


def mon(species, moves, nature="Serious", evs=None, item=None, ability=None):
    return PokemonSet(
        species=species, nature=nature, evs={**stat_table(), **(evs or {})},
        moves=list(moves), item=item, ability=ability,
    )


def chomp(**kw):
    return mon("Garchomp", ["Dragon Claw", "Earthquake"], "Jolly",
               {"atk": 252, "spe": 252}, **kw)


TEAM = [
    chomp(),
    mon("Azumarill", ["Play Rough", "Aqua Jet"], "Adamant",
        {"hp": 252, "atk": 252}),
    mon("Gholdengo", ["Shadow Ball", "Recover"], "Timid",
        {"spa": 252, "spe": 252}),
]


# --------------------------------------------------------------- TeamStore ---

def test_team_store_roundtrip_and_versions(tmp_path):
    store = TeamStore(tmp_path / "teams.json")
    assert store.save("ladder", TEAM) == 0
    v1 = [chomp(item="Choice Scarf"), TEAM[1], TEAM[2]]
    assert store.save("ladder", v1) == 1

    latest = store.get("ladder")
    assert latest[0].item == "Choice Scarf"
    original = store.get("ladder", version=0)
    assert original[0].item is None
    assert original[0].evs == TEAM[0].evs

    meta = store.versions("ladder")
    assert len(meta) == 2 and meta[0]["species"][0] == "Garchomp"
    assert store.names() == ["ladder"]

    # persistence: a fresh instance reads the same file
    again = TeamStore(tmp_path / "teams.json")
    assert again.get("ladder")[0].item == "Choice Scarf"
    again.delete("ladder")
    assert again.names() == []


def test_team_store_validation_catches_problems(tmp_path):
    store = TeamStore(tmp_path / "teams.json")
    with pytest.raises(ValueError, match="unknown species"):
        store.save("bad", [mon("Missingno", ["Tackle"])])
    with pytest.raises(ValueError, match="can't learn"):
        store.save("bad", [mon("Azumarill", ["Earthquake"])])
    with pytest.raises(ValueError, match="stat points"):  # 96 SP > 66
        store.save("bad", [mon("Garchomp", ["Earthquake"],
                               evs={"hp": 252, "atk": 252, "spe": 252})])
    # 32/32/2 SP is legal in Champions though it's 516 EVs (> mainline 508)
    assert validate_set(mon("Garchomp", ["Earthquake"],
                            evs={"atk": 252, "spe": 252, "hp": 12})) == []
    with pytest.raises(ValueError, match="item"):
        store.save("bad", [chomp(item="Choice Band")])  # not in champions!
    with pytest.raises(ValueError, match="ability"):
        store.save("bad", [chomp(ability="Huge Power")])
    # the escape hatch still works
    store.save("theory", [chomp(ability="Huge Power")], validate=False)
    assert validate_set(chomp()) == []


# ----------------------------------------------------------- OpponentScout ---

def test_scout_revealed_facts_filter_the_posterior(tmp_path):
    scout = OpponentScout(tmp_path / "scout.json")
    scout.add_pokemon("rival", "Garchomp")
    scout.reveal_move("rival", "Garchomp", "Earthquake")
    scout.reveal_item("rival", "Garchomp", "Choice Scarf")
    scout.reveal_ability("rival", "Garchomp", "Rough Skin")

    est = scout.estimator("rival", "Garchomp")
    assert all(c.item == "Choice Scarf" for c, _ in est.posterior())
    assert all(c.ability == "Rough Skin" for c, _ in est.posterior())

    with pytest.raises(ValueError, match="can't learn"):
        scout.reveal_move("rival", "Garchomp", "Recover")
    with pytest.raises(ValueError, match="ability"):
        scout.reveal_ability("rival", "Garchomp", "Huge Power")


def test_scout_observations_replay_identically(tmp_path):
    path = tmp_path / "scout.json"
    scout = OpponentScout(path)
    scout.add_pokemon("rival", "Azumarill")
    target = chomp()
    scout.observe(
        "rival", "Azumarill", "damage_taken",
        attacker=target, move="Earthquake", remaining_percent=55.0,
        tolerance=3.0,
    )
    scout.observe("rival", "Azumarill", "set_hp", percent=55.0)
    live = scout.estimator("rival", "Azumarill")

    # a fresh process: same file, estimator rebuilt from the log
    reloaded = OpponentScout(path).estimator("rival", "Azumarill")
    assert reloaded.hp_percent == live.hp_percent
    assert [w for _, w in reloaded.posterior()] == pytest.approx(
        [w for _, w in live.posterior()]
    )
    # and the evidence actually moved the posterior
    fresh = StatEstimator("Azumarill")
    assert len(live.posterior()) < len(fresh.posterior())


def test_scout_feeds_the_recommender(tmp_path):
    scout = OpponentScout(tmp_path / "scout.json")
    scout.add_pokemon("rival", "Tyranitar")
    scout.reveal_move("rival", "Tyranitar", "Crunch")
    ests, revealed = scout.for_recommend("rival")
    assert set(ests) == {0} and revealed == {0: ["Crunch"]}

    b = Battle(TEAM, [mon("Tyranitar", ["Crunch"])], seed=1)
    rec = recommend(
        b.snapshot(), estimators=ests, revealed_moves=revealed,
        n_worlds=2, depth=0, seed=0,
    )
    assert rec.best[0].kind == "move"

    summary = scout.summary("rival", "Tyranitar")
    assert summary["revealed_moves"] == ["Crunch"]
    assert "spe" in summary and "item" in summary


# ------------------------------------------------------------- lead assist ---

def test_lead_assist_ranks_leads_by_outcome():
    # a hard matchup where the lead choice genuinely moves the win rate
    # (all options tie in lopsided matchups and the ranking is meaningless)
    mine = [
        mon("Alakazam", ["Psychic", "Shadow Ball"], "Timid",
            {"spa": 252, "spe": 252}),
        mon("Azumarill", ["Play Rough", "Aqua Jet"], "Adamant",
            {"hp": 252, "atk": 252}),
        mon("Dragapult", ["Dragon Claw", "Phantom Force"], "Jolly",
            {"atk": 252, "spe": 252}),
    ]
    darks = [
        mon("Tyranitar", ["Crunch", "Stone Edge"], "Adamant",
            {"hp": 252, "atk": 252}),
        mon("Hydreigon", ["Crunch", "Dragon Pulse"], "Timid",
            {"spa": 252, "spe": 252}),
        mon("Gholdengo", ["Shadow Ball", "Thunderbolt"], "Timid",
            {"spa": 252, "spe": 252}),
    ]
    plan = recommend_leads(mine, darks, games=10, seed=0)
    assert len(plan.ranked) == 3  # bring all 3, choose the lead
    assert plan.best.win_rate > plan.ranked[-1].win_rate  # discriminates
    assert plan.best.species[0] == "Dragapult"
    # deterministic: same seed, same ranking
    again = recommend_leads(mine, darks, games=10, seed=0)
    assert [(o.bring, o.win_rate) for o in again.ranked] == \
        [(o.bring, o.win_rate) for o in plan.ranked]


def test_lead_assist_accepts_estimators_and_doubles():
    opp = [
        StatEstimator("Garchomp"),
        mon("Tyranitar", ["Crunch", "Stone Edge"], "Adamant",
            {"hp": 252, "atk": 252}),
        StatEstimator("Alakazam"),
        mon("Politoed", ["Surf", "Ice Beam"], "Modest", {"spa": 252}),
    ]
    team4 = TEAM + [mon("Tyranitar", ["Crunch", "Stone Edge"], "Adamant",
                        {"hp": 252, "atk": 252})]
    plan = recommend_leads(
        team4, opp, format="doubles", games=4, seed=2,
        opp_revealed_moves={0: ["Earthquake"]},
    )
    assert len(plan.best.leads) == 2
    assert len(plan.best.bring) == 4
    assert all(0.0 <= o.win_rate <= 1.0 for o in plan.ranked)
    # C(4,4) * C(4,2) = 6 options for a 4-mon team
    assert len(plan.ranked) == 6
