"""A persistent, cross-run benchmark record for the scenario harness
(app.core.benchmark_scenarios / backend/run_benchmark.py) -- long-running by
design, "append forever, query later," not a one-off report. Two real,
sliceable tables rather than one wide row with a JSON blob, mirroring Evo's
own backend/benchmark_db.py: this is meant to be queried and filtered later
(every trial for one model in one scenario, everything since a given
scenario_version), not just appended-to and read back whole.

Deliberately stores only raw facts, never a computed score -- see
benchmark_scoring.py's own module docstring for why baking a score in here
would silently invalidate every historical trial the moment the scoring
formula is tuned.

One deviation from Evo's shape: actions_by_type is a per-agent dict keyed by
whatever ActionType values exist (13 today, more if the action space grows),
not a fixed small set like Evo's raid/expedition counters -- storing it as
fixed columns would mean a schema migration every time a new action type is
added. It's kept as a JSON text column; idle_count is pulled out as its own
real integer column since "did this agent find anything to do" is the one
single most meaningful signal to aggregate on directly in SQL.
"""

import json
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_DB_PATH = "logs/benchmark_results.db"

_TRIAL_AGENT_INT_COLUMNS = (
    "survived", "final_health", "min_health_reached", "damage_taken_total",
    "resources_gathered_total", "resources_contributed_total", "aliens_killed", "sectors_explored",
    "cognition_fallbacks", "orders_issued", "orders_complied", "orders_ignored",
    "risky_orders_complied", "risky_orders_backfired",
    "idle_count", "final_loyalty", "is_captain",
)
_TRIAL_AGENT_TEXT_COLUMNS = ("agent_id", "model", "actions_by_type_json")
_TRIAL_AGENT_REAL_COLUMNS = ("temperature",)

# One row per tick per trial -- end-of-trial facts alone (the tables above)
# can only ever answer "how did it end," never "what actually happened" (a
# death at tick 5 looks identical to one at tick 95 if all you keep is the
# final state). Cheap to record: no extra Ollama calls, just reading
# already-computed WorldEngine/AgentState fields once per tick.
_TRIAL_TICK_INT_COLUMNS = (
    "tick", "colony_metal", "colony_food", "colony_energy", "colony_biomatter",
    "alive_count", "avg_health", "structures_completed", "aliens_remaining",
    "cumulative_fallbacks",
)


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    target = Path(path or DEFAULT_DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trials (
            trial_id TEXT PRIMARY KEY,
            scenario_key TEXT NOT NULL,
            scenario_version INTEGER NOT NULL,
            trial_seed INTEGER NOT NULL,
            tick_budget INTEGER NOT NULL,
            ticks_run INTEGER NOT NULL,
            ended_reason TEXT NOT NULL,
            memory_enabled INTEGER NOT NULL,
            cognition_mode TEXT NOT NULL,
            git_commit TEXT,
            started_ts REAL NOT NULL,
            finished_ts REAL NOT NULL
        )
        """
    )
    text_cols = ", ".join(f"{c} TEXT" for c in _TRIAL_AGENT_TEXT_COLUMNS)
    real_cols = ", ".join(f"{c} REAL" for c in _TRIAL_AGENT_REAL_COLUMNS)
    int_cols = ", ".join(f"{c} INTEGER" for c in _TRIAL_AGENT_INT_COLUMNS)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS trial_agents (
            trial_id TEXT NOT NULL,
            {text_cols},
            {real_cols},
            {int_cols},
            PRIMARY KEY (trial_id, agent_id),
            FOREIGN KEY (trial_id) REFERENCES trials(trial_id)
        )
        """
    )
    tick_int_cols = ", ".join(f"{c} INTEGER" for c in _TRIAL_TICK_INT_COLUMNS)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS trial_ticks (
            trial_id TEXT NOT NULL,
            {tick_int_cols},
            PRIMARY KEY (trial_id, tick),
            FOREIGN KEY (trial_id) REFERENCES trials(trial_id)
        )
        """
    )
    return conn


def current_git_commit() -> Optional[str]:
    """Best-effort -- a missing/unavailable git binary shouldn't break a real
    benchmark trial over a nice-to-have provenance stamp."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def extract_agent_facts(agent) -> dict:
    """The raw, per-agent facts a completed trial records -- kept separate
    from the run loop itself so it's directly unit-testable against a
    hand-built AgentState. `agent` is an app.schemas.agent.AgentState."""
    stats = agent.stats
    return {
        "agent_id": agent.profile.agent_id,
        "model": agent.profile.model,
        "temperature": agent.profile.temperature,
        "is_captain": int(agent.profile.is_captain),
        "survived": int(agent.health > 0),
        "final_health": agent.health,
        "final_loyalty": agent.loyalty,
        "min_health_reached": stats.min_health_reached,
        "damage_taken_total": stats.damage_taken_total,
        "resources_gathered_total": stats.resources_gathered_total,
        "resources_contributed_total": stats.resources_contributed_total,
        "aliens_killed": stats.aliens_killed,
        "sectors_explored": stats.sectors_explored,
        "cognition_fallbacks": stats.cognition_fallbacks,
        "orders_issued": stats.orders_issued,
        "orders_complied": stats.orders_complied,
        "orders_ignored": stats.orders_ignored,
        "risky_orders_complied": stats.risky_orders_complied,
        "risky_orders_backfired": stats.risky_orders_backfired,
        "idle_count": stats.actions_by_type.get("idle", 0),
        "actions_by_type_json": json.dumps(stats.actions_by_type),
    }


def extract_tick_snapshot(engine, cumulative_fallbacks: int) -> dict:
    """One tick's worth of colony-wide state -- called once per tick from
    run_benchmark.py's run_trial loop, right after engine.tick(). Duck-typed
    against WorldEngine/AgentState rather than importing them, same as
    extract_agent_facts, to keep this module decoupled from the engine's
    schema types. `cumulative_fallbacks` is passed in rather than recomputed
    here since it's a running total across all agents the caller already has
    to track for other reasons."""
    agents = list(engine.agents.values())
    alive = [a for a in agents if a.health > 0]
    resources = engine.world.colony_resources
    return {
        "tick": engine.world.tick,
        "colony_metal": resources.metal,
        "colony_food": resources.food,
        "colony_energy": resources.energy,
        "colony_biomatter": resources.biomatter,
        "alive_count": len(alive),
        "avg_health": round(sum(a.health for a in agents) / len(agents)) if agents else 0,
        "structures_completed": sum(
            1 for s in engine.world.structures.values() if s.build_progress >= 100
        ),
        "aliens_remaining": len(engine.world.aliens),
        "cumulative_fallbacks": cumulative_fallbacks,
    }


def record_tick_snapshots(trial_id: str, snapshots: List[dict], path: Optional[str] = None) -> None:
    conn = _connect(path)
    with conn:
        placeholders = ", ".join(f":{c}" for c in _TRIAL_TICK_INT_COLUMNS)
        for snapshot in snapshots:
            row = {"trial_id": trial_id, **snapshot}
            conn.execute(
                f"INSERT OR REPLACE INTO trial_ticks "
                f"(trial_id, {', '.join(_TRIAL_TICK_INT_COLUMNS)}) VALUES (:trial_id, {placeholders})",
                row,
            )
    conn.close()


def read_trial_ticks(trial_id: str, path: Optional[str] = None) -> List[dict]:
    conn = _connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trial_ticks WHERE trial_id = ? ORDER BY tick", (trial_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_trial(trial_facts: dict, agent_facts: List[dict], path: Optional[str] = None) -> None:
    """`trial_facts` needs: trial_id, scenario_key, scenario_version,
    trial_seed, tick_budget, ticks_run, ended_reason, memory_enabled,
    cognition_mode, git_commit, started_ts, finished_ts. `agent_facts` is a
    list of dicts shaped like extract_agent_facts's return value, one per
    colonist."""
    conn = _connect(path)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO trials
            (trial_id, scenario_key, scenario_version, trial_seed, tick_budget,
             ticks_run, ended_reason, memory_enabled, cognition_mode, git_commit, started_ts, finished_ts)
            VALUES (:trial_id, :scenario_key, :scenario_version, :trial_seed, :tick_budget,
                    :ticks_run, :ended_reason, :memory_enabled, :cognition_mode, :git_commit, :started_ts, :finished_ts)
            """,
            trial_facts,
        )
        all_columns = _TRIAL_AGENT_TEXT_COLUMNS + _TRIAL_AGENT_REAL_COLUMNS + _TRIAL_AGENT_INT_COLUMNS
        placeholders = ", ".join(f":{c}" for c in all_columns)
        for facts in agent_facts:
            row = {"trial_id": trial_facts["trial_id"], **facts}
            conn.execute(
                f"INSERT OR REPLACE INTO trial_agents "
                f"(trial_id, {', '.join(all_columns)}) VALUES (:trial_id, {placeholders})",
                row,
            )
    conn.close()


def list_trials(
    scenario_key: Optional[str] = None, model: Optional[str] = None,
    cognition_mode: Optional[str] = None, path: Optional[str] = None,
) -> List[dict]:
    """Every trial matching the given filters, newest first, each with its
    agents' facts nested under "agents". cognition_mode is a real column
    filter (unlike model, which lives on trial_agents) -- mock and llm runs
    can otherwise end up in the same DB and be indistinguishable by model
    name alone, which is exactly what happened once building this harness."""
    conn = _connect(path)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM trials"
    clauses, params = [], []
    if scenario_key is not None:
        clauses.append("scenario_key = ?")
        params.append(scenario_key)
    if cognition_mode is not None:
        clauses.append("cognition_mode = ?")
        params.append(cognition_mode)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY started_ts DESC"
    trial_rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    results = []
    for trial in trial_rows:
        agent_query = "SELECT * FROM trial_agents WHERE trial_id = ? ORDER BY agent_id"
        agents = [dict(r) for r in conn.execute(agent_query, (trial["trial_id"],)).fetchall()]
        if model is not None and not any(a["model"] == model for a in agents):
            continue
        trial["agents"] = agents
        results.append(trial)
    conn.close()
    return results


def read_trial(trial_id: str, path: Optional[str] = None) -> Optional[dict]:
    conn = _connect(path)
    conn.row_factory = sqlite3.Row
    trial_row = conn.execute("SELECT * FROM trials WHERE trial_id = ?", (trial_id,)).fetchone()
    if trial_row is None:
        conn.close()
        return None
    trial = dict(trial_row)
    agents = conn.execute(
        "SELECT * FROM trial_agents WHERE trial_id = ? ORDER BY agent_id", (trial_id,)
    ).fetchall()
    conn.close()
    trial["agents"] = [dict(r) for r in agents]
    return trial


def new_trial_id(scenario_key: str, trial_seed: int) -> str:
    return f"{scenario_key}_{trial_seed}_{int(time.time())}"


def export_report_data(
    scenario_key: Optional[str] = None, model: Optional[str] = None,
    cognition_mode: Optional[str] = None, path: Optional[str] = None,
) -> dict:
    """Everything a report (chart or otherwise) needs in one call: every
    matching trial's facts, its per-tick snapshots, and its agents' facts,
    nested together. Kept separate from list_trials (which stays flat/fast
    for the plain-text leaderboard) since pulling every trial's full tick
    history is a heavier query only worth paying for when building an
    actual report."""
    trials = list_trials(scenario_key=scenario_key, model=model, cognition_mode=cognition_mode, path=path)
    for trial in trials:
        trial["ticks"] = read_trial_ticks(trial["trial_id"], path=path)
    return {"trials": trials}
