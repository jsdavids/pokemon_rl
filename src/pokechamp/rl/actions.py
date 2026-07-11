"""Phase 4b: the fixed per-slot action space the policy net emits over.

Each active slot picks one index out of SLOT_ACTION_DIM:
- 0..7   moves: move_slot * 2 + target_option (target option = opposing slot
         for single-target moves in doubles; option 0 everywhere else)
- 8..13  switch to absolute team index 0..5
- 14     default (pass when the slot is empty/fainted, Struggle when the
         whole moveset is unusable)

Legality is not re-derived here: the mask and the index->Action table are
built directly from Battle.legal_actions, so every engine rule (PP, Taunt,
Encore, Choice lock, trapping, ...) is inherited for free. An index is
usable iff it appears in the table.
"""

from __future__ import annotations

import numpy as np

from pokechamp.sim.battle import Action, Battle

N_MOVES = 4
N_TARGET_OPTS = 2
N_TEAM = 6
SWITCH_BASE = N_MOVES * N_TARGET_OPTS  # 8
DEFAULT_IDX = SWITCH_BASE + N_TEAM  # 14
SLOT_ACTION_DIM = DEFAULT_IDX + 1  # 15
N_SLOTS = 2  # the net always emits two heads; singles masks the second


def action_table(
    battle: Battle, side_idx: int, slot: int
) -> tuple[np.ndarray, dict[int, Action]]:
    """(mask over SLOT_ACTION_DIM, index -> concrete Action) for one slot."""
    table: dict[int, Action] = {}
    for act in battle.legal_actions(side_idx, slot):
        if act.kind == "move":
            t_opt = act.target if act.target is not None else 0
            idx = act.index * N_TARGET_OPTS + t_opt
        elif act.kind == "switch":
            idx = SWITCH_BASE + act.index
        else:  # pass / struggle
            idx = DEFAULT_IDX
        table[idx] = act
    mask = np.zeros(SLOT_ACTION_DIM, dtype=np.float32)
    mask[list(table)] = 1.0
    return mask, table


def slot_tables(
    battle: Battle, side_idx: int
) -> tuple[np.ndarray, list[dict[int, Action]]]:
    """Masks (N_SLOTS x SLOT_ACTION_DIM) and tables for every head. Slots
    beyond the format's n_active are dead: only DEFAULT (pass) is legal."""
    masks = np.zeros((N_SLOTS, SLOT_ACTION_DIM), dtype=np.float32)
    tables: list[dict[int, Action]] = []
    for slot in range(N_SLOTS):
        if slot < battle.n_active:
            mask, table = action_table(battle, side_idx, slot)
        else:
            mask = np.zeros(SLOT_ACTION_DIM, dtype=np.float32)
            mask[DEFAULT_IDX] = 1.0
            table = {DEFAULT_IDX: Action("pass")}
        masks[slot] = mask
        tables.append(table)
    return masks, tables


def forbid_switch_conflict(masks: np.ndarray, chosen_first: int) -> np.ndarray:
    """After slot 0 commits to a switch, slot 1 may not claim the same bench
    Pokemon. Returns slot 1's adjusted mask (a copy)."""
    mask = masks[1].copy()
    if SWITCH_BASE <= chosen_first < DEFAULT_IDX:
        mask[chosen_first] = 0.0
        if not mask.any():  # nothing else legal: fall back to default
            mask[DEFAULT_IDX] = 1.0
    return mask
