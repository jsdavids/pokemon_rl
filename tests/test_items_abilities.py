"""The last unmodeled held items (Bright Powder, Focus Band, Iron Ball,
King's Rock, Light Ball, Metronome, Quick Claw, Scope Lens, Shell Bell,
White Herb), Focus Sash/Sturdy, crit stages, status-immunity abilities,
and the Champions abilities Piercing Drill, Eelevate and Thermal Exchange."""

import random

import pytest

from pokechamp.damage import calc_damage
from pokechamp.data.loader import DATA_DIR
from pokechamp.estimator.estimator import effective_speed
from pokechamp.schemas import PokemonSet, stat_table
from pokechamp.sim import Action, Battle

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "champions" / "species.json").exists(),
    reason="champions ruleset not scraped yet",
)

CH = "champions"


def mon(species, moves=(), nature="Serious", evs=None, item=None, ability=None):
    return PokemonSet(
        species=species, nature=nature, evs={**stat_table(), **(evs or {})},
        moves=list(moves), item=item, ability=ability,
    )


def chomp(moves=("Earthquake", "Dragon Claw"), **kw):
    return mon("Garchomp", moves, "Jolly", {"atk": 252, "spe": 252}, **kw)


class FixedRandom(random.Random):
    """random() always returns `value`; choice/randint still vary (they draw
    from getrandbits), so damage rolls stay legal."""

    def __init__(self, value: float, seed: int = 0):
        super().__init__(seed)
        self.value = value

    def random(self) -> float:
        return self.value


# ------------------------------------------------------------- calculator ---

def test_light_ball_doubles_pikachu_only():
    target = mon("Snorlax")
    for species, ratio in (("Pikachu", 2.0), ("Raichu", 1.0)):
        plain = calc_damage(mon(species), target, "Thunderbolt", ruleset=CH)
        ball = calc_damage(mon(species, item="Light Ball"), target,
                           "Thunderbolt", ruleset=CH)
        assert ball.max_damage / plain.max_damage == pytest.approx(ratio, abs=0.06)


def test_metronome_chain_and_cap():
    target = mon("Snorlax", evs={"hp": 252})
    base = calc_damage(chomp(), target, "Dragon Claw", ruleset=CH).max_damage

    def dmg(uses):
        return calc_damage(chomp(item="Metronome"), target, "Dragon Claw",
                           metronome_uses=uses, ruleset=CH).max_damage
    assert dmg(0) == base  # the first use is unboosted
    assert dmg(1) / base == pytest.approx(1.2, abs=0.03)
    assert dmg(5) / base == pytest.approx(2.0, abs=0.03)
    assert dmg(9) == dmg(5)  # capped at 2x


def test_iron_ball_grounds_flying_and_levitating_targets():
    skarm = mon("Skarmory")
    assert calc_damage(chomp(), skarm, "Earthquake", ruleset=CH).max_damage == 0
    skarm.item = "Iron Ball"
    res = calc_damage(chomp(), skarm, "Earthquake", ruleset=CH)
    assert res.type_effectiveness == 1.0  # neutral: NOT its Steel weakness
    assert any("Iron Ball" in n for n in res.notes)
    zchomp = mon("Mega Garchomp Z", ability="Levitate", item="Iron Ball")
    assert calc_damage(chomp(), zchomp, "Earthquake", ruleset=CH).max_damage > 0


def test_iron_ball_halves_speed():
    fast = chomp()
    assert effective_speed(chomp(item="Iron Ball")) == effective_speed(fast) // 2
    b = Battle([chomp(item="Iron Ball")], [chomp()], seed=1)
    assert b.effective_speed(0) == b.effective_speed(1) // 2
    assert b.sides[0].active(0).grounded


# ----------------------------------------------------------- crit stages ---

def _crit(attacker, defender, move, value):
    b = Battle([attacker], [defender], seed=1)
    b.rng = FixedRandom(value)
    return b._rolls_crit(b.sides[0].active(0), b.sides[1].active(0),
                         b.rs.get_move(move))


def test_crit_stage_ladder():
    target = mon("Snorlax")
    # a 0.10 draw: misses the 1/24 base, lands at +1 (1/8)
    assert not _crit(chomp(), target, "Dragon Claw", 0.10)
    assert _crit(chomp(item="Scope Lens"), target, "Dragon Claw", 0.10)
    assert _crit(chomp(("Stone Edge",)), target, "Stone Edge", 0.10)
    # Scope Lens + high-crit move = +2 = 1/2
    assert _crit(chomp(("Stone Edge",), item="Scope Lens"), target,
                 "Stone Edge", 0.45)
    absol = mon("Absol", ["Night Slash"], ability="Super Luck", item="Scope Lens")
    assert _crit(absol, target, "Night Slash", 0.99)  # +3: always
    assert _crit(mon("Kingambit", ["Wicked Blow"]), target, "Wicked Blow", 0.99)
    assert not _crit(mon("Kingambit", ["Wicked Blow"]),
                     mon("Snorlax", ability="Shell Armor"), "Wicked Blow", 0.0)


# ------------------------------------------------------------ simulator ---

def test_quick_claw_moves_first_about_a_fifth_of_the_time():
    slow = mon("Snorlax", ["Body Slam"], item="Quick Claw")
    b = Battle([slow], [chomp()], seed=5)
    chosen = {(0, 0): Action("move", 0), (1, 0): Action("move", 0)}
    firsts = sum(b._order(chosen, switches=False)[0] == (0, 0) for _ in range(2000))
    assert 0.16 < firsts / 2000 < 0.24


def test_bright_powder_cuts_accuracy():
    # a 0.75 draw: Stone Edge (80%) lands normally, misses at 72%
    for item, hit in ((None, True), ("Bright Powder", False)):
        b = Battle([chomp(("Stone Edge",))], [mon("Snorlax", item=item)], seed=1)
        b.rng = FixedRandom(0.75)
        lax = b.sides[1].active(0)
        b.step(Action("move", 0), Action("pass"))
        assert (lax.hp < lax.max_hp) is hit, item


def test_kings_rock_flinches():
    holder = chomp(("Earthquake",), item="King's Rock")
    azu = mon("Azumarill", ["Play Rough"], evs={"hp": 252})
    b = Battle([holder], [azu], seed=1)
    b.rng = FixedRandom(0.05)  # under the 10% flinch roll
    c = b.sides[0].active(0)
    b.step(Action("move", 0), Action("move", 0))
    assert c.hp == c.max_hp  # Azumarill flinched before Play Rough


def test_focus_sash_and_sturdy_hold_at_one_hp():
    for kw in ({"item": "Focus Sash"}, {"ability": "Sturdy"}):
        gold = mon("Gholdengo", ["Recover"], **kw)
        b = Battle([chomp()], [gold], seed=1)
        g = b.sides[1].active(0)
        b.step(Action("move", 0), Action("pass"))  # Earthquake: an OHKO
        assert g.hp == 1, kw
        if "item" in kw:
            assert g.set.item is None
        b.step(Action("move", 0), Action("pass"))
        assert g.fainted  # no second save from 1 HP


def test_focus_band_holds_on_a_lucky_roll():
    gold = mon("Gholdengo", ["Recover"], item="Focus Band")
    b = Battle([chomp()], [gold], seed=1)
    b.rng = FixedRandom(0.05)
    g = b.sides[1].active(0)
    g.hp = g.max_hp // 2  # Focus Band works at any HP
    b.step(Action("move", 0), Action("pass"))
    assert g.hp == 1 and g.set.item == "Focus Band"  # not consumed


def test_shell_bell_heals_an_eighth_of_damage_dealt():
    b = Battle([chomp(("Dragon Claw",), item="Shell Bell")],
               [mon("Snorlax", ["Rest"], evs={"hp": 252})], seed=1)
    c, lax = b.sides[0].active(0), b.sides[1].active(0)
    c.hp = c.max_hp // 2
    before = c.hp
    b.step(Action("move", 0), Action("pass"))
    dealt = lax.max_hp - lax.hp
    assert c.hp - before == dealt // 8


def test_white_herb_restores_lowered_stats_once():
    b = Battle([mon("Gholdengo", ["Make It Rain"], item="White Herb")],
               [mon("Snorlax", ["Rest"], evs={"hp": 252})], seed=1)
    g = b.sides[0].active(0)
    b.step(Action("move", 0), Action("pass"))  # Make It Rain: -1 SpA
    assert g.boosts["spa"] == 0 and g.set.item is None
    b.step(Action("move", 0), Action("pass"))
    assert g.boosts["spa"] == -1  # herb already spent


def test_metronome_streak_in_the_sim():
    b = Battle([chomp(item="Metronome")],
               [mon("Snorlax", ["Rest"], evs={"hp": 252, "def": 252})], seed=1)
    c = b.sides[0].active(0)
    lax = b.sides[1].active(0)
    for expected in (0, 1, 2):
        b.step(Action("move", 1), Action("pass"))
        assert c.metronome_streak == expected
        lax.hp = lax.max_hp  # keep the target standing
    b.step(Action("move", 0), Action("pass"))  # different move breaks it
    assert c.metronome_streak == 0


# --------------------------------------------------------------- abilities ---

def test_piercing_drill_hits_through_protect_at_a_quarter():
    def run(ability, move):
        drill = mon("Mega Excadrill", ["Drill Run", "Earthquake"], "Adamant",
                    {"atk": 252}, ability=ability)
        b = Battle([drill], [mon("Snorlax", ["Protect"], evs={"hp": 252})],
                   seed=1)
        lax = b.sides[1].active(0)
        b.step(Action("move", move), Action("move", 0))
        return lax.max_hp - lax.hp, lax
    assert run(None, 0)[0] == 0  # plain Protect blocks it
    pierced, lax = run("Piercing Drill", 0)
    full = calc_damage(
        mon("Mega Excadrill", ["Drill Run"], "Adamant", {"atk": 252}),
        mon("Snorlax", evs={"hp": 252}), "Drill Run", ruleset=CH,
    )
    # a crit could push one roll higher; a quarter of max is the normal cap
    assert 0 < pierced <= full.max_damage * 1.5 // 4 + 1
    assert run("Piercing Drill", 1)[0] == 0  # Earthquake: no contact


def test_eelevate_boosts_highest_stat_on_ko():
    eel = mon("Mega Eelektross", ["Thunderbolt"], ability="Eelevate")
    b = Battle([eel], [mon("Azumarill", ["Aqua Jet"]),
                       mon("Snorlax", ["Rest"])], seed=1)
    e = b.sides[0].active(0)
    b.sides[1].active(0).hp = 1
    b.step(Action("move", 0), Action("pass"))
    best = max(("atk", "def", "spa", "spd", "spe"), key=lambda k: e.stats[k])
    assert e.boosts[best] == 1
    assert sum(e.boosts.values()) == 1
    # and it floats: Spikes / Ground can't touch it
    assert not e.grounded


def test_moxie_on_ko():
    sala = mon("Salamence", ["Dragon Claw"], ability="Moxie")
    b = Battle([sala], [mon("Snorlax", ["Rest"]), mon("Azumarill", ["Aqua Jet"])],
               seed=1)
    b.sides[1].active(0).hp = 1
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[0].active(0).boosts["atk"] == 1


def test_thermal_exchange():
    bax = mon("Baxcalibur", ["Icicle Crash"], evs={"hp": 252},
              ability="Thermal Exchange")
    b = Battle([mon("Charizard", ["Flamethrower", "Will-O-Wisp"])], [bax], seed=1)
    x = b.sides[1].active(0)
    b.step(Action("move", 0), Action("pass"))
    assert x.boosts["atk"] == 1  # Fire hit -> +1 Atk
    b.rng = FixedRandom(0.0)  # Will-O-Wisp can't miss on this draw
    b.step(Action("move", 1), Action("pass"))
    assert x.status is None  # can't be burned


def test_status_immunity_abilities():
    b = Battle([mon("Gholdengo", ["Thunder Wave"])],
               [mon("Persian", ["Fake Out"], ability="Limber")], seed=1)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[1].active(0).status is None


# ------------------------------------------------- Focus Energy / Dragon Cheer ---

def test_focus_energy_pumps_crits_and_doesnt_stack():
    user = chomp(("Focus Energy", "Dragon Claw", "Baton Pass"))
    b = Battle([user, mon("Azumarill", ["Aqua Jet"])],
               [mon("Snorlax", ["Rest"], evs={"hp": 252})], seed=1)
    c = b.sides[0].active(0)
    lax = b.sides[1].active(0)
    b.step(Action("move", 0), Action("pass"))
    assert c.crit_stage == 2
    b.step(Action("move", 0), Action("pass"))  # fails: already pumped
    assert c.crit_stage == 2
    # +2 = 1/2: a 0.45 draw crits Dragon Claw
    b.rng = FixedRandom(0.45)
    assert b._rolls_crit(c, lax, b.rs.get_move("Dragon Claw"))
    # + Scope Lens = +3: always
    c.set.item = "Scope Lens"
    b.rng = FixedRandom(0.99)
    assert b._rolls_crit(c, lax, b.rs.get_move("Dragon Claw"))
    c.set.item = None
    # the volatile survives a snapshot round trip (recommender rollouts)
    b2 = Battle.from_state(b.snapshot(), seed=1)
    assert b2.sides[0].active(0).crit_stage == 2
    # Baton Pass hands it over; the passer loses it
    b.rng = random.Random(1)
    b.step(Action("move", 2), Action("pass"))
    assert b.sides[0].active(0).set.species == "Azumarill"
    assert b.sides[0].active(0).crit_stage == 2
    assert c.crit_stage == 0


def test_focus_energy_ends_on_switch():
    b = Battle([chomp(("Focus Energy",)), mon("Azumarill", ["Aqua Jet"])],
               [mon("Snorlax", ["Rest"])], seed=1)
    c = b.sides[0].active(0)
    b.step(Action("move", 0), Action("pass"))
    b.step(Action("switch", 1), Action("pass"))
    assert c.crit_stage == 0


def test_dragon_cheer_in_doubles():
    def cheer(ally):
        b = Battle(
            [mon("Dragonite", ["Dragon Cheer"]), ally],
            [mon("Snorlax", ["Rest"]), mon("Snorlax", ["Rest"])],
            seed=1, format="doubles",
        )
        b.step([Action("move", 0), Action("pass")],
               [Action("pass"), Action("pass")])
        return b.sides[0].active(0).crit_stage, b.sides[0].active(1).crit_stage
    assert cheer(chomp()) == (0, 2)  # Dragon ally: +2, user unaffected
    assert cheer(mon("Azumarill", ["Aqua Jet"])) == (0, 1)
    # singles: no ally, nothing happens
    b = Battle([mon("Dragonite", ["Dragon Cheer"])], [mon("Snorlax", ["Rest"])],
               seed=1)
    b.step(Action("move", 0), Action("pass"))
    assert b.sides[0].active(0).crit_stage == 0
