"""Session tools: consistent data entry and persistence around matches.

The MCP server (Phase 5) exposes these as tools; they are plain library
code so everything is testable without a server:
- TeamStore: named, versioned storage of the user's team builds,
  validated against the champions ruleset at entry time
- OpponentScout: revealed facts + replayable observation logs per
  opposing Pokemon; rebuilds StatEstimators identically across restarts
  and feeds the recommender via for_recommend()
- recommend_leads: team-preview assist — which Pokemon to bring against
  a scouted opponent, and who leads
"""

from pokechamp.session.leads import LeadOption, LeadPlan, recommend_leads
from pokechamp.session.scout import OpponentScout
from pokechamp.session.serialize import (
    field_from_dict,
    field_to_dict,
    set_from_dict,
    set_to_dict,
)
from pokechamp.session.teams import TeamStore, validate_set

__all__ = [
    "LeadOption", "LeadPlan", "OpponentScout", "TeamStore",
    "field_from_dict", "field_to_dict", "recommend_leads",
    "set_from_dict", "set_to_dict", "validate_set",
]
