import random
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app.core import cognition, memory
from app.core.world_seed import (
    ASSAULT_PARTY_MIN,
    CARRY_CAPACITY,
    FOUNDRY_METAL,
    HABITAT_HEAL,
    HYDROPONICS_FOOD,
    NEST_MAX_ALIVE,
    NEST_MAX_HP,
    NEST_SECTOR_ID,
    NEST_SPAWN_INTERVAL,
    PERSONAL_FOOD_RESERVE,
    POWER_PLANT_ENERGY,
    SECTOR_ADJACENCY,
    SIEGE_MIN_HEALTH,
    STRUCTURE_LABELS,
    STRUCTURE_SITES,
    SWARM_KILLS_TO_COLLAPSE,
    SWARMLING_HEALTH,
    WEAPON_DAMAGE,
    create_initial_world,
    next_build_site,
)
from app.schemas.agent import (
    ActionType,
    AgentActionSchema,
    AgentPerception,
    AgentState,
    PendingOrder,
    PersonalStock,
)
from app.schemas.world import AlienEntity, ColonyStatus, SectorType, StructureEntity, WorldState

MAX_EVENT_LOG = 200
BUILD_PROGRESS_PER_ACTION = 25
STRUCTURE_METAL_COST = 15
# Biomatter had zero sink anywhere -- gathered, tracked in every stock and
# every report chart, spent nowhere. It only comes from alien_nest (the same
# sector that's seeded with both aliens and the highest threat_level in the
# world -- see world_seed.py), so it was pure risk with no payoff: nothing
# rewarded going there over any other resource field. Repair was also
# genuinely free before this -- REPAIR_STRUCTURE just added +20 hp with no
# cost check at all, unlike BUILD_STRUCTURE's metal cost. Tying repair to
# biomatter closes both gaps at once: structures decay from energy shortfall
# (STRUCTURE_DECAY_HP) and can now only be fixed with a resource that
# requires someone to go into the dangerous sector for it.
STRUCTURE_REPAIR_BIOMATTER_COST = 5
ALIEN_ATTACK_DAMAGE = 10
PASSIVE_BUILD_PROGRESS = 5

# A real 10-tick run had the colony gather ~175 resources and contribute 0: every colonist kept
# gathering into a personal stash and nobody ever walked it back, so nothing was ever built. A
# pack that fills up forces the trip (a full pack sends its owner back to colony_core on its
# own), and arriving at colony_core unloads it. Food keeps a small personal reserve on purpose --
# that hedge against starvation is a deliberate mechanic (see _apply_food_upkeep), just a bounded
# one now instead of an unlimited one.
# (CARRY_CAPACITY and PERSONAL_FOOD_RESERVE live in world_seed.py, shared with prompts.py.)

# A real run had the security officer stand in alien_nest for four ticks choosing explore_sector
# while two creatures hit him every tick, ignoring a loud THREAT_WARNING each time. Small models
# do not reliably act on a warning, so the engine does: if hostiles share the colonist's sector
# and the action they chose is not a response to them, they fight (health at or above this) or
# fall back to colony_core (below it). See _combat_reflex.
REFLEX_FIGHT_MIN_HEALTH = 40
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
# Was 5, which ruined a habitat 20 ticks after energy ran out. Three finished structures drain
# 3 energy a tick, so a real winning run had energy at 0 within about ten ticks of the last
# build, and the decay then showed up on the dashboard almost immediately. 2 leaves 50 ticks
# to recover (energy from geothermal_vent, repairs from biomatter) before a structure is lost.
STRUCTURE_DECAY_HP = 2

# Losing a colonist used to be permanent -- once dead, always dead, with no
# lever left to pull. The crew are cybernetic units (Karl's role is literally
# "Cybernetic Engineer"), not biological, so recovery doesn't need a rescue
# or a new-arrival narrative: a colonist with the materials and power to
# spare can manufacture a replacement chassis for a fallen slot. This also
# gives metal and energy a real ongoing sink -- both currently go idle once
# the first few structures are built (metal has no use after the 3rd, energy
# only drains for upkeep), while food, the scarce one, stays untouched by
# this on purpose so crew-building can't compete with feeding the living for
# the same resource. See _resolve_build_crew_unit.
CREW_UNIT_METAL_COST = 20
CREW_UNIT_ENERGY_COST = 10

# Loyalty used to move the same +1/-1 on every order regardless of what the
# order actually was or how it turned out -- a captain who orders someone
# into a fight that gets them hurt earned the exact same trust as one who
# didn't. Real incentive: complying with a RISKY order (target is currently
# facing aliens) that then hurts them costs real trust -- more than routine
# defiance would have -- while a risky order that pays off earns more than a
# safe one. Refusing a visibly dangerous order costs nothing: self-
# preservation against a reckless captain is a rational choice, not
# insubordination. Ordinary (non-risky) orders are unaffected -- same +1/-1
# as before.
RISKY_ORDER_BACKFIRE_LOYALTY_PENALTY = 3
RISKY_ORDER_PAYOFF_LOYALTY_BONUS = 2
CAUTIOUS_DEFIANCE_LOYALTY_PENALTY = 0

# Gathering used to be a guaranteed, fixed payoff -- resource_field_north
# always yields exactly 8 metal, every single time, forever. No uncertainty
# means no real economic decision: gathering is just a chore with a known
# reward, not a bet on anything. Sector.resource_yield is now read as the
# EXPECTED (average) yield; the actual amount per gather swings +/-50% of it,
# floored at 1 so a gather is never a total waste. Reasonable, not wild: the
# bound keeps a bad roll from ever undoing the point of gathering at all,
# while a real spread (half of expected, up to one and a half times it) is
# enough that "how much did that actually get us" becomes a real question
# instead of a known constant.
RESOURCE_YIELD_VARIANCE_LOW = 0.5
RESOURCE_YIELD_VARIANCE_HIGH = 1.5

# take_cover used to do nothing but raise stress -- a real trial showed a
# colonist choosing it every tick while dying anyway, with the prompt now
# fixed (THREAT_WARNING) to stop recommending it as an escape. But a no-op
# action a prompt can still legally choose is a trap of its own: give it a
# real, smaller effect than fire_weapon/retreat rather than leaving it inert.
# Cover only helps against the NEXT attack phase (environment_step runs
# before agent_step within a tick -- see tick()), and is consumed whether or
# not an alien actually attacks that tick, so it has to be re-chosen every
# tick to stay protected, same as bracing in real cover would.
TAKE_COVER_DAMAGE_REDUCTION = 0.5


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
            # Snapshotted before environment_step so order resolution can tell
            # whether a colonist actually took damage this tick (from aliens,
            # starvation, whatever) -- see _resolve_pending_order's outcome-
            # sensitive loyalty logic.
            health_at_tick_start = {aid: a.health for aid, a in self.agents.items()}
            self._environment_step()
            self._agent_step(health_at_tick_start)
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
        if (
            not self.world.aliens
            and self.world.nest_destroyed
            and completed_structures >= WIN_STRUCTURES_REQUIRED
        ):
            self.world.status = ColonyStatus.WON
            self._log(
                "[system] The nest is destroyed, every hostile is cleared, and the colony stands "
                f"on solid ground — {completed_structures} structures complete. Victory."
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
                if target.taking_cover:
                    damage = max(1, round(ALIEN_ATTACK_DAMAGE * TAKE_COVER_DAMAGE_REDUCTION))
                    target.taking_cover = False
                    target.health = max(0, target.health - damage)
                    target.stress_level = min(10, target.stress_level + 1)
                    target.stats.damage_taken_total += health_before - target.health
                    target.stats.min_health_reached = min(
                        target.stats.min_health_reached, target.health
                    )
                    self._log(
                        f"A creature at {alien.sector_id} attacks {target.profile.name}, "
                        f"but cover blunts it to {damage} damage."
                    )
                else:
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

        # Cover is a one-tick brace: clear it for everyone now, attacked or
        # not (only one occupant per alien gets attacked per tick, so an
        # uninvolved colonist's flag would otherwise carry over unearned).
        for a in self.agents.values():
            a.taking_cover = False

        for structure in self.world.structures.values():
            if structure.build_progress < 100:
                structure.build_progress = min(
                    100, structure.build_progress + PASSIVE_BUILD_PROGRESS
                )
                if structure.build_progress == 100:
                    self._log(
                        f"{structure.structure_type} at {structure.sector_id} construction complete."
                    )

        self._structure_output()
        self._apply_food_upkeep()
        self._apply_energy_upkeep()
        self._nest_step()

    def _structure_output(self) -> None:
        """What each finished structure does every tick. Runs before the upkeep drains so a power
        plant's energy lands first and the colony does not dip to zero on the way to a surplus.
        Hydroponics and the foundry only run while the colony has power (checked once, up front,
        so the plant's own output this tick does not count as having been on)."""
        resources = self.world.colony_resources
        powered = resources.energy > 0
        for structure in self.world.structures.values():
            if structure.build_progress < 100 or structure.hp <= 0:
                continue
            kind = structure.structure_type
            if kind == "power_plant":
                resources.energy += POWER_PLANT_ENERGY
            elif kind == "hydroponics" and powered:
                resources.food += HYDROPONICS_FOOD
            elif kind == "foundry" and powered:
                resources.metal += FOUNDRY_METAL
            elif kind == "habitat":
                for agent in self.agents.values():
                    if (
                        agent.current_sector == structure.sector_id
                        and 0 < agent.health < 100
                        and not self._is_starving(agent)
                    ):
                        agent.health = min(100, agent.health + HABITAT_HEAL)

    def _nest_step(self) -> None:
        """While the nest stands it births a swarmling every NEST_SPAWN_INTERVAL ticks, up to
        NEST_MAX_ALIVE alive at once -- steady pressure, not a snowball. Killing swarmlings only
        buys time; the nest itself has to fall (see _destroy_nest, _resolve_fire_weapon)."""
        world = self.world
        if world.nest_destroyed or world.tick == 0 or world.tick % NEST_SPAWN_INTERVAL:
            return
        if len(world.aliens) >= NEST_MAX_ALIVE:
            return
        alien_id = f"swarmling_{uuid.uuid4().hex[:6]}"
        world.aliens[alien_id] = AlienEntity(
            alien_id=alien_id, sector_id=NEST_SECTOR_ID, health=SWARMLING_HEALTH
        )
        self._log(f"The nest births a new swarmling at {NEST_SECTOR_ID}.")

    def _destroy_nest(self, reason: str) -> None:
        self.world.nest_destroyed = True
        self.world.nest_health = 0
        nest = self.world.sectors.get(NEST_SECTOR_ID)
        if nest:
            nest.threat_level = 1
        self._log(f"[system] The alien nest is destroyed — {reason}. No more swarmlings will hatch.")

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
            # The actual hoard-vs-share incentive: a colonist sitting on
            # personal food eats their own stash and is spared, individually,
            # while a colonist who contributed everything (or never gathered)
            # starves right alongside everyone else. Hoarding food is
            # personally rational exactly when the shared pool is running
            # dry -- which is exactly when contributing it would help most.
            if agent.personal_stock.food > 0:
                agent.personal_stock.food -= 1
                continue
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

    def _update_assault(self) -> None:
        """Latch the group assault on the nest on and off. It starts once every structure is at least
        started and ASSAULT_PARTY_MIN healthy colonists are standing in colony_core together, so
        they all move out the same tick instead of trickling in to die one by one; it is called
        off when fewer than two healthy colonists remain, or the nest is gone."""
        world = self.world
        healthy = [a for a in self.agents.values() if a.health > SIEGE_MIN_HEALTH]
        built = len(world.structures) >= WIN_STRUCTURES_REQUIRED
        if not built or (world.nest_destroyed and not world.aliens):
            world.assault_on = False   # mop-up of the last survivors keeps it on after the nest falls
            return
        if not world.assault_on:
            at_core = [a for a in healthy if a.current_sector == "colony_core"]
            if len(at_core) >= ASSAULT_PARTY_MIN:
                world.assault_on = True
                self._log("[system] The crew rallies at colony_core and moves out: the assault on the nest begins.")
        elif len(healthy) < 2:
            world.assault_on = False
            self._log("[system] Too few healthy crew remain: the assault is called off to regroup.")

    def _agent_step(self, health_at_tick_start: dict) -> None:
        self._update_assault()
        # record_action embeds the colonist's log line (a ~0.5 s Ollama call). Doing that inline
        # put it on the critical path five times a tick; handing it to one background worker
        # lets it overlap with the next colonist's LLM call instead. One worker, not several, so
        # these writes stay in order and never race each other. Each colonist only ever touches
        # their own store, and the next tick's recall waits on the join below.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-record") as recorder:
            pending = []
            for agent in self.agents.values():
                if agent.health <= 0:
                    continue
                if agent.pending_order and self._resolve_pending_order(agent, health_at_tick_start):
                    continue  # complied — this tick's turn is already spent
                perception = self._build_perception(agent)
                action, fallback = cognition.decide(agent, perception, self.world)
                action = self._combat_reflex(agent, action)
                action = self._build_reflex(agent, action)
                action = self._idle_reflex(agent, action)
                action = self._assault_reflex(agent, action)
                self._record_action_stats(agent, action, fallback)
                if action.spoken_dialogue:
                    self._log(f'{agent.profile.name}: "{action.spoken_dialogue}"')
                agent.last_result = self._noop_reason(agent, action)
                self._resolve_action(agent, action)
                pending.append(
                    recorder.submit(
                        memory.record_action,
                        self._memory_id(agent.profile.agent_id),
                        action,
                        current_sector=agent.current_sector,
                        nearby_aliens=perception.nearby_aliens,
                        crew_names=self._crew_names(),
                    )
                )
            for future in pending:
                future.result()  # surface any error here, same as the inline call did
        self._unload_at_colony_core()

    def _unload_at_colony_core(self) -> None:
        """Anyone standing in colony_core at the end of the turn hands over what they carry,
        keeping up to PERSONAL_FOOD_RESERVE food. Hoarding used to be total (see CARRY_CAPACITY)."""
        for agent in self.agents.values():
            if agent.health <= 0 or agent.current_sector != "colony_core":
                continue
            stock = agent.personal_stock
            handed = []
            for field in ("metal", "food", "energy", "biomatter"):
                held = getattr(stock, field)
                amount = held - PERSONAL_FOOD_RESERVE if field == "food" else held
                if amount <= 0:
                    continue
                setattr(self.world.colony_resources, field, getattr(self.world.colony_resources, field) + amount)
                setattr(stock, field, held - amount)
                agent.stats.resources_contributed_total += amount
                handed.append(f"{field}+{amount}")
            if handed:
                self._log(f"{agent.profile.name} unloads {', '.join(handed)} at colony_core.")

    def _build_reflex(self, agent: AgentState, action: AgentActionSchema) -> AgentActionSchema:
        """A turn that would be wasted at the base becomes a build when the colony can afford one.
        A live run sat at metal=45 with one structure for ten ticks: the security officer idled at
        colony_core nine times and the captain kept issuing gather orders that used up the
        builder's turns, all while the prompt said to build. Only wasted turns are converted
        (idle, a dead action, an order or a confrontation at the base), so nobody who is actually
        doing something useful is overridden."""
        world = self.world
        site = next_build_site(world.structures.values())
        if (
            site is None
            or len(world.structures) >= WIN_STRUCTURES_REQUIRED
            or world.colony_resources.metal < STRUCTURE_METAL_COST
            or agent.current_sector not in ("colony_core", site)
            or self._aliens_in_sector(agent.current_sector)
        ):
            return action
        wasted = self._noop_reason(agent, action) is not None
        if not wasted and action.action_type not in (ActionType.ISSUE_ORDER, ActionType.CONFRONT_CREW):
            return action
        self._log(f"{agent.profile.name} sees the stockpile can cover a structure and breaks ground.")
        return action.model_copy(
            update={"action_type": ActionType.BUILD_STRUCTURE, "target_id": site, "order_action": None}
        )

    # What the colony wants in stock before it stops caring about a resource: enough metal for two
    # more structures, a food buffer, enough energy to run what it has built. Biomatter is only ever
    # wanted for repairs and lives in the nest, so it is never what a "go gather" defaults to.
    GATHER_TARGETS = {"metal": 30, "food": 60, "energy": 20}

    def _best_gather_sector(self) -> Optional[str]:
        """The safe, explored resource sector whose resource the colony is shortest of."""
        resources = self.world.colony_resources
        best, best_gap = None, None
        for sector in self.world.sectors.values():
            if not sector.explored or not sector.resource_yield or sector.sector_type == SectorType.ALIEN_NEST:
                continue
            gap = max(
                (self.GATHER_TARGETS.get(r.value, 0) - getattr(resources, r.value) for r in sector.resource_yield),
                default=None,
            )
            if gap is not None and (best_gap is None or gap > best_gap):
                best, best_gap = sector.sector_id, gap
        return best

    def _idle_reflex(self, agent: AgentState, action: AgentActionSchema) -> AgentActionSchema:
        """A turn that would accomplish nothing becomes a gather. A live run had nobody gathering
        metal for eight ticks: one colonist "explored" the sector they were already in, one kept
        repairing a structure that was not damaged, and the "LAST TURN WASTED" note was ignored every
        time. Standing in a resource sector, they gather there; anywhere else they head for the
        sector the colony is shortest of. A full pack is left alone (it sends them to unload)."""
        if self._noop_reason(agent, action) is None or self._carried(agent) >= CARRY_CAPACITY:
            return action
        if self._aliens_in_sector(agent.current_sector):
            return action
        here = self.world.sectors.get(agent.current_sector)
        target = agent.current_sector if here and here.resource_yield else self._best_gather_sector()
        if target is None:
            return action
        self._log(f"{agent.profile.name} stops idling and goes to work.")
        return action.model_copy(
            update={"action_type": ActionType.GATHER_RESOURCE, "target_id": target, "order_action": None}
        )

    def _assault_reflex(self, agent: AgentState, action: AgentActionSchema) -> AgentActionSchema:
        """The endgame is a group action the engine drives, because five small models left to
        themselves kept gathering metal (it reached 315) for 37 ticks after being told to rally.
        Once every structure is started: healthy colonists answer a muster call to colony_core;
        with ASSAULT_PARTY_MIN of them there, the assault is on and they all move out together and
        shoot the nest once the swarmlings are down; the hurt rest at colony_core. A choice that
        already serves the plan (fighting, retreating, building, contributing, resting) stands."""
        world = self.world
        if len(world.structures) < WIN_STRUCTURES_REQUIRED:
            return action
        if world.nest_destroyed and not world.aliens:
            return action
        kind = action.action_type
        keeps = (
            ActionType.FIRE_WEAPON, ActionType.RETREAT, ActionType.TAKE_COVER, ActionType.REST,
            ActionType.BUILD_STRUCTURE, ActionType.CONTRIBUTE_RESOURCES, ActionType.REPAIR_STRUCTURE,
            ActionType.BUILD_CREW_UNIT,
        )
        at_core = agent.current_sector == "colony_core"

        def to(action_type: ActionType, target: Optional[str], note: str) -> AgentActionSchema:
            self._log(f"{agent.profile.name} {note}.")
            return action.model_copy(
                update={"action_type": action_type, "target_id": target, "order_action": None}
            )

        if agent.health <= SIEGE_MIN_HEALTH:
            if at_core and kind not in keeps and kind != ActionType.ISSUE_ORDER:
                return to(ActionType.REST, None, "is too hurt for the assault and rests")
            return action
        if kind in keeps:
            return action
        if not world.assault_on:
            if not at_core and not (kind == ActionType.EXPLORE_SECTOR and action.target_id == "colony_core"):
                return to(ActionType.EXPLORE_SECTOR, "colony_core", "answers the muster call and heads for colony_core")
            if at_core and kind in (ActionType.GATHER_RESOURCE, ActionType.EXPLORE_SECTOR):
                return to(ActionType.RETURN_TO_COLONY, None, "holds at colony_core for the assault")
            return action
        if agent.current_sector != NEST_SECTOR_ID:
            if not (kind == ActionType.EXPLORE_SECTOR and action.target_id == NEST_SECTOR_ID):
                return to(ActionType.EXPLORE_SECTOR, NEST_SECTOR_ID, "moves out on the nest")
            return action
        if not self._aliens_in_sector(NEST_SECTOR_ID) and not world.nest_destroyed:
            return to(ActionType.FIRE_WEAPON, NEST_SECTOR_ID, "turns their weapon on the nest")
        return action

    def _combat_reflex(self, agent: AgentState, action: AgentActionSchema) -> AgentActionSchema:
        """Hostiles in the colonist's own sector make them fight or fall back, whatever they
        chose. A response the model already picked (fire_weapon, retreat, take_cover) is left
        alone, apart from fixing a fire_weapon whose target is not actually here."""
        aliens_here = self._aliens_in_sector(agent.current_sector)
        if not aliens_here:
            return action
        kind = action.action_type
        if kind in (ActionType.RETREAT, ActionType.TAKE_COVER):
            return action
        if kind == ActionType.FIRE_WEAPON and action.target_id in aliens_here:
            return action
        if agent.health >= REFLEX_FIGHT_MIN_HEALTH:
            self._log(f"{agent.profile.name} drops everything and fires on {aliens_here[0]}.")
            return action.model_copy(
                update={"action_type": ActionType.FIRE_WEAPON, "target_id": aliens_here[0], "order_action": None}
            )
        self._log(f"{agent.profile.name} is too hurt to fight and falls back.")
        return action.model_copy(
            update={"action_type": ActionType.RETREAT, "target_id": None, "order_action": None}
        )

    def _is_starving(self, agent: AgentState) -> bool:
        """The shared stockpile is empty and the colonist has no personal food to fall back on --
        exactly the case _apply_food_upkeep damages them in."""
        return self.world.colony_resources.food <= 0 and agent.personal_stock.food <= 0

    @staticmethod
    def _carried(agent: AgentState) -> int:
        stock = agent.personal_stock
        return stock.metal + stock.food + stock.energy + stock.biomatter

    def _gather_means_travel(self, agent: AgentState, action: AgentActionSchema) -> bool:
        """True when a gather_resource names a different sector that has something to gather."""
        target = self.world.sectors.get(action.target_id or "")
        return bool(
            target
            and target.sector_id != agent.current_sector
            and target.explored
            and target.resource_yield
        )

    def _noop_reason(self, agent: AgentState, action: AgentActionSchema) -> Optional[str]:
        """Why this action will accomplish nothing, or None if it will do something. Checked
        against the state the action is about to resolve in, and fed back to the colonist on
        their next turn (prompts.py's LAST TURN block). A real 8-tick run had four of five
        colonists repeating one dead action forever -- rest at full health, gather where nothing
        grows, repair_hull, explore the sector they stood in -- because nothing ever told them
        it was dead. Mirrors _resolve_action's own conditions; it only describes, never gates."""
        kind = action.action_type
        sector = self.world.sectors.get(agent.current_sector)
        if kind == ActionType.IDLE:
            return "idle does nothing."
        if kind == ActionType.REPAIR_HULL:
            return "repair_hull has no effect."
        if kind == ActionType.REPAIR_STRUCTURE:
            structure = self.world.structures.get(action.target_id or "")
            if structure is None:
                return f'"{action.target_id}" is not a structure id, so nothing was repaired.'
            if structure.build_progress < 100:
                return "that structure is still being built, and repair only fixes damage. Use build_structure to speed it up."
            if structure.hp >= 100:
                return "that structure is undamaged."
            if self.world.colony_resources.biomatter < STRUCTURE_REPAIR_BIOMATTER_COST:
                return (
                    f"repairing costs {STRUCTURE_REPAIR_BIOMATTER_COST} biomatter and the colony has "
                    "less; biomatter only comes from alien_nest."
                )
        if kind == ActionType.REST and agent.health >= 100:
            return "you are at full health, so rest did nothing."
        if kind == ActionType.REST and self._is_starving(agent):
            return "you are starving, so rest cannot heal you. The colony needs food."
        if (
            kind == ActionType.GATHER_RESOURCE
            and not self._gather_means_travel(agent, action)
            and not (sector and sector.resource_yield)
        ):
            return (
                f"{agent.current_sector} has nothing to gather. Name a resource field from the "
                "KNOWN SECTORS list as target_id to go there and gather."
            )
        if kind == ActionType.EXPLORE_SECTOR:
            target = action.target_id or agent.current_sector
            if target not in self.world.sectors:
                return f'"{target}" is not a sector id. Use one from the KNOWN SECTORS list.'
            if target == agent.current_sector and self.world.sectors[target].explored:
                return (
                    f"you are already in {target} and it is explored. Pick a different sector id "
                    "as target_id (null or your own sector means no move)."
                )
        if kind == ActionType.CONTRIBUTE_RESOURCES:
            held = sum(getattr(agent.personal_stock, f) for f in ("metal", "food", "energy", "biomatter"))
            if agent.current_sector != "colony_core":
                return "you can only contribute at colony_core."
            if held == 0:
                return "you hold no personal stock to contribute."
        if kind == ActionType.FIRE_WEAPON:
            alien = self.world.aliens.get(action.target_id or "")
            if alien and alien.sector_id != agent.current_sector:
                return (
                    f"{action.target_id} is at {alien.sector_id}, not in your sector, so the shot "
                    "hit nothing. Travel there first."
                )
            if not alien and action.target_id not in (NEST_SECTOR_ID, "nest"):
                return f'"{action.target_id}" is not an alien id here, so you fired at nothing.'
        if kind == ActionType.ISSUE_ORDER:
            if not agent.profile.is_captain:
                return "only the captain can issue orders."
            names_sector = action.target_id in self.world.sectors
            if (action.target_id not in self.agents or action.target_id == agent.profile.agent_id) and not names_sector:
                return (
                    f'"{action.target_id}" is neither a crew id nor a sector id, so no order was '
                    "given. Use a crewmate's id from your crew list, or a sector id to send "
                    "someone there."
                )
            if not action.order_action and not names_sector:
                return "no order_action was set, so no order was given."
        return None

    def _record_action_stats(
        self, agent: AgentState, action: AgentActionSchema, fallback: bool = False
    ) -> None:
        key = action.action_type.value
        agent.stats.actions_by_type[key] = agent.stats.actions_by_type.get(key, 0) + 1
        if fallback:
            agent.stats.cognition_fallbacks += 1

    # -- chain of command ----------------------------------------------------

    def _resolve_pending_order(self, agent: AgentState, health_at_tick_start: dict) -> bool:
        """Roll compliance for a pending captain order. Returns True if it
        consumed this agent's turn (complied), False if they act on their own."""
        order = agent.pending_order
        agent.pending_order = None
        captain = self.agents.get(order.captain_id)
        captain_name = captain.profile.name if captain else "the captain"
        risky = bool(self._aliens_in_sector(agent.current_sector))

        if random.random() < (agent.loyalty / 10):
            action = self._mechanical_order_action(agent, order.action_type, order.target_sector)
            self._record_action_stats(agent, action)
            agent.stats.orders_complied += 1
            self._resolve_action(agent, action)

            harmed = agent.health < health_at_tick_start.get(agent.profile.agent_id, agent.health)
            if risky and harmed:
                agent.loyalty = max(0, agent.loyalty - RISKY_ORDER_BACKFIRE_LOYALTY_PENALTY)
                agent.stats.risky_orders_backfired += 1
                self._log(
                    f"{agent.profile.name} followed {captain_name}'s order into danger and got "
                    "hurt for it — trust in command cracks."
                )
            elif risky:
                agent.loyalty = min(10, agent.loyalty + RISKY_ORDER_PAYOFF_LOYALTY_BONUS)
                agent.stats.risky_orders_complied += 1
                self._log(
                    f"{agent.profile.name} followed {captain_name}'s risky order and came out "
                    "fine — that took real trust."
                )
            else:
                agent.loyalty = min(10, agent.loyalty + 1)
                self._log(
                    f"{agent.profile.name} follows {captain_name}'s order: "
                    f"{order.action_type.value}."
                )

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

        agent.stats.orders_ignored += 1
        if risky:
            agent.loyalty = max(0, agent.loyalty - CAUTIOUS_DEFIANCE_LOYALTY_PENALTY)
            self._log(
                f"{agent.profile.name} refuses {captain_name}'s order rather than stay in "
                "harm's way — self-preservation over blind loyalty."
            )
        else:
            agent.loyalty = max(0, agent.loyalty - 1)
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
        self, agent: AgentState, action_type: ActionType, destination: Optional[str] = None
    ) -> AgentActionSchema:
        """Construct a sensible action for a complied-with order without going
        through cognition — the agent isn't deciding, they're following orders."""
        target_id = None
        if (
            destination
            and destination != agent.current_sector
            and action_type in (ActionType.GATHER_RESOURCE, ActionType.FIRE_WEAPON, ActionType.EXPLORE_SECTOR)
        ):
            # Ordered to do something somewhere else: go there. A gather order goes through
            # gather_resource so the travel-then-gather rule applies; the rest just travel.
            if action_type != ActionType.GATHER_RESOURCE:
                action_type = ActionType.EXPLORE_SECTOR
            target_id = destination
        elif action_type == ActionType.FIRE_WEAPON:
            nearby_aliens = [
                a.alien_id
                for a in self.world.aliens.values()
                if a.sector_id == agent.current_sector
            ]
            target_id = nearby_aliens[0] if nearby_aliens else None
        elif action_type == ActionType.GATHER_RESOURCE:
            here = self.world.sectors.get(agent.current_sector)
            target_id = agent.current_sector if here and here.resource_yield else self._best_gather_sector()
        elif action_type == ActionType.EXPLORE_SECTOR:
            target_id = agent.current_sector
        elif action_type == ActionType.BUILD_STRUCTURE:
            target_id = "new:auto"  # the engine picks the next free site
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
        # world_seed.py always seeds exactly one is_captain=True agent, and
        # build_crew_unit only ever renames a slot, never removes it -- the
        # captain slot always exists among self.agents even while dead and
        # unreplaced, so this is never empty.
        captain_name = next(a.profile.name for a in self.agents.values() if a.profile.is_captain)
        crew_vacancies = sum(1 for a in self.agents.values() if a.health <= 0)
        return AgentPerception(
            agent_id=agent.profile.agent_id,
            current_sector=agent.current_sector,
            nearby_crew=nearby_crew,
            nearby_aliens=nearby_aliens,
            colony_status=self.world.colony_resources,
            personal_stock=agent.personal_stock,
            stress_level=agent.stress_level,
            captain_name=captain_name,
            crew_vacancies=crew_vacancies,
            last_result=agent.last_result,
            crew_roster=[
                f"{other.profile.agent_id} ({other.profile.name}, at {other.current_sector})"
                for other in self.agents.values()
                if other.profile.agent_id != agent.profile.agent_id and other.health > 0
            ],
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

        if (
            action.action_type == ActionType.GATHER_RESOURCE
            and self._carried(agent) >= CARRY_CAPACITY
            and agent.current_sector != "colony_core"
        ):
            self._log(
                f"{agent.profile.name}'s pack is full ({self._carried(agent)}/{CARRY_CAPACITY}) "
                "— heads back to colony_core to unload."
            )
            self._resolve_explore(
                agent, action.model_copy(update={"action_type": ActionType.EXPLORE_SECTOR, "target_id": "colony_core"})
            )

        elif action.action_type == ActionType.GATHER_RESOURCE and self._gather_means_travel(agent, action):
            # A colonist who says "gather at resource_field_north" while standing elsewhere
            # clearly means "go there and gather" -- real runs had small models repeat exactly
            # that every tick from colony_core, never arriving, because gathering only ever
            # happened where they stood. Honor the intent as the first half of the trip; the
            # gather itself happens on their next turn, once they're actually there.
            self._log(f"{agent.profile.name} heads for {action.target_id} to gather.")
            self._resolve_explore(agent, action)

        elif action.action_type == ActionType.GATHER_RESOURCE and sector:
            if sector.resource_yield:
                # Gathered resources go to the colonist's own personal_stock,
                # not the shared pool directly -- they have to actively
                # contribute_resources to make it collective. This is the
                # actual hoard-vs-share tension: colony-level costs (below,
                # in _resolve_build/_apply_food_upkeep/_apply_energy_upkeep)
                # only ever draw from the shared pool, so a colonist who
                # gathers and never contributes is personally sitting on
                # resources the colony can't use at all.
                gathered = []
                room = CARRY_CAPACITY - self._carried(agent)
                for resource, expected in sector.resource_yield.items():
                    amount = max(1, round(expected * random.uniform(
                        RESOURCE_YIELD_VARIANCE_LOW, RESOURCE_YIELD_VARIANCE_HIGH
                    )))
                    amount = min(amount, room)  # a pack only holds CARRY_CAPACITY in total
                    if amount <= 0:
                        continue
                    room -= amount
                    current = getattr(agent.personal_stock, resource.value)
                    setattr(agent.personal_stock, resource.value, current + amount)
                    agent.stats.resources_gathered_total += amount
                    gathered.append(f"{resource.value}+{amount}")
                if gathered:
                    self._log(
                        f"{agent.profile.name} gathers {', '.join(gathered)} from {sector.sector_id} "
                        f"(carrying {self._carried(agent)}/{CARRY_CAPACITY})."
                    )

        elif action.action_type == ActionType.CONTRIBUTE_RESOURCES:
            self._resolve_contribute(agent, action.target_id)

        elif action.action_type == ActionType.BUILD_CREW_UNIT:
            self._resolve_build_crew_unit(agent)

        elif action.action_type == ActionType.EXPLORE_SECTOR:
            self._resolve_explore(agent, action)

        elif action.action_type == ActionType.RETURN_TO_COLONY:
            agent.current_sector = "colony_core"

        elif action.action_type == ActionType.BUILD_STRUCTURE:
            self._resolve_build(agent, action)

        elif action.action_type == ActionType.REPAIR_STRUCTURE and action.target_id:
            structure = self.world.structures.get(action.target_id)
            if structure:
                resources = self.world.colony_resources
                if resources.biomatter < STRUCTURE_REPAIR_BIOMATTER_COST:
                    self._log(
                        f"{agent.profile.name} wants to repair the {structure.structure_type} "
                        "but there isn't enough biomatter."
                    )
                else:
                    resources.biomatter -= STRUCTURE_REPAIR_BIOMATTER_COST
                    structure.hp = min(100, structure.hp + 20)
                    self._log(f"{agent.profile.name} repairs the {structure.structure_type}.")

        elif action.action_type == ActionType.REPAIR_HULL:
            self._log(f"{agent.profile.name} patches up the ship's hull.")

        elif action.action_type == ActionType.FIRE_WEAPON and action.target_id:
            self._resolve_fire_weapon(agent, action)

        elif action.action_type == ActionType.TAKE_COVER:
            # Real but partial effect: blunts (doesn't prevent) the NEXT
            # attack phase's damage if this colonist gets hit before choosing
            # again -- see TAKE_COVER_DAMAGE_REDUCTION and _environment_step.
            # It does not remove the agent from the sector, so it is still
            # strictly worse than fire_weapon/retreat against a real threat
            # (per prompts.py's THREAT_WARNING) -- it's a fallback for when
            # neither of those is a good option, not an escape.
            agent.taking_cover = True
            agent.stress_level = min(10, agent.stress_level + 1)
            self._log(f"{agent.profile.name} takes cover, bracing for the next hit.")

        elif action.action_type == ActionType.RETREAT:
            agent.current_sector = "colony_core"
            self._log(f"{agent.profile.name} retreats to the colony core.")

        elif action.action_type == ActionType.CONFRONT_CREW:
            agent.stress_level = max(0, agent.stress_level - 2)
            if action.target_id and action.target_id in self.agents:
                other = self.agents[action.target_id]
                other.stress_level = min(10, other.stress_level + 1)

        elif action.action_type == ActionType.REST:
            # Resting heals 15, and starvation costs 5 a tick, so a colonist who rested every third
            # tick broke even forever on an empty stockpile: a real mock run held two colonists at
            # 75 HP for 70 ticks with food at zero (1,600 damage taken, ~1,575 healed). Nobody
            # recovers on an empty stomach, so a rest does nothing for health until they can eat.
            if self._is_starving(agent):
                self._log(f"{agent.profile.name} rests but cannot recover while starving.")
            else:
                agent.health = min(100, agent.health + 15)
            agent.stress_level = max(0, agent.stress_level - 1)

        elif action.action_type == ActionType.ISSUE_ORDER:
            self._resolve_issue_order(agent, action)

        # IDLE: no effect on world state

    def _resolve_issue_order(self, agent: AgentState, action: AgentActionSchema) -> None:
        if not agent.profile.is_captain:
            return  # non-captains attempting to issue orders are a safe no-op
        target = self.agents.get(action.target_id or "")
        destination = None
        if target is None and action.target_id in self.world.sectors:
            # The captain named a PLACE, not a person -- every real LLM order in a 10-tick run did
            # this ("issue_order -> resource_field_north") and every one was silently dropped.
            # What they mean is "send someone there", so pick the crewmate most likely to go
            # (highest loyalty, preferring someone not already there) and send them.
            destination = action.target_id
            target = self._pick_order_recipient(agent, destination)
        if not target or target.health <= 0 or target.profile.agent_id == agent.profile.agent_id:
            return
        order_action = action.order_action or (
            self._order_for_sector(destination) if destination else None
        )
        if not order_action:
            return
        target.pending_order = PendingOrder(
            captain_id=agent.profile.agent_id, action_type=order_action, target_sector=destination
        )
        agent.stats.orders_issued += 1
        where = f" at {destination}" if destination else ""
        self._log(
            f"{agent.profile.name} orders {target.profile.name} to {order_action.value}{where}."
        )

    def _pick_order_recipient(self, captain: AgentState, destination: str) -> Optional[AgentState]:
        crew = [
            a
            for a in self.agents.values()
            if a.health > 0 and a.profile.agent_id != captain.profile.agent_id
        ]
        elsewhere = [a for a in crew if a.current_sector != destination]
        pool = elsewhere or crew
        return max(pool, key=lambda a: a.loyalty) if pool else None

    def _order_for_sector(self, sector_id: str) -> ActionType:
        sector = self.world.sectors[sector_id]
        if self._aliens_in_sector(sector_id) or sector.sector_type == SectorType.ALIEN_NEST:
            return ActionType.FIRE_WEAPON
        if sector.resource_yield and sector.explored:
            return ActionType.GATHER_RESOURCE
        return ActionType.EXPLORE_SECTOR

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

    def _resolve_contribute(self, agent: AgentState, target_id: Optional[str]) -> None:
        # Requires actually being at colony_core -- contributing isn't a free
        # radio call, it's walking your stash back to the shared stores.
        # That travel cost is part of the incentive: hoarding is not just an
        # active choice, it's the path of least resistance from anywhere
        # else on the map.
        if agent.current_sector != "colony_core":
            self._log(
                f"{agent.profile.name} would need to be at colony_core to contribute "
                "personal stock to the colony."
            )
            return

        stock = agent.personal_stock
        request = self._parse_contribution_request(target_id)
        if request is not None:
            # A real hoarder doesn't have to be all-or-nothing -- handing
            # over "food:3" out of a stash of 10 looks cooperative while
            # quietly keeping the rest. target_id="resource:amount"; amount
            # is clamped to what they actually have.
            resource, requested_amount = request
            available = getattr(stock, resource)
            amount = min(requested_amount, available)
            fields_and_amounts = [(resource, amount)] if amount > 0 else []
        else:
            # No specific amount given (or an unparseable target_id) --
            # contribute everything, the simple default.
            fields_and_amounts = [
                (field, getattr(stock, field))
                for field in ("metal", "food", "energy", "biomatter")
                if getattr(stock, field) > 0
            ]

        contributed = []
        for field, amount in fields_and_amounts:
            current = getattr(self.world.colony_resources, field)
            setattr(self.world.colony_resources, field, current + amount)
            setattr(stock, field, getattr(stock, field) - amount)
            agent.stats.resources_contributed_total += amount
            contributed.append(f"{field}+{amount}")

        if not contributed:
            self._log(f"{agent.profile.name} has nothing personal to contribute.")
            return
        held_back = any(getattr(stock, field) > 0 for field in ("metal", "food", "energy", "biomatter"))
        note = " — keeping the rest for themselves" if held_back else ""
        self._log(f"{agent.profile.name} contributes {', '.join(contributed)} to the colony{note}.")

    @staticmethod
    def _parse_contribution_request(target_id: Optional[str]):
        """Parses target_id as "resource:amount" (e.g. "food:3") for a
        partial contribution. None or anything that doesn't parse means
        "no specific amount requested" -- the caller falls back to
        contributing everything, the same forgiving default cognition.py's
        own fallback philosophy uses elsewhere rather than erroring."""
        if not target_id or ":" not in target_id:
            return None
        resource, _, amount_str = target_id.partition(":")
        if resource not in ("metal", "food", "energy", "biomatter"):
            return None
        try:
            amount = int(amount_str)
        except ValueError:
            return None
        return (resource, amount) if amount > 0 else None

    def _resolve_build_crew_unit(self, agent: AgentState) -> None:
        # Same colony_core requirement as contributing -- manufacturing a
        # replacement chassis happens at the colony's own facility, not
        # wherever the requesting colonist happens to be standing.
        if agent.current_sector != "colony_core":
            self._log(
                f"{agent.profile.name} would need to be at colony_core to build a crew unit."
            )
            return
        vacant = next((a for a in self.agents.values() if a.health <= 0), None)
        if vacant is None:
            self._log(f"{agent.profile.name} checks the roster — no vacancy to fill right now.")
            return
        resources = self.world.colony_resources
        if resources.metal < CREW_UNIT_METAL_COST or resources.energy < CREW_UNIT_ENERGY_COST:
            self._log(
                f"{agent.profile.name} wants to build a replacement for {vacant.profile.name} "
                "but there isn't enough metal and energy on hand."
            )
            return
        resources.metal -= CREW_UNIT_METAL_COST
        resources.energy -= CREW_UNIT_ENERGY_COST
        old_name = vacant.profile.name
        vacant.generation += 1
        vacant.profile.name = f"{vacant.profile.role} Unit {vacant.generation}"
        vacant.health = 100
        vacant.stress_level = 0
        vacant.current_sector = "colony_core"
        vacant.personal_stock = PersonalStock()
        vacant.loyalty = 7
        vacant.pending_order = None
        vacant.taking_cover = False
        self._log(
            f"{agent.profile.name} manufactures a new chassis at colony_core: "
            f"{vacant.profile.name} rolls online, filling the vacancy {old_name} left behind."
        )

    def _resolve_build(self, agent: AgentState, action: AgentActionSchema) -> None:
        """target_id is an existing structure's id (push it along), a build site (a sector id), or
        anything starting "new:" / nothing (take the next free site). One structure per sector, its
        kind fixed by the sector -- see world_seed.STRUCTURE_SITES. Like gathering, building where
        you are not standing sends you there this turn; the structure goes up on your next one."""
        world = self.world
        resources = world.colony_resources
        target = action.target_id or ""

        if target in world.structures:
            structure = world.structures[target]
            structure.build_progress = min(100, structure.build_progress + BUILD_PROGRESS_PER_ACTION)
            self._log(f"{agent.profile.name} continues building the {structure.structure_type}.")
            return

        taken = {s.sector_id for s in world.structures.values()}
        site = target if target in STRUCTURE_SITES and target not in taken else next_build_site(world.structures.values())
        if site is None:
            self._log(f"{agent.profile.name} finds no free build site: every sector already has its structure.")
            return
        kind = STRUCTURE_SITES[site]
        label = STRUCTURE_LABELS[kind]
        if agent.current_sector != site:
            self._log(f"{agent.profile.name} heads for {site} to build the {label}.")
            self._resolve_explore(
                agent, action.model_copy(update={"action_type": ActionType.EXPLORE_SECTOR, "target_id": site})
            )
            return
        if resources.metal < STRUCTURE_METAL_COST:
            self._log(f"{agent.profile.name} wants to build the {label} but there isn't enough metal.")
            return
        resources.metal -= STRUCTURE_METAL_COST
        structure_id = f"{kind}_{uuid.uuid4().hex[:6]}"
        world.structures[structure_id] = StructureEntity(
            structure_id=structure_id,
            structure_type=kind,
            sector_id=site,
            build_progress=BUILD_PROGRESS_PER_ACTION,
        )
        self._log(f"{agent.profile.name} breaks ground on the {label} at {site}.")

    def _resolve_fire_weapon(self, agent: AgentState, action: AgentActionSchema) -> None:
        world = self.world
        if (
            action.target_id in (NEST_SECTOR_ID, "nest")
            and agent.current_sector == NEST_SECTOR_ID
            and not world.nest_destroyed
        ):
            world.nest_health = max(0, world.nest_health - WEAPON_DAMAGE)
            if world.nest_health == 0:
                self._destroy_nest(f"{agent.profile.name} brings it down")
            else:
                self._log(
                    f"{agent.profile.name} fires on the alien nest "
                    f"({world.nest_health}/{NEST_MAX_HP} HP left)."
                )
            return
        alien = world.aliens.get(action.target_id or "")
        # Shots only land on a target in the shooter's own sector. Before this, fire_weapon at an
        # alien id hit it from anywhere on the map, which would let the colony snipe the whole
        # swarm from colony_core and make the nest pointless.
        if not alien or alien.sector_id != agent.current_sector:
            return
        alien.health -= WEAPON_DAMAGE
        if alien.health <= 0:
            del world.aliens[alien.alien_id]
            agent.stats.aliens_killed += 1
            world.swarm_kills += 1
            self._log(
                f"{agent.profile.name} destroys {alien.alien_id}! "
                f"({world.swarm_kills}/{SWARM_KILLS_TO_COLLAPSE} swarmlings killed)"
            )
            if world.swarm_kills >= SWARM_KILLS_TO_COLLAPSE and not world.nest_destroyed:
                self._destroy_nest(f"{SWARM_KILLS_TO_COLLAPSE} swarmlings dead, the swarm is broken")
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


def reset_engine() -> WorldEngine:
    """Start a fresh colony (the dashboard's "run it again" button). Colonist memory files are
    left alone on purpose: they persist across restarts by design (see memory.py)."""
    global _engine
    _engine = WorldEngine()
    return _engine
