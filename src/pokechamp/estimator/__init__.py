"""Stat/set estimator (Phase 2).

Inverts the damage calculator: given observed damage percentages and turn
order, maintain a Bayesian posterior over candidate (EVs, nature, item)
builds for an opponent Pokemon. The recommender samples opponent sets from
this posterior before simulating (determinization).

Working: exact damage-roll likelihoods, speed constraints, archetype-grid
candidates extended by usage data, ability hypotheses, current-HP tracking,
usage-based priors (estimator/usage.py), posterior/marginal/stat-summary
queries.
"""

from pokechamp.estimator.candidates import default_candidates
from pokechamp.estimator.estimator import (
    InconsistentObservation,
    StatEstimator,
    effective_speed,
)

__all__ = [
    "StatEstimator",
    "InconsistentObservation",
    "default_candidates",
    "effective_speed",
]
