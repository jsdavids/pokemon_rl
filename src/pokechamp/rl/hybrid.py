"""Phase 4b endpoint: the 4a searcher guided by the learned net.

Three plug-in points, all optional and composable:
- leaf value: the net's value head replaces the HP-differential score at
  non-terminal leaves (depth=0 search becomes one real turn + a learned
  judgment of the aftermath — no rollout needed)
- rollout policy: greedy PolicyAgent instead of MaxDamage inside rollouts
- opponent model: the opponent is predicted by the same policy rather
  than assumed maximally greedy

`hybrid_agent` wires all three into a SearchAgent so the combination can
be evaluated against every baseline with the usual harness.
"""

from __future__ import annotations

import torch

from pokechamp.recommender import SearchAgent
from pokechamp.rl.features import encode
from pokechamp.rl.policy import PolicyAgent, PolicyNet
from pokechamp.sim.battle import Battle


def net_value(net: PolicyNet):
    """(battle, side_idx) -> the value head's judgment, for recommender
    value_fn hooks."""

    @torch.no_grad()
    def fn(battle: Battle, side_idx: int) -> float:
        obs = torch.from_numpy(encode(battle, side_idx)).unsqueeze(0)
        return float(net(obs)[1][0])

    return fn


def hybrid_agent(
    net: PolicyNet,
    depth: int = 0,
    n_worlds: int = 4,
    max_candidates: int = 16,
    budget: int | None = None,
    seed: int = 0,
) -> SearchAgent:
    """Search over determinized worlds with the net as leaf value, rollout
    policy, and opponent model. depth=0 is the cheap default: one simulated
    turn per pull, then the value head."""
    return SearchAgent(
        depth=depth,
        n_worlds=n_worlds,
        max_candidates=max_candidates,
        budget=budget,
        seed=seed,
        opp_model=PolicyAgent(net, greedy=True),
        rollout=PolicyAgent(net, greedy=True),
        value_fn=net_value(net),
    )
