# 🌌 Void Marauders: AI Agent Space RPG Adventure

An observer-style, autonomous space simulation where a crew of independent AI agents manage a starship, face deep-space hazards, and dynamically build relationships or slip into mutiny. The player acts as the Ship's AI Overseer—managing the ship's infrastructure and injecting structural anomalies while watching the crew's autonomous actions unfold.

---

## 🛠 Project Architecture

The game uses a decoupled, local-first stack to eliminate cloud API costs:

```text
    ┌────────────────────────┐
    │     Godot 4 Engine     │ <--- Visual Viewport (Polls every tick)
    └───────────┬────────────┘
                │ HTTP / JSON
                ▼
    ┌────────────────────────┐
    │    FastAPI Backend     │ <--- Runs the Agent Cognition Loop
    └───────────┬────────────┘
                │
        ┌───────┴───────┐
        ▼               ▼
   ┌─────────┐    ┌───────────┐
   │ Ollama  │    │ Qdrant DB │
   │ (LLM)   │    │ (Memory)  │
   └─────────┘    └───────────┘
```

- **Frontend (`/frontend`):** Godot 4 (GDScript) for rendering the ship tiles, UI terminals, and crew movement vectors.
- **Backend (`/backend`):** FastAPI orchestration framework managing state machines, vector generation, and local language pipelines.
- **Agent Cognition:** Local LLM inference powered by **Ollama** (`Llama 3.1 8B` or `DeepSeek-R1-Distill-8B`).
- **Persistent Memory:** Local Vector DB powered by **Qdrant** for episodic semantic recall and relationship history.

---

## 🚀 Quick Start Guide

### Prerequisites
- [Python 3.11+](https://python.org)
- [Godot 4.x](https://godotengine.org)
- [Ollama](https://ollama.com)

### 1. Setup the Brain (Ollama)
Install Ollama and pull your local model of choice. For low-latency agent iterations, an 8B model is recommended:
```bash
# Pull your desired local reasoning model
ollama pull llama3.1

# Alternatively, for deep reasoning paths:
ollama pull deepseek-r1:8b
```

### 2. Configure the Backend Agent Mind
Open a terminal window and initialize your Python microservice environment:
```bash
cd backend

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows use: .\venv\Scripts\activate

# Install required local-AI orchestration dependencies
pip install fastapi uvicorn pydantic qdrant-client ollama requests

# Run the FastAPI server
uvicorn app.main:app --reload
```
The local server documentation will be accessible at `http://127.0.0`.

### 3. Initialize the Frontend Viewport
1. Open the **Godot Engine Project Manager**.
2. Click **Import**, navigate to your root folder, and select `void-marauders/frontend/project.godot`.
3. Press **Play (F5)** to boot up the tactical ship terminal grid.

---

## 🧠 Core Agentic Gameplay Loops

1. **Perceive:** The game engine flags environmental hazards (e.g., *Hull Leak in Engineering*).
2. **Reflect:** The agent queries the local Vector Database (`Qdrant`) for semantic weights matching their profile history (e.g., *Grudge against Valerie*).
3. **Act:** The model interprets the state and delivers a strict JSON payload mapping out internal monologue, structural audio strings, and hard game engine action keys (`repair_hull`, `panic_flee`).

---

## 🗺 Development Roadmap

- [ ] **Phase 1:** Core FastAPI loop returning mock JSON strings to the engine.
- [ ] **Phase 2:** Ollama system integration with rigid Pydantic functional validation schemas.
- [ ] **Phase 3:** Qdrant persistent episodic vector logging (The "Blackbox Registry").
- [ ] **Phase 4:** Godot UI integration, scrolling character speech bubbles, and ship layout pathfinding.
