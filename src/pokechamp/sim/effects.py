"""Hand-curated move effects for the simulator (Phase 3).

Serebii's effect text is prose, so the sim doesn't parse it — the common
competitive moves are compiled here by hand instead, and everything else
stays a no-op. Coverage is intentionally the moves that decide real games:
setup, recovery, status, screens, speed control, protection, redirection,
and the big secondary effects.

Percentages are effect rates (0-100). Stat deltas are stage changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StatusMoveEffect:
    """What a status move does when it executes."""

    boosts_self: dict[str, int] = field(default_factory=dict)
    boosts_target: dict[str, int] = field(default_factory=dict)
    status_target: str | None = None  # "slp", "par", "brn", "tox", "psn"
    heal_fraction: float = 0.0  # of user's max HP
    protect: bool = False
    wide_guard: bool = False
    redirect: bool = False  # Follow Me / Rage Powder
    helping_hand: bool = False
    side_condition: str | None = None  # "reflect", "light_screen", "aurora_veil", "tailwind"
    weather: str | None = None
    terrain: str | None = None
    trick_room: bool = False
    hazard: str | None = None  # set on the FOE side: "stealth_rock",
    #                            "spikes", "toxic_spikes", "sticky_web"
    clear_hazards: bool = False  # Defog: both sides' hazards + foe screens
    substitute: bool = False  # 1/4 max HP -> a damage-absorbing doll
    taunt: bool = False  # target loses status moves for 3 turns
    encore: bool = False  # target repeats its last move for 3 turns
    confuse_target: bool = False  # 2-5 turns, 33% self-hit per action
    baton_pass: bool = False  # switch out, handing over boosts + substitute
    transform: bool = False  # copy the target (Imposter uses this too)
    disable: bool = False  # target's last move unusable for 4 turns
    perish_song: bool = False  # every hearer faints in 3 turns unless it switches
    phaze: bool = False  # Whirlwind/Roar: target dragged to a random teammate
    destiny_bond: bool = False  # KO the user before its next move -> KO back
    wish: bool = False  # heal this slot for 1/2 user's max HP next turn's end
    heal_team_status: bool = False  # Heal Bell / Aromatherapy
    crit_boost: int = 0  # Focus Energy: +2 crit stages until it switches out
    crit_boost_ally: bool = False  # Dragon Cheer: ally +2 if Dragon, else +1


@dataclass(frozen=True)
class SecondaryEffect:
    """A damaging move's rider."""

    chance: int  # percent
    status: str | None = None
    flinch: bool = False
    boosts_target: dict[str, int] = field(default_factory=dict)
    boosts_self: dict[str, int] = field(default_factory=dict)  # Close Combat
    phaze: bool = False  # Dragon Tail / Circle Throw


def _b(**kw) -> dict[str, int]:
    return dict(kw)


STATUS_MOVES: dict[str, StatusMoveEffect] = {
    # setup
    "Swords Dance": StatusMoveEffect(boosts_self=_b(atk=2)),
    "Nasty Plot": StatusMoveEffect(boosts_self=_b(spa=2)),
    "Dragon Dance": StatusMoveEffect(boosts_self=_b(atk=1, spe=1)),
    "Calm Mind": StatusMoveEffect(boosts_self=_b(spa=1, spd=1)),
    "Bulk Up": StatusMoveEffect(boosts_self=_b(atk=1, **{"def": 1})),
    "Iron Defense": StatusMoveEffect(boosts_self=_b(**{"def": 2})),
    "Agility": StatusMoveEffect(boosts_self=_b(spe=2)),
    "Quiver Dance": StatusMoveEffect(boosts_self=_b(spa=1, spd=1, spe=1)),
    "Shell Smash": StatusMoveEffect(
        boosts_self=_b(atk=2, spa=2, spe=2, spd=-1, **{"def": -1})
    ),
    "Growth": StatusMoveEffect(boosts_self=_b(atk=1, spa=1)),
    # target stat drops
    "Charm": StatusMoveEffect(boosts_target=_b(atk=-2)),
    "Growl": StatusMoveEffect(boosts_target=_b(atk=-1)),
    "Screech": StatusMoveEffect(boosts_target=_b(**{"def": -2})),
    "Fake Tears": StatusMoveEffect(boosts_target=_b(spd=-2)),
    "Scary Face": StatusMoveEffect(boosts_target=_b(spe=-2)),
    # status infliction (accuracy comes from move data)
    "Spore": StatusMoveEffect(status_target="slp"),
    "Sleep Powder": StatusMoveEffect(status_target="slp"),
    "Hypnosis": StatusMoveEffect(status_target="slp"),
    "Thunder Wave": StatusMoveEffect(status_target="par"),
    "Will-O-Wisp": StatusMoveEffect(status_target="brn"),
    "Toxic": StatusMoveEffect(status_target="tox"),
    # recovery
    "Recover": StatusMoveEffect(heal_fraction=0.5),
    "Roost": StatusMoveEffect(heal_fraction=0.5),
    "Slack Off": StatusMoveEffect(heal_fraction=0.5),
    "Soft-Boiled": StatusMoveEffect(heal_fraction=0.5),
    "Morning Sun": StatusMoveEffect(heal_fraction=0.5),
    "Moonlight": StatusMoveEffect(heal_fraction=0.5),
    "Synthesis": StatusMoveEffect(heal_fraction=0.5),
    # protection & doubles support
    "Protect": StatusMoveEffect(protect=True),
    "Detect": StatusMoveEffect(protect=True),
    "Spiky Shield": StatusMoveEffect(protect=True),
    "Baneful Bunker": StatusMoveEffect(protect=True),
    "Wide Guard": StatusMoveEffect(wide_guard=True),
    "Follow Me": StatusMoveEffect(redirect=True),
    "Rage Powder": StatusMoveEffect(redirect=True),
    "Helping Hand": StatusMoveEffect(helping_hand=True),
    # side conditions
    "Reflect": StatusMoveEffect(side_condition="reflect"),
    "Light Screen": StatusMoveEffect(side_condition="light_screen"),
    "Aurora Veil": StatusMoveEffect(side_condition="aurora_veil"),
    "Tailwind": StatusMoveEffect(side_condition="tailwind"),
    # substitute & team protection
    "Substitute": StatusMoveEffect(substitute=True),
    "Safeguard": StatusMoveEffect(side_condition="safeguard"),
    # locks, confusion, pivots
    "Taunt": StatusMoveEffect(taunt=True),
    "Encore": StatusMoveEffect(encore=True),
    "Confuse Ray": StatusMoveEffect(confuse_target=True),
    "Swagger": StatusMoveEffect(confuse_target=True, boosts_target=_b(atk=2)),
    "Baton Pass": StatusMoveEffect(baton_pass=True),
    "Transform": StatusMoveEffect(transform=True),
    "Disable": StatusMoveEffect(disable=True),
    "Perish Song": StatusMoveEffect(perish_song=True),
    "Whirlwind": StatusMoveEffect(phaze=True),
    "Roar": StatusMoveEffect(phaze=True),
    "Destiny Bond": StatusMoveEffect(destiny_bond=True),
    "Wish": StatusMoveEffect(wish=True),
    "Heal Bell": StatusMoveEffect(heal_team_status=True),
    "Aromatherapy": StatusMoveEffect(heal_team_status=True),
    # crit setup (one crit volatile: neither stacks with the other)
    "Focus Energy": StatusMoveEffect(crit_boost=2),
    "Dragon Cheer": StatusMoveEffect(crit_boost_ally=True),
    # hazards
    "Stealth Rock": StatusMoveEffect(hazard="stealth_rock"),
    "Spikes": StatusMoveEffect(hazard="spikes"),
    "Toxic Spikes": StatusMoveEffect(hazard="toxic_spikes"),
    "Sticky Web": StatusMoveEffect(hazard="sticky_web"),
    "Defog": StatusMoveEffect(clear_hazards=True),
    # field
    "Rain Dance": StatusMoveEffect(weather="rain"),
    "Sunny Day": StatusMoveEffect(weather="sun"),
    "Sandstorm": StatusMoveEffect(weather="sand"),
    "Snowscape": StatusMoveEffect(weather="snow"),
    "Electric Terrain": StatusMoveEffect(terrain="electric"),
    "Grassy Terrain": StatusMoveEffect(terrain="grassy"),
    "Psychic Terrain": StatusMoveEffect(terrain="psychic"),
    "Misty Terrain": StatusMoveEffect(terrain="misty"),
    "Trick Room": StatusMoveEffect(trick_room=True),
}

SECONDARY_EFFECTS: dict[str, SecondaryEffect] = {
    # status riders
    "Scald": SecondaryEffect(30, status="brn"),
    "Flamethrower": SecondaryEffect(10, status="brn"),
    "Fire Blast": SecondaryEffect(10, status="brn"),
    "Heat Wave": SecondaryEffect(10, status="brn"),
    "Fire Punch": SecondaryEffect(10, status="brn"),
    "Thunderbolt": SecondaryEffect(10, status="par"),
    "Discharge": SecondaryEffect(30, status="par"),
    "Thunder": SecondaryEffect(30, status="par"),
    "Thunder Punch": SecondaryEffect(10, status="par"),
    "Body Slam": SecondaryEffect(30, status="par"),
    "Ice Beam": SecondaryEffect(10, status="frz"),
    "Blizzard": SecondaryEffect(10, status="frz"),
    "Ice Punch": SecondaryEffect(10, status="frz"),
    "Sludge Bomb": SecondaryEffect(30, status="psn"),
    "Poison Jab": SecondaryEffect(30, status="psn"),
    "Gunk Shot": SecondaryEffect(30, status="psn"),
    # flinch
    "Fake Out": SecondaryEffect(100, flinch=True),
    "Rock Slide": SecondaryEffect(30, flinch=True),
    "Iron Head": SecondaryEffect(30, flinch=True),
    "Air Slash": SecondaryEffect(30, flinch=True),
    "Icicle Crash": SecondaryEffect(30, flinch=True),
    "Waterfall": SecondaryEffect(20, flinch=True),
    "Dark Pulse": SecondaryEffect(20, flinch=True),
    "Zen Headbutt": SecondaryEffect(20, flinch=True),
    "Bite": SecondaryEffect(30, flinch=True),
    # stat-drop riders
    "Icy Wind": SecondaryEffect(100, boosts_target=_b(spe=-1)),
    "Electroweb": SecondaryEffect(100, boosts_target=_b(spe=-1)),
    "Bulldoze": SecondaryEffect(100, boosts_target=_b(spe=-1)),
    "Moonblast": SecondaryEffect(30, boosts_target=_b(spa=-1)),
    "Shadow Ball": SecondaryEffect(20, boosts_target=_b(spd=-1)),
    "Earth Power": SecondaryEffect(10, boosts_target=_b(spd=-1)),
    "Crunch": SecondaryEffect(20, boosts_target=_b(**{"def": -1})),
    "Play Rough": SecondaryEffect(10, boosts_target=_b(atk=-1)),
    "Breaking Swipe": SecondaryEffect(100, boosts_target=_b(atk=-1)),
    "Snarl": SecondaryEffect(100, boosts_target=_b(spa=-1)),
    # self-drop costs
    "Close Combat": SecondaryEffect(100, boosts_self=_b(spd=-1, **{"def": -1})),
    "Superpower": SecondaryEffect(100, boosts_self=_b(atk=-1, **{"def": -1})),
    "Make It Rain": SecondaryEffect(100, boosts_self=_b(spa=-1)),
    "Leaf Storm": SecondaryEffect(100, boosts_self=_b(spa=-2)),
    "Draco Meteor": SecondaryEffect(100, boosts_self=_b(spa=-2)),
    "Overheat": SecondaryEffect(100, boosts_self=_b(spa=-2)),
    # utility riders (Rapid Spin also clears the user's hazards — engine)
    "Rapid Spin": SecondaryEffect(100, boosts_self=_b(spe=1)),
    # phazing riders
    "Dragon Tail": SecondaryEffect(100, phaze=True),
    "Circle Throw": SecondaryEffect(100, phaze=True),
}

# damaging moves that bind the target: no switching + 1/8 chip for 4-5 turns
TRAP_MOVES = {
    "Fire Spin", "Whirlpool", "Sand Tomb", "Infestation", "Magma Storm",
    "Thunder Cage", "Snap Trap",
}

# ability -> who it traps ("all", "grounded", "steel"); Ghosts always escape
TRAPPING_ABILITIES = {
    "shadowtag": "all",
    "arenatrap": "grounded",
    "magnetpull": "steel",
}

# switch-in field setters — the engines of "weather wars": on simultaneous
# entry the slower Pokemon's ability triggers last and wins
WEATHER_ABILITIES = {
    "drizzle": "rain", "drought": "sun",
    "sandstream": "sand", "snowwarning": "snow",
}
TERRAIN_ABILITIES = {
    "electricsurge": "electric", "grassysurge": "grassy",
    "mistysurge": "misty", "psychicsurge": "psychic",
}
# held item extending a matching weather to 8 turns (all in Champions' M-B pool)
WEATHER_ROCKS = {
    "rain": "damprock", "sun": "heatrock", "sand": "smoothrock", "snow": "icyrock",
}
TERRAIN_EXTENDER = "terrainextender"  # M-C: the setter's terrain lasts 8 turns
# M-C terrain seeds: norm(item) -> (terrain, stat +1), consumed when the
# matching terrain is up (on set, or on the holder's entry into it)
TERRAIN_SEEDS: dict[str, tuple[str, str]] = {
    "electricseed": ("electric", "def"),
    "grassyseed": ("grassy", "def"),
    "psychicseed": ("psychic", "spd"),
    "mistyseed": ("misty", "spd"),
}

# punishing contact: holder's ability/item -> fraction of the ATTACKER's
# max HP lost (denominator). Rocky Helmet is new in M-C.
CONTACT_CHIP_ABILITIES = {"roughskin": 8, "ironbarbs": 8}
ROCKY_HELMET_DENOM = 6
BINDING_BAND_DENOM = 6  # M-C: binding-move chip 1/6 instead of 1/8
TRAP_CHIP_DENOM = 8

# --- critical hits (Gen 7+ stage ladder over Champions' 1/24 base) ---
CRIT_RATES = (1 / 24, 1 / 8, 1 / 2, 1.0)  # stage 0, +1, +2, +3 and up
HIGH_CRIT_MOVES = {
    "Aeroblast", "Air Cutter", "Aqua Cutter", "Attack Order", "Blaze Kick",
    "Crabhammer", "Cross Chop", "Cross Poison", "Dire Claw", "Drill Run",
    "Esper Wing", "Ivy Cudgel", "Karate Chop", "Leaf Blade", "Night Slash",
    "Poison Tail", "Psycho Cut", "Razor Leaf", "Razor Wind", "Shadow Claw",
    "Sky Attack", "Slash", "Snipe Shot", "Spacial Rend", "Stone Edge",
    "Triple Arrows",
}
ALWAYS_CRIT_MOVES = {
    "Flower Trick", "Frost Breath", "Storm Throw", "Surging Strikes",
    "Wicked Blow", "Zippy Zap",
}
CRIT_IMMUNE_ABILITIES = {"battlearmor", "shellarmor"}
LEEK_SPECIES = ("farfetchd", "sirfetchd")  # norm()ed prefixes (incl. Galarian)

# abilities that make the holder immune to non-volatile statuses
STATUS_IMMUNE_ABILITIES: dict[str, tuple[str, ...]] = {
    "thermalexchange": ("brn",),  # Mega/Baxcalibur
    "waterveil": ("brn",),
    "waterbubble": ("brn",),
    "limber": ("par",),
    "insomnia": ("slp",),
    "vitalspirit": ("slp",),
    "sweetveil": ("slp",),
    "immunity": ("psn", "tox"),
    "pastelveil": ("psn", "tox"),
    "magmaarmor": ("frz",),
    "purifyingsalt": ("brn", "par", "slp", "psn", "tox", "frz"),
    "comatose": ("brn", "par", "slp", "psn", "tox", "frz"),
}

# ability -> stat raised by 1 when its attack KOs a target. None = the
# holder's highest raw stat (ties: Atk > Def > SpA > SpD > Spe).
# Eelevate (Mega Eelektross) boosts like Beast Boost.
KO_BOOST_ABILITIES: dict[str, str | None] = {
    "moxie": "atk",
    "chillingneigh": "atk",
    "grimneigh": "spa",
    "beastboost": None,
    "eelevate": None,
}

# Piercing Drill (Mega Excadrill): contact moves go through Protect at 1/4
PIERCE_PROTECT_FRACTION = 0.25
QUICK_CLAW_CHANCE = 0.2
KINGS_ROCK_CHANCE = 0.1
FOCUS_BAND_CHANCE = 0.1
BRIGHT_POWDER_ACCURACY = 0.9  # 3686/4096 on moves aimed at the holder

# type-absorbing abilities: the damage calculator already zeroes the damage;
# the sim adds the on-hit effect. ability -> (absorbed type, effect)
# effect: ("boost", {stat: stages}) | ("heal", denominator) | None
ABSORB_ABILITIES: dict[str, tuple[str, tuple | None]] = {
    "lightningrod": ("electric", ("boost", {"spa": 1})),
    "stormdrain": ("water", ("boost", {"spa": 1})),
    "motordrive": ("electric", ("boost", {"spe": 1})),
    "voltabsorb": ("electric", ("heal", 4)),
    "waterabsorb": ("water", ("heal", 4)),
    "dryskin": ("water", ("heal", 4)),
    "eartheater": ("ground", ("heal", 4)),
    "sapsipper": ("grass", ("boost", {"atk": 1})),
    "wellbakedbody": ("fire", ("boost", {"def": 2})),
    "flashfire": ("fire", None),  # damage denial only (boost unmodeled)
}

# absorbing abilities that also DRAW single-target moves of their type
# toward the holder in doubles
REDIRECT_ABILITIES = {"lightningrod": "electric", "stormdrain": "water"}

# --- single-use held berries (all present in Champions' item list) ---
# norm(item) -> statuses it cures on the spot
STATUS_CURE_BERRIES: dict[str, tuple[str, ...]] = {
    "cheriberry": ("par",),
    "chestoberry": ("slp",),
    "pechaberry": ("psn", "tox"),
    "rawstberry": ("brn",),
    "aspearberry": ("frz",),
    "lumberry": ("par", "slp", "psn", "tox", "brn", "frz"),
}
CONFUSION_CURE_BERRIES = {"persimberry", "lumberry"}
# norm(item) -> (hp threshold fraction, heal): heal <=1 is a max-HP fraction,
# heal >1 is flat HP (Oran)
HEAL_BERRIES: dict[str, tuple[float, float]] = {
    "sitrusberry": (0.5, 0.25),
    "oranberry": (0.5, 10),
}
# norm(item) -> attacking type it halves; super-effective hits only,
# except Chilan which halves any Normal hit
RESIST_BERRIES: dict[str, str] = {
    "occaberry": "fire", "passhoberry": "water", "wacanberry": "electric",
    "rindoberry": "grass", "yacheberry": "ice", "chopleberry": "fighting",
    "kebiaberry": "poison", "shucaberry": "ground", "cobaberry": "flying",
    "payapaberry": "psychic", "tangaberry": "bug", "chartiberry": "rock",
    "kasibberry": "ghost", "habanberry": "dragon", "colburberry": "dark",
    "babiriberry": "steel", "roseliberry": "fairy", "chilanberry": "normal",
}

# moves usable only on the user's first turn on the field
FIRST_TURN_ONLY = {"Fake Out", "First Impression"}

# type-based immunity to non-volatile statuses
STATUS_TYPE_IMMUNITY: dict[str, tuple[str, ...]] = {
    "brn": ("fire",),
    "par": ("electric",),
    "psn": ("poison", "steel"),
    "tox": ("poison", "steel"),
    "frz": ("ice",),
}

# condition durations, in turns
DURATIONS = {
    "reflect": 5, "light_screen": 5, "aurora_veil": 5, "tailwind": 4,
    "safeguard": 5, "weather": 5, "terrain": 5, "trick_room": 5,
}
