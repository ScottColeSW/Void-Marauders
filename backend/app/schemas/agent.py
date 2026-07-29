from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.world import ColonyResources


class ActionType(str, Enum):
    IDLE = "idle"
    REPAIR_HULL = "repair_hull"
    REPAIR_STRUCTURE = "repair_structure"
    BUILD_STRUCTURE = "build_structure"
    GATHER_RESOURCE = "gather_resource"
    EXPLORE_SECTOR = "explore_sector"
    RETURN_TO_COLONY = "return_to_colony"
    FIRE_WEAPON = "fire_weapon"
    TAKE_COVER = "take_cover"
    RETREAT = "retreat"
    CONFRONT_CREW = "confront_crew"
    REST = "rest"
    ISSUE_ORDER = "issue_order"


class AgentProfile(BaseModel):
    agent_id: str
    name: str
    role: str
    personality_trait: str
    model: str = "qwen2.5:3b"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    is_captain: bool = False


class PendingOrder(BaseModel):
    captain_id: str
    action_type: ActionType


class AgentState(BaseModel):
    """Mutable per-agent runtime state tracked by the WorldEngine across ticks."""

    profile: AgentProfile
    current_sector: str
    health: int = Field(default=100, ge=0, le=100)
    stress_level: int = Field(default=0, ge=0, le=10)
    inventory: List[str] = Field(default_factory=list)
    loyalty: int = Field(default=7, ge=0, le=10)
    pending_order: Optional[PendingOrder] = None


class AgentPerception(BaseModel):
    """The situational snapshot fed into an agent's cognition each tick."""

    agent_id: str
    current_sector: str
    nearby_crew: List[str] = Field(default_factory=list)
    nearby_aliens: List[str] = Field(default_factory=list)
    colony_status: ColonyResources
    stress_level: int
    retrieved_memories: List[str] = Field(default_factory=list)


class AgentActionSchema(BaseModel):
    inner_monologue: str = Field(
        description="The agent's private reasoning, considering their current stress, traits, and hidden motives."
    )
    spoken_dialogue: Optional[str] = Field(
        default=None,
        description="What the agent actually shouts or says out loud to other crew members nearby. Can be empty.",
    )
    action_type: ActionType = Field(
        description="Must be exactly one of the permitted systemic action keys."
    )
    target_id: Optional[str] = Field(
        default=None,
        description="The target of the action: a sector id, an alien id, a structure id, or another agent's id.",
    )
    order_action: Optional[ActionType] = Field(
        default=None,
        description="Only used with action_type='issue_order': the action you want target_id to take.",
    )
