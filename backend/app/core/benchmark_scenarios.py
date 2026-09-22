"""Fixed, reproducible starting conditions for comparing local models against
each other on the same colony -- the "define a small set of benchmark
scenarios" half of the harness (scoring lives in benchmark_scoring.py,
persistence in benchmark_db.py). Mirrors the shape of Evo's own
backend/benchmark_scenarios.py.

SCENARIO_VERSION exists so a stored trial (benchmark_db.py) stays
attributable to the exact definition that produced it -- bump this whenever a
scenario's starting conditions change materially (tick_budget, overrides),
so historical trials under an old definition are never silently treated as
comparable to trials under a new one.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

SCENARIO_VERSION = 3
# Bumped 1->2: swarm_pressure's extra aliens moved from alien_nest (a sector
# real trials showed agents reliably never visit voluntarily -- two real LLM
# runs, memory on and off, both scored identically because nobody ever
# engaged) to the colonists' own starting sectors, and its tick_budget cut
# from 60 to 20 -- forced combat resolves in the first few ticks, no reason
# to pay for 60 real-inference ticks to observe it.
# Bumped 2->3: version 2 put 2 of the 4 extra aliens in colony_core itself --
# a real trial (qwen2.5:3b/gemma2:2b, memory off) wiped the entire colony by
# tick 18/20 (colony_lost), because RETREAT hardcodes colony_core as its
# destination (engine.py's _resolve_action) and colony_core wasn't actually
# safe, so fleeing just traded one fight for another. Every colonist scored
# 0 -- a real result, but useless for comparing models since there's no
# differentiation left once everyone's dead. v3 keeps colony_core genuinely
# alien-free (a real place to retreat TO) and halves extra_aliens to 2, one
# each on the two solo colonists (landing_ship, resource_field_south) --
# still an unavoidable fight-or-flee test, but a survivable one.


@dataclass(frozen=True)
class Scenario:
    key: str
    category: str
    tick_budget: int
    description: str
    # None means "use create_initial_world()'s own defaults" (20 metal, 40
    # food, 15 energy, 0 biomatter). Set per-scenario to deliberately
    # override -- run_benchmark.py applies this to world.colony_resources
    # right after WorldEngine() is constructed.
    starting_resources: Optional[Dict[str, int]] = None
    # How many additional aliens to spawn before the trial starts, beyond
    # the two the world always seeds into alien_nest -- tests combat
    # decision-making under real pressure. Placed via alien_spawn_sectors
    # below, not necessarily alien_nest -- see that field's own comment for
    # why.
    extra_aliens: int = 0
    # Which sector(s) extra_aliens spawn into, cycled round-robin. Defaults
    # to alien_nest when unset -- the same place the world's own two seeded
    # aliens live -- but a sector nobody's actually in is not a forcing
    # function: real trials showed every colonist just stays in their own
    # starting sector (colony_core, landing_ship, resource_field_south) the
    # whole run and never wanders toward alien_nest at all. Aliens placed in
    # a colonist's own starting sector are unavoidable from tick 1 instead
    # of depending on voluntary exploration. Deliberately NOT colony_core
    # alone: RETREAT hardcodes colony_core as its destination (engine.py's
    # _resolve_action), so if every extra alien landed there, fleeing combat
    # would mean fleeing into more of it -- spreading across all three
    # starting sectors keeps a lighter-risk sector to retreat toward.
    alien_spawn_sectors: Optional[Tuple[str, ...]] = None
    # None means every colonist keeps world_seed.py's own model assignment.
    # Set to override every agent's model, cycled across the five colonists
    # in world_seed order (engineer_karl, pilot_valerie, medic_amara,
    # security_otieno, botanist_priya) the same way Evo's run_benchmark.py
    # cycles models across tribe_configs -- this is what actually lets a
    # trial compare "this model" against "that model" on the same scenario.
    models: Optional[Tuple[str, ...]] = None


SCENARIOS: Dict[str, Scenario] = {
    "baseline": Scenario(
        key="baseline",
        category="survival",
        tick_budget=100,
        description="Default seed, no overrides -- how far does a model get unassisted?",
    ),
    "swarm_pressure": Scenario(
        key="swarm_pressure",
        category="combat",
        tick_budget=20,
        extra_aliens=2,
        alien_spawn_sectors=("landing_ship", "resource_field_south"),
        description=(
            "One alien each ambushes the two solo colonists (landing_ship, "
            "resource_field_south) from tick 1 -- an unavoidable, survivable "
            "fight-or-flee test. colony_core stays alien-free on purpose: it's "
            "the only place RETREAT sends anyone, so it has to actually be safe."
        ),
    ),
    "resource_scarcity": Scenario(
        key="resource_scarcity",
        category="economy",
        tick_budget=100,
        starting_resources={"metal": 5, "food": 10, "energy": 5, "biomatter": 0},
        description="Colony starts poor -- tests prioritization under real scarcity, not just execution.",
    ),
}
