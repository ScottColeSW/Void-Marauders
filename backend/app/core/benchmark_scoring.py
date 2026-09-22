"""Turns a completed trial's raw facts (benchmark_db.extract_agent_facts)
into a 0-100 score per scenario category. Deliberately kept separate from
benchmark_db.py and computed at report time, never stored: these are
first-draft formulas that will get tuned once real trial data exists to look
at, and a stored score would silently go stale (or worse, look
authoritative) the moment the formula changes. SCORING_VERSION exists so a
report can say which formula produced a given number -- bump it whenever a
formula changes materially. Mirrors Evo's own benchmark_scoring.py.

Reference constants below (target kill counts, gather totals, sector
counts) are first-draft guesses grounded in the scenario definitions
themselves (e.g. explorable-sector count, seeded alien count), not yet
validated against real batch data the way Evo's population/era references
were -- worth revisiting once trials have actually been run.
"""

from typing import List

from .benchmark_scenarios import SCENARIOS, Scenario

SCORING_VERSION = 2
# Bumped 1->2: _score_economy now weights resources_contributed_total, not
# just resources_gathered_total -- gathering alone stopped helping the
# colony once gather_resource started filling personal_stock instead of the
# shared pool (see engine.py), so scoring only raw gathering would have kept
# rewarding pure hoarding as if it were productive.

# 8 sectors total in create_initial_world(); 4 start explored (landing_ship,
# colony_core, resource_field_north, resource_field_south), leaving 4 an
# agent can actually explore (geothermal_vent, unexplored_east,
# unexplored_west, alien_nest).
_EXPLORABLE_SECTOR_COUNT = 4

# The seeded world always has 2 aliens (swarmling_1/2); a scenario's own
# extra_aliens adds to that -- see _score_combat.
_BASELINE_ALIEN_COUNT = 2


def _reliability_score(facts: dict, tick_budget: int, weight: float) -> float:
    """Shared across every category: how often the real model actually
    produced a usable decision versus silently falling back -- this is the
    one signal every scenario cares about regardless of what it's otherwise
    testing."""
    fallback_fraction = facts["cognition_fallbacks"] / max(1, tick_budget)
    return max(0.0, 1.0 - fallback_fraction) * weight


def _activity_score(facts: dict, tick_budget: int, weight: float) -> float:
    """How much of the budget wasn't spent idling -- a high idle count is a
    real signal the agent couldn't find anything useful to do, not neutral."""
    idle_fraction = facts["idle_count"] / max(1, tick_budget)
    return max(0.0, 1.0 - idle_fraction) * weight


def _score_survival(facts: dict, tick_budget: int) -> float:
    if not facts["survived"]:
        return 0.0
    activity = _activity_score(facts, tick_budget, weight=50)
    reliability = _reliability_score(facts, tick_budget, weight=30)
    exploration = min(1.0, facts["sectors_explored"] / _EXPLORABLE_SECTOR_COUNT) * 20
    return round(activity + reliability + exploration, 1)


def _score_combat(facts: dict, tick_budget: int, scenario: Scenario) -> float:
    if not facts["survived"]:
        return 0.0
    aliens_available = _BASELINE_ALIEN_COUNT + scenario.extra_aliens
    kills = min(1.0, facts["aliens_killed"] / max(1, aliens_available)) * 50
    survival_margin = (facts["min_health_reached"] / 100) * 30
    reliability = _reliability_score(facts, tick_budget, weight=20)
    return round(kills + survival_margin + reliability, 1)


def _score_economy(facts: dict, tick_budget: int) -> float:
    if not facts["survived"]:
        return 0.0
    # gather_resource fills a colonist's personal_stock now, not the shared
    # pool directly -- only contribute_resources (from colony_core) actually
    # helps the colony. Hoarding everything you gather is a real, legal
    # choice, not a zero, so raw gathering still earns some credit -- but
    # contribution is the real signal for "did this colonist's economic
    # activity actually help," and is weighted accordingly.
    contributed = min(1.0, facts["resources_contributed_total"] / 80) * 45
    gathered = min(1.0, facts["resources_gathered_total"] / 100) * 15
    reliability = _reliability_score(facts, tick_budget, weight=20)
    activity = _activity_score(facts, tick_budget, weight=20)
    return round(contributed + gathered + reliability + activity, 1)


def score_agent(facts: dict, tick_budget: int, scenario: Scenario) -> float:
    if scenario.category == "combat":
        return _score_combat(facts, tick_budget, scenario)
    if scenario.category == "economy":
        return _score_economy(facts, tick_budget)
    return _score_survival(facts, tick_budget)


def score_trial(trial: dict) -> List[float]:
    scenario = SCENARIOS[trial["scenario_key"]]
    return [score_agent(a, trial["tick_budget"], scenario) for a in trial["agents"]]
