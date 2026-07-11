"""Phase 4b: masked PPO on the sim engine.

The simultaneous-move game is trained as a single-agent problem: the other
seat is part of the environment. Opponent modes:
- scripted (any Agent): the learner alternates seats each episode so it
  sees both teams and both sides
- self-play (opponent=None): both seats run the current policy and BOTH
  trajectories enter the batch, each from its own seat's perspective (the
  observation encoding is always mine-then-theirs, so one net serves both)
- self-play + pool: train() periodically freezes a checkpoint; a fraction
  of episodes are then played against a random frozen checkpoint or a
  scripted anchor instead of the mirror. This is the standard guard
  against self-play cycling (beating only your current self).

Reward is terminal win/loss (+1/-1, draws 0) plus potential-based shaping
on the team-HP differential (phi' - phi each turn) — it densifies credit
without changing the optimal policy.
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

import numpy as np
import torch
from torch import nn

from pokechamp.rl.policy import PolicyAgent

from pokechamp.rl.actions import N_SLOTS
from pokechamp.rl.policy import PolicyNet, masked_dist, sample_turn
from pokechamp.schemas import PokemonSet
from pokechamp.sim.agents import Agent
from pokechamp.sim.battle import Battle


@dataclass
class PPOConfig:
    lr: float = 3e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip: float = 0.2
    epochs: int = 4
    minibatch: int = 256
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    shaping: float = 0.5
    max_grad_norm: float = 0.5
    # stop a batch's update epochs once the policy has moved this far
    # (approx KL); the main guard against the entropy collapse we saw on
    # fixed-team self-play. None disables.
    target_kl: float | None = 0.03


def _potential(battle: Battle, side_idx: int) -> float:
    def health(side) -> float:
        return sum(p.hp_fraction for p in side.team) / len(side.team)

    return health(battle.sides[side_idx]) - health(battle.sides[1 - side_idx])


class _Trajectory:
    """One seat's transitions within one episode."""

    def __init__(self) -> None:
        self.obs: list[np.ndarray] = []
        self.idxs: list[np.ndarray] = []
        self.masks: list[np.ndarray] = []
        self.logprobs: list[float] = []
        self.values: list[float] = []
        self.rewards: list[float] = []


class PPOTrainer:
    def __init__(
        self,
        net: PolicyNet,
        team_a: list[PokemonSet],
        team_b: list[PokemonSet],
        opponent: Agent | None = None,
        format: str = "singles",
        ruleset: str = "champions",
        seed: int = 0,
        config: PPOConfig | None = None,
        team_factory: Callable[[random.Random], tuple[list[PokemonSet], list[PokemonSet]]] | None = None,
        pool_size: int = 5,
        pool_prob: float = 0.5,
        snapshot_every: int | None = None,
        pool_agents: list[Agent] | None = None,
    ):
        self.net = net
        self.teams = (team_a, team_b)
        self.opponent = opponent  # None = self-play
        self.format = format
        self.ruleset = ruleset
        self.cfg = config or PPOConfig()
        self.opt = torch.optim.Adam(net.parameters(), lr=self.cfg.lr)
        self.rng = random.Random(seed)
        torch.manual_seed(seed)
        self.episodes_played = 0
        # varied-team training: called per episode instead of the fixed pair
        self.team_factory = team_factory
        # frozen-checkpoint pool (self-play mode only) + scripted anchors
        self.pool: list[dict] = []
        self.pool_size = pool_size
        self.pool_prob = pool_prob
        self.snapshot_every = snapshot_every
        self.pool_agents = pool_agents or []
        self._frozen: PolicyNet | None = None

    def snapshot_pool(self) -> None:
        """Freeze the current weights into the opponent pool (FIFO-bounded)."""
        self.pool.append(deepcopy(self.net.state_dict()))
        if len(self.pool) > self.pool_size:
            self.pool.pop(0)

    def _pool_opponent(self) -> Agent | None:
        """A frozen checkpoint or scripted anchor, or None for mirror play."""
        if self.opponent is not None:
            return self.opponent
        choices = len(self.pool) + len(self.pool_agents)
        if choices == 0 or self.rng.random() >= self.pool_prob:
            return None
        pick = self.rng.randrange(choices)
        if pick < len(self.pool_agents):
            return self.pool_agents[pick]
        if self._frozen is None:
            self._frozen = deepcopy(self.net)
        self._frozen.load_state_dict(self.pool[pick - len(self.pool_agents)])
        return PolicyAgent(self._frozen, greedy=False)

    # --- rollout collection ---

    def _play_episode(self) -> tuple[list[_Trajectory], float]:
        """One battle; returns the learner trajectories (both seats in
        mirror self-play, one seat against a scripted/frozen opponent) and
        the learner's outcome."""
        self.episodes_played += 1
        teams = (
            self.team_factory(self.rng) if self.team_factory else self.teams
        )
        battle = Battle(
            list(teams[0]), list(teams[1]),
            ruleset=self.ruleset, format=self.format,
            seed=self.rng.randrange(1 << 30),
        )
        opponent = self._pool_opponent()
        seats = [0, 1] if opponent is None else [self.episodes_played % 2]
        trajs = {s: _Trajectory() for s in seats}

        while not battle.is_over:
            per_side: dict[int, list] = {}
            phi = {s: _potential(battle, s) for s in seats}
            for side in (0, 1):
                if side in trajs:
                    acts, obs, idxs, masks, logprob, value = sample_turn(
                        self.net, battle, side
                    )
                    t = trajs[side]
                    t.obs.append(obs)
                    t.idxs.append(idxs)
                    t.masks.append(masks)
                    t.logprobs.append(logprob)
                    t.values.append(value)
                    per_side[side] = acts[: battle.n_active]
                else:
                    per_side[side] = opponent.choose(battle, side)
            battle.step(per_side[0], per_side[1])
            for s in seats:
                r = self.cfg.shaping * (_potential(battle, s) - phi[s])
                if battle.is_over:
                    w = battle.winner()
                    r += 0.0 if w is None else (1.0 if w == s else -1.0)
                trajs[s].rewards.append(r)

        w = battle.winner()
        learner = seats[0] if len(seats) == 1 else 0
        outcome = 0.5 if w is None else (1.0 if w == learner else 0.0)
        return list(trajs.values()), outcome

    def collect(self, episodes: int) -> tuple[dict[str, torch.Tensor], float]:
        """Play episodes, compute GAE, and pack one flat batch. Also returns
        the learner win rate over the collected episodes."""
        trajs: list[_Trajectory] = []
        wins = 0.0
        for _ in range(episodes):
            ts, outcome = self._play_episode()
            trajs += ts
            wins += outcome
        cfg = self.cfg

        obs, idxs, masks, logprobs, advs, rets = [], [], [], [], [], []
        for t in trajs:
            adv = np.zeros(len(t.rewards), dtype=np.float32)
            last = 0.0
            for i in reversed(range(len(t.rewards))):
                next_v = t.values[i + 1] if i + 1 < len(t.values) else 0.0
                delta = t.rewards[i] + cfg.gamma * next_v - t.values[i]
                last = delta + cfg.gamma * cfg.gae_lambda * last
                adv[i] = last
            obs += t.obs
            idxs += t.idxs
            masks += t.masks
            logprobs += t.logprobs
            advs.append(adv)
            rets.append(adv + np.array(t.values, dtype=np.float32))

        adv = torch.from_numpy(np.concatenate(advs))
        adv = (adv - adv.mean()) / (adv.std() + 1e-8)
        batch = {
            "obs": torch.from_numpy(np.stack(obs)),
            "idxs": torch.from_numpy(np.stack(idxs)),
            "masks": torch.from_numpy(np.stack(masks)),
            "logprobs": torch.tensor(logprobs, dtype=torch.float32),
            "adv": adv,
            "ret": torch.from_numpy(np.concatenate(rets)),
        }
        return batch, wins / episodes

    # --- optimization ---

    def update(self, batch: dict[str, torch.Tensor]) -> dict[str, float]:
        cfg = self.cfg
        n = len(batch["obs"])
        stats = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0,
                 "kl": 0.0}
        steps = 0
        stop = False
        for _ in range(cfg.epochs):
            if stop:
                break
            for start in torch.randperm(n).split(cfg.minibatch):
                mb = {k: v[start] for k, v in batch.items()}
                logits, values = self.net(mb["obs"])
                logprob = torch.zeros(len(start))
                entropy = torch.zeros(len(start))
                for slot in range(N_SLOTS):
                    dist = masked_dist(
                        logits[:, slot], mb["masks"][:, slot]
                    )
                    logprob = logprob + dist.log_prob(mb["idxs"][:, slot])
                    entropy = entropy + dist.entropy()

                log_ratio = logprob - mb["logprobs"]
                with torch.no_grad():
                    # approx KL(old || new), Schulman's low-variance form
                    kl = ((torch.exp(log_ratio) - 1) - log_ratio).mean().item()
                if cfg.target_kl is not None and kl > 1.5 * cfg.target_kl:
                    stop = True
                    break
                ratio = torch.exp(log_ratio)
                clipped = torch.clamp(ratio, 1 - cfg.clip, 1 + cfg.clip)
                policy_loss = -torch.min(
                    ratio * mb["adv"], clipped * mb["adv"]
                ).mean()
                value_loss = ((values - mb["ret"]) ** 2).mean()
                loss = (
                    policy_loss
                    + cfg.value_coef * value_loss
                    - cfg.entropy_coef * entropy.mean()
                )
                self.opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(
                    self.net.parameters(), cfg.max_grad_norm
                )
                self.opt.step()

                stats["policy_loss"] += policy_loss.item()
                stats["value_loss"] += value_loss.item()
                stats["entropy"] += entropy.mean().item()
                stats["kl"] += kl
                steps += 1
        return {k: v / max(1, steps) for k, v in stats.items()}

    def train(
        self, iterations: int, episodes_per_iter: int = 32
    ) -> list[dict[str, float]]:
        history = []
        for it in range(iterations):
            batch, win_rate = self.collect(episodes_per_iter)
            stats = self.update(batch)
            stats["win_rate"] = win_rate
            stats["iteration"] = it
            history.append(stats)
            if self.snapshot_every and (it + 1) % self.snapshot_every == 0:
                self.snapshot_pool()
        return history
