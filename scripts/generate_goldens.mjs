// Generates golden damage fixtures from @smogon/calc, the reference
// implementation our Python calculator must match.
//
// Usage:
//   cd scripts
//   npm ci
//   node generate_goldens.mjs > ../tests/golden/gen9_cases.json
//   node generate_goldens.mjs champions > ../tests/golden/champions_cases.json
//   node generate_goldens.mjs champions-species > ../tests/golden/champions_species.json
//
// Modes:
//   gen9               damage cases on the small gen9 sample ruleset
//   champions          damage cases on the scraped champions ruleset (names
//                      are ours; mapped to smogon's: Mega Salamence ->
//                      Salamence-Mega). Each case carries a `ref` block —
//                      smogon's stats/types/move BP — so the Python side can
//                      tell a calc divergence from a data divergence.
//   champions-species  smogon's base stats / types / weight for every form
//                      in our species.json: a parity check on the scrape

import { readFileSync } from 'node:fs';
import { calculate, Generations, Pokemon, Move, Field, Side } from '@smogon/calc';

const gen = Generations.get(9);
const MODE = process.argv[2] ?? 'gen9';

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

// --- champions: our species names -> smogon's ---
const REGIONS = { Alolan: 'Alola', Galarian: 'Galar', Hisuian: 'Hisui', Paldean: 'Paldea' };
const FORM_SUFFIX = { Female: 'F', Jumbo: 'Super' };
// forms whose smogon name doesn't follow from ours by rule
const SMOGON_ALIASES = {
  'Aegislash': 'Aegislash-Shield',
  'Eternal Floette': 'Floette-Eternal',
  'Paldean Tauros': 'Tauros-Paldea-Combat',
  'Mega Meowstic': 'Meowstic-M-Mega',
};

function smogonName(ours) {
  if (SMOGON_ALIASES[ours]) return SMOGON_ALIASES[ours];
  let m = ours.match(/^(\w+) Rotom$/); // Heat Rotom -> Rotom-Heat
  if (m) return `Rotom-${m[1]}`;
  m = ours.match(/^Mega (.+?) ([XYZ])$/);
  if (m) return `${m[1]}-Mega-${m[2]}`;
  m = ours.match(/^Mega (.+)$/);
  if (m) return `${m[1]}-Mega`;
  m = ours.match(/^(Alolan|Galarian|Hisuian|Paldean) (\S+)(?: \((\w+) Breed\))?$/);
  if (m) return `${m[2]}-${REGIONS[m[1]]}${m[3] ? '-' + m[3] : ''}`;
  m = ours.match(/^(.+) \((\w+)(?: \w+)?\)$/); // "Lycanroc (Dusk Form)"
  if (m) return `${m[1]}-${FORM_SUFFIX[m[2]] ?? m[2]}`;
  return ours;
}

const toID = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
// smogon defaults an unnamed ability to the species' first one (Mega
// Lucario Z -> Adaptability, as smogon lacks Champions' Aura Guard); our
// sets leave it None. Pin champions cases to an inert name instead.
const NO_ABILITY = 'No Ability';

function makeCase(a, d, moveName, extras = {}) {
  if (MODE === 'champions') {
    a = { ...a, smogon: smogonName(a.species) };
    d = { ...d, smogon: smogonName(d.species) };
  }
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
  const attacker = new Pokemon(gen, a.smogon ?? a.species, {
    level: 50,
    nature: a.nature ?? 'Serious',
    evs: a.evs ?? {},
    item: a.item,
    ability: a.ability ?? (MODE === 'champions' ? NO_ABILITY : undefined),
    status: a.status,
    teraType: a.teraType,
    boosts: extras.attackerBoosts ?? {},
  });
  const defender = new Pokemon(gen, d.smogon ?? d.species, {
    level: 50,
    nature: d.nature ?? 'Serious',
    evs: d.evs ?? {},
    item: d.item,
    ability: d.ability ?? (MODE === 'champions' ? NO_ABILITY : undefined),
    status: d.status,
    teraType: d.teraType,
    boosts: extras.defenderBoosts ?? {},
  });
  if (extras.attackerCurHPFraction !== undefined) {
    attacker.originalCurHP = Math.floor(attacker.maxHP() * extras.attackerCurHPFraction);
  }
  const move = new Move(gen, moveName, {
    isCrit: extras.isCrit ?? false,
    hits: extras.hits,
    timesUsedWithMetronome: extras.metronome,
  });
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
  if (extras.metronome !== undefined) c.metronome_uses = extras.metronome;
  if (MODE === 'champions') {
    const ref = (p) => ({
      smogon: p.name, base_stats: statsOf(p.species), types: p.types.map(toID),
    });
    c.ref = { attacker: ref(attacker), defender: ref(defender),
              move_bp: new Move(gen, moveName).bp, move_type: toID(new Move(gen, moveName).type) };
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

function statsOf(sp) {
  const b = sp.baseStats;
  return { hp: b.hp, atk: b.atk, def: b.def, spa: b.spa, spd: b.spd, spe: b.spe };
}

// --- champions-ruleset damage cases (names are OURS) ---
const C_CHOMP = { species: 'Garchomp', nature: 'Jolly', evs: { atk: 252 } };
const C_LAX = { species: 'Snorlax', nature: 'Careful', evs: { hp: 252 } };
const C_SALA = { species: 'Mega Salamence', nature: 'Adamant', evs: { atk: 252 }, ability: 'Aerilate' };
const championsCases = [
  // Light Ball: Pikachu only, both categories
  [{ species: 'Pikachu', nature: 'Modest', evs: { spa: 252 }, item: 'Light Ball' }, C_LAX, 'Thunderbolt', {}],
  [{ species: 'Pikachu', nature: 'Adamant', evs: { atk: 252 }, item: 'Light Ball' }, C_LAX, 'Volt Tackle', {}],
  [{ species: 'Raichu', nature: 'Modest', evs: { spa: 252 }, item: 'Light Ball' }, C_LAX, 'Thunderbolt', {}],
  // Metronome chain: +819/4096 per repeat, capped at 2x from the 5th
  ...[1, 2, 3, 4, 5, 8].map((n) =>
    [{ ...C_CHOMP, item: 'Metronome' }, C_LAX, 'Dragon Claw', { metronome: n }]),
  [{ ...C_CHOMP, item: 'Metronome' }, C_LAX, 'Dragon Claw', { metronome: 3, isCrit: true }],
  // Iron Ball: Flying holders take Ground NEUTRALLY; Levitate is ignored
  [C_CHOMP, { species: 'Skarmory', item: 'Iron Ball' }, 'Earthquake', {}],
  [C_CHOMP, { species: 'Charizard', item: 'Iron Ball' }, 'Earthquake', {}],
  [C_CHOMP, { species: 'Mega Garchomp Z', ability: 'Levitate', item: 'Iron Ball' }, 'Earthquake', {}],
  [C_CHOMP, { species: 'Mega Garchomp Z', ability: 'Levitate' }, 'Earthquake', {}],
  [C_CHOMP, { species: 'Skarmory' }, 'Earthquake', {}],
  // Air Balloon: Ground immunity only
  [C_CHOMP, { species: 'Gholdengo', item: 'Air Balloon' }, 'Earthquake', {}],
  [C_CHOMP, { species: 'Gholdengo', item: 'Air Balloon' }, 'Dragon Claw', {}],
  // Normal Gem (and -ate moves are no longer Normal, so no gem)
  [{ ...C_CHOMP, item: 'Normal Gem' }, C_LAX, 'Double-Edge', {}],
  [{ ...C_SALA, item: 'Normal Gem' }, C_LAX, 'Double-Edge', {}],
  // -ate abilities: retype + 1.2x, STAB on the new type
  [C_SALA, C_LAX, 'Double-Edge', {}],
  [C_SALA, { species: 'Gengar' }, 'Double-Edge', {}],          // Normal immune -> hits
  [C_SALA, { species: 'Heracross' }, 'Body Slam', {}],         // 4x as Flying
  [C_SALA, C_LAX, 'Extreme Speed', { field: { terrain: 'Electric' } }],
  [{ species: 'Mega Gardevoir', nature: 'Modest', evs: { spa: 252 }, ability: 'Pixilate' },
    { species: 'Garchomp' }, 'Hyper Voice', {}],
  [{ species: 'Mega Gardevoir', nature: 'Modest', evs: { spa: 252 }, ability: 'Pixilate' },
    { species: 'Garchomp' }, 'Hyper Voice', { spread: true }],
  // the new M-C Megas as plain stat blocks (data + formula parity)
  [{ species: 'Mega Garchomp Z', nature: 'Timid', evs: { spa: 252 } }, C_LAX, 'Draco Meteor', {}],
  [{ species: 'Mega Lucario Z', nature: 'Timid', evs: { spa: 252 } }, C_LAX, 'Flash Cannon', {}],
  [{ species: 'Mega Absol Z', nature: 'Jolly', evs: { atk: 252 } }, { species: 'Gholdengo' }, 'Shadow Claw', {}],
  [{ species: 'Mega Baxcalibur', nature: 'Adamant', evs: { atk: 252 } }, { species: 'Garchomp' }, 'Icicle Crash', {}],
  [{ species: 'Mega Golisopod', nature: 'Adamant', evs: { atk: 252 }, ability: 'Tough Claws' }, C_LAX, 'Close Combat', {}],
  [C_CHOMP, { species: 'Mega Lucario Z' }, 'Earthquake', {}],
  [C_CHOMP, { species: 'Indeedee (Female)' }, 'Dragon Claw', {}],
];

function speciesParity() {
  const path = new URL('../src/pokechamp/data/rulesets/champions/species.json', import.meta.url);
  const ours = JSON.parse(readFileSync(path, 'utf-8'));
  return Object.keys(ours).map((name) => {
    const sp = gen.species.get(toID(smogonName(name)));
    if (!sp) return { species: name, smogon: null };
    return {
      species: name, smogon: sp.name, base_stats: statsOf(sp),
      types: sp.types.map(toID), weight: sp.weightkg,
    };
  });
}

let out;
if (MODE === 'gen9') {
  out = [];
  for (const a of basicAttackers) {
    for (const d of basicDefenders) {
      for (const move of basicMoves) {
        out.push(makeCase(a, d, move));
      }
    }
  }
  for (const [a, d, move, extras] of specialCases) {
    out.push(makeCase(a, d, move, extras));
  }
} else if (MODE === 'champions') {
  out = championsCases.map(([a, d, move, extras]) => makeCase(a, d, move, extras));
} else if (MODE === 'champions-species') {
  out = speciesParity();
} else {
  throw new Error(`unknown mode ${MODE}`);
}

console.log(JSON.stringify(out, null, 2));
