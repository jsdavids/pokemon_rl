"""Phase 4b: action space, feature encoding, masked policy, PPO smoke."""

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from pokechamp.data.loader import DATA_DIR
from pokechamp.rl import (
    DEFAULT_IDX,
    OBS_DIM,
    PPOConfig,
    PPOTrainer,
    PolicyAgent,
    PolicyNet,
    SLOT_ACTION_DIM,
    action_table,
    encode,
    forbid_switch_conflict,
    sample_turn,
    slot_tables,
)
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.sim import Action, Battle, MaxDamageAgent, RandomAgent, play_game

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


TEAM = [
    chomp(),
    mon("Azumarill", ["Play Rough", "Aqua Jet"], "Adamant", {"atk": 252}),
    mon("Gholdengo", ["Shadow Ball", "Recover"], "Timid", {"spa": 252}),
]


# ---------------------------------------------------------------- encoding ---

def test_encode_shape_range_determinism():
    b = Battle(TEAM, [t for t in TEAM], seed=1)
    obs = encode(b, 0)
    assert obs.shape == (OBS_DIM,)
    assert obs.dtype == np.float32
    assert np.all(obs >= 0.0) and np.all(obs <= 1.5)
    assert np.array_equal(obs, encode(b, 0))
    # mirror teams encode identically from either seat...
    assert np.array_equal(obs, encode(b, 1))
    # ...until the sides actually differ
    b.sides[1].active(0).hp //= 2
    assert not np.array_equal(encode(b, 0), encode(b, 1))


def test_encode_reflects_battle_state():
    b = Battle(TEAM, [t for t in TEAM], seed=1)
    base = encode(b, 0)
    b.sides[0].active(0).hp //= 2
    b.sides[0].active(0).boosts["atk"] = 4
    changed = encode(b, 0)
    assert not np.array_equal(base, changed)


# ------------------------------------------------------------ action space ---

def test_action_table_matches_legal_actions():
    b = Battle(TEAM, [t for t in TEAM], format="doubles", seed=1)
    for side in (0, 1):
        for slot in (0, 1):
            mask, table = action_table(b, side, slot)
            legal = b.legal_actions(side, slot)
            # bijective up to encoding: every legal action appears once
            assert sorted(table.values(), key=repr) == sorted(legal, key=repr)
            assert set(np.nonzero(mask)[0]) == set(table)


def test_single_target_moves_get_two_target_indices_in_doubles():
    b = Battle(TEAM, [t for t in TEAM], format="doubles", seed=1)
    _, table = action_table(b, 0, 0)  # Garchomp: Dragon Claw is "normal"
    assert table[0] == Action("move", 0, target=0)
    assert table[1] == Action("move", 0, target=1)
    # Earthquake is allAdjacent: single index, no target choice
    assert table[2] == Action("move", 1)
    assert 3 not in table


def test_forbid_switch_conflict():
    b = Battle(TEAM + [mon("Tyranitar", ["Crunch"])],
               [t for t in TEAM], format="doubles", seed=1)
    masks, tables = slot_tables(b, 0)
    switch_idx = next(
        i for i, a in tables[0].items() if a.kind == "switch"
    )
    adjusted = forbid_switch_conflict(masks, switch_idx)
    assert masks[1][switch_idx] == 1.0
    assert adjusted[switch_idx] == 0.0


def test_dead_slot_in_singles_only_passes():
    b = Battle(TEAM, [t for t in TEAM], seed=1)
    masks, tables = slot_tables(b, 0)
    assert masks[1].sum() == 1.0
    assert tables[1][DEFAULT_IDX] == Action("pass")


# ------------------------------------------------------------------ policy ---

def test_sample_turn_emits_legal_actions():
    torch.manual_seed(0)
    net = PolicyNet(hidden=32)
    b = Battle(TEAM, [t for t in TEAM], format="doubles", seed=1)
    for _ in range(10):
        actions, obs, idxs, masks, logprob, value = sample_turn(net, b, 0)
        legal0 = b.legal_actions(0, 0)
        legal1 = b.legal_actions(0, 1)
        assert actions[0] in legal0
        assert actions[1] in legal1 or actions[1] == Action("pass")
        assert obs.shape == (OBS_DIM,)
        assert idxs.shape == (2,) and masks.shape == (2, SLOT_ACTION_DIM)
        assert logprob <= 0.0
        assert np.isfinite(value)


def test_policy_agent_completes_games():
    torch.manual_seed(0)
    net = PolicyNet(hidden=32)
    for fmt in ("singles", "doubles"):
        w = play_game(
            PolicyAgent(net, greedy=False), RandomAgent(seed=1),
            TEAM, [t for t in TEAM], seed=3, format=fmt,
        )
        assert w in (0, 1, None)


# --------------------------------------------------------------------- ppo ---

@torch.no_grad()
def _eq_probability(net, battle) -> float:
    """P(Earthquake) for Garchomp's opening turn (action index 2 = move 1)."""
    logits, _ = net(torch.from_numpy(encode(battle, 0)).unsqueeze(0))
    mask, _ = action_table(battle, 0, 0)
    from pokechamp.rl.policy import masked_dist
    dist = masked_dist(logits[0, 0], torch.from_numpy(mask))
    return float(dist.probs[2])


def test_ppo_learns_the_winning_move():
    # Garchomp: Earthquake 2HKOs Tyranitar, Dragon Claw drags the game out
    # and loses the race — PPO should shift probability mass onto EQ
    team_a = [chomp()]
    team_b = [mon("Tyranitar", ["Crunch", "Stone Edge"], "Adamant",
                  {"hp": 252, "atk": 252})]
    net = PolicyNet(hidden=64)
    trainer = PPOTrainer(
        net, team_a, team_b, opponent=MaxDamageAgent(), seed=0,
        config=PPOConfig(lr=1e-3, minibatch=64),
    )
    probe = Battle(team_a, team_b, seed=1)
    before = _eq_probability(net, probe)
    history = trainer.train(iterations=5, episodes_per_iter=16)
    after = _eq_probability(net, probe)
    assert after > before
    assert after > 0.6, (before, after, history)


def test_ppo_self_play_collects_both_seats():
    net = PolicyNet(hidden=32)
    trainer = PPOTrainer(net, [chomp()], [chomp()], opponent=None, seed=0)
    batch, win_rate = trainer.collect(episodes=4)
    # both seats recorded: at least 2 transitions per turn played
    assert len(batch["obs"]) >= 8
    assert 0.0 <= win_rate <= 1.0
    stats = trainer.update(batch)
    assert np.isfinite(stats["policy_loss"])
    assert np.isfinite(stats["kl"]) and stats["kl"] >= 0.0


def test_opponent_pool_snapshots_and_gets_used():
    net = PolicyNet(hidden=32)
    trainer = PPOTrainer(
        net, [chomp()], [chomp()], opponent=None, seed=0,
        pool_size=2, pool_prob=1.0, snapshot_every=1,
    )
    trainer.train(iterations=3, episodes_per_iter=2)
    assert len(trainer.pool) == 2  # FIFO-bounded
    # pool_prob=1 with a non-empty pool: episodes are single-seat now
    batch, _ = trainer.collect(episodes=2)
    assert trainer._frozen is not None  # a frozen checkpoint actually played


def test_scripted_anchor_in_pool():
    net = PolicyNet(hidden=32)
    trainer = PPOTrainer(
        net, [chomp()], [chomp()], opponent=None, seed=0,
        pool_prob=1.0, pool_agents=[MaxDamageAgent()],
    )
    opp = trainer._pool_opponent()
    assert isinstance(opp, MaxDamageAgent)


def test_team_factory_varies_teams():
    from pokechamp.sim import random_team

    import random as _random
    rng = _random.Random(0)
    team = random_team(rng, size=3)
    assert len(team) == 3
    assert len({p.species for p in team}) == 3
    assert all(not p.species.startswith("Mega ") for p in team)
    assert all(len(p.moves) == 4 for p in team)

    net = PolicyNet(hidden=32)
    trainer = PPOTrainer(
        net, [], [], opponent=None, seed=0,
        team_factory=lambda r: (random_team(r, 2), random_team(r, 2)),
    )
    batch, _ = trainer.collect(episodes=2)
    assert len(batch["obs"]) >= 4


# ------------------------------------------------------------------ hybrid ---

def test_hybrid_agent_plays_and_uses_the_net():
    from pokechamp.rl import hybrid_agent, net_value

    torch.manual_seed(0)
    net = PolicyNet(hidden=32)
    b = Battle(TEAM, [t for t in TEAM], seed=1)
    v = net_value(net)(b, 0)
    assert np.isfinite(v) and np.isfinite(net_value(net)(b, 1))

    agent = hybrid_agent(net, depth=0, n_worlds=2, seed=3)
    w = play_game(agent, MaxDamageAgent(), TEAM, [t for t in TEAM], seed=5)
    assert w in (0, 1, None)


def test_value_fn_reaches_the_leaves():
    from pokechamp.recommender import recommend

    calls = []

    def spy_value(battle, side_idx):
        calls.append(side_idx)
        return 0.0

    b = Battle(TEAM, [t for t in TEAM], seed=1)
    recommend(b.snapshot(), n_worlds=1, depth=0, seed=0, value_fn=spy_value)
    assert calls and set(calls) == {0}
