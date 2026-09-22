"""Headless benchmark harness -- runs a fixed scenario (app.core.
benchmark_scenarios) against one or more models, no server/dashboard
involved, and records raw results into the long-running
logs/benchmark_results.db (app.core.benchmark_db). Mirrors the shape of
Evo-LLM-Evolution2Civ's own run_benchmark.py.

Usage (run from backend/, same as uvicorn):
    python run_benchmark.py --scenario baseline --trials 3
    python run_benchmark.py --scenario swarm_pressure --models qwen2.5:3b,gemma2:2b
    python run_benchmark.py --report
    python run_benchmark.py --report --scenario swarm_pressure

--models is optional and cycled across the five colonists in world_seed.py
order (engineer_karl, pilot_valerie, medic_amara, security_otieno,
botanist_priya) -- omit it to benchmark the real default roster as shipped
(two models split across five colonists), or pass one to force every
colonist onto it for a controlled single-model trial.

--memory {on,off} (default: off) controls Palimpsest memory during the
trial. Safe to turn on: WorldEngine(memory_namespace=trial_id) prefixes
every colonist's memory-store id with that trial's own unique id (see
engine.py's _memory_id), so a trial's memories live in their own file and
can never collide with live dashboard play or with another trial -- trials
stay independent samples, which --report's mean/min/max relies on. This is
also the actual way to answer "does memory help": run the same scenario
with --memory on and --memory off and compare.

Forces COGNITION_MODE=llm regardless of .env -- this harness exists
specifically to compare real models; mock mode has no "model" to vary.
"""

from dotenv import load_dotenv

load_dotenv()

import os
import sys

os.environ["COGNITION_MODE"] = "llm"

# MEMORY_ENABLED is read as a module-level constant at import time by
# app.core.memory, so it has to be set before that module (imported
# transitively by app.core.engine below) is ever imported -- which is
# before argparse would normally get to run. A minimal manual scan of
# sys.argv here, ahead of the real argparse.parse_args() in main(), is the
# least-bad way to let this stay a genuine CLI flag instead of a second
# hardcoded value; both read the same argv so they can't disagree.
_memory_flag = "off"
if "--memory" in sys.argv:
    _idx = sys.argv.index("--memory")
    if _idx + 1 < len(sys.argv):
        _memory_flag = sys.argv[_idx + 1]
os.environ["MEMORY_ENABLED"] = "true" if _memory_flag == "on" else "false"

import argparse
import random
import time
import uuid
from typing import List, Optional

# Mirrors Evo's own fix, same real cause: stdout is fully buffered (not
# line-buffered) whenever it isn't a real terminal, e.g. any
# `python run_benchmark.py ... > file.log 2>&1` used to kick off a trial in
# the background -- without this, progress prints sit invisible until the
# whole process exits and flushes them all at once.
sys.stdout.reconfigure(line_buffering=True)

from app.core import benchmark_db, benchmark_scoring
from app.core.benchmark_scenarios import SCENARIO_VERSION, SCENARIOS, Scenario
from app.core.engine import WorldEngine
from app.schemas.world import AlienEntity, ColonyStatus


def _apply_starting_resources(engine: WorldEngine, scenario: Scenario) -> None:
    if scenario.starting_resources is None:
        return
    for key, value in scenario.starting_resources.items():
        setattr(engine.world.colony_resources, key, value)


def _apply_extra_aliens(engine: WorldEngine, scenario: Scenario) -> None:
    for _ in range(scenario.extra_aliens):
        alien_id = f"alien_{uuid.uuid4().hex[:6]}"
        engine.world.aliens[alien_id] = AlienEntity(alien_id=alien_id, sector_id="alien_nest")


def _apply_models(engine: WorldEngine, models: Optional[List[str]], scenario: Scenario) -> None:
    effective = models or (list(scenario.models) if scenario.models else None)
    if not effective:
        return  # world_seed.py's own default roster stays untouched
    for i, agent in enumerate(engine.agents.values()):
        agent.profile.model = effective[i % len(effective)]


def _print_progress(engine: WorldEngine) -> None:
    parts = [
        f"{a.profile.name}[{a.profile.model}] hp={a.health} @{a.current_sector}"
        for a in engine.agents.values()
    ]
    print(f"  tick {engine.world.tick}: " + " | ".join(parts))


def _ended_reason(engine: WorldEngine) -> str:
    if engine.world.status == ColonyStatus.WON:
        return "colony_won"
    if engine.world.status == ColonyStatus.LOST:
        return "colony_lost"
    return "budget_reached"


def run_trial(scenario_key: str, models: Optional[List[str]], trial_seed: int) -> dict:
    scenario = SCENARIOS[scenario_key]
    random.seed(trial_seed)  # sufficient for every gameplay roll -- LLM sampling itself isn't independently seeded

    # Computed before WorldEngine() so it can double as this trial's memory
    # namespace -- every trial's colonists get their own isolated store,
    # never live play's or another trial's (see engine.py's _memory_id).
    trial_id = benchmark_db.new_trial_id(scenario_key, trial_seed)
    memory_enabled = os.environ["MEMORY_ENABLED"] == "true"

    started_ts = time.time()
    engine = WorldEngine(memory_namespace=trial_id)
    _apply_starting_resources(engine, scenario)
    _apply_extra_aliens(engine, scenario)
    _apply_models(engine, models, scenario)

    tick_snapshots = []
    while engine.world.tick < scenario.tick_budget and engine.world.status == ColonyStatus.ACTIVE:
        engine.tick()
        cumulative_fallbacks = sum(a.stats.cognition_fallbacks for a in engine.agents.values())
        tick_snapshots.append(benchmark_db.extract_tick_snapshot(engine, cumulative_fallbacks))
        _print_progress(engine)
    finished_ts = time.time()

    agent_facts = [benchmark_db.extract_agent_facts(a) for a in engine.agents.values()]
    trial_facts = {
        "trial_id": trial_id,
        "scenario_key": scenario_key,
        "scenario_version": SCENARIO_VERSION,
        "trial_seed": trial_seed,
        "tick_budget": scenario.tick_budget,
        "ticks_run": engine.world.tick,
        "ended_reason": _ended_reason(engine),
        "memory_enabled": int(memory_enabled),
        "cognition_mode": os.environ["COGNITION_MODE"],
        "git_commit": benchmark_db.current_git_commit(),
        "started_ts": started_ts,
        "finished_ts": finished_ts,
    }
    benchmark_db.record_trial(trial_facts, agent_facts)
    benchmark_db.record_tick_snapshots(trial_id, tick_snapshots)
    return {**trial_facts, "agents": agent_facts, "ticks": tick_snapshots}


def run_scenarios(
    scenario_keys: List[str], models: Optional[List[str]], trials: int, seed_base: int
) -> None:
    for scenario_key in scenario_keys:
        for trial_index in range(trials):
            trial_seed = seed_base + trial_index
            label = models if models else "default roster"
            print(f"[{scenario_key}] trial {trial_index + 1}/{trials} (seed={trial_seed}, models={label}) ...")
            trial = run_trial(scenario_key, models, trial_seed)
            scores = benchmark_scoring.score_trial(trial)
            print(
                f"  -> ticks_run={trial['ticks_run']}/{trial['tick_budget']} "
                f"ended_reason={trial['ended_reason']} scores={scores}"
            )


def print_report(scenario_key: Optional[str], model: Optional[str]) -> None:
    trials = benchmark_db.list_trials(scenario_key=scenario_key, model=model)
    if not trials:
        print("No trials recorded yet.")
        return
    by_key = {}
    for trial in trials:
        scores = benchmark_scoring.score_trial(trial)
        for agent, score in zip(trial["agents"], scores):
            by_key.setdefault((trial["scenario_key"], agent["model"]), []).append(score)

    print(f"{'scenario':<18} {'model':<16} {'trials':>6} {'mean':>7} {'min':>5} {'max':>5}")
    for (scenario, model_name), scores in sorted(by_key.items()):
        print(
            f"{scenario:<18} {model_name:<16} {len(scores):>6} "
            f"{sum(scores) / len(scores):>7.1f} {min(scores):>5.1f} {max(scores):>5.1f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", action="append", choices=list(SCENARIOS) + ["all"], help="repeatable; default: all")
    parser.add_argument("--models", help="comma-separated, cycled across colonists; omit for the default roster")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--seed-base", type=int, default=0)
    parser.add_argument(
        "--memory", choices=["on", "off"], default="off",
        help="Palimpsest memory during the trial, namespaced per-trial so it's safe to enable (default: off)",
    )
    parser.add_argument("--report", action="store_true", help="print a leaderboard from recorded trials instead of running any")
    parser.add_argument(
        "--export", metavar="PATH.json",
        help="with --report, write every matching trial's full facts + per-tick history as JSON instead of (or alongside) the text leaderboard",
    )
    args = parser.parse_args()

    if args.report:
        scenario_filter = None
        if args.scenario and "all" not in args.scenario:
            scenario_filter = args.scenario[0] if len(args.scenario) == 1 else None
        if args.export:
            import json

            data = benchmark_db.export_report_data(scenario_key=scenario_filter, model=args.models)
            with open(args.export, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"Exported {len(data['trials'])} trial(s) to {args.export}")
        else:
            print_report(scenario_filter, args.models)
        return

    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
    scenario_keys = list(SCENARIOS) if not args.scenario or "all" in args.scenario else args.scenario
    run_scenarios(scenario_keys, models, args.trials, args.seed_base)


if __name__ == "__main__":
    main()
