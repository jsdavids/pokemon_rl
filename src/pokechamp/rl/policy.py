"""Phase 4b: masked policy/value network and its Agent wrapper.

One shared trunk, one action head per slot (doubles = two heads, singles
masks the second down to "pass"), one tanh value head. Illegal actions are
masked to -inf before the softmax, so the policy can only ever emit engine-
legal choices — action masking is what makes PPO viable here at all (the
raw space is 15^2 per turn, most of it illegal most of the time).
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical

from pokechamp.rl.actions import (
    SLOT_ACTION_DIM,
    N_SLOTS,
    forbid_switch_conflict,
    slot_tables,
)
from pokechamp.rl.features import OBS_DIM, encode
from pokechamp.sim.agents import Agent
from pokechamp.sim.battle import Action, Battle

NEG_INF = -1e9


class PolicyNet(nn.Module):
    def __init__(self, obs_dim: int = OBS_DIM, hidden: int = 256):
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.policy = nn.Linear(hidden, N_SLOTS * SLOT_ACTION_DIM)
        self.value = nn.Linear(hidden, 1)

    def forward(
        self, obs: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """obs [B, OBS_DIM] -> (logits [B, N_SLOTS, SLOT_ACTION_DIM],
        value [B]). The value head is linear: returns live near [-1, 1] but
        shaping can push past it, and tanh would starve the gradient exactly
        at the win/loss boundary."""
        h = self.trunk(obs)
        logits = self.policy(h).view(-1, N_SLOTS, SLOT_ACTION_DIM)
        return logits, self.value(h).squeeze(-1)


def masked_dist(logits: torch.Tensor, mask: torch.Tensor) -> Categorical:
    """logits/mask [B, SLOT_ACTION_DIM]; illegal entries go to -inf."""
    return Categorical(logits=logits + (1.0 - mask) * NEG_INF)


@torch.no_grad()
def sample_turn(
    net: PolicyNet,
    battle: Battle,
    side_idx: int,
    greedy: bool = False,
) -> tuple[list[Action], np.ndarray, np.ndarray, np.ndarray, float, float]:
    """One seat's whole turn: sample (or argmax) each slot head under its
    legality mask, applying the switch-conflict adjustment to slot 1.

    Returns (engine actions, observation, chosen indices [N_SLOTS], the
    masks actually used [N_SLOTS, SLOT_ACTION_DIM], joint log-prob, value
    estimate)."""
    obs = encode(battle, side_idx)
    masks, tables = slot_tables(battle, side_idx)
    logits, value = net(torch.from_numpy(obs).unsqueeze(0))

    actions: list[Action] = []
    idxs = np.zeros(N_SLOTS, dtype=np.int64)
    used = masks.copy()
    logprob = 0.0
    for slot in range(N_SLOTS):
        mask = used[slot]
        if slot == 1:
            mask = forbid_switch_conflict(
                np.stack([used[0], mask]), int(idxs[0])
            )
            used[1] = mask
        dist = masked_dist(logits[0, slot], torch.from_numpy(mask))
        idx = (
            int(dist.probs.argmax()) if greedy else int(dist.sample())
        )
        logprob += float(dist.log_prob(torch.tensor(idx)))
        idxs[slot] = idx
        actions.append(tables[slot].get(idx, Action("pass")))
    return actions, obs, idxs, used, logprob, float(value[0])


class PolicyAgent(Agent):
    """The net wearing the Agent interface (greedy by default), so it plugs
    into evaluate() against every baseline — and, later, into the searcher
    as its rollout policy / candidate prior."""

    def __init__(self, net: PolicyNet, greedy: bool = True):
        self.net = net
        self.greedy = greedy

    def choose(self, battle: Battle, side_idx: int) -> list[Action]:
        actions = sample_turn(self.net, battle, side_idx, greedy=self.greedy)[0]
        return actions[: battle.n_active]
