"""Phase 6 latency budget: tool-side compute vs the ~45s move timer.

Worst-case realistic setup — full 6-mon teams, all six opponents scouted
with damage observations feeding real estimators — then each tool the
assistant calls mid-battle is timed. The LLM's own round-trips (a few
seconds each on a 16GB GPU) spend whatever this leaves over.

    .venv\\Scripts\\python scripts\\benchmark_latency.py
"""

import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pokechamp import server

MY_TEAM = [
    {"species": "Garchomp", "moves": ["Dragon Claw", "Earthquake", "Swords Dance", "Fire Fang"],
     "nature": "Jolly", "evs": {"atk": 252, "spe": 252}},
    {"species": "Azumarill", "moves": ["Play Rough", "Aqua Jet", "Liquidation"],
     "nature": "Adamant", "evs": {"hp": 252, "atk": 252}},
    {"species": "Gholdengo", "moves": ["Shadow Ball", "Thunderbolt", "Recover"],
     "nature": "Timid", "evs": {"spa": 252, "spe": 252}},
    {"species": "Tyranitar", "moves": ["Crunch", "Stone Edge", "Earthquake"],
     "nature": "Adamant", "evs": {"hp": 252, "atk": 252}},
    {"species": "Politoed", "moves": ["Surf", "Ice Beam", "Protect"],
     "nature": "Modest", "evs": {"hp": 252, "spa": 252}},
    {"species": "Alakazam", "moves": ["Psyshock", "Shadow Ball", "Focus Blast"],
     "nature": "Timid", "evs": {"spa": 252, "spe": 252}},
]
OPP = ["Garchomp", "Dragonite", "Gholdengo", "Politoed", "Hydreigon", "Alakazam"]


def timed(label, fn, budget=45.0):
    t0 = time.perf_counter()
    fn()
    dt = time.perf_counter() - t0
    flag = "" if dt < budget * 0.5 else ("  <-- tight" if dt < budget else "  <-- OVER BUDGET")
    print(f"{label:>34}: {dt:7.2f}s{flag}")
    return dt


def main():
    with TemporaryDirectory() as tmp:
        server.init(tmp)
        server.save_team("bench", MY_TEAM)
        for sp in OPP:
            server.scout("rival", sp)
        server.scout("rival", "Garchomp", move="Earthquake")
        server.scout("rival", "Politoed", ability="Drizzle")
        # observations make the estimators non-trivial
        server.observe_damage("rival", "Garchomp", "Play Rough", MY_TEAM[1],
                              remaining_percent=44.0)
        server.observe_damage("rival", "Gholdengo", "Crunch", MY_TEAM[3],
                              remaining_percent=30.0)

        print("cold paths (estimator construction included):")
        timed("opponent_summary (all 6)", lambda: [
            server.opponent_summary("rival", sp) for sp in OPP
        ])
        timed("lead_assist singles g8", lambda: server.lead_assist(
            "bench", "rival", games=8))
        timed("lead_assist doubles g8", lambda: server.lead_assist(
            "bench", "rival", format="doubles", games=8))

        print("hot per-turn paths:")
        timed("damage", lambda: server.damage(MY_TEAM[0], MY_TEAM[3],
                                              "Earthquake"))
        server.start_battle("bench", "rival")
        server.battle_update("opponent", "Garchomp", hp_percent=44.0,
                             now_active=True)
        timed("recommend_move singles", server.recommend_move)
        server.start_battle("bench", "rival", format="doubles")
        timed("recommend_move doubles", server.recommend_move)

        print("\nbudget: ~45s move timer minus 2-4 LLM round-trips.")


if __name__ == "__main__":
    main()
