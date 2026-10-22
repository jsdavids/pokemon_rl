"""Usage-based priors: the prior math, usage-weighted rollout movesets,
scout integration, and (when fetched) the real usage file."""

import random
from collections import Counter

import pytest

from pokechamp.data.loader import DATA_DIR, load_ruleset, norm
from pokechamp.estimator import StatEstimator
from pokechamp.estimator.candidates import NATURES
from pokechamp.estimator.usage import SpeciesUsage, load_usage
from pokechamp.session import scout as scout_mod
from pokechamp.session.scout import OpponentScout
from pokechamp.sim.determinize import fill_moves, sample_opponent
from pokechamp.sim.effects import STATUS_MOVES
from pokechamp.stats import NATURES as ALL_NATURES

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)

REG = "regulation-m-c"


def azu_usage(samples=2000, **kw):
    base = dict(
        species="Azumarill", regulation=REG, source="limitless",
        samples=samples, usage=0.1,
        items={"Sitrus Berry": 0.55, "Choice Scarf": 0.25, "Life Orb": 0.15},
        abilities={"Huge Power": 0.96, "Thick Fat": 0.04},
        natures={"Adamant": 0.75, "Brave": 0.2, "Jolly": 0.05},
        moves={"Aqua Jet": 0.95, "Play Rough": 0.9, "Protect": 0.6,
               "Belly Drum": 0.5, "Liquidation": 0.4},
        teammates={"Incineroar": 0.3},
    )
    base.update(kw)
    return SpeciesUsage(**base)


def primed(usage):
    from pokechamp.estimator.candidates import default_candidates
    cands = default_candidates("Azumarill", ruleset="champions", regulation=REG,
                               extra_natures=usage.extra_natures())
    est = StatEstimator("Azumarill", candidates=cands)
    est.apply_prior(usage.prior(est.candidates), usage.description)
    return est


# ------------------------------------------------------------ prior math ---

def test_extra_natures_join_the_grid():
    u = azu_usage()
    assert u.extra_natures() == ["Brave"]  # Adamant/Jolly already in NATURES
    est = primed(u)
    assert {c.nature for c in est.candidates} == set(NATURES) | {"Brave"}


def test_prior_follows_usage_shares():
    est = primed(azu_usage())
    ab = est.marginal(lambda c: c.ability)
    assert ab["Huge Power"] == pytest.approx(0.96 * 0.95 + 0.05 / 2, abs=0.01)
    nat = est.marginal(lambda c: c.nature)
    assert max(nat, key=nat.get) == "Adamant"
    items = est.marginal(lambda c: c.item)
    # None = every item the grid doesn't model (here Sitrus, 55%)
    assert max(items, key=items.get) is None
    assert items["Choice Scarf"] > items["Life Orb"] > items.get("Expert Belt", 0)


def test_prior_never_vetoes():
    """An off-meta build is unlikely, not impossible: every candidate keeps
    weight, so damage/speed evidence can still overturn the prior."""
    est = primed(azu_usage())
    assert min(est.weights) > 0
    assert est.marginal(lambda c: c.nature)["Bold"] > 0  # 0% in usage


def test_thin_samples_lean_uniform():
    thick = primed(azu_usage(samples=2000)).marginal(lambda c: c.ability)
    thin = primed(azu_usage(samples=3)).marginal(lambda c: c.ability)
    assert thin["Huge Power"] < thick["Huge Power"]
    # 3 sets barely move it off uniform (Azumarill has 3 abilities here)
    assert thin["Thick Fat"] > 3 * thick["Thick Fat"]


def test_evidence_overturns_the_prior():
    """Usage says Adamant (~75%); outspeeding a mark that only a Jolly or
    Scarf set can reach must eliminate every non-Scarf Adamant anyway."""
    from pokechamp.estimator.estimator import effective_speed
    est = primed(azu_usage())
    assert est.marginal(lambda c: c.nature)["Adamant"] > 0.5
    fastest_adamant = max(
        effective_speed(c) for c in est.candidates
        if c.nature == "Adamant" and c.item != "Choice Scarf"
    )
    est.observe_speed(opponent_moved_first=True,
                      reference_speed=fastest_adamant + 1)
    slow_adamant = est.marginal(
        lambda c: c.nature == "Adamant" and c.item != "Choice Scarf")
    assert slow_adamant.get(True, 0.0) == 0.0
    assert est.marginal(lambda c: c.nature).get("Jolly", 0) > 0


# ------------------------------------------------------- rollout movesets ---

def test_fill_moves_follows_usage_weights():
    u = azu_usage()
    rng = random.Random(0)
    counts = Counter()
    for _ in range(400):
        moves = fill_moves("Azumarill", rng, ["Liquidation"], weights=u.moves)
        assert moves[0] == "Liquidation" and len(moves) == 4
        assert len(set(moves)) == 4
        counts.update(moves[1:])
    assert counts["Aqua Jet"] > counts["Belly Drum"]
    learnset = load_ruleset("champions").get_species("Azumarill").learnset
    assert all(norm(m) in learnset for m in counts)


def test_fill_moves_skips_unmodeled_status_moves():
    assert "Belly Drum" not in STATUS_MOVES  # dead slot in the sim
    moves = fill_moves("Azumarill", random.Random(1), weights={"Belly Drum": 1.0})
    assert "Belly Drum" not in moves and len(moves) == 4


def test_sample_opponent_uses_estimator_usage():
    est = primed(azu_usage())
    est.usage = azu_usage()
    rng = random.Random(3)
    seen = Counter(m for _ in range(200) for m in sample_opponent(est, rng).moves)
    assert seen["Aqua Jet"] > 150  # 95% usage, nearly always drawn


# -------------------------------------------------------------- the scout ---

def test_scout_applies_usage_prior(tmp_path, monkeypatch):
    monkeypatch.setattr(scout_mod, "usage_for",
                        lambda sp, rs, reg: azu_usage() if sp == "Azumarill" else None)
    sc = OpponentScout(tmp_path / "s.json", regulation=REG)
    sc.add_pokemon("rival", "Azumarill")
    est = sc.estimator("rival", "Azumarill")
    assert est.usage is not None
    assert est.log[0].description.startswith("usage prior")
    summ = sc.summary("rival", "Azumarill")
    assert summ["meta"]["abilities"]["Huge Power"] == 0.96
    assert summ["ability"]["Huge Power"] > 0.9
    # no regulation (or use_usage=False): the old uniform behavior
    plain = OpponentScout(tmp_path / "p.json", regulation=REG, use_usage=False)
    plain.add_pokemon("rival", "Azumarill")
    assert plain.estimator("rival", "Azumarill").usage is None


def test_revealed_ability_still_overrides_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(scout_mod, "usage_for", lambda sp, rs, reg: azu_usage())
    sc = OpponentScout(tmp_path / "s.json", regulation=REG)
    sc.reveal_ability("rival", "Azumarill", "Thick Fat")
    assert sc.summary("rival", "Azumarill")["ability"] == {
        "Thick Fat": pytest.approx(1.0)}


# --------------------------------------------------------- the real file ---

USAGE_FILE = DATA_DIR / "champions" / "usage" / f"{REG}.json"


@pytest.mark.skipif(not USAGE_FILE.exists(), reason="usage not fetched yet")
def test_fetched_usage_is_sane():
    rs = load_ruleset("champions")
    usage = load_usage("champions", REG)
    assert len(usage) >= 50
    for name, u in usage.items():
        assert rs.get_species(name).name == name  # keyed by OUR names
        for shares in (u.items, u.abilities, u.natures, u.moves):
            assert all(0.0 <= v <= 1.0 for v in shares.values()), name
        # free-text sheets once leaked 'relaxed' into a candidate grid
        assert set(u.natures) <= set(ALL_NATURES), name
    # Megas resolved from the held stone, with their own entry
    megas = [n for n in usage if n.startswith("Mega ")]
    assert megas
    for m in megas:
        stones = [i for i in usage[m].items if rs.items.get(i, {}).get("mega_stone")]
        assert stones, m
