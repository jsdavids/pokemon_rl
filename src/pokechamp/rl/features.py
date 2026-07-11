"""Phase 4b: Battle -> fixed-length observation vector, from one side's seat.

Layout (all values roughly in [0, 1]; sides always ordered mine-then-theirs
so the same net plays either seat):

- 12 Pokemon blocks (my team in team order, then theirs), MON_DIM each:
  types multi-hot(18), stats/400(6), hp_fraction(1), status one-hot(6),
  boosts/6(5), is_active(1), fainted(1), sub_hp/max(1), has_item(1)
- my active slots' movesets (N_SLOTS x N_MOVES blocks, MOVE_DIM each):
  move type(18), bp/150, acc, category one-hot(3), pp/16, (priority+7)/12
- field: weather one-hot(4), terrain one-hot(4), trick room(1), then per
  side (mine, theirs): SR, spikes/3, tspikes/2, web, reflect, light screen,
  aurora veil, tailwind, safeguard

Transform/Imposter is reflected automatically: mon.types, mon.stats and the
active moveset all read the battle's current (possibly copied) state.
"""

from __future__ import annotations

import numpy as np

from pokechamp.rl.actions import N_MOVES, N_SLOTS
from pokechamp.schemas import STATUSES
from pokechamp.sim.battle import Battle, BattlePokemon

TYPES = (
    "normal", "fire", "water", "electric", "grass", "ice", "fighting",
    "poison", "ground", "flying", "psychic", "bug", "rock", "ghost",
    "dragon", "dark", "steel", "fairy",
)
_TYPE_IDX = {t: i for i, t in enumerate(TYPES)}
WEATHERS = ("sun", "rain", "sand", "snow")
TERRAINS = ("electric", "grassy", "psychic", "misty")
CATEGORIES = ("Physical", "Special", "Status")
BOOSTS = ("atk", "def", "spa", "spd", "spe")

MON_DIM = 18 + 6 + 1 + 6 + 5 + 1 + 1 + 1 + 1  # 40
MOVE_DIM = 18 + 1 + 1 + 3 + 1 + 1  # 25
FIELD_DIM = 4 + 4 + 1 + 2 * 9  # 27
OBS_DIM = 12 * MON_DIM + N_SLOTS * N_MOVES * MOVE_DIM + FIELD_DIM


def _mon_block(mon: BattlePokemon, active: bool, out: np.ndarray) -> None:
    for t in mon.types:
        out[_TYPE_IDX[t.lower()]] = 1.0
    for i, k in enumerate(("hp", "atk", "def", "spa", "spd", "spe")):
        out[18 + i] = mon.stats[k] / 400.0
    out[24] = mon.hp_fraction
    if mon.status in STATUSES:
        out[25 + STATUSES.index(mon.status)] = 1.0
    for i, k in enumerate(BOOSTS):
        out[31 + i] = mon.boosts[k] / 6.0
    out[36] = 1.0 if active else 0.0
    out[37] = 1.0 if mon.fainted else 0.0
    out[38] = mon.sub_hp / max(1, mon.max_hp)
    out[39] = 1.0 if mon.set.item else 0.0


def encode(battle: Battle, side_idx: int) -> np.ndarray:
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    pos = 0

    for side in (battle.sides[side_idx], battle.sides[1 - side_idx]):
        active = {side.slots[s] for s in range(battle.n_active)}
        for i in range(6):
            if i < len(side.team):
                _mon_block(side.team[i], i in active, obs[pos:pos + MON_DIM])
            pos += MON_DIM

    mine = battle.sides[side_idx]
    for slot in range(N_SLOTS):
        mon = mine.active(slot) if slot < battle.n_active else None
        for m in range(N_MOVES):
            if mon is not None and not mon.fainted and m < len(mon.set.moves):
                move = battle.rs.get_move(mon.set.moves[m])
                block = obs[pos:pos + MOVE_DIM]
                block[_TYPE_IDX[move.type.lower()]] = 1.0
                block[18] = move.base_power / 150.0
                block[19] = (move.accuracy or 100) / 100.0
                block[20 + CATEGORIES.index(move.category)] = 1.0
                block[23] = mon.pp[m] / 16.0
                block[24] = (move.priority + 7) / 12.0
            pos += MOVE_DIM

    if battle.field.weather in WEATHERS:
        obs[pos + WEATHERS.index(battle.field.weather)] = 1.0
    pos += 4
    if battle.field.terrain in TERRAINS:
        obs[pos + TERRAINS.index(battle.field.terrain)] = 1.0
    pos += 4
    obs[pos] = 1.0 if battle.field.trick_room else 0.0
    pos += 1
    for side in (battle.sides[side_idx], battle.sides[1 - side_idx]):
        hz, c = side.hazards, side.conditions
        obs[pos:pos + 9] = (
            float(hz.get("stealth_rock", 0)),
            hz.get("spikes", 0) / 3.0,
            hz.get("toxic_spikes", 0) / 2.0,
            float(hz.get("sticky_web", 0)),
            float(c.reflect), float(c.light_screen), float(c.aurora_veil),
            float(c.tailwind), float(c.safeguard),
        )
        pos += 9
    return obs
