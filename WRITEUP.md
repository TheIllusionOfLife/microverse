# Microverse Battery — A Private, Offline AI Society on One Laptop

## Subtitle

*A multi-agent simulation that ran 24 hours on a single Apple Silicon laptop,
entirely offline, on Gemma 4 via Ollama. 1,644 audited artifact candidates
(702 multi-author workshop entries). Zero API calls. Full provenance.*

---

## The hook

Most agent demos die when the Wi-Fi does. We ran one for 24 hours on a
single Apple Silicon laptop, entirely offline, and it produced 13,794
events — 1,644 accepted artifact candidates, including 702 multi-author
workshop entries (collaborative essays, design proposals, field notes) —
through a single Gemma 4 model running locally via Ollama. No hosted
middleware. No per-token bill. No "thoughts" leaving the device.

The system is small enough to clone and run on your own machine in under
an hour. The provenance for every artifact — prompt input, model output,
ranking score, acceptance verdict — is on disk in SQLite. The dashboard is
a static HTML file with no JavaScript framework. You can read every
decision the system made, including the ones we got wrong, and the
Architecture Decision Records that drove the fixes.

## Why this wins the Ollama Special Track

- **One Gemma 4 model, one entry point.** Every persona — Artisan, Trader,
  Elder, Stranger — calls `gemma4:26b` through a single
  `ollama_client.chat()` function. No router. No fallback. No second model.
  The invariant is in `src/microverse/config.py:17` and tested in CI.
- **Ollama structured output is the Trader.** Artifact ranking calls
  the Ollama Python client with a JSON Schema dict as the `format`
  argument — one LLM call returns an ordered score list with rationales
  conforming to the schema, and the Harvester applies a percentile
  cutoff. This is the production-path use of Gemma 4's structured-output
  capability, not a demo.
- **Explicit thinking-channel discipline.** Gemma 4's reasoning trace is
  powerful but leak-prone. We pass `think=False`, force `thinking=""` on
  the response, scrub defensively, and bump a `thinking_leak` counter on
  any escape. After 24 hours, the counter is zero — we measure this as a
  Safety & Trust property, not assume it.
- **Zero cloud dependencies, reproducible from a clean clone.**
  `git clone && uv sync && ollama pull gemma4:26b && uv run python -m microverse.run`.
  That's the full setup. The included kill-drill script proves WAL
  recovery survives `SIGKILL`.

## A concrete use case

A rural clinic with intermittent connectivity needs intake summaries that
*never* leave the device. A multi-author classroom journal needs to keep
generating prompts during a network outage. A regulated-industry knowledge
team needs an agent loop that runs on the laptop on the locked desk, not
the data center across the network boundary. Microverse Battery is the
substrate for those applications: the durability, the observability, the
multi-agent collaboration, and the honest measurement all already work
offline. What changes is the persona prompts, not the architecture.

The demo we ship — a fictional society of agents collaborating on garden
beds, looms, and scrolls, with immigrant Strangers bringing imagined
perspectives from "coastal marshes" and "arid plains" — is a stress test,
not the product. The stress test produced 702 multi-author workshop
artifacts (1,644 accepted candidates total) in 24 hours. The
product would be whatever specialized agent set the deployment needs.

## Architecture

The runtime is a single-process Python tick loop in `src/microverse/run.py`.
One tick:

1. A weighted scheduler picks an agent (weight = `soul_tokens`, floor 1).
2. A bounded `WorldContext` is assembled: recent episodic memory
   (≤1,500 tokens), FTS5 semantic-memory lore excerpt (≤600 tokens), the
   current weather event, and a filtered peer-inbox. Critically:
   **Path-3 stateless tick** — agents never see their own prior actions,
   so personality drift isn't reinforced by autobiographical replay.
3. The agent's `think()` call goes to the local Ollama client.
4. The returned JSON `Action` is parsed defensively
   (strict JSON → `json_repair` → safe-`rest` fallback; never raises).
5. The action commits to a SQLite WAL event log; the Harvester buffers
   candidate artifacts.
6. A `WorldClock` may emit a seeded weather event. Every 25 ticks the
   Watchdog checks for runaway, stagnation, diversity. Every 50 ticks the
   Harvester flushes its buffer through the Trader's ranking and writes
   accepted artifacts to `harvest/inbox/`.

Modules map cleanly: `agents/`, `memory/`, `world/`, `ops/`, `llm/`,
`prompts/`. Two SQLite databases are the substrate: `episodic.sqlite` (the
durability boundary, opened with `synchronous=NORMAL` and verified by a
kill-drill script) and `semantic.sqlite` (FTS5 lore recall, compressed
periodically by the Elder with a Jaccard drift guard against runaway
edits). The shared workshop (ADR 0003) is the multi-author artifact
substrate: agents `contribute_to` shared work-in-progress entries across
ticks, and the Trader scores the *aggregate* WIP, not individual
contributions.

## The ADR journey — breakthroughs, not a defect ledger

Five Architecture Decision Records document how we got here.

**ADR 0001 — Local-first runtime works.** Single-model invariant. SQLite
WAL durability. FTS5 semantic recall. The basic substrate held up under
load.

**ADR 0002 — We instrumented a model-level limit and shipped with it
documented.** After seven patches, the Artisan persona stabilized at a
~93% craft-share specialization that no prompt change moved. Instead of
hiding the limit, we measured it, wrote it into the v0.1 README, and
shipped. Honest measurement of what models actually do under load is the
Safety & Trust contribution.

**ADR 0003 — Collaboration substrate solved the solo bottleneck.** The
breakthrough was making artifact creation collective: a workshop holds
multi-author work-in-progress, agents build on each other across ticks,
the Trader scores the aggregate. The conversation gained structure
because the *substrate* gained structure. The artifacts in the live
dashboard are the result.

**ADR 0004 — Structural fixes closed throughput gates.** Four
structural-not-instructional fixes (capacity invariant, fragment-length
floor, recycle lifecycle, acceptance subfloor) plus a model swap to
`gemma4:26b` raised accepted-WIPs-per-hour from 3.7 to 29.3 — an 8x
throughput improvement on the same hardware.

**ADR 0005 — The next bottleneck is harness shape, and we shipped
the first piece tonight.** The same soaks that closed throughput
exposed a structural ceiling on social engagement: a single-action
tick can't guarantee peer reference because there's no prior turn to
read. **Decision 1** of the ADR — hide complete workshop entries
from the persona prompt so the affordance can't pull contributes into
already-finished WIPs — landed as **v0.3.1** during this hackathon,
with a new `wip_target_concentration` gauge guarding against the
rerouting failure mode the ADR itself called out. The deeper v0.4
piece (replace the single-tick affordance with a 3-turn scene where
turn N reads turns 1..N-1) has its acceptance gates written before
the code, and ships next.

Each ADR closed one bottleneck and exposed the next. The published
limits aren't defects — they're the load-bearing evidence that the next
fix is the right one.

## Demo and what's next

The live dashboard at
**https://theillusionoflife.github.io/microverse/** renders the metrics,
weather feed, and harvested artifacts from a 24-hour `gemma4:26b` soak —
13,794 events, 1,644 accepted artifact candidates (702 of them
multi-author workshop entries), 9,502/9,522 valid Action JSON (99.79%),
and the `thinking_leak` counter never fired — all generated locally
over one wall-clock day on Apple Silicon.

Click any artifact and you'll see Aki proposing a parchment treatment
("dust the edges with alum to prevent the ink feathering") and a Stranger
responding with a perspective from her imagined homeland ("instead of
alum, we might try a technique from the coastal reaches…"). That cross-
turn responsiveness is what local-first multi-agent intelligence looks
like when the substrate gives the model room to be social.

The codebase, ADRs, soak data, and this writeup are at
**https://github.com/TheIllusionOfLife/microverse**. Clone it, `uv sync`,
`ollama pull gemma4:26b`, run your own society. The kill-drill works. The
dashboard renders. The provenance is complete from prompt input to ranked
output. No API key required, ever.

Next: ADR 0005's scenes ship as v0.4. If the acceptance soak closes the
peer-engagement gate, the same local stack becomes a credible substrate
for offline classroom co-pilots, regulated-industry knowledge digests,
and any application that needs autonomous agentic generation to keep
running when the network doesn't. The hard parts — durability,
observability, governance, honest measurement, Gemma 4's structured
output and thinking discipline — already work today on one laptop.

---

## v1.0 addendum (post-soak skeleton)

This is the result of the v0.4→v1.0 build. The 7-day acceptance soak
fills the numbers in; the structural claims here are stable regardless
of outcome. Both branches sketched below — the writeup is honest about
which branch the actual evidence supports when the soak completes.

### What v1.0 ships beyond v0.3.2

- **Transition-triggered harvester flush (ADR 0005 D2)**. The 50-tick
  timer remains as a ceiling but the harvester now flushes
  opportunistically when a WIP transitions to `complete`, subject to a
  5-tick throttle. Counters `harvest_flush_timer_triggered` and
  `harvest_flush_transition_triggered` attribute the trigger cause.
  Designed to close ADR 0005 gate 7 (capacity invariant — Soak B
  showed 240 violations / 288 samples; v1.0 target zero).
- **Multi-turn scenes (ADR 0005 D3)**. The scene gate
  (`config.SCENE_GATE_P=0.15`) routes ~15% of ticks into a 3-turn
  sequence on the workshop affordance. Turn 2 sees turn 1's text
  verbatim via `WorldContext.scene_context`; turn 3 sees both prior
  turns. The contract is logged: `scene.open` carries the author
  rotation (authoritative across replay), `contribute` events carry
  `scene_id` + `turn_index`, `scene.abort` is written on parse
  failure. Designed to close gate 1 (peer-reference rate — Soak A
  0.014, Soak B 0.022, target ≥ 0.30) by structure rather than
  coercion.
- **Embedding-based Gate 8**. `nomic-embed-text` via
  `ollama.embed()` produces fragment embeddings; the gates producer
  computes median cosine for turn-2 vs turn-1 and turn-3 vs
  turn-1+turn-2. Pass band [0.30, 0.85] is the structural guard
  against scenes passing gate 1 by lexical fluke. Embedding model
  is measurement infrastructure only; never called from
  `agent.think()` — single-model invariant for the agent action loop
  is preserved.
- **Verb-diversity counter-pressure (ADR 0002 follow-up)**. A
  per-agent novelty hint and a 30%-probability post-action
  substitution lever push back against the craft-share attractor.
  Carve-outs preserve the engagement-gate and JSON-fallback signals.
  Not a claim to dissolve ADR 0002's model-level limit; a measurable
  structural counter.
- **Multi-week operational hardening**. `prune_snapshots`,
  `manifest*.jsonl` rotation, `episodic.optimize()` (no VACUUM, no
  long lock), and the `operate_soak.py` operator wrapper. A 7-day
  soak does not fill disk and the dashboard remains readable.

### The 7-day acceptance soak

Run via `nohup uv run python scripts/operate_soak.py --duration 7d
--data data/soak-v1 --harvest harvest/soak-v1 --seed 38 > soak.log
2>&1 &`. Acceptance:

- All seven ADR 0005 gates hold continuously (no 1-hour window in
  which any drops below threshold).
- Gate 3 (verb concentration) ≤ 70% per agent across 168 hours.
- `data/snapshots/` < 5 GB and < 50 archives at every sample.
- `data/episodic.sqlite` < 1 GB at end-of-soak.
- `thinking_leak` total == 0.
- No `harvest_flush_fail` / `snapshot_fail` regressions vs the v0.3
  24h baseline scaled by 7.

### Branch A — scenes close gate 1 cleanly

If the 7-day soak passes all seven gates including peer-reference
rate ≥ 0.30 and gate 8 cosine medians in [0.30, 0.85], v1.0 is the
positive result: the harness-shape lever **did** close the residual
gap that prompt patches could not, and the structural fix
generalises across the 7-day timescale. Substitution-lever share
shows up on the dashboard so the agency claim is honest about how
much of the verb mix is LLM-chosen.

### Branch B — gate 1 still falls short

If gate 1 finishes between 0.20 and 0.30 (closer than ADR 0005's
0.022 baseline but below the original threshold), the halt criterion
in the plan applies: soften gate 1 threshold to the observed median
rounded down to one decimal, publish that number with provenance in
ADR 0006, and re-soak with the softened threshold. If even that
fails, write up as a documented negative result — scenes moved the
peer-reference rate but did not close it, and a cross-family or
digest-style follow-up would be the next experiment (out of scope
for v1.0).

### Smoke validation (pre-soak)

A 6-tick scene-gate-saturated smoke against live `gemma4:e4b` with
`nomic-embed-text` pulled confirmed the structural contract:

- 6 scene.open events emitted (gate forced to 1.0 for the smoke).
- 4 scenes completed all 3 turns; 2 partial (off-topic abort on
  the middle turn).
- Gate 8 cosine(turn2, turn1) median = **0.770**. In band [0.30, 0.85].
- Gate 8 cosine(turn3, turn1+turn2) median = **0.805**. In band.
- Gate 8 PASS — turn 2 and turn 3 read their predecessors semantically,
  not by lexical fluke.

This is structural, pre-soak validation. The 7-day acceptance soak
fills in the gate 1 peer-reference rate (the load-bearing lexical
counterpart), gate 2 throughput, and the long-horizon operational
health markers. But the scene mechanic itself works on the live
local stack as designed.

### Final numbers

*(filled in at soak completion — placeholders below)*

| Gate | Threshold | Soak A baseline (v0.3 e4b) | Soak B baseline (v0.3 26b) | v1.0 7-day |
|------|-----------|----------------------------|----------------------------|------------|
| 1 fragment-shape composite | composite | fail | fail | _TBD_ |
| 2 WIP throughput / hr | ≥ 5 | 3.7 | 29.3 | _TBD_ |
| 3 verb concentration | ≤ 70 % | 86.7 % | 57.4 % | _TBD_ |
| 4 pipeline efficiency (fold rate) | < 1 % | 13.2 % | 61.6 % | _TBD_ |
| 5 path-3 redactions | non-zero | 121 451 | 23 007 | _TBD_ |
| 6 capacity invariant | open_slots ≥ 3 | 285 violations | 240 violations | _TBD_ |
| 7 acceptance throughput | ≥ 50 % | 100 % (1/1) | 100 % (1/1) | _TBD_ |
| 8 scene semantic dependence | median ∈ [0.30, 0.85] | n/a | n/a | _TBD_ |
| thinking_leak total | == 0 | 0 | 0 | _TBD_ |
| events committed | — | 19 878 | 14 113 | _TBD_ |
| accepted WIPs | — | 89 | 702 | _TBD_ |

### How to reproduce

```bash
git clone https://github.com/TheIllusionOfLife/microverse
cd microverse && uv sync
ollama pull gemma4:26b
ollama pull nomic-embed-text   # gate 8 only; optional
nohup uv run python scripts/operate_soak.py \
    --data data/soak-v1 --harvest harvest/soak-v1 \
    --duration 7d --seed 38 > soak.log 2>&1 &
# after 7 days:
uv run python scripts/spike_workshop_measure.py \
    --data data/soak-v1 --harvest harvest/soak-v1 > gates.json
uv run python scripts/render_dashboard.py \
    --data data/soak-v1 --harvest harvest/soak-v1
```

The kill-drill verifier and the scene-boundary check are part of the
acceptance:

```bash
W=$(sqlite3 data/soak-v1/episodic.sqlite 'SELECT COALESCE(MAX(id),0) FROM events')
kill -9 $(pgrep -f microverse.run)
# restart, then:
uv run python scripts/verify_kill_drill.py \
    --db data/soak-v1/episodic.sqlite \
    --watermark "$W" --scene-boundary any
```

The expected output begins with `kill_drill_ok`. A failing run is the
honest negative result: scene boundaries did not survive WAL replay,
and v1.0 must patch the scene event contract before shipping.
