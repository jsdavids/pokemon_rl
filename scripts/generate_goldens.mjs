// Generates golden damage fixtures from @smogon/calc, the reference
// implementation our Python calculator must match.
//
// Usage:
//   cd scripts
//   npm install @smogon/calc
//   node generate_goldens.mjs > ../tests/golden/gen9_cases.json

import { calculate, Generations, Pokemon, Move, Field, Side } from '@smogon/calc';

const gen = Generations.get(9);

// Keep species/moves in sync with src/pokechamp/data/rulesets/gen9/.
// Extend freely: more cases = better gate.
const basicAttackers = [
  { species: 'Garchomp', nature: 'Jolly', evs: { atk: 252 } },
  { species: 'Garchomp', nature: 'Adamant', evs: { atk: 252 } },
  { species: 'Gholdengo', nature: 'Modest', evs: { spa: 252 } },
  { species: 'Iron Valiant', nature: 'Timid', evs: { spa: 252 } },
];
const basicDefenders = [
  { species: 'Great Tusk' },
  { species: 'Great Tusk', nature: 'Impish', evs: { hp: 252, def: 124 } },
  { species: 'Amoonguss', nature: 'Calm', evs: { hp: 244, spd: 156 } },
  { species: 'Dragonite' },
];
const basicMoves = ['Earthquake', 'Dragon Claw', 'Close Combat', 'Make It Rain', 'Shadow Ball', 'Moonblast', 'Ice Beam'];

const CHOMP = { species: 'Garchomp', nature: 'Jolly', evs: { atk: 252 } };
const GHOLD = { species: 'Gholdengo', nature: 'Modest', evs: { spa: 252 } };
const VALIANT = { species: 'Iron Valiant', nature: 'Timid', evs: { spa: 252 } };
const TUSK = { species: 'Great Tusk' };
const TTAR = { species: 'Tyranitar' };

// [attacker, defender, move, extras]
const specialCases = [
  // --- items ---
  [{ ...CHOMP, item: 'Choice Band' }, TUSK, 'Earthquake', {}],
  [{ ...CHOMP, item: 'Life Orb' }, TUSK, 'Earthquake', {}],
  [{ ...CHOMP, item: 'Life Orb' }, { species: 'Gholdengo' }, 'Earthquake', {}],
  [{ ...CHOMP, item: 'Expert Belt' }, { species: 'Gholdengo' }, 'Earthquake', {}],
  [{ ...CHOMP, item: 'Expert Belt' }, TUSK, 'Earthquake', {}], // neutral: no boost
  [{ ...CHOMP, item: 'Soft Sand' }, TUSK, 'Earthquake', {}],
  [{ ...CHOMP, item: 'Muscle Band' }, TUSK, 'Earthquake', {}],
  [{ ...GHOLD, item: 'Choice Specs' }, TUSK, 'Make It Rain', {}],
  [{ ...GHOLD, item: 'Wise Glasses' }, TUSK, 'Shadow Ball', {}],
  [GHOLD, { ...TUSK, item: 'Assault Vest' }, 'Make It Rain', {}],
  [{ species: 'Dragonite', nature: 'Adamant', evs: { atk: 252 }, item: 'Silk Scarf' },
    { species: 'Amoonguss' }, 'Extreme Speed', {}],
  // --- boosts ---
  [CHOMP, TUSK, 'Earthquake', { attackerBoosts: { atk: 2 } }],
  [CHOMP, TUSK, 'Earthquake', { attackerBoosts: { atk: -1 } }],
  [CHOMP, TUSK, 'Earthquake', { defenderBoosts: { def: 1 } }],
  [GHOLD, TUSK, 'Make It Rain', { attackerBoosts: { spa: 1 }, defenderBoosts: { spd: -1 } }],
  // --- crits ---
  [CHOMP, TUSK, 'Earthquake', { isCrit: true }],
  [CHOMP, TUSK, 'Earthquake', { isCrit: true, defenderBoosts: { def: 2 } }],
  [CHOMP, TUSK, 'Earthquake', { isCrit: true, attackerBoosts: { atk: -2 } }],
  [{ ...CHOMP, item: 'Life Orb' }, TUSK, 'Earthquake', { attackerBoosts: { atk: 1 } }],
  // --- weather ---
  [VALIANT, { species: 'Amoonguss' }, 'Flamethrower', { field: { weather: 'Sun' } }],
  [VALIANT, { species: 'Amoonguss' }, 'Flamethrower', { field: { weather: 'Rain' } }],
  [{ species: 'Azumarill', nature: 'Modest', evs: { spa: 252 } }, TTAR, 'Surf', { field: { weather: 'Rain' } }],
  [{ species: 'Azumarill', nature: 'Modest', evs: { spa: 252 } }, TTAR, 'Surf', { field: { weather: 'Sun' } }],
  [GHOLD, TTAR, 'Shadow Ball', { field: { weather: 'Sand' } }], // sand SpD boost for Rock
  [CHOMP, TTAR, 'Earthquake', { field: { weather: 'Sand' } }],  // physical: no sand boost
  // --- terrain ---
  [GHOLD, { species: 'Azumarill' }, 'Thunderbolt', { field: { terrain: 'Electric' } }],
  [{ species: 'Dragonite', nature: 'Modest', evs: { spa: 252 } }, { species: 'Azumarill' }, 'Thunderbolt', { field: { terrain: 'Electric' } }], // flier: no boost
  [CHOMP, TUSK, 'Earthquake', { field: { terrain: 'Grassy' } }],
  [CHOMP, TUSK, 'Dragon Claw', { field: { terrain: 'Misty' } }],
  [{ species: 'Iron Valiant', nature: 'Timid', evs: { spa: 252 } }, { species: 'Amoonguss' }, 'Psyshock', { field: { terrain: 'Psychic' } }],
  // --- screens ---
  [CHOMP, TUSK, 'Earthquake', { field: { reflect: true } }],
  [GHOLD, TUSK, 'Make It Rain', { field: { light_screen: true } }],
  [CHOMP, TUSK, 'Earthquake', { field: { aurora_veil: true } }],
  [CHOMP, TUSK, 'Earthquake', { isCrit: true, field: { reflect: true } }], // crit ignores screens
  // --- spread (doubles) ---
  [CHOMP, TUSK, 'Earthquake', { spread: true }],
  [{ species: 'Azumarill', nature: 'Modest', evs: { spa: 252 } }, TUSK, 'Surf', { spread: true }],
  // --- tera ---
  [{ ...CHOMP, teraType: 'Ground' }, TUSK, 'Earthquake', {}],
  [{ ...GHOLD, teraType: 'Steel' }, TUSK, 'Make It Rain', {}],
  [{ ...CHOMP, teraType: 'Fire', nature: 'Modest', evs: { spa: 252 } }, { species: 'Amoonguss' }, 'Flamethrower', {}],
  [CHOMP, { species: 'Dragonite', teraType: 'Steel' }, 'Earthquake', {}], // no longer immune
  [CHOMP, { ...TUSK, teraType: 'Flying' }, 'Earthquake', {}],             // now immune
  [GHOLD, { ...TTAR, teraType: 'Fairy' }, 'Shadow Ball', {}],             // loses Dark resist
  // --- abilities ---
  [{ species: 'Azumarill', nature: 'Adamant', evs: { atk: 252 }, ability: 'Huge Power' }, TUSK, 'Play Rough', {}],
  [{ ...CHOMP, ability: 'Guts', status: 'brn' }, TUSK, 'Earthquake', {}],
  [{ species: 'Dragonite', nature: 'Adamant', evs: { atk: 252 }, status: 'brn' }, TUSK, 'Facade', {}],
  [{ ...CHOMP, ability: 'Technician' }, TTAR, 'Bullet Punch', {}],
  [{ ...CHOMP, ability: 'Adaptability' }, TUSK, 'Earthquake', {}],
  [VALIANT, { species: 'Amoonguss', ability: 'Thick Fat' }, 'Ice Beam', {}],
  [CHOMP, { ...TUSK, ability: 'Fur Coat' }, 'Earthquake', {}],
  [CHOMP, { species: 'Dragonite', ability: 'Multiscale' }, 'Dragon Claw', {}],
  [VALIANT, { species: 'Garchomp', ability: 'Filter' }, 'Ice Beam', {}],
  [{ ...GHOLD, ability: 'Tinted Lens' }, TTAR, 'Shadow Ball', {}],
  [GHOLD, { species: 'Amoonguss', ability: 'Ice Scales' }, 'Make It Rain', {}],
  [CHOMP, { ...TUSK, ability: 'Marvel Scale', status: 'brn' }, 'Earthquake', {}],
  [CHOMP, { ...TTAR, ability: 'Levitate' }, 'Earthquake', {}],
  [{ species: 'Azumarill', nature: 'Modest', evs: { spa: 252 } }, { ...TUSK, ability: 'Water Absorb' }, 'Surf', {}],
  [VALIANT, { species: 'Amoonguss', ability: 'Flash Fire' }, 'Flamethrower', {}],
  // --- move flags: contact / sound / punch / bite / slicing ---
  [{ ...CHOMP, ability: 'Tough Claws' }, TUSK, 'Dragon Claw', {}],
  [{ ...CHOMP, ability: 'Tough Claws' }, TUSK, 'Earthquake', {}], // no contact: no boost
  [{ ...CHOMP, ability: 'Iron Fist' }, TUSK, 'Ice Punch', {}],
  [{ ...CHOMP, item: 'Punching Glove' }, TUSK, 'Ice Punch', {}],
  // Punching Glove negates contact, so Fluffy's halving must NOT apply:
  [{ ...CHOMP, item: 'Punching Glove' }, { ...TUSK, ability: 'Fluffy' }, 'Ice Punch', {}],
  [CHOMP, { ...TUSK, ability: 'Fluffy' }, 'Close Combat', {}],   // contact: halved
  [VALIANT, { species: 'Amoonguss', ability: 'Fluffy' }, 'Flamethrower', {}], // fire: doubled
  [VALIANT, { species: 'Amoonguss', ability: 'Punk Rock' }, 'Hyper Voice', {}],
  [{ ...VALIANT, ability: 'Punk Rock' }, { species: 'Amoonguss' }, 'Hyper Voice', {}],
  [{ species: 'Tyranitar', nature: 'Adamant', evs: { atk: 252 }, ability: 'Strong Jaw' },
    { species: 'Gholdengo' }, 'Crunch', {}],
  [{ ...CHOMP, ability: 'Sharpness' }, { species: 'Azumarill' }, 'Leaf Blade', {}],
  // --- multi-hit ---
  [{ species: 'Cloyster', nature: 'Jolly', evs: { atk: 252 } }, TUSK, 'Icicle Spear', { hits: 3 }],
  [{ species: 'Cloyster', nature: 'Jolly', evs: { atk: 252 } }, TUSK, 'Icicle Spear', { hits: 5 }],
  [{ species: 'Cloyster', nature: 'Jolly', evs: { atk: 252 } }, { species: 'Gholdengo' }, 'Rock Blast', { hits: 2 }],
  [CHOMP, TUSK, 'Bonemerang', {}],   // fixed 2 hits
  [{ species: 'Azumarill', nature: 'Adamant', evs: { atk: 252 }, ability: 'Huge Power' }, TUSK, 'Triple Dive', {}], // fixed 3 hits + ability
  [{ species: 'Cloyster', nature: 'Jolly', evs: { atk: 252 }, ability: 'Technician' }, TUSK, 'Icicle Spear', { hits: 3 }],
  // --- weight & current-HP scaled base power ---
  [{ ...CHOMP, nature: 'Modest', evs: { spa: 252 } }, TUSK, 'Grass Knot', {}],          // 320 kg -> 120 BP
  [{ ...CHOMP, nature: 'Modest', evs: { spa: 252 } }, { species: 'Amoonguss' }, 'Grass Knot', {}], // 10.5 kg -> 40
  [CHOMP, { species: 'Cloyster' }, 'Low Kick', {}],   // 132.5 kg -> 100
  [CHOMP, { species: 'Gholdengo' }, 'Low Kick', {}],  // 30 kg -> 60
  [{ species: 'Great Tusk', nature: 'Adamant', evs: { atk: 252 } }, { species: 'Amoonguss' }, 'Heavy Slam', {}], // 320/10.5 -> 120
  [{ species: 'Tyranitar', nature: 'Adamant', evs: { atk: 252 } }, { species: 'Garchomp' }, 'Heavy Slam', {}],   // 202/95 -> 60
  [{ species: 'Tyranitar', nature: 'Modest', evs: { spa: 252 } }, { species: 'Corviknight' }, 'Eruption', {}],   // full HP: 150
  [{ species: 'Tyranitar', nature: 'Modest', evs: { spa: 252 } }, { species: 'Corviknight' }, 'Eruption', { attackerCurHPFraction: 0.5 }],
  [{ species: 'Tyranitar', nature: 'Modest', evs: { spa: 252 } }, { species: 'Corviknight' }, 'Eruption', { attackerCurHPFraction: 0.1 }],
  // --- move special cases ---
  [{ species: 'Corviknight', nature: 'Impish', evs: { def: 252 } }, TTAR, 'Body Press', {}],
  [{ species: 'Corviknight', nature: 'Impish', evs: { def: 252 } }, TTAR, 'Body Press', { attackerBoosts: { def: 2 } }],
  [VALIANT, { species: 'Amoonguss', nature: 'Calm', evs: { hp: 244, spd: 156 } }, 'Psyshock', {}],
  [TUSK, { species: 'Amoonguss' }, 'Seismic Toss', {}],
  [TUSK, { species: 'Gholdengo' }, 'Seismic Toss', {}], // Ghost: immune
  [{ species: 'Amoonguss' }, TUSK, 'Night Shade', {}],
];

function zeroEvs() {
  return { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
}

function pokemonJson(p) {
  const out = { species: p.species, level: 50, nature: p.nature ?? 'Serious', evs: { ...zeroEvs(), ...(p.evs ?? {}) } };
  if (p.item) out.item = p.item;
  if (p.ability) out.ability = p.ability;
  if (p.status) out.status = p.status;
  if (p.teraType) out.tera_type = p.teraType;
  return out;
}

function makeCase(a, d, moveName, extras = {}) {
  const f = extras.field ?? {};
  const field = new Field({
    gameType: extras.spread ? 'Doubles' : 'Singles',
    weather: f.weather,
    terrain: f.terrain,
    defenderSide: new Side({
      isReflect: !!f.reflect,
      isLightScreen: !!f.light_screen,
      isAuroraVeil: !!f.aurora_veil,
    }),
  });
  const attacker = new Pokemon(gen, a.species, {
    level: 50,
    nature: a.nature ?? 'Serious',
    evs: a.evs ?? {},
    item: a.item,
    ability: a.ability,
    status: a.status,
    teraType: a.teraType,
    boosts: extras.attackerBoosts ?? {},
  });
  const defender = new Pokemon(gen, d.species, {
    level: 50,
    nature: d.nature ?? 'Serious',
    evs: d.evs ?? {},
    item: d.item,
    ability: d.ability,
    status: d.status,
    teraType: d.teraType,
    boosts: extras.defenderBoosts ?? {},
  });
  if (extras.attackerCurHPFraction !== undefined) {
    attacker.originalCurHP = Math.floor(attacker.maxHP() * extras.attackerCurHPFraction);
  }
  const move = new Move(gen, moveName, { isCrit: extras.isCrit ?? false, hits: extras.hits });
  const result = calculate(gen, attacker, defender, move, field);

  let rolls = Array.isArray(result.damage) ? [...result.damage] : [result.damage];
  let hits = 1;
  if (Array.isArray(rolls[0])) {
    // multi-hit: one 16-roll array per hit; ours must all be identical
    hits = rolls.length;
    const first = JSON.stringify(rolls[0]);
    if (!rolls.every((h) => JSON.stringify(h) === first)) {
      throw new Error(`per-hit rolls differ for ${moveName}; case not supported yet`);
    }
    rolls = rolls[0];
  }
  if (rolls.length === 1) rolls = Array(16).fill(rolls[0]);

  const c = { attacker: pokemonJson(a), defender: pokemonJson(d), move: moveName, rolls };
  if (hits > 1) c.hits = hits;
  if (extras.attackerBoosts) c.attacker_boosts = extras.attackerBoosts;
  if (extras.defenderBoosts) c.defender_boosts = extras.defenderBoosts;
  if (extras.isCrit) c.is_crit = true;
  if (extras.spread) c.spread = true;
  if (extras.attackerCurHPFraction !== undefined) {
    c.attacker_hp_fraction = extras.attackerCurHPFraction;
  }
  if (extras.field) {
    c.field = {};
    if (f.weather) c.field.weather = f.weather.toLowerCase();
    if (f.terrain) c.field.terrain = f.terrain.toLowerCase();
    for (const k of ['reflect', 'light_screen', 'aurora_veil']) {
      if (f[k]) c.field[k] = true;
    }
  }
  return c;
}

const cases = [];
for (const a of basicAttackers) {
  for (const d of basicDefenders) {
    for (const move of basicMoves) {
      cases.push(makeCase(a, d, move));
    }
  }
}
for (const [a, d, move, extras] of specialCases) {
  cases.push(makeCase(a, d, move, extras));
}

console.log(JSON.stringify(cases, null, 2));
