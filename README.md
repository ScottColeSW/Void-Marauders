# 🌌 Void Marauders: AI Agent Colony Sim

[![License](https://img.shields.io/github/license/ScottColeSW/Void-Marauders)](LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/ScottColeSW/Void-Marauders)](https://github.com/ScottColeSW/Void-Marauders/releases/latest)

An observer-style, autonomous colony simulation. A crew of independent AI agents lands on a hostile world, explores for resources, fights off aliens, and builds up a colony — while you watch it unfold. No cloud API costs: everything runs on local models.

---

## 🛠 Project Architecture

```text
    ┌────────────────────────┐
    │   Web Dashboard (now)  │ <--- Polls the backend every 2s
    │   Godot 4 (planned)     │
    └───────────┬────────────┘
                │ HTTP / JSON
                ▼
    ┌────────────────────────┐
    │    FastAPI Backend     │ <--- Tick loop + agent cognition
    └───────────┬────────────┘
                │
        ┌───────┴───────┐
        ▼               ▼
   ┌─────────┐    ┌─────────────────┐
   │ Ollama  │    │   Palimpsest    │
   │ (LLM)   │    │ (per-colonist   │
   │         │    │  memory mesh)   │
   └─────────┘    └─────────────────┘
```

- **Backend (`/backend`):** FastAPI app owning the world state and the tick loop. Each tick, aliens act on simple rules, and every colonist agent perceives its surroundings and decides an action (explore, gather, build, fight, retreat, confront crew, rest, ...).
- **Frontend (now):** a zero-build web dashboard served directly by the backend at `http://127.0.0.1:8000/` — a visual sector map (colonists and aliens shown where they actually are, unexplored/threatened sectors visually distinct), colonist status, live event feed.
- **Frontend (planned):** Godot 4 (`/frontend`) — not built yet, tracked as Phase 4 below.
- **Agent Cognition:** local LLM inference via **Ollama**, or a dependency-free mock brain for fast iteration.
- **Persistent Memory:** [**Palimpsest**](https://github.com/ScottColeSW/Palimpsest) gives each colonist their own curated, weighted memory mesh — not just "similar text retrieved again." A colonist's read on a crewmate or a sector's safety can **reinforce** with repeated evidence, or **collide** and sit as an open, unresolved tension when new evidence contradicts it (this is what gives Karl's paranoia about Valerie actual teeth — a repeatedly-reinforced trust read that a single reckless act can genuinely collide with). Weight decays slowly over time if unreinforced, so old grudges soften rather than staying permanent. No Docker/vector-DB required — it's an in-process mesh per colonist, persisted to `backend/app/data/memory/<agent_id>.json` so memory survives a backend restart even though world state doesn't.

---

## 🚀 Quick Start

### Fastest path — no Ollama or Docker required

This runs the full sim with a rule-based "mock" brain instead of an LLM, and memory turned off. Good for a first look, or for fast iteration on game balance.

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
cp .env.example .env
```

`.env.example`'s defaults (`COGNITION_MODE=mock`, `MEMORY_ENABLED=false`) are already this fastest path — no edits needed.

Run it:
```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/** — that's the live dashboard. It polls the backend every 2 seconds; you'll see colonists gather resources, build structures, and fight off aliens in real time.

### Full experience — real LLM agents with persistent memory

**1. Prerequisites**
- [Ollama](https://ollama.com), running locally
- [Palimpsest](https://github.com/ScottColeSW/Palimpsest), cloned as a sibling of this repo and installed into `backend`'s venv:
  ```bash
  git clone https://github.com/ScottColeSW/Palimpsest.git ../../Palimpsest
  cd backend
  venv\Scripts\activate
  pip install -e ../../Palimpsest
  ```

**2. Pull the models you need**

By default, colonists share **two** small models rather than five distinct ones — deliberately.
Five different models resident at once is 10+ GB, more than an 8 GB-class consumer GPU (e.g. an
RTX 2080 Super) can hold; Ollama then evicts and reloads a model from disk on nearly every
agent's turn, every tick, which is far slower than the "personality variety" was worth. Two
small models (~3.5 GB combined) stay resident together comfortably, and personality still comes
through via temperature and the system prompt (see `backend/app/core/world_seed.py`):

| Colonist | Model | Temp | Why |
|---|---|---|---|
| Karl (paranoid engineer) | `qwen2.5:3b` | 0.5 | the sharper of the two — suits a deliberate, suspicious engineer |
| Valerie (impulsive pilot, captain) | `qwen2.5:3b` | 0.9 | sharper model, high temperature — reckless but still issues sane orders |
| Amara (calm medic) | `gemma2:2b` | 0.3 | lighter model, low temperature — steady, mostly support actions |
| Otieno (security officer) | `qwen2.5:3b` | 0.6 | sharper model — he's in combat most often, reasoning quality matters |
| Priya (curious botanist) | `gemma2:2b` | 0.8 | lighter model, high temperature — flavor over precision |

Pull both, plus the embedding model:
```bash
ollama pull qwen2.5:3b
ollama pull gemma2:2b
ollama pull nomic-embed-text   # required if MEMORY_ENABLED=true
```

**Got more VRAM to spare (12+ GB)?** Feel free to spread colonists back out across more distinct
models in `world_seed.py` for more voice variety — nothing else needs to change. Check what fits
first: `nvidia-smi --query-gpu=memory.total,memory.free --format=csv` (or Task Manager's
Performance tab on Windows) shows your GPU's VRAM, and `ollama list` shows each model's size — add
up the models you want resident at once and leave a couple GB of headroom for context/KV cache.

**3. Configure and run the backend**

In `backend/.env` (see `.env.example` for the full list), flip the two fast-path defaults:
```
COGNITION_MODE=llm
MEMORY_ENABLED=true
```

```bash
cd backend
venv\Scripts\activate
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/** as before. Agent monologue/dialogue is now real LLM output, and agents recall relevant past events — plus their standing reads on crewmates and sectors, including any unresolved contradictions — each tick.

> **Performance note:** with memory enabled, every agent triggers one embedding call per tick (used only to rank recent personal-log recall by relevance) on top of its cognition call — ticks take noticeably longer than in mock mode. Running five *different* models also means Ollama may not keep them all resident in memory at once on a RAM-constrained machine, which can make each tick far slower still as models reload. If it feels sluggish, either raise `TICK_INTERVAL_SECONDS` in `.env`, point more agents at the same model in `world_seed.py`, or set `MEMORY_ENABLED=false` while iterating and turn it back on for the real demo.

### Useful endpoints while it's running
- `GET /state` — full world state (resources, sectors, aliens, structures) as JSON
- `GET /agents` — colonist status (health, stress, location)
- `GET /events?limit=50` — recent event/dialogue feed
- `POST /tick` — manually advance one tick (handy for testing without waiting)

---

## 🧠 Core Agentic Gameplay Loop

Each tick:
1. **Environment step:** aliens attack any colonist sharing their sector; structures under construction progress; food and energy upkeep are charged (below). Rule-based, no LLM cost.
2. **Perceive:** each living colonist gets a snapshot of their sector, nearby crew/aliens, colony resources, and (if memory is on) their standing reads on nearby crew and the current sector, any unresolved contradictions, and their most relevant recent history.
3. **Decide:** the agent's cognition (mock heuristic or local LLM) returns a strict JSON action — inner monologue, spoken dialogue, one of a fixed set of action types, and a target.
4. **Resolve:** the engine applies the action to world state and logs it to the event feed. If memory is on, the outcome is checked against that colonist's mesh — reinforcing a standing belief, opening a new contradiction, or just adding to their personal log.

**Movement is real, not a teleport.** Sectors connect through `colony_core` in a hub-and-spoke graph (`app/core/world_seed.py`'s `SECTOR_ADJACENCY`, matching the dashboard's own map layout exactly) — from `colony_core` a colonist reaches anywhere in one move, but two outlying sectors are two moves apart, through the hub. A colonist heading somewhere unreachable in one hop moves toward it instead of teleporting, and continues the trip if they pick the same target again next turn.

**Food and energy are real costs, not decoration.** Food drains every tick regardless of colonist count; if it can't be covered, the whole living crew takes starvation damage until someone gathers again. Energy costs 1/tick per *completed* structure to stay powered; left unpaid, structures decay and can be destroyed outright, undoing both the build progress and the win condition's structure count. Metal (spent building) and biomatter are the only resources without an ongoing drain.

**Loyalty is an incentive, not a scoreboard.** The captain (Valerie) can `issue_order` to nearby crew; whether they comply depends on loyalty, and compliance used to move the same flat +1/-1 regardless of what the order actually cost anyone. Now it's outcome-sensitive: complying with an order that sends a colonist somewhere aliens already are, and then getting hurt, costs real trust — more than simple defiance would have. The same risky order paying off (they comply and come out fine) earns more trust than a routine one. Refusing an order that's visibly dangerous costs little to nothing — rational self-preservation, not insubordination. A captain who keeps giving orders that get people hurt erodes their own authority over time, in a way you can watch happen and that `AgentStats.risky_orders_complied`/`risky_orders_backfired` make directly measurable via the benchmark harness.

The colony has a real end state: it's lost if every colonist reaches 0 HP (starvation included), and won once every alien threat is cleared and the colony has completed at least three structures. Either way the dashboard shows a clear banner and the tick loop stops.

---

## 📊 Benchmarking models headlessly

`backend/run_benchmark.py` drives the same `WorldEngine` the live server uses, with no
FastAPI/dashboard involved, for reproducible model-vs-model comparison — mirrors the harness
shape from this project's sibling, [Evo-LLM-Evolution2Civ](https://github.com/ScottColeSW/Evo):

```bash
cd backend
python run_benchmark.py --scenario baseline --trials 3
python run_benchmark.py --scenario swarm_pressure --models qwen2.5:3b,gemma2:2b
python run_benchmark.py --scenario swarm_pressure --memory on --trials 3   # does memory help? -- see below
python run_benchmark.py --report
python run_benchmark.py --report --scenario swarm_pressure
```

With the server running, open **http://127.0.0.1:8000/benchmark/report** (or click **BENCHMARK REPORT** in
the dashboard header) for a chart report instead of the text leaderboard — it queries the same DB fresh on
every load, so re-running a trial and reloading the page shows the new result with no restart needed.

Every colonist's raw counters (idle ticks, cognition fallbacks, resources gathered, aliens
killed, sectors explored, orders issued/complied/ignored, min health reached, ...) are tracked
live on `AgentState.stats` during normal play, not just benchmark runs, and get written as
**raw facts — never a computed score** — into a long-running `backend/logs/benchmark_results.db`
(same reasoning as Evo's own harness: a stored score silently goes stale the moment the scoring
formula changes). `--report` computes a 0-100 score per trial at read time instead
(`app/core/benchmark_scoring.py`, versioned via `SCORING_VERSION`).

`--models` is optional — omit it to benchmark the real default two-model roster as shipped, or
pass one/several (cycled across the five colonists) for a controlled single-model comparison.
Scenarios live in `app/core/benchmark_scenarios.py` (`baseline`, `swarm_pressure`,
`resource_scarcity` today); the harness forces `COGNITION_MODE=llm` regardless of `.env` — real
models are the whole point. `--memory {on,off}` (default `off`) controls Palimpsest memory
during the trial — safe to turn on: `WorldEngine` takes a `memory_namespace` that prefixes every
colonist's memory-store id with that trial's own unique id, so a trial's memories can never
collide with live dashboard play or another trial. That's also the actual way to answer "does
memory help": run the same scenario with `--memory on` and `--memory off` and compare — the
chart report renders this scoped per-scenario, since different scenario categories use
incompatible scoring formulas and shouldn't be pooled together.

---

## 🗺 Development Roadmap

- [x] **Phase 1:** Core FastAPI tick loop with mock cognition and a full colony/combat/exploration action model.
- [x] **Phase 2:** Ollama integration with strict Pydantic-validated JSON output (schema-constrained, with a safe fallback if a local model hallucinates).
- [x] **Phase 3:** Persistent episodic memory — agents recall past events, standing reads on crew/sectors, and unresolved contradictions via a [Palimpsest](https://github.com/ScottColeSW/Palimpsest) mesh per colonist.
- [x] **Interim frontend:** zero-build web dashboard for observing the colony live.
- [x] **Win/loss conditions:** colony wipeout ends the run; clearing all threats and completing 3 structures wins it.
- [x] **Real space and real economy:** sector adjacency (hub-and-spoke through colony_core, matching the dashboard's map) replaces free teleportation; food and energy have real upkeep costs and consequences instead of climbing forever untouched.
- [x] **Headless benchmarking:** `run_benchmark.py` drives the engine directly for reproducible model comparison, raw facts recorded (never a stored score), with a chart report served live from the running dashboard.
- [ ] **Phase 4:** Godot UI integration — tile-based ship/colony rendering, scrolling speech bubbles, pathfinding.
