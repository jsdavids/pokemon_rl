"""Regulation M-C (Sept 9th - Dec 2nd 2026): the scraped data, the new
abilities in the calculator, and the new held items in the simulator."""

import pytest

from pokechamp.damage import calc_damage
from pokechamp.data.loader import DATA_DIR, load_ruleset
from pokechamp.estimator.candidates import default_candidates, legal_items
from pokechamp.schemas import FieldState, PokemonSet, stat_table
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


@pytest.fixture(scope="module")
def rs():
    return load_ruleset(CH)


# ------------------------------------------------------------------- data ---

def test_regulation_mc_scraped(rs):
    mc = rs.regulations["regulation-m-c"]
    assert mc["dates"].startswith("September 9th 2026")
    for name in ("Mega Salamence", "Mega Garchomp Z", "Mega Lucario Z",
                 "Mega Absol Z", "Mega Baxcalibur", "Rillaboom"):
        assert name in mc["pokemon"]
    assert {"Rocky Helmet", "Air Balloon", "Terrain Extender"} <= set(mc["items"])


def test_new_species_and_z_megas(rs):
    z = rs.get_species("Mega Garchomp Z")
    assert z.base_species == "Garchomp"
    assert z.types == ("dragon",)
    assert z.abilities == ("Levitate",)
    assert rs.get_species("Mega Lucario Z").abilities == ("Aura Guard",)
    assert rs.get_species("Mega Absol Z").types == ("dark", "ghost")
    assert rs.get_species("Mega Salamence").abilities == ("Aerilate",)
    assert "Grassy Surge" in rs.get_species("Rillaboom").abilities


def test_stat_only_forms_are_split_out(rs):
    """Forms with their own 'Stats - <Form>' table but no separate type row
    used to be dropped (only the first form survived)."""
    f = rs.get_species("Indeedee (Female)")
    assert f.base_species == "Indeedee"
    assert f.base_stats["spd"] == 105 and f.base_stats["atk"] == 55
    assert "Own Tempo" in f.abilities
    assert "Inner Focus" in rs.get_species("Indeedee").abilities
    assert rs.get_species("Lycanroc (Dusk Form)").abilities == ("Tough Claws",)


def test_no_mojibake_survives(rs):
    """Serebii serves cp1252 pages; decoding them as UTF-8 put U+FFFD
    where every 'é' belonged."""
    for sp in rs.all_species():
        assert "�" not in sp.name
    for name, item in rs.items.items():
        assert "�" not in name + item["description"], name


def test_new_items_are_held_items(rs):
    for name in ("Rocky Helmet", "Air Balloon", "Red Card", "Eject Button",
                 "Binding Band", "Normal Gem", "Terrain Extender",
                 "Grassy Seed", "Psychic Seed"):
        assert rs.items[name]["held"], name


def test_item_pool_is_cumulative_through_mc():
    mc = legal_items(CH, "regulation-m-c")
    assert "Life Orb" in mc  # added in M-B, still legal
    assert set(legal_items(CH, "regulation-m-b")) <= set(mc)
    # Z megas are megas: their item slot is committed to the stone
    assert {c.item for c in default_candidates("Mega Garchomp Z", ruleset=CH)} == {None}


# ------------------------------------------------------------- calculator ---

def test_aerilate_retypes_normal_moves():
    sala = mon("Mega Salamence", ["Double-Edge"], "Adamant", {"atk": 252})
    gengar = mon("Gengar")
    plain = calc_damage(sala, gengar, "Double-Edge", ruleset=CH)
    assert plain.type_effectiveness == 0.0  # Normal can't touch a Ghost

    sala.ability = "Aerilate"
    res = calc_damage(sala, gengar, "Double-Edge", ruleset=CH)
    assert res.type_effectiveness == 1.0  # it's a Flying move now
    assert res.stab == 1.5
    assert any("becomes flying-type" in n for n in res.notes)


def test_ate_boost_is_stab_times_1_2():
    target = mon("Snorlax")
    sala = mon("Mega Salamence", ["Double-Edge"], "Adamant", {"atk": 252})
    plain = calc_damage(sala, target, "Double-Edge", ruleset=CH)
    sala.ability = "Aerilate"
    ate = calc_damage(sala, target, "Double-Edge", ruleset=CH)
    assert 1.75 <= ate.max_damage / plain.max_damage <= 1.85  # 1.5 * 1.2


def test_dragonize_hits_dragons_super_effectively():
    gatr = mon("Mega Feraligatr", ["Double-Edge"], ability="Dragonize")
    res = calc_damage(gatr, mon("Garchomp"), "Double-Edge", ruleset=CH)
    assert res.type_effectiveness == 2.0


def test_aura_guard_halves_contact_only():
    luc = mon("Mega Lucario Z")
    guarded = mon("Mega Lucario Z", ability="Aura Guard")
    for move, ratio in (("Dragon Claw", 0.5), ("Earthquake", 1.0)):
        base = calc_damage(chomp(), luc, move, ruleset=CH).max_damage
        got = calc_damage(chomp(), guarded, move, ruleset=CH).max_damage
        assert got / base == pytest.approx(ratio, abs=0.02), move


def test_mega_garchomp_z_levitates():
    res = calc_damage(chomp(), mon("Mega Garchomp Z", ability="Levitate"),
                      "Earthquake", ruleset=CH)
    assert res.max_damage == 0


def test_air_balloon_grants_ground_immunity():
    res = calc_damage(chomp(), mon("Gholdengo", item="Air Balloon"),
                      "Earthquake", ruleset=CH)
    assert res.type_effectiveness == 0.0
    assert any("Air Balloon" in n for n in res.notes)
    # and lifts the holder out of terrain effects (no Grassy halving needed)
    hit = calc_damage(chomp(), mon("Gholdengo", item="Air Balloon"),
                      "Dragon Claw", ruleset=CH)
    assert hit.max_damage > 0
    assert not any("not implemented" in n for n in hit.notes)


def test_air_balloon_holder_loses_terrain_boost():
    pinc = mon("Pincurchin", ["Thunderbolt"], "Modest", {"spa": 252})
    target = mon("Azumarill", evs={"hp": 252})
    field = FieldState(terrain="electric")
    grounded = calc_damage(pinc, target, "Thunderbolt", field=field, ruleset=CH)
    pinc.item = "Air Balloon"
    floating = calc_damage(pinc, target, "Thunderbolt", field=field, ruleset=CH)
    assert floating.max_damage < grounded.max_damage


def test_normal_gem_boosts_normal_moves_30_percent():
    target = mon("Snorlax")
    plain = calc_damage(chomp(("Facade",)), target, "Facade", ruleset=CH)
    gem = calc_damage(chomp(("Facade",), item="Normal Gem"), target, "Facade",
                      ruleset=CH)
    assert gem.max_damage / plain.max_damage == pytest.approx(1.3, abs=0.03)
    # a retyped move no longer counts as Normal for the gem
    sala = mon("Mega Salamence", ["Double-Edge"], item="Normal Gem",
               ability="Aerilate")
    sala_plain = mon("Mega Salamence", ["Double-Edge"], ability="Aerilate")
    assert (calc_damage(sala, target, "Double-Edge", ruleset=CH).max_damage
            == calc_damage(sala_plain, target, "Double-Edge", ruleset=CH).max_damage)


def test_fire_mane_and_mega_sol():
    target = mon("Snorlax")
    pyroar = mon("Mega Pyroar", ["Flamethrower"], "Modest", {"spa": 252})
    plain = calc_damage(pyroar, target, "Flamethrower", ruleset=CH)
    pyroar.ability = "Fire Mane"
    maned = calc_damage(pyroar, target, "Flamethrower", ruleset=CH)
    assert maned.max_damage / plain.max_damage == pytest.approx(1.5, abs=0.03)

    zard = mon("Charizard", ["Flamethrower"], "Modest", {"spa": 252})
    in_sun = calc_damage(zard, target, "Flamethrower",
                         field=FieldState(weather="sun"), ruleset=CH)
    zard.ability = "Mega Sol"
    own_sun = calc_damage(zard, target, "Flamethrower", ruleset=CH)
    assert own_sun.rolls == in_sun.rolls


# -------------------------------------------------------------- simulator ---

def test_air_balloon_pops_on_the_first_hit():
    gold = mon("Gholdengo", ["Recover"], evs={"hp": 252, "def": 252},
               item="Air Balloon")
    b = Battle([chomp()], [gold], seed=3)
    g = b.sides[1].active(0)
    assert not g.grounded
    b.step(Action("move", 0), Action("pass"))  # Earthquake: floats over it
    assert g.hp == g.max_hp and g.set.item == "Air Balloon"
    b.step(Action("move", 1), Action("pass"))  # Dragon Claw pops it
    assert g.set.item is None and g.grounded
    hp = g.hp
    b.step(Action("move", 0), Action("pass"))  # now Earthquake lands
    assert g.hp < hp


def test_rocky_helmet_chips_contact_attackers():
    gold = mon("Gholdengo", ["Recover"], evs={"hp": 252}, item="Rocky Helmet")
    b = Battle([chomp()], [gold], seed=3)
    c = b.sides[0].active(0)
    b.step(Action("move", 0), Action("pass"))  # Earthquake: no contact
    assert c.hp == c.max_hp
    b.step(Action("move", 1), Action("pass"))  # Dragon Claw: contact
    assert c.hp == c.max_hp - c.max_hp // 6


def test_rough_skin_chips_contact_attackers():
    attacker = mon("Azumarill", ["Aqua Jet"], "Adamant", {"atk": 252})
    b = Battle([attacker], [chomp(ability="Rough Skin")], seed=3)
    a = b.sides[0].active(0)
    b.step(Action("move", 0), Action("pass"))
    assert a.max_hp - a.hp == a.max_hp // 8


def test_red_card_drags_the_attacker_out():
    gold = mon("Gholdengo", ["Recover"], evs={"hp": 252}, item="Red Card")
    b = Battle([chomp(), mon("Azumarill", ["Aqua Jet"])], [gold], seed=3)
    b.step(Action("move", 1), Action("pass"))
    assert b.sides[0].active(0).set.species == "Azumarill"
    assert b.sides[1].active(0).set.item is None


def test_eject_button_bails_the_holder_out():
    gold = mon("Gholdengo", ["Recover"], evs={"hp": 252}, item="Eject Button")
    b = Battle([chomp()], [gold, mon("Azumarill", ["Aqua Jet"])], seed=3)
    b.step(Action("move", 1), Action("pass"))
    assert b.sides[1].active(0).set.species == "Azumarill"
    assert b.sides[1].team[0].set.item is None


def test_terrain_seed_and_extender():
    rilla = mon("Rillaboom", ["Wood Hammer"], ability="Grassy Surge",
                item="Terrain Extender")
    b = Battle([rilla], [chomp(item="Grassy Seed")], seed=3)
    assert b.field.terrain == "grassy"
    assert b.field_timers["terrain"] == 8
    c = b.sides[1].active(0)
    assert c.boosts["def"] == 1 and c.set.item is None


def test_terrain_seed_on_entry_into_existing_terrain():
    rilla = mon("Rillaboom", ["Wood Hammer"], ability="Grassy Surge")
    b = Battle(
        [rilla],
        [mon("Azumarill", ["Aqua Jet"]), chomp(item="Grassy Seed")],
        seed=3,
    )
    b.step(Action("pass"), Action("switch", 1))
    c = b.sides[1].active(0)
    assert c.set.species == "Garchomp"
    assert c.boosts["def"] == 1 and c.set.item is None


def test_from_state_keeps_unconsumed_seed():
    """The constructor fires entry effects for team[0]; from_state must not
    let that eat a seed the snapshot says is still held (here the seed
    holder is team[0] but sits on the bench in the snapshot)."""
    b = Battle([mon("Azumarill", ["Aqua Jet"])],
               [chomp(item="Grassy Seed"), mon("Azumarill", ["Aqua Jet"])],
               seed=3)
    state = b.snapshot()
    state.field.terrain = "grassy"
    state.sides[1].active = [1]
    b2 = Battle.from_state(state, seed=3)
    benched = b2.sides[1].team[0]
    assert benched.set.item == "Grassy Seed"
    assert b2.sides[1].active(0).set.species == "Azumarill"


def test_binding_band_raises_trap_chip():
    zard = mon("Charizard", ["Fire Spin"], "Modest", {"spa": 252},
               item="Binding Band")
    for seed in range(30):  # Fire Spin is 85% accurate; find a landing seed
        b = Battle([zard], [mon("Snorlax", ["Rest"], evs={"hp": 252})], seed=seed)
        b.step(Action("move", 0), Action("pass"))
        lax = b.sides[1].active(0)
        if lax.trapped_turns > 0:
            break
    else:
        pytest.fail("Fire Spin never landed")
    assert lax.trap_denom == 6
    hp = lax.hp
    b.step(Action("pass"), Action("pass"))
    assert hp - lax.hp == lax.max_hp // 6
    # the chip rate survives a snapshot round trip (recommender rollouts)
    b2 = Battle.from_state(b.snapshot(), seed=1)
    if b2.sides[1].active(0).trapped_turns:
        assert b2.sides[1].active(0).trap_denom == 6


def test_spicy_spray_burns_the_attacker():
    scov = mon("Mega Scovillain", ["Flamethrower"], evs={"hp": 252},
               ability="Spicy Spray")
    b = Battle([chomp()], [scov], seed=3)
    b.step(Action("move", 1), Action("pass"))
    assert b.sides[0].active(0).status == "brn"


def test_phaze_into_eject_button_holder_does_not_crash():
    """Dragon Tail's drag and the Eject Button both want the holder out;
    the button's deferred switch found it already gone and raised
    (found by scripts/eval_recommender_meta.py on real team sheets)."""
    for seed in range(20):
        gold = mon("Gholdengo", ["Recover"], evs={"hp": 252}, item="Eject Button")
        b = Battle(
            [chomp(("Dragon Tail",))],
            [gold, mon("Azumarill", ["Aqua Jet"]), mon("Snorlax", ["Rest"])],
            seed=seed,
        )
        b.step(Action("move", 0), Action("pass"))  # must not raise
        if b.sides[1].team[0].set.item is None:  # the button fired
            assert b.sides[1].active(0).set.species != "Gholdengo"
            return
    pytest.fail("Dragon Tail never connected")
