"""Checks the opt-in judged memory (MEMORY_JUDGE=nli) against the bug it exists for. No server, no Ollama, no GPU.

    python check_memory_judge.py

Scenario: a colonist twice complies with the captain's order, then ignores it.
  - judge off: the token-overlap rules call "ignored" a restatement of "complied" (no dispute opens). This is the old behavior.
  - judge on : "ignored" contradicts "complied", so an open collision is recorded with its reason.
  - judge on but unavailable: the game falls back to the rules and does not crash.
Needs the NLI extra for the second check: pip install -e "../../Palimpsest[nli]"
"""

import os
import subprocess
import sys
import tempfile

SCENARIO = r'''
import os, sys, json
from app.core import memory
from palimpsest.models import EdgeStatus, EdgeType

if os.environ.get("BREAK_NLI") == "1":          # simulate "torch/transformers missing" or a corrupt model
    import palimpsest.nli as nli
    class Broken:
        def __init__(self, *a, **k): raise ImportError("simulated: NLI extra not installed")
    nli.NLI = Broken

for complied in (True, True, False):
    memory.record_order_outcome("karl", "vance", "Captain Vance", complied)
store = memory._store_for("karl")
collisions = [e for e in store.all_edges() if e.type == EdgeType.COLLIDES and e.status == EdgeStatus.OPEN]
reinforces = [e for e in store.all_edges() if e.type == EdgeType.REINFORCES]
print(json.dumps({"open_collisions": len(collisions), "reinforcements": len(reinforces), "stats": memory.JUDGE_STATS,
                  "reason": collisions[0].tolerance_context if collisions else ""}))
'''


def run(label, env_extra):
    env = {**os.environ, "MEMORY_ENABLED": "true", "MEMORY_DIR": tempfile.mkdtemp(), "PYTHONIOENCODING": "utf-8",
           "HF_HUB_DISABLE_PROGRESS_BARS": "1", "PYTHONWARNINGS": "ignore", **env_extra}
    out = subprocess.run([sys.executable, "-c", SCENARIO], capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.abspath(__file__)))
    line = next((l for l in out.stdout.splitlines() if l.startswith("{")), None)
    if out.returncode != 0 or line is None:
        print(f"[FAIL] {label}: the scenario crashed\n{out.stderr[-600:]}")
        return None
    import json
    return json.loads(line)


failures = 0
off = run("judge off", {"MEMORY_JUDGE": "off"})
on = run("judge on", {"MEMORY_JUDGE": "nli"})
broken = run("judge on, NLI unavailable", {"MEMORY_JUDGE": "nli", "BREAK_NLI": "1"})

def check(label, ok, detail=""):
    global failures
    failures += not ok
    print(f"[{'ok' if ok else 'FAIL'}] {label} {detail}")

if off:
    check("judge off: 'ignored' reads as a restatement (the old behavior)", off["open_collisions"] == 0, f"({off['open_collisions']} collisions, {off['reinforcements']} reinforcements)")
if on:
    check("judge on: 'ignored' contradicts 'complied' and opens a collision", on["open_collisions"] == 1, f"({on['open_collisions']} collisions)")
    check("judge on: the collision carries a reason", "contradiction" in on["reason"], f"({on['reason'][:70]!r})")
    check("judge on: observations were judged, none fell back", on["stats"]["judged"] == 3 and on["stats"]["fallback"] == 0, f"({on['stats']['judged']} judged, {on['stats']['seconds']:.2f}s total)")
if broken:
    check("judge on but NLI unavailable: falls back to the rules without crashing", broken["open_collisions"] == off["open_collisions"] if off else True,
          f"({broken['open_collisions']} collisions, same as judge off)")
if not (off and on and broken):
    failures += 1
print("\nALL CHECKS PASSED" if failures == 0 else f"\n{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)
