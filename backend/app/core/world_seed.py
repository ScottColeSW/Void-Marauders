from typing import Dict, List, Tuple

from app.schemas.agent import AgentProfile, AgentState
from app.schemas.world import (
    AlienEntity,
    ColonyResources,
    ResourceType,
    Sector,
    SectorType,
    WorldState,
)

# Hub-and-spoke: colony_core reaches every other sector directly, but two
# "spoke" sectors are two hops apart, through colony_core -- matches the map
# layout drawn in app/static/app.js exactly (colony_core linked to all seven
# others). engine.py's _resolve_explore enforces this: a colonist heading
# for an unreachable target moves one hop toward it instead of teleporting,
# so a distant sector genuinely takes more than one tick to reach. Colony
# resets always reseed this same fixed 8-sector world -- if that ever
# changes, this table and app.js's SECTOR_LAYOUT both need updating, since
# neither can be derived from the other.
SECTOR_ADJACENCY: Dict[str, List[str]] = {
    "colony_core": [
        "landing_ship", "resource_field_north", "resource_field_south",
        "geothermal_vent", "unexplored_east", "unexplored_west", "alien_nest",
    ],
    "landing_ship": ["colony_core"],
    "resource_field_north": ["colony_core"],
    "resource_field_south": ["colony_core"],
    "geothermal_vent": ["colony_core"],
    "unexplored_east": ["colony_core"],
    "unexplored_west": ["colony_core"],
    "alien_nest": ["colony_core"],
}


# The alien nest is what makes the swarm a standing problem instead of two sitting ducks: while it
# stands it births a new swarmling every NEST_SPAWN_INTERVAL ticks (never more than NEST_MAX_ALIVE
# alive at once, so the pressure is steady, not a snowball). It falls one of two ways: shoot it
# (fire_weapon with target_id "alien_nest" while standing in that sector -- NEST_MAX_HP / WEAPON_DAMAGE
# hits), or break the swarm by killing SWARM_KILLS_TO_COLLAPSE of them in total. Lives here, not in
# engine.py, because prompts.py quotes the same numbers to the colonists and cannot import the engine.
NEST_SECTOR_ID = "alien_nest"
NEST_MAX_HP = 100
NEST_SPAWN_INTERVAL = 6
NEST_MAX_ALIVE = 4
SWARM_KILLS_TO_COLLAPSE = 10
SWARMLING_HEALTH = 25
WEAPON_DAMAGE = 15

# How much a colonist can carry in total before gathering sends them back to colony_core, and how
# much food they keep when they unload there. See engine.py's CARRY_CAPACITY comment for why.
CARRY_CAPACITY = 20

# The assault on the nest is a group action: a real run sent two colonists in separately against four
# swarmlings and both died inside three ticks with one kill between them. The crew rallies at
# colony_core, and the assault only starts once ASSAULT_PARTY_MIN colonists healthier than
# SIEGE_MIN_HEALTH are standing there together (see engine.py's _update_assault).
ASSAULT_PARTY_MIN = 3
SIEGE_MIN_HEALTH = 60
PERSONAL_FOOD_RESERVE = 5


# Structures: one per sector, and each type has its own job. Before this every structure was a
# generic "habitat" put up wherever the builder stood, so all three piled up in colony_core and
# did nothing but cost energy. Now a sector's structure is fixed by the sector (a power plant can
# only go where the heat is), and the win still needs WIN_STRUCTURES_REQUIRED of them finished.
# The dict order is the build priority: the first sector without a structure is the next site.
STRUCTURE_SITES = {
    "colony_core": "habitat",
    "geothermal_vent": "power_plant",
    "resource_field_south": "hydroponics",
    "resource_field_north": "foundry",
}
STRUCTURE_LABELS = {
    "habitat": "Habitat",
    "power_plant": "Power Plant",
    "hydroponics": "Hydroponics",
    "foundry": "Foundry",
}
HABITAT_HEAL = 3
POWER_PLANT_ENERGY = 4
HYDROPONICS_FOOD = 2
FOUNDRY_METAL = 2
STRUCTURE_EFFECTS = {
    "habitat": f"colonists in its sector recover {HABITAT_HEAL} HP a tick",
    "power_plant": f"+{POWER_PLANT_ENERGY} energy a tick",
    "hydroponics": f"+{HYDROPONICS_FOOD} food a tick while the colony has energy",
    "foundry": f"+{FOUNDRY_METAL} metal a tick while the colony has energy",
}


def next_build_site(structures):
    """The first site in STRUCTURE_SITES with no structure on it, or None when every site is taken.
    `structures` is any iterable of StructureEntity."""
    taken = {s.sector_id for s in structures}
    return next((site for site in STRUCTURE_SITES if site not in taken), None)


def create_initial_world() -> Tuple[WorldState, Dict[str, AgentState]]:
    sectors = {
        "landing_ship": Sector(
            sector_id="landing_ship", sector_type=SectorType.SHIP, explored=True
        ),
        "colony_core": Sector(
            sector_id="colony_core", sector_type=SectorType.COLONY_CORE, explored=True
        ),
        "resource_field_north": Sector(
            sector_id="resource_field_north",
            sector_type=SectorType.RESOURCE_FIELD,
            explored=True,
            resource_yield={ResourceType.METAL: 8},
        ),
        "resource_field_south": Sector(
            sector_id="resource_field_south",
            sector_type=SectorType.RESOURCE_FIELD,
            explored=True,
            # Doubled from 6 -- food is the one resource with an unconditional,
            # always-on drain (FOOD_UPKEEP_PER_TICK never stops, unlike energy's
            # upkeep which only applies once structures exist). Real batches
            # showed the shared food pool hit zero and then never recovered even
            # after contribution itself started working (see engine.py,
            # prompts.py) -- the amounts landing per trip back were too small to
            # matter. A richer yield here means a single successful gather-and-
            # contribute cycle buys a real buffer instead of a token one.
            resource_yield={ResourceType.FOOD: 12},
        ),
        "geothermal_vent": Sector(
            sector_id="geothermal_vent",
            sector_type=SectorType.RESOURCE_FIELD,
            # Explored from the start: energy is the one resource the colony always has to
            # keep sourcing (see engine.py's _apply_energy_upkeep), and with the vent hidden the
            # colonist assigned to it just "explored" it forever instead of gathering.
            explored=True,
            resource_yield={ResourceType.ENERGY: 10},
        ),
        "unexplored_east": Sector(
            sector_id="unexplored_east", sector_type=SectorType.UNEXPLORED, explored=False
        ),
        "unexplored_west": Sector(
            sector_id="unexplored_west", sector_type=SectorType.UNEXPLORED, explored=False
        ),
        "alien_nest": Sector(
            sector_id="alien_nest",
            sector_type=SectorType.ALIEN_NEST,
            explored=False,
            threat_level=6,
            resource_yield={ResourceType.BIOMATTER: 4},
        ),
    }

    aliens = {
        "swarmling_1": AlienEntity(alien_id="swarmling_1", sector_id="alien_nest", health=25),
        "swarmling_2": AlienEntity(alien_id="swarmling_2", sector_id="alien_nest", health=25),
    }

    world = WorldState(
        tick=0,
        colony_resources=ColonyResources(metal=20, food=40, energy=15, biomatter=0),
        sectors=sectors,
        aliens=aliens,
        structures={},
        event_log=["The Void Wanderer has touched down. The colony's first tick begins."],
    )

    agents = {
        # Two distinct models, not five -- deliberately. Five different models
        # resident at once is 10+ GB, more than an 8 GB-class consumer GPU
        # (e.g. an RTX 2080 Super) can hold; Ollama then evicts and reloads a
        # model from disk on nearly every agent's turn, every tick, which is
        # far slower than one extra model would ever save. Two small models
        # (~3.5 GB combined) stay resident together comfortably. Personality
        # variety still comes through via temperature and the system prompt
        # (see cognition.py/prompts.py) -- the sharper qwen2.5:3b goes to
        # roles where reasoning quality matters most (combat, command,
        # vigilance), the lighter gemma2:2b to a role leaning more on flavor
        # than precision. The medic started on gemma2:2b at 0.3 and idled
        # every tick in a real run, so she is on qwen2.5:3b now. If your GPU has more headroom, feel free to spread
        # these back out to more distinct models per agent.
        "engineer_karl": AgentState(
            profile=AgentProfile(
                agent_id="engineer_karl",
                name="Karl",
                role="Cybernetic Engineer",
                personality_trait="Cynical and paranoid, secretly distrusts Valerie",
                model="qwen2.5:3b",
                temperature=0.5,
                duty="BUILDER. Whenever the colony stockpile has metal of 15 or more and fewer than 3 structures exist, use build_structure with the build site named in the prompt (each sector gets one structure of its own kind; you walk there and build). An unfinished structure's id as target_id pushes it along. Otherwise gather metal at resource_field_north.",
            ),
            current_sector="colony_core",
        ),
        "pilot_valerie": AgentState(
            profile=AgentProfile(
                agent_id="pilot_valerie",
                name="Valerie",
                role="Pilot",
                personality_trait="Confident and impulsive, quick to take risks",
                model="qwen2.5:3b",
                temperature=0.9,
                is_captain=True,
                duty="COMMANDER. Direct the crew with issue_order: send crewmates to resource_field_north for metal (gather_resource), and when the crew is healthy send them at alien_nest (fire_weapon). Name a sector id as target_id and the best-placed crewmate goes.",
            ),
            current_sector="landing_ship",
        ),
        "medic_amara": AgentState(
            profile=AgentProfile(
                agent_id="medic_amara",
                name="Amara",
                role="Medic",
                personality_trait="Calm and dutiful, prioritizes crew welfare above all",
                model="qwen2.5:3b",
                temperature=0.7,
                duty="SUPPLY. Metal and energy keep the colony standing: gather metal at resource_field_north, and keep energy above 8 by gathering at geothermal_vent. Rest only if you are hurt.",
            ),
            current_sector="colony_core",
        ),
        "security_otieno": AgentState(
            profile=AgentProfile(
                agent_id="security_otieno",
                name="Otieno",
                role="Security Officer",
                personality_trait="Vigilant and aggressive toward threats",
                model="qwen2.5:3b",
                temperature=0.6,
                duty="DEFENDER. Your job is the alien nest. Go to alien_nest with health above 60 and fire_weapon on every swarmling; once none are left there, fire_weapon at the nest itself (target_id alien_nest). Fall back to colony_core to rest when hurt, then go back.",
            ),
            current_sector="colony_core",
        ),
        "botanist_priya": AgentState(
            profile=AgentProfile(
                agent_id="botanist_priya",
                name="Priya",
                role="Botanist",
                personality_trait="Curious and optimistic, driven to explore",
                model="gemma2:2b",
                temperature=0.8,
                duty="FORAGER. Keep the colony fed: gather food at resource_field_south, but only while the colony food stockpile is under 60. When food is plentiful, gather metal at resource_field_north instead. If energy is under 8, gather energy at geothermal_vent.",
            ),
            current_sector="resource_field_south",
        ),
    }

    return world, agents
