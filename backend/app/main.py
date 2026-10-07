from dotenv import load_dotenv

load_dotenv()

import asyncio
import os
import signal
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core import benchmark_db, cognition, memory
from app.core.engine import get_engine, reset_engine
from app.schemas.world import ColonyStatus

# generate_report.py lives at the backend/ root (sibling of app/), not inside
# the app package -- importable here because uvicorn is always run from
# backend/ (see README), which Python puts on sys.path same as it does for
# `app` itself.
import generate_report

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Pause between the END of one tick and the start of the next. A tick is
# already seconds long in LLM mode (and the loop waits for it), so this is only
# a breather -- a large value here is pure dead time on top of every tick.
TICK_INTERVAL_SECONDS = float(os.getenv("TICK_INTERVAL_SECONDS", "1"))

_tick_task: Optional[asyncio.Task] = None

# The colony is built and waiting when the server boots, but nothing happens until someone presses
# Start (POST /start). Quit (POST /quit) is a clean shutdown: see _request_quit and _cleanup.
_started = False
_stopping = False


async def _tick_loop() -> None:
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
        if not _started or _stopping:
            continue
        # tick() does blocking network I/O (Ollama calls) — run it off the event
        # loop so it doesn't freeze every other request for the tick's duration.
        # Looked up every time, not held: a reset swaps in a brand-new engine.
        await loop.run_in_executor(None, lambda: get_engine().tick())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tick_task
    memory.warm_up()  # load the NLI judge in the background if MEMORY_JUDGE=nli, so the first tick doesn't wait for it
    engine = get_engine()  # seed the colony immediately so /state has data before the first tick
    # Load every model the colony uses into memory now, in the background, so
    # the first ticks don't each pay a 10-20 s cold load.
    cognition.warm_up(sorted({a.profile.model for a in engine.agents.values()}))
    memory.warm_up_embeddings()
    _tick_task = asyncio.create_task(_tick_loop())
    yield
    if _tick_task:
        _tick_task.cancel()
    _cleanup()


def _cleanup() -> None:
    """Everything the game leaves running or unsaved, put away. Runs on every way out of the server
    (the Quit button, Ctrl+C in the terminal, a kill signal), because uvicorn runs it on shutdown."""
    engine = get_engine()
    with engine._lock:  # a tick still in flight finishes first: never save or unload mid-tick
        memory.save_all()
    cognition.unload_models(sorted({a.profile.model for a in engine.agents.values()}))
    memory.unload_embeddings()


def _request_quit() -> None:
    """Runs off the request thread: let the tick in flight finish, then ask uvicorn to exit the way
    Ctrl+C does, which runs the shutdown above. The response has already gone back to the page."""
    with get_engine()._lock:
        pass
    signal.raise_signal(signal.SIGINT)


app = FastAPI(title="Void Marauders", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    # This dashboard is actively edited during development — a browser silently
    # serving stale cached JS/CSS after an edit is more confusing than a demo
    # server always doing one extra round-trip.
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/summary"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def get_dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/summary")
def get_summary_page():
    return FileResponse(os.path.join(STATIC_DIR, "summary.html"))


@app.get("/state")
def get_state():
    return get_engine().world


@app.get("/agents")
def get_agents():
    return list(get_engine().agents.values())


@app.get("/events")
def get_events(limit: int = 20):
    return get_engine().world.event_log[-limit:]


@app.get("/control")
def get_control():
    """Where the game is, for the Start/Quit buttons: ready (built, waiting for Start), running, over
    (won or lost), or stopping (Quit pressed, shutting down)."""
    if _stopping:
        phase = "stopping"
    elif get_engine().world.status != ColonyStatus.ACTIVE:
        phase = "over"
    elif _started:
        phase = "running"
    else:
        phase = "ready"
    return {"phase": phase}


@app.post("/start")
def start_game():
    global _started
    if get_engine().world.status != ColonyStatus.ACTIVE:
        reset_engine()  # Start after a finished game means a fresh one
    _started = True
    return {"phase": "running"}


@app.post("/quit")
def quit_game():
    global _stopping
    if not _stopping:
        _stopping = True
        threading.Thread(target=_request_quit, name="quit", daemon=True).start()
    return {"phase": "stopping"}


@app.post("/reset")
def reset_game():
    reset_engine()
    return {"status": "reset"}


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
