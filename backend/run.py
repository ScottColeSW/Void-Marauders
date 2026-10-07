"""Start the Void Marauders backend: python run.py

Serves the dashboard at http://127.0.0.1:8000/. Defaults (LLM cognition,
memory on) come from backend/.env, or the in-code defaults if it's absent.
Pass --mock to run the rule-based brain with memory off (no Ollama needed).

The colony waits for the Start button. Quit (the button, or Ctrl+C here) saves colonist
memory, unloads the models from Ollama and exits.
"""
import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mock", action="store_true", help="mock brain, memory off (no Ollama)")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (development only)")
    args = parser.parse_args()

    if args.mock:
        os.environ["COGNITION_MODE"] = "mock"
        os.environ["MEMORY_ENABLED"] = "false"

    import uvicorn

    # One process by default. Auto-reload spawns a supervisor plus a worker, and the Quit button
    # can only end the worker, leaving the supervisor behind in the terminal.
    print(f"Void Marauders: open http://{args.host}:{args.port}/ and press Start. Quit (or Ctrl+C here) cleans up.", flush=True)
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


# The guard matters: with reload on, uvicorn re-imports this file in a child
# process on Windows, and an unguarded uvicorn.run() would recurse.
if __name__ == "__main__":
    main()
