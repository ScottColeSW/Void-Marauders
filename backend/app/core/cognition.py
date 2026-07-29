import json
import os
import random

from app.core.prompts import build_prompt
from app.schemas.agent import ActionType, AgentActionSchema, AgentPerception, AgentState
from app.schemas.world import SectorType, WorldState

COGNITION_MODE = os.getenv("COGNITION_MODE", "mock").lower()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")


def decide(agent: AgentState, perception: AgentPerception, world: WorldState) -> AgentActionSchema:
    if COGNITION_MODE == "llm":
        try:
            return _llm_decide(agent, perception)
        except Exception as exc:
            # A single flaky/hallucinated LLM response should never take down
            # the whole tick loop — fall back to a safe no-op for this agent.
            return AgentActionSchema(
                inner_monologue=f"{agent.profile.name} hesitates, unable to decide ({exc}).",
                spoken_dialogue=None,
                action_type=ActionType.IDLE,
                target_id=None,
            )
    return _mock_decide(agent, perception, world)


def _mock_decide(
    agent: AgentState, perception: AgentPerception, world: WorldState
) -> AgentActionSchema:
    name = agent.profile.name
    sector = world.sectors[perception.current_sector]

    if perception.nearby_aliens:
        target = perception.nearby_aliens[0]
        if agent.health > 30:
            return AgentActionSchema(
                inner_monologue=f"{name} spots a hostile and readies a weapon.",
                spoken_dialogue=f"Contact! Engaging near {sector.sector_id}!",
                action_type=ActionType.FIRE_WEAPON,
                target_id=target,
            )
        return AgentActionSchema(
            inner_monologue=f"{name} is too hurt to fight and looks for cover.",
            spoken_dialogue="I need cover, now!",
            action_type=ActionType.TAKE_COVER,
            target_id=sector.sector_id,
        )

    if agent.profile.is_captain and perception.nearby_crew and random.random() < 0.3:
        target = random.choice(perception.nearby_crew)
        if perception.nearby_aliens:
            order_action = ActionType.FIRE_WEAPON
        else:
            order_action = random.choice(
                [ActionType.GATHER_RESOURCE, ActionType.BUILD_STRUCTURE, ActionType.EXPLORE_SECTOR]
            )
        return AgentActionSchema(
            inner_monologue=f"{name} decides the crew needs direction.",
            spoken_dialogue=f"You there — {order_action.value.replace('_', ' ')}, now!",
            action_type=ActionType.ISSUE_ORDER,
            target_id=target,
            order_action=order_action,
        )

    if not sector.explored:
        return AgentActionSchema(
            inner_monologue=f"{name} pushes further into the unknown.",
            spoken_dialogue="Scanning this area now.",
            action_type=ActionType.EXPLORE_SECTOR,
            target_id=sector.sector_id,
        )

    unexplored = [s for s in world.sectors.values() if not s.explored]
    if unexplored and random.random() < 0.3:
        target = random.choice(unexplored)
        return AgentActionSchema(
            inner_monologue=f"{name} decides to venture out and see what's beyond {sector.sector_id}.",
            spoken_dialogue=f"Heading out to check {target.sector_id}.",
            action_type=ActionType.EXPLORE_SECTOR,
            target_id=target.sector_id,
        )

    if sector.resource_yield:
        return AgentActionSchema(
            inner_monologue=f"{name} gets to work harvesting what this sector offers.",
            spoken_dialogue=None,
            action_type=ActionType.GATHER_RESOURCE,
            target_id=sector.sector_id,
        )

    if perception.stress_level >= 7 and perception.nearby_crew:
        target = random.choice(perception.nearby_crew)
        return AgentActionSchema(
            inner_monologue=f"{name}'s stress boils over.",
            spoken_dialogue="We need to talk. Right now.",
            action_type=ActionType.CONFRONT_CREW,
            target_id=target,
        )

    if agent.health < 70:
        return AgentActionSchema(
            inner_monologue=f"{name} takes a moment to recover.",
            spoken_dialogue=None,
            action_type=ActionType.REST,
            target_id=None,
        )

    if sector.sector_type == SectorType.COLONY_CORE and perception.colony_status.metal >= 15:
        return AgentActionSchema(
            inner_monologue=f"{name} thinks the colony could use another structure.",
            spoken_dialogue="Let's get building.",
            action_type=ActionType.BUILD_STRUCTURE,
            target_id="new:habitat",
        )

    return AgentActionSchema(
        inner_monologue=f"{name} keeps watch, nothing urgent to do right now.",
        spoken_dialogue=None,
        action_type=ActionType.IDLE,
        target_id=None,
    )


def _llm_decide(agent: AgentState, perception: AgentPerception) -> AgentActionSchema:
    import ollama

    prompt = build_prompt(
        is_captain=agent.profile.is_captain,
        loyalty=agent.loyalty,
        agent_name=agent.profile.name,
        agent_role=agent.profile.role,
        personality_trait=agent.profile.personality_trait,
        health=agent.health,
        stress_level=perception.stress_level,
        current_sector=perception.current_sector,
        nearby_crew=", ".join(perception.nearby_crew) or "none",
        nearby_aliens=", ".join(perception.nearby_aliens) or "none",
        metal=perception.colony_status.metal,
        food=perception.colony_status.food,
        energy=perception.colony_status.energy,
        biomatter=perception.colony_status.biomatter,
        retrieved_memories="\n".join(perception.retrieved_memories) or "None yet.",
    )

    client = ollama.Client(host=OLLAMA_HOST)
    response = client.generate(
        model=agent.profile.model,
        prompt=prompt,
        format=AgentActionSchema.model_json_schema(),
        options={"temperature": agent.profile.temperature},
    )
    payload = json.loads(response["response"])
    return AgentActionSchema.model_validate(payload)
