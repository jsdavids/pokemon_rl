"""Live-match session: the state the user narrates during a battle.

The MCP client (a local LLM relaying what's on screen) enters facts as
they happen — HP bars, statuses, switches, field changes — and this
object folds them together with the scouting book into the BattleState
that recommend() consumes. Opponent HP lives in the scout's estimators
(they already track the bar for censored-KO likelihoods), so damage
observations and move recommendations stay consistent automatically.
"""

from __future__ import annotations

from pokechamp.data import load_ruleset
from pokechamp.data.loader import canonical_species
from pokechamp.schemas import (
    BattleState,
    FieldState,
    PokemonSet,
    PokemonSnapshot,
    SideState,
    TeamSnapshot,
    boost_table,
)
from pokechamp.session.scout import OpponentScout

N_ACTIVE = {"singles": 1, "doubles": 2}


class BattleSession:
    def __init__(
        self,
        my_team: list[PokemonSet],
        opponent: str,
        scout: OpponentScout,
        format: str = "singles",
        ruleset: str = "champions",
    ):
        self.my_team = my_team
        self.opponent = opponent
        self.scout = scout
        self.format = format
        self.ruleset = ruleset
        n = N_ACTIVE[format]
        self.my_state = [
            {"hp": 100.0, "status": None, "boosts": boost_table()}
            for _ in my_team
        ]
        self.my_active: list[int] = list(range(min(n, len(my_team))))
        # opponent team order fixes the estimator indexing for recommend()
        self.opp_species: list[str] = scout.team(opponent)
        self.opp_state = {
            sp: {"status": None, "boosts": boost_table()}
            for sp in self.opp_species
        }
        self.opp_active: list[str] = self.opp_species[:n]
        self.field = FieldState()
        self.my_conditions = SideState()
        self.opp_conditions = SideState()
        self.my_hazards: dict = {}
        self.opp_hazards: dict = {}
        self.turn = 0
        self.log: list[dict] = []

    # --- data entry ---

    def note(self, text: str) -> None:
        self.log.append({"turn": self.turn, "text": text})

    def _opp_entry(self, species: str) -> dict:
        species = canonical_species(species, self.ruleset)
        if species not in self.opp_state:
            self.scout.add_pokemon(self.opponent, species)
            self.opp_species.append(species)
            self.opp_state[species] = {
                "status": None, "boosts": boost_table()
            }
        return self.opp_state[species]

    def update_mine(
        self,
        index: int,
        hp_percent: float | None = None,
        status: str | None = "unchanged",
        boosts: dict[str, int] | None = None,
    ) -> None:
        s = self.my_state[index]
        if hp_percent is not None:
            s["hp"] = hp_percent
        if status != "unchanged":
            s["status"] = status
        if boosts is not None:
            s["boosts"] = {**boost_table(), **boosts}

    def update_opponent(
        self,
        species: str,
        hp_percent: float | None = None,
        status: str | None = "unchanged",
        boosts: dict[str, int] | None = None,
    ) -> None:
        species = canonical_species(species, self.ruleset)
        s = self._opp_entry(species)
        if hp_percent is not None:
            # the estimator owns the opponent's HP bar
            self.scout.estimator(self.opponent, species).set_hp(hp_percent)
        if status != "unchanged":
            s["status"] = status
        if boosts is not None:
            s["boosts"] = {**boost_table(), **boosts}

    def set_actives(
        self,
        mine: list[int] | None = None,
        opponent: list[str] | None = None,
    ) -> None:
        if mine is not None:
            self.my_active = mine
        if opponent is not None:
            opponent = [canonical_species(sp, self.ruleset) for sp in opponent]
            for sp in opponent:
                self._opp_entry(sp)
            self.opp_active = opponent

    # --- assembling the snapshot ---

    def _placeholder_set(self, species: str) -> PokemonSet:
        """A syntactically valid stand-in; determinization replaces it with
        posterior samples before any rollout reads it."""
        rec_moves = self.scout._mon(self.opponent, species)["revealed_moves"]
        if not rec_moves:
            rs = load_ruleset(self.ruleset)
            sp = rs.get_species(species)
            learnset = sorted(sp.learnset)  # frozenset: hash-seed order
            damaging = [
                rs.get_move(m).name for m in learnset
                if rs.get_move(m).base_power > 0
            ]
            rec_moves = damaging[:1] or [rs.get_move(learnset[0]).name]
        return PokemonSet(species=species, moves=list(rec_moves))

    def state(self) -> BattleState:
        n = N_ACTIVE[self.format]
        mine = TeamSnapshot(
            pokemon=[
                PokemonSnapshot(
                    set=pset,
                    hp_fraction=max(0.0, s["hp"] / 100.0),
                    status=s["status"],
                    boosts=dict(s["boosts"]),
                )
                for pset, s in zip(self.my_team, self.my_state)
            ],
            active=[
                self.my_active[i] if i < len(self.my_active) else None
                for i in range(n)
            ],
            conditions=self.my_conditions,
            hazards=dict(self.my_hazards),
        )
        opp_active = [
            self.opp_species.index(sp) for sp in self.opp_active
            if sp in self.opp_species
        ]
        theirs = TeamSnapshot(
            pokemon=[
                PokemonSnapshot(
                    set=self._placeholder_set(sp),
                    hp_fraction=max(
                        0.0,
                        self.scout.estimator(self.opponent, sp).hp_percent
                        / 100.0,
                    ),
                    status=self.opp_state[sp]["status"],
                    boosts=dict(self.opp_state[sp]["boosts"]),
                )
                for sp in self.opp_species
            ],
            active=[
                opp_active[i] if i < len(opp_active) else None
                for i in range(n)
            ],
            conditions=self.opp_conditions,
            hazards=dict(self.opp_hazards),
        )
        return BattleState(sides=(mine, theirs), field=self.field,
                           format=self.format)

    def for_recommend(self):
        """(state, estimators, revealed_moves) — the recommend() call."""
        ests, revealed = self.scout.for_recommend(
            self.opponent, self.opp_species
        )
        return self.state(), ests, revealed
