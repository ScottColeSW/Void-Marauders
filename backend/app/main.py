from dotenv import load_dotenv

load_dotenv()

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core import memory
from app.core.engine import get_engine

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
