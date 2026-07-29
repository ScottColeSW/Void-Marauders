# 🌌 Void Marauders: AI Agent Colony Sim

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
   ┌─────────┐    ┌───────────┐
   │ Ollama  │    │ Qdrant DB │
   │ (LLM)   │    │ (Memory)  │
   └─────────┘    └───────────┘
```

- **Backend (`/backend`):** FastAPI app owning the world state and the tick loop. Each tick, aliens act on simple rules, and every colonist agent perceives its surroundings and decides an action (explore, gather, build, fight, retreat, confront crew, rest, ...).
- **Frontend (now):** a zero-build web dashboard served directly by the backend at `http://127.0.0.1:8000/` — sector grid, colonist status, live event feed.
- **Frontend (planned):** Godot 4 (`/frontend`) — not built yet, tracked as Phase 4 below.
- **Agent Cognition:** local LLM inference via **Ollama**, or a dependency-free mock brain for fast iteration.
- **Persistent Memory:** **Qdrant** (via Docker) stores each agent's past inner monologue/actions, retrieved by semantic similarity each tick so agents can recall prior events.

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
```

Edit `backend/.env` and set:
```
COGNITION_MODE=mock
MEMORY_ENABLED=false
```

Run it:
```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/** — that's the live dashboard. It polls the backend every 2 seconds; you'll see colonists gather resources, build structures, and fight off aliens in real time.

### Full experience — real LLM agents with persistent memory

**1. Prerequisites**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (for Qdrant)
- [Ollama](https://ollama.com), running locally

**2. Pull the models you need**

Each colonist runs on a *different* local model and temperature — partly for variety of
"voice", partly a deliberate personality match (see `backend/app/core/world_seed.py`):

| Colonist | Model | Temp | Why |
|---|---|---|---|
| Karl (paranoid engineer) | `qwen2.5:7b` | 0.5 | sharpest reasoning, low variance — suits a deliberate, suspicious engineer |
| Valerie (impulsive pilot) | `llama3.2:latest` | 0.9 | fast, high variance — suits reckless decisions |
| Amara (calm medic) | `phi4-mini:latest` | 0.3 | steady, low variance — suits a dutiful, consistent caretaker |
| Otieno (security officer) | `qwen2.5:3b` | 0.6 | good enough tactical reasoning, fast — he's in combat most often |
| Priya (curious botanist) | `gemma2:2b` | 0.8 | fastest, most whimsical/creative flavor text |

Pull all of them, plus the embedding model:
```bash
ollama pull qwen2.5:7b
ollama pull qwen2.5:3b
ollama pull llama3.2
ollama pull phi4-mini
ollama pull gemma2:2b
ollama pull nomic-embed-text   # required if MEMORY_ENABLED=true
```
Don't have all five? Any agent's `model` in `world_seed.py` can be pointed at whatever you've
already got — nothing else needs to change. `qwen2.5:3b` alone is a fine single fallback: on
everyday decisions (gather/build/explore) it's nearly as sharp as `qwen2.5:7b` at roughly half
the latency, and only occasionally fumbles a combat target under pressure — which fails safe,
since the engine just no-ops rather than crashing.

**3. Start Qdrant**
```bash
docker compose up -d
```
This starts Qdrant at `localhost:6333` with a persistent volume (`./qdrant_storage`, already gitignored).

> **Troubleshooting:** if Qdrant logs a `gridstore`/`OutputTooSmall` panic (a storage bug we hit
> once during development, likely from an unclean shutdown), it's corrupted local data, not your
> setup. Non-fatal — the sim keeps running without memory — but to clear it: `docker compose down`,
> delete `qdrant_storage/`, then `docker compose up -d` again.

**4. Configure and run the backend**

In `backend/.env`:
```
COGNITION_MODE=llm
MEMORY_ENABLED=true
EMBED_MODEL=nomic-embed-text
TICK_INTERVAL_SECONDS=20
```

```bash
cd backend
venv\Scripts\activate
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/** as before. Agent monologue/dialogue is now real LLM output, and agents recall relevant past events each tick.

> **Performance note:** with memory enabled, every agent triggers a couple of embedding calls per tick on top of its cognition call — ticks take noticeably longer than in mock mode. If it feels sluggish on your hardware, either raise `TICK_INTERVAL_SECONDS` in `.env`, or set `MEMORY_ENABLED=false` while iterating and turn it back on for the real demo.

### Useful endpoints while it's running
- `GET /state` — full world state (resources, sectors, aliens, structures) as JSON
- `GET /agents` — colonist status (health, stress, location)
- `GET /events?limit=50` — recent event/dialogue feed
- `POST /tick` — manually advance one tick (handy for testing without waiting)

---

## 🧠 Core Agentic Gameplay Loop

Each tick:
1. **Environment step:** aliens attack any colonist sharing their sector; structures under construction progress. Rule-based, no LLM cost.
2. **Perceive:** each living colonist gets a snapshot of their sector, nearby crew/aliens, colony resources, and (if memory is on) their most relevant past memories.
3. **Decide:** the agent's cognition (mock heuristic or local LLM) returns a strict JSON action — inner monologue, spoken dialogue, one of a fixed set of action types, and a target.
4. **Resolve:** the engine applies the action to world state and logs it to the event feed. If memory is on, the outcome is embedded and stored so the agent can recall it later.

---

## 🗺 Development Roadmap

- [x] **Phase 1:** Core FastAPI tick loop with mock cognition and a full colony/combat/exploration action model.
- [x] **Phase 2:** Ollama integration with strict Pydantic-validated JSON output (schema-constrained, with a safe fallback if a local model hallucinates).
- [x] **Phase 3:** Qdrant persistent episodic memory (the "Blackbox Registry") — agents recall past events via semantic search.
- [x] **Interim frontend:** zero-build web dashboard for observing the colony live.
- [ ] **Phase 4:** Godot UI integration — tile-based ship/colony rendering, scrolling speech bubbles, pathfinding.
