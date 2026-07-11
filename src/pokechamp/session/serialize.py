"""JSON round-tripping for the shared schemas.

The session tools persist teams and scouting observations to disk; these
helpers are the single place schema objects become plain dicts and back,
so the MCP layer (Phase 5) can reuse the exact same wire shapes.
"""

from __future__ import annotations

from dataclasses import asdict

from pokechamp.schemas import FieldState, PokemonSet, SideState


def set_to_dict(pset: PokemonSet) -> dict:
    return asdict(pset)


def set_from_dict(d: dict) -> PokemonSet:
    return PokemonSet(**d)


def field_to_dict(field: FieldState) -> dict:
    return asdict(field)


def field_from_dict(d: dict) -> FieldState:
    d = dict(d)
    for side in ("attacker_side", "defender_side"):
        if isinstance(d.get(side), dict):
            d[side] = SideState(**d[side])
    return FieldState(**d)
