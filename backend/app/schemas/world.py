from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ResourceType(str, Enum):
    METAL = "metal"
    FOOD = "food"
    ENERGY = "energy"
    BIOMATTER = "biomatter"


class SectorType(str, Enum):
    COLONY_CORE = "colony_core"
    RESOURCE_FIELD = "resource_field"
    UNEXPLORED = "unexplored"
    ALIEN_NEST = "alien_nest"
    SHIP = "ship"


class Sector(BaseModel):
    sector_id: str
    sector_type: SectorType
    explored: bool = False
    resource_yield: Dict[ResourceType, int] = Field(default_factory=dict)
    threat_level: int = Field(default=0, ge=0, le=10)


class AlienEntity(BaseModel):
    alien_id: str
    sector_id: str
    health: int = 20
    aggro_target: Optional[str] = None


class StructureEntity(BaseModel):
    structure_id: str
    structure_type: str
    sector_id: str
    hp: int = 100
    build_progress: int = Field(default=0, ge=0, le=100)


class ColonyResources(BaseModel):
    metal: int = 0
    food: int = 100
    energy: int = 50
    biomatter: int = 0


class WorldState(BaseModel):
    tick: int = 0
    colony_resources: ColonyResources = Field(default_factory=ColonyResources)
    sectors: Dict[str, Sector] = Field(default_factory=dict)
    aliens: Dict[str, AlienEntity] = Field(default_factory=dict)
    structures: Dict[str, StructureEntity] = Field(default_factory=dict)
    event_log: List[str] = Field(default_factory=list)
