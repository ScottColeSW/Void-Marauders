# Does Palimpsest memory help the colony? A paired test on swarm_pressure

Twelve paired trials of the `swarm_pressure` scenario (the one that forces combat), same seed per pair, memory off against
memory on (with `MEMORY_JUDGE=nli`), order alternated, default roster (qwen2.5:3b and gemma2:2b), scenario version 3.
Score is the mean of the five colonists' benchmark scores per trial. Run on 2026-10-02.

| seed | memory off | memory on |
|---|---|---|
| 5000 | 32.6 | 52.6 |
| 5001 | 40.1 | 41.0 |
| 5002 | 40.7 | 41.3 |
| 5003 | 43.6 | 41.1 |
| 5004 | 39.6 | 34.0 |
| 5005 | 44.1 | 40.7 |
| 5006 | 42.8 | 34.0 |
| 5007 | 38.9 | 18.8 |
| 5008 | 34.1 | 39.4 |
| 5009 | 45.5 | 30.0 |
| 5010 | 38.6 | 30.0 |
| 5011 | 39.5 | 30.0 |
| **mean** | **40.0** | **36.1** |

Mean difference (on minus off): -3.9 points, bootstrap 95% range -9.2 to +1.9. Memory scored higher in 4 pairs and lower in 8.

**Reading it.** There is no evidence here that memory helps this scenario, and a hint that it may cost a little (the range
includes zero, so this is not established either way). Twelve pairs, one scenario, two small models, and LLM sampling that
the seed does not control: the test can only detect a large effect. It does not say memory is useless in general; it says the
current wiring (colonists recall personal logs, crew trust and threat observations) did not make small models fight or flee
better in a 20-tick forced-combat scenario. Memory stays off by default, as it already was.
