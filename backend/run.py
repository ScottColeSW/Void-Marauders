"""Start the Void Marauders backend: python run.py

Serves the dashboard at http://127.0.0.1:8000/. Defaults (LLM cognition,
memory on) come from backend/.env, or the in-code defaults if it's absent.
Pass --mock to run the rule-based brain with memory off (no Ollama needed).
"""
import argparse
import os

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--mock", action="store_true", help="mock brain, memory off (no Ollama)")
parser.add_argument("--no-reload", action="store_true", help="disable auto-reload on code changes")
args = parser.parse_args()

if args.mock:
    os.environ["COGNITION_MODE"] = "mock"
    os.environ["MEMORY_ENABLED"] = "false"

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host=args.host, port=args.port, reload=not args.no_reload)
