"""Phase 4b: self-play RL on the sim engine (requires the [rl] extra).

Building blocks: a fixed maskable action space (actions), a Battle ->
vector encoder (features), a masked policy/value net + Agent wrapper
(policy), and a PPO trainer with scripted-opponent and self-play modes
(ppo). The endpoint is the 4a searcher guided by the learned policy/value
net instead of MaxDamage rollouts.
"""

from pokechamp.rl.actions import (
    DEFAULT_IDX,
    N_SLOTS,
    SLOT_ACTION_DIM,
    action_table,
    forbid_switch_conflict,
    slot_tables,
)
from pokechamp.rl.features import MON_DIM, MOVE_DIM, OBS_DIM, encode
from pokechamp.rl.hybrid import hybrid_agent, net_value
from pokechamp.rl.policy import PolicyAgent, PolicyNet, sample_turn
from pokechamp.rl.ppo import PPOConfig, PPOTrainer

__all__ = [
    "DEFAULT_IDX", "MON_DIM", "MOVE_DIM", "N_SLOTS", "OBS_DIM",
    "PPOConfig", "PPOTrainer", "PolicyAgent", "PolicyNet",
    "SLOT_ACTION_DIM", "action_table", "encode", "forbid_switch_conflict",
    "hybrid_agent", "net_value", "sample_turn", "slot_tables",
]
