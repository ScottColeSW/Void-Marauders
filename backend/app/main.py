from dotenv import load_dotenv

load_dotenv()

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core import benchmark_db, memory
from app.core.engine import get_engine

# generate_report.py lives at the backend/ root (sibling of app/), not inside
# the app package -- importable here because uvicorn is always run from
# backend/ (see README), which Python puts on sys.path same as it does for
# `app` itself.
import generate_report

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

TICK_INTERVAL_SECONDS = float(os.getenv("TICK_INTERVAL_SECONDS", "15"))

_tick_task: Optional[asyncio.Task] = None


async def _tick_loop() -> None:
    engine = get_engine()
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
        # tick() does blocking network I/O (Ollama calls) — run it off the event
        # loop so it doesn't freeze every other request for the tick's duration.
        await loop.run_in_executor(None, engine.tick)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tick_task
    get_engine()  # seed the colony immediately so /state has data before the first tick
    _tick_task = asyncio.create_task(_tick_loop())
    yield
    if _tick_task:
        _tick_task.cancel()
    memory.save_all()


app = FastAPI(title="Void Marauders", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    # This dashboard is actively edited during development — a browser silently
    # serving stale cached JS/CSS after an edit is more confusing than a demo
    # server always doing one extra round-trip.
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def get_dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/state")
def get_state():
    return get_engine().world


@app.get("/agents")
def get_agents():
    return list(get_engine().agents.values())


@app.get("/events")
def get_events(limit: int = 20):
    return get_engine().world.event_log[-limit:]


@app.post("/tick")
def advance_tick():
    return get_engine().tick()


@app.get("/benchmark/report", response_class=HTMLResponse)
def get_benchmark_report(scenario: Optional[str] = None, model: Optional[str] = None):
    # Queries backend/logs/benchmark_results.db fresh on every request --
    # re-run backend/run_benchmark.py and reload this page to see new
    # trials, no server restart needed.
    data = benchmark_db.export_report_data(scenario_key=scenario, model=model)
    if not data["trials"]:
        return HTMLResponse(
            "<body style='background:#0a0e0c;color:#c8e6d8;font-family:monospace;padding:2rem'>"
            "<h1>No benchmark trials recorded yet</h1>"
            "<p>Run one from backend/: <code>python run_benchmark.py --scenario swarm_pressure --trials 1</code></p>"
            "</body>"
        )
    return HTMLResponse(generate_report.build_report(data))
