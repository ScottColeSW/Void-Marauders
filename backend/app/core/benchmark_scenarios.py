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

SCENARIO_VERSION = 1


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
    # How many additional aliens to spawn into alien_nest before the trial
    # starts, beyond the two the world always seeds with -- tests combat
    # decision-making under real pressure rather than the default,
    # relatively tame threat level.
    extra_aliens: int = 0
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
        tick_budget=60,
        extra_aliens=4,
        description="Alien nest starts hot -- tests combat decisions under real, sustained threat.",
    ),
    "resource_scarcity": Scenario(
        key="resource_scarcity",
        category="economy",
        tick_budget=100,
        starting_resources={"metal": 5, "food": 10, "energy": 5, "biomatter": 0},
        description="Colony starts poor -- tests prioritization under real scarcity, not just execution.",
    ),
}
