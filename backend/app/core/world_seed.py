from typing import Dict, Tuple

from app.schemas.agent import AgentProfile, AgentState
from app.schemas.world import (
    AlienEntity,
    ColonyResources,
    ResourceType,
    Sector,
    SectorType,
    WorldState,
)


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
            resource_yield={ResourceType.FOOD: 6},
        ),
        "geothermal_vent": Sector(
            sector_id="geothermal_vent",
            sector_type=SectorType.RESOURCE_FIELD,
            explored=False,
            resource_yield={ResourceType.ENERGY: 5},
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
        # vigilance), the lighter gemma2:2b to roles leaning more on flavor
        # than precision. If your GPU has more headroom, feel free to spread
        # these back out to more distinct models per agent.
        "engineer_karl": AgentState(
            profile=AgentProfile(
                agent_id="engineer_karl",
                name="Karl",
                role="Cybernetic Engineer",
                personality_trait="Cynical and paranoid, secretly distrusts Valerie",
                model="qwen2.5:3b",
                temperature=0.5,
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
            ),
            current_sector="landing_ship",
        ),
        "medic_amara": AgentState(
            profile=AgentProfile(
                agent_id="medic_amara",
                name="Amara",
                role="Medic",
                personality_trait="Calm and dutiful, prioritizes crew welfare above all",
                model="gemma2:2b",
                temperature=0.3,
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
            ),
            current_sector="resource_field_south",
        ),
    }

    return world, agents
