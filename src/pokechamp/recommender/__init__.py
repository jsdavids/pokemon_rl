"""Phase 4: the move recommender.

4a (here): determinized search — sample opponent builds from estimator
posteriors, score candidate joint actions by 1-ply expectation +
depth-limited rollouts on the sim engine. 4b will add self-play RL: a
learned policy/value net guiding the same search.
"""

from pokechamp.recommender.search import (
    Recommendation,
    SearchAgent,
    determinize,
    joint_actions,
    recommend,
    state_value,
)

__all__ = [
    "Recommendation", "SearchAgent", "determinize", "joint_actions",
    "recommend", "state_value",
]
