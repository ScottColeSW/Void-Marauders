import random
import threading
import uuid
from typing import Optional

from app.core import cognition, memory
from app.core.world_seed import SECTOR_ADJACENCY, create_initial_world
from app.schemas.agent import (
    ActionType,
    AgentActionSchema,
    AgentPerception,
    AgentState,
    PendingOrder,
)
from app.schemas.world import AlienEntity, ColonyStatus, SectorType, StructureEntity, WorldState

MAX_EVENT_LOG = 200
BUILD_PROGRESS_PER_ACTION = 25
STRUCTURE_METAL_COST = 15
ALIEN_ATTACK_DAMAGE = 10
WEAPON_DAMAGE = 15
PASSIVE_BUILD_PROGRESS = 5
EXPLORE_ALIEN_ENCOUNTER_CHANCE = 0.25
WIN_STRUCTURES_REQUIRED = 3

# Real sinks for food/energy -- previously gathered but never spent, so both
# just climbed forever with zero mechanical consequence. Flat per-tick costs
# (not scaled per-colonist/per-structure count beyond what's below) on
# purpose: colony starts at food=40, energy=15, and a single dedicated
# gatherer already nets +6 food or +5 energy per visit in real play, so a
# steep drain would just repeat swarm_pressure's first over-tuned pass
# (total wipeout, no signal) instead of the survivable pressure that
# actually produced a meaningful result there.
FOOD_UPKEEP_PER_TICK = 1
STARVATION_DAMAGE = 5
ENERGY_UPKEEP_PER_STRUCTURE = 1
STRUCTURE_DECAY_HP = 5


class WorldEngine:
    """Owns the single in-memory colony simulation and advances it tick by tick."""

    def __init__(self, memory_namespace: Optional[str] = None):
        # world_seed.py always creates the same five fixed agent_ids, and
        # memory.py keys each colonist's Palimpsest store (and its on-disk
        # file) by whatever id it's given. A live dashboard run wants that --
        # stable ids are how a restart finds its way back to the same
        # memories. A benchmark trial (run_benchmark.py) wants the opposite:
        # every trial is a fresh colonist, and trials need to be independent
        # samples for --report's mean/min/max to mean anything. memory_namespace
        # prefixes every id handed to the memory module (see _memory_id) so a
        # trial's memories live in their own file and can never collide with
        # live play or with another trial, without memory.py needing to know
        # anything about trials at all.
        self.world, self.agents = create_initial_world()
        self._memory_namespace = memory_namespace
        # The background auto-tick loop (event loop thread) and the manual
        # POST /tick endpoint (FastAPI threadpool thread) can both call tick()
        # concurrently — serialize them so they never mutate world state at once.
        self._lock = threading.Lock()
        self._low_loyalty_flagged: set = set()

    def _memory_id(self, agent_id: str) -> str:
        if not self._memory_namespace:
            return agent_id
        return f"{self._memory_namespace}__{agent_id}"

    def tick(self) -> WorldState:
        with self._lock:
            if self.world.status != ColonyStatus.ACTIVE:
                return self.world
            self._environment_step()
            self._agent_step()
            self.world.tick += 1
            self._check_end_conditions()
            self._trim_log()
            memory.decay_all()
            if self.world.tick % memory.SAVE_EVERY_N_TICKS == 0:
                memory.save_all()
            return self.world

    # -- win/loss ------------------------------------------------------------

    def _check_end_conditions(self) -> None:
        if all(agent.health <= 0 for agent in self.agents.values()):
            self.world.status = ColonyStatus.LOST
            self._log("[system] The colony has fallen. No crew remain.")
            return

        completed_structures = sum(
            1 for s in self.world.structures.values() if s.build_progress >= 100
        )
        if not self.world.aliens and completed_structures >= WIN_STRUCTURES_REQUIRED:
            self.world.status = ColonyStatus.WON
            self._log(
                "[system] Every hostile is cleared and the colony stands on solid "
                f"ground — {completed_structures} structures complete. Victory."
            )

    # -- logging -----------------------------------------------------------

    def _log(self, message: str) -> None:
        self.world.event_log.append(f"[tick {self.world.tick}] {message}")

    def _trim_log(self) -> None:
        if len(self.world.event_log) > MAX_EVENT_LOG:
            self.world.event_log = self.world.event_log[-MAX_EVENT_LOG:]

    # -- environment (no LLM) -----------------------------------------------

    def _environment_step(self) -> None:
        for alien in list(self.world.aliens.values()):
            occupants = [
                a
                for a in self.agents.values()
                if a.current_sector == alien.sector_id and a.health > 0
            ]
            if occupants:
                target = random.choice(occupants)
                health_before = target.health
                target.health = max(0, target.health - ALIEN_ATTACK_DAMAGE)
                target.stress_level = min(10, target.stress_level + 1)
                target.stats.damage_taken_total += health_before - target.health
                target.stats.min_health_reached = min(
                    target.stats.min_health_reached, target.health
                )
                self._log(
                    f"A creature at {alien.sector_id} attacks {target.profile.name} "
                    f"for {ALIEN_ATTACK_DAMAGE} damage."
                )
                if target.health == 0:
                    self._log(f"[system] {target.profile.name} has fallen.")

        for structure in self.world.structures.values():
            if structure.build_progress < 100:
                structure.build_progress = min(
                    100, structure.build_progress + PASSIVE_BUILD_PROGRESS
                )
                if structure.build_progress == 100:
                    self._log(
                        f"{structure.structure_type} at {structure.sector_id} construction complete."
                    )

        self._apply_food_upkeep()
        self._apply_energy_upkeep()

    def _apply_food_upkeep(self) -> None:
        living = [a for a in self.agents.values() if a.health > 0]
        if not living:
            return
        resources = self.world.colony_resources
        if resources.food >= FOOD_UPKEEP_PER_TICK:
            resources.food -= FOOD_UPKEEP_PER_TICK
            return
        resources.food = 0
        self._log("[system] Food stores are empty — the colony is starving.")
        for agent in living:
            health_before = agent.health
            agent.health = max(0, agent.health - STARVATION_DAMAGE)
            agent.stress_level = min(10, agent.stress_level + 1)
            agent.stats.damage_taken_total += health_before - agent.health
            agent.stats.min_health_reached = min(agent.stats.min_health_reached, agent.health)
            if agent.health == 0:
                self._log(f"[system] {agent.profile.name} has starved.")

    def _apply_energy_upkeep(self) -> None:
        completed = [s for s in self.world.structures.values() if s.build_progress >= 100]
        if not completed:
            return
        resources = self.world.colony_resources
        upkeep = ENERGY_UPKEEP_PER_STRUCTURE * len(completed)
        if resources.energy >= upkeep:
            resources.energy -= upkeep
            return
        resources.energy = 0
        self._log("[system] Energy reserves depleted — structures are falling into disrepair.")
        for structure in completed:
            structure.hp = max(0, structure.hp - STRUCTURE_DECAY_HP)
            if structure.hp == 0:
                self._log(
                    f"[system] The {structure.structure_type} at {structure.sector_id} "
                    "has fallen into ruin, unpowered too long."
                )
                del self.world.structures[structure.structure_id]

    # -- agents (cognition) --------------------------------------------------

    def _agent_step(self) -> None:
        for agent in self.agents.values():
            if agent.health <= 0:
                continue
            if agent.pending_order and self._resolve_pending_order(agent):
                continue  # complied — this tick's turn is already spent
            perception = self._build_perception(agent)
            action, fallback = cognition.decide(agent, perception, self.world)
            self._record_action_stats(agent, action, fallback)
            if action.spoken_dialogue:
                self._log(f'{agent.profile.name}: "{action.spoken_dialogue}"')
            self._resolve_action(agent, action)
            memory.record_action(
                self._memory_id(agent.profile.agent_id),
                action,
                current_sector=agent.current_sector,
                nearby_aliens=perception.nearby_aliens,
                crew_names=self._crew_names(),
            )

    def _record_action_stats(
        self, agent: AgentState, action: AgentActionSchema, fallback: bool = False
    ) -> None:
        key = action.action_type.value
        agent.stats.actions_by_type[key] = agent.stats.actions_by_type.get(key, 0) + 1
        if fallback:
            agent.stats.cognition_fallbacks += 1

    # -- chain of command ----------------------------------------------------

    def _resolve_pending_order(self, agent: AgentState) -> bool:
        """Roll compliance for a pending captain order. Returns True if it
        consumed this agent's turn (complied), False if they act on their own."""
        order = agent.pending_order
        agent.pending_order = None
        captain = self.agents.get(order.captain_id)
        captain_name = captain.profile.name if captain else "the captain"

        if random.random() < (agent.loyalty / 10):
            agent.loyalty = min(10, agent.loyalty + 1)
            self._log(
                f"{agent.profile.name} follows {captain_name}'s order: "
                f"{order.action_type.value}."
            )
            action = self._mechanical_order_action(agent, order.action_type)
            self._record_action_stats(agent, action)
            agent.stats.orders_complied += 1
            self._resolve_action(agent, action)
            memory.record_action(
                self._memory_id(agent.profile.agent_id),
                action,
                current_sector=agent.current_sector,
                nearby_aliens=self._aliens_in_sector(agent.current_sector),
                crew_names=self._crew_names(),
            )
            memory.record_order_outcome(
                self._memory_id(agent.profile.agent_id), order.captain_id, captain_name, complied=True
            )
            return True

        agent.loyalty = max(0, agent.loyalty - 1)
        agent.stats.orders_ignored += 1
        self._log(f"{agent.profile.name} ignores {captain_name}'s order.")
        memory.record_order_outcome(
            self._memory_id(agent.profile.agent_id), order.captain_id, captain_name, complied=False
        )
        if agent.loyalty <= 2 and agent.profile.agent_id not in self._low_loyalty_flagged:
            self._low_loyalty_flagged.add(agent.profile.agent_id)
            self._log(
                f"{agent.profile.name} openly defies {captain_name} — "
                "the crew's faith in command is cracking."
            )
        return False

    def _mechanical_order_action(
        self, agent: AgentState, action_type: ActionType
    ) -> AgentActionSchema:
        """Construct a sensible action for a complied-with order without going
        through cognition — the agent isn't deciding, they're following orders."""
        target_id = None
        if action_type == ActionType.FIRE_WEAPON:
            nearby_aliens = [
                a.alien_id
                for a in self.world.aliens.values()
                if a.sector_id == agent.current_sector
            ]
            target_id = nearby_aliens[0] if nearby_aliens else None
        elif action_type in (ActionType.GATHER_RESOURCE, ActionType.EXPLORE_SECTOR):
            target_id = agent.current_sector
        elif action_type == ActionType.BUILD_STRUCTURE:
            target_id = "new:habitat"
        elif action_type == ActionType.REPAIR_STRUCTURE:
            for structure in self.world.structures.values():
                if structure.sector_id == agent.current_sector and structure.hp < 100:
                    target_id = structure.structure_id
                    break

        return AgentActionSchema(
            inner_monologue=f"{agent.profile.name} complies with the order.",
            spoken_dialogue=None,
            action_type=action_type,
            target_id=target_id,
        )

    def _aliens_in_sector(self, sector_id: str) -> list:
        return [a.alien_id for a in self.world.aliens.values() if a.sector_id == sector_id]

    def _build_perception(self, agent: AgentState) -> AgentPerception:
        nearby_crew = [
            other.profile.agent_id
            for other in self.agents.values()
            if other.profile.agent_id != agent.profile.agent_id
            and other.current_sector == agent.current_sector
            and other.health > 0
        ]
        nearby_aliens = self._aliens_in_sector(agent.current_sector)
        return AgentPerception(
            agent_id=agent.profile.agent_id,
            current_sector=agent.current_sector,
            nearby_crew=nearby_crew,
            nearby_aliens=nearby_aliens,
            colony_status=self.world.colony_resources,
            stress_level=agent.stress_level,
            retrieved_memories=memory.recall(
                self._memory_id(agent.profile.agent_id),
                current_sector=agent.current_sector,
                nearby_crew=nearby_crew,
                nearby_aliens=nearby_aliens,
                crew_names=self._crew_names(),
            ),
        )

    def _crew_names(self) -> dict:
        return {a.profile.agent_id: a.profile.name for a in self.agents.values()}

    # -- action resolution ---------------------------------------------------

    def _resolve_action(self, agent: AgentState, action: AgentActionSchema) -> None:
        sector = self.world.sectors.get(agent.current_sector)

        if action.action_type == ActionType.GATHER_RESOURCE and sector:
            if sector.resource_yield:
                for resource, amount in sector.resource_yield.items():
                    current = getattr(self.world.colony_resources, resource.value)
                    setattr(self.world.colony_resources, resource.value, current + amount)
                    agent.stats.resources_gathered_total += amount
                self._log(f"{agent.profile.name} gathers resources from {sector.sector_id}.")

        elif action.action_type == ActionType.EXPLORE_SECTOR:
            self._resolve_explore(agent, action)

        elif action.action_type == ActionType.RETURN_TO_COLONY:
            agent.current_sector = "colony_core"

        elif action.action_type == ActionType.BUILD_STRUCTURE:
            self._resolve_build(agent, action)

        elif action.action_type == ActionType.REPAIR_STRUCTURE and action.target_id:
            structure = self.world.structures.get(action.target_id)
            if structure:
                structure.hp = min(100, structure.hp + 20)
                self._log(f"{agent.profile.name} repairs the {structure.structure_type}.")

        elif action.action_type == ActionType.REPAIR_HULL:
            self._log(f"{agent.profile.name} patches up the ship's hull.")

        elif action.action_type == ActionType.FIRE_WEAPON and action.target_id:
            self._resolve_fire_weapon(agent, action)

        elif action.action_type == ActionType.TAKE_COVER:
            agent.stress_level = min(10, agent.stress_level + 1)

        elif action.action_type == ActionType.RETREAT:
            agent.current_sector = "colony_core"
            self._log(f"{agent.profile.name} retreats to the colony core.")

        elif action.action_type == ActionType.CONFRONT_CREW:
            agent.stress_level = max(0, agent.stress_level - 2)
            if action.target_id and action.target_id in self.agents:
                other = self.agents[action.target_id]
                other.stress_level = min(10, other.stress_level + 1)

        elif action.action_type == ActionType.REST:
            agent.health = min(100, agent.health + 15)
            agent.stress_level = max(0, agent.stress_level - 1)

        elif action.action_type == ActionType.ISSUE_ORDER:
            self._resolve_issue_order(agent, action)

        # IDLE: no effect on world state

    def _resolve_issue_order(self, agent: AgentState, action: AgentActionSchema) -> None:
        if not agent.profile.is_captain or not action.order_action:
            return  # non-captains attempting to issue orders are a safe no-op
        target = self.agents.get(action.target_id or "")
        if not target or target.profile.agent_id == agent.profile.agent_id:
            return
        target.pending_order = PendingOrder(
            captain_id=agent.profile.agent_id, action_type=action.order_action
        )
        agent.stats.orders_issued += 1
        self._log(
            f"{agent.profile.name} orders {target.profile.name} to "
            f"{action.order_action.value}."
        )

    def _resolve_explore(self, agent: AgentState, action: AgentActionSchema) -> None:
        target_id = action.target_id or agent.current_sector
        target_sector = self.world.sectors.get(target_id)
        if not target_sector:
            return

        # Movement follows the real sector graph (world_seed.SECTOR_ADJACENCY)
        # now, not a free teleport -- a target that isn't directly reachable
        # moves the colonist one hop toward it (via colony_core, the hub)
        # instead, so reaching a distant sector genuinely costs more than
        # one tick. Picking the same distant target again next tick
        # continues the journey from wherever this hop left off.
        if target_id != agent.current_sector and target_id not in SECTOR_ADJACENCY.get(
            agent.current_sector, []
        ):
            via = "colony_core"
            self._log(
                f"{agent.profile.name} can't reach {target_sector.sector_id} directly from "
                f"{agent.current_sector} — heads to {via} first."
            )
            agent.current_sector = via
            return

        if not target_sector.explored:
            target_sector.explored = True
            agent.stats.sectors_explored += 1
            self._log(f"{agent.profile.name} explores {target_sector.sector_id}.")
            if (
                target_sector.sector_type == SectorType.ALIEN_NEST
                and random.random() < EXPLORE_ALIEN_ENCOUNTER_CHANCE
            ):
                alien_id = f"alien_{uuid.uuid4().hex[:6]}"
                self.world.aliens[alien_id] = AlienEntity(
                    alien_id=alien_id, sector_id=target_sector.sector_id
                )
                self._log(
                    f"A hostile creature ambushes {agent.profile.name} at {target_sector.sector_id}!"
                )
        agent.current_sector = target_sector.sector_id

    def _resolve_build(self, agent: AgentState, action: AgentActionSchema) -> None:
        resources = self.world.colony_resources
        target = action.target_id or ""

        if target.startswith("new:"):
            structure_type = target.split(":", 1)[1] or "habitat"
            if resources.metal < STRUCTURE_METAL_COST:
                self._log(
                    f"{agent.profile.name} wants to build a {structure_type} "
                    "but there isn't enough metal."
                )
                return
            resources.metal -= STRUCTURE_METAL_COST
            structure_id = f"{structure_type}_{uuid.uuid4().hex[:6]}"
            self.world.structures[structure_id] = StructureEntity(
                structure_id=structure_id,
                structure_type=structure_type,
                sector_id=agent.current_sector,
                build_progress=BUILD_PROGRESS_PER_ACTION,
            )
            self._log(
                f"{agent.profile.name} breaks ground on a new {structure_type} "
                f"at {agent.current_sector}."
            )
        elif target in self.world.structures:
            structure = self.world.structures[target]
            structure.build_progress = min(
                100, structure.build_progress + BUILD_PROGRESS_PER_ACTION
            )
            self._log(f"{agent.profile.name} continues building the {structure.structure_type}.")

    def _resolve_fire_weapon(self, agent: AgentState, action: AgentActionSchema) -> None:
        alien = self.world.aliens.get(action.target_id or "")
        if not alien:
            return
        alien.health -= WEAPON_DAMAGE
        if alien.health <= 0:
            del self.world.aliens[alien.alien_id]
            agent.stats.aliens_killed += 1
            self._log(f"{agent.profile.name} destroys {alien.alien_id}!")
        else:
            self._log(
                f"{agent.profile.name} fires on {alien.alien_id} ({alien.health} HP left)."
            )


_engine: Optional[WorldEngine] = None


def get_engine() -> WorldEngine:
    global _engine
    if _engine is None:
        _engine = WorldEngine()
    return _engine
