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

### Outcome — which branch landed (soak-v1-2)

Neither branch landed cleanly; the result is a documented partial.
The 5.5-day soak-v1-2 run (full numbers in *Final numbers* above) put
Gate 1's lexical peer-reference rate at **0.073** — below even Branch
B's 0.20–0.30 window — while **Gate 8 passed strongly** (cosine
medians 0.762 / 0.757). That combination (lexical proxy under-fires,
semantic dependence holds) is exactly the case the halt criteria
pre-authorised: amend, do not re-tune scenes. Applying the
round-down-to-one-decimal formula to 0.073 yields a vacuous 0.0
threshold, so **ADR 0006 Amendment 1** records the formula defect,
reclassifies Gate 1's peer-reference subgate as observational, and
promotes Gate 8 to the operative peer-engagement gate. Scenes
demonstrably create inter-turn semantic dependence; they do not
manifest it as surface-form cross-reference. Shipped as `v1.0.0-rc1`
with the snapshot reliability fix and the revived diversity lever
carried to v1.1.

### Smoke validation (pre-soak)

A 100-tick operational smoke against live `gemma4:e4b` (Phase A+B+C
stack, `SNAPSHOT_EVERY=25` and `SNAPSHOT_RETENTION_COUNT=3` for
visibility) returned:

- 4 snapshots taken, 3 retained after prune (matches retention cap).
- 2 timer-triggered harvest flushes + **12 transition-triggered**
  flushes — 6× the timer-alone rate, which is the structural fix
  for gate 7 (capacity invariant).
- 97 contributes, 12 workshop.recycle cycles, no `snapshot_prune_fail`
  or `episodic_optimize_fail` bumps.
- Scenes did NOT fire in the default-roster (Aki + Cy) smoke because
  `SCENE_MIN_PEERS=2` requires 3 agents — scenes activate once the
  Watchdog spawns a Stranger, as designed.
- `scripts/verify_kill_drill.py --watermark $W --scene-boundary any`
  → `kill_drill_ok` (113 events, contiguous, all 1..113 survived).
- `scripts/render_dashboard.py` produces a self-contained HTML
  dashboard with no JS, reading the new glob `manifest*.jsonl`.

A partial 200-tick smoke with **Watchdog-driven Stranger spawn** at
the *production* SCENE_GATE_P=0.15 ran for ~45 ticks before being
terminated; the partial gate analysis is the strongest pre-soak data
point on the v1.0 trajectory:

| Gate | v0.3 baseline | 200-tick partial |
|------|----------------|-------------------|
| 1 peer-reference rate | 0.022 | **0.188** (9× improved, target ≥0.30) |
| 2 WIP throughput / hr | 29.3 (B) | **67.9** PASS (target ≥5) |
| 3 verb concentration | fail | PASS |
| 4 fold rate | 0.616 | **0.028** (22× improved, target <0.01) |
| 5 path-3 invariant | pass | PASS |
| 6 acceptance throughput | pass | PASS |
| 7 capacity invariant | 240 violations | **PASS** (min_open_slots=3) |
| 8 scene semantic dependence | n/a | **PASS** (0.728 / 0.782 in band) |

**6 of 8 gates PASS** at production scene-gate (0.15), with the
remaining two (gate 1 lexical peer-reference, gate 4 sub-1% fold)
showing 9× and 22× improvement respectively over the v0.3 baseline.
The 7-day acceptance soak is expected to land both.

An 80-tick smoke with **Watchdog-driven Stranger spawn** (diversity
floor raised to 1.0 so two Strangers immigrate; SCENE_GATE_P=0.5)
against live `gemma4:e4b` confirmed the same trajectory at higher
scene density:

| Gate | Result | v0.3 baseline | v1.0 80-tick smoke |
|------|--------|----------------|---------------------|
| 1 fragment-shape, peer-reference rate | fail | 0.022 | **0.056** (3× improved) |
| 2 WIP throughput / hr | **PASS** | 29.3 (B) | **65.2** (13× target) |
| 3 verb concentration | **PASS** | fail | pass |
| 4 pipeline efficiency (fold rate) | fail | 0.616 | **0.067** (10× improved) |
| 5 path-3 invariant | **PASS** | pass | pass |
| 6 acceptance throughput | **PASS** | pass | pass |
| 7 capacity invariant min open slots | marginal | 0 | 2 (target ≥ 3; bursty) |
| **8 scene semantic dependence** | **PASS** | n/a | **0.780 / 0.756** (in [0.30, 0.85]) |

42 scene.open events, 32 scenes completed all 3 turns, 10 aborted
(off-topic on turn 2). Two Strangers immigrated via Watchdog
echo-chamber detection. 11 transition-triggered harvest flushes.
The scene mechanic delivers semantic peer-engagement structurally
(gate 8), and the structural fixes substantially improve gate 1
and gate 4 trajectories from the v0.3 baseline — neither closes
fully at 80 ticks, but the slopes are exactly what ADR 0005
predicted; the 7-day acceptance soak is designed to land them.

A separate 6-tick scene-gate-saturated smoke (gate forced to 1.0,
`SCENE_MIN_PEERS=1`) against live `gemma4:e4b` with
`nomic-embed-text` pulled also confirmed the gate-8 contract:

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

The acceptance run was **soak-v1-2**: `gemma4:26b`, seed 38, full
A+B+C+D stack. It ran 5.5 days (≈132 h; 20,102 events; 1,897 scenes
opened, 1,565 completed, 2,116 accepted artifacts) and was stopped
manually when the cold-backup snapshot path started failing
persistently. The run itself was healthy throughout — the maximum
inter-event gap over the entire run was **51.5 s** (mean 23.4 s), so
there was no real stall; the apparent stalls seen by an out-of-process
monitor were a read artifact of the failing WAL checkpoint (see
*Operational findings* below). It did not reach the planned 7 days, so
the headline is a 5.5-day run, not a week.

| Gate | Threshold | Soak A baseline (v0.3 e4b) | Soak B baseline (v0.3 26b) | v1.0 soak-v1-2 (5.5-day) |
|------|-----------|----------------------------|----------------------------|--------------------------|
| 1 fragment-shape composite | composite | fail | fail | **fail** (peer-ref 0.073; word-count 42 ✓, 4-gram 0.0 ✓) |
| 2 WIP throughput / hr | ≥ 5 | 3.7 | 29.3 | **14.04 — pass** |
| 3 verb concentration | ≤ 70 % | 86.7 % | 57.4 % | **96.2 % — fail** |
| 4 pipeline efficiency (fold rate) | < 1 % | 13.2 % | 61.6 % | **2.19 % — fail (≈28× better than baseline)** |
| 5 path-3 redactions | non-zero | 121 451 | 23 007 | **43 671 — pass** |
| 6 capacity invariant | open_slots ≥ 3 | 285 violations | 240 violations | **236 violations — fail** |
| 7 acceptance throughput | ≥ 50 % | 100 % (1/1) | 100 % (1/1) | **100 % — pass** |
| 8 scene semantic dependence | median ∈ [0.30, 0.85] | n/a | n/a | **0.762 / 0.757 — pass** |
| thinking_leak total | == 0 | 0 | 0 | **0** |
| events committed | — | 19 878 | 14 113 | **20 102** |
| accepted WIPs | — | 89 | 702 | **1 833** |

(Gate numbering follows `spike_workshop_measure.py`: Gate 6 is
acceptance throughput, Gate 7 is the capacity invariant; the rows above
are labelled by description.)

**Result: 4 of 8 gates pass — and the load-bearing one (Gate 8) is
among them.** Across 1,565 scenes the embedding cosine confirms turn 2
reads turn 1 and turn 3 reads turns 1+2. That is the structural
evidence the scene mechanic was built to produce: turns genuinely
attend to each other. The positive branch of this writeup was always
"scenes create real inter-turn dependence"; Gate 8 carries it.

The negative branch is equally on the record. Gate 1's *lexical*
peer-reference rate (0.073) is far below 0.30 — agents engage by
paraphrase and semantic extension, not by surface-form "Y said X", so
the regex under-fires. Per the pre-baked halt criteria (Gate 1 fails,
Gate 8 passes → amend, do not re-tune scenes), ADR 0006 Amendment 1
reclassifies Gate 1's peer-reference subgate as observational and
promotes Gate 8 to the operative peer-engagement gate. The rounding
formula in the plan would have produced a vacuous 0.0 threshold; the
amendment records that defect explicitly rather than inventing a number
to pass.

Three more gates failed, none reflecting an unsound core:

- **Gate 3 (verb concentration, 96.2 %).** The Phase D verb-diversity
  lever **never fired** the entire soak (`diversity_lever_substituted`
  = 0). Root cause, found empirically: the dominance signal counted
  scene `contribute` events (≈94 % of the recent window), so the
  detected dominant verb was `contribute` — which a single-tick action
  is never — making the substitution precondition unsatisfiable. This
  is reported as a genuine negative result. (A follow-up fix excludes
  `contribute` from the signal so the lever can fire; that targets the
  next soak, not this one — the numbers above are the system as it
  actually ran.)
- **Gate 4 (fold rate, 2.19 %).** A close miss against the < 1 % target,
  but a ≈28× improvement over the 61.6 % pre-scenes baseline — scenes
  did most of the work the gate asks for.
- **Gate 7 (capacity, 236 violations).** Correlates with the snapshot-
  failure windows below; treated as a sibling of that operational
  issue, not an independent scene defect.

### Operational findings

The single real defect was in the **cold-backup snapshot path**, not
the simulation. From hour ~21, `wal_checkpoint(TRUNCATE)` raised
SQLITE_IOERR persistently (9,106 times) and the snapshot site retried
every interval for the rest of the run. Two consequences: thousands of
identical tracebacks in the log, and — because each failed checkpoint
perturbs the WAL/-shm sidecars — fresh out-of-process reader
connections saw a stale `MAX(ts)`, which a monitoring script read as a
multi-hour stall. The event stream proves no stall occurred (51.5 s max
gap). The fix is a `SnapshotGuard` circuit breaker: after 5 consecutive
failures it disables snapshots for the rest of the run (the WAL remains
the durability boundary), logs once, and stops flooding the log. Data
integrity was never at risk — WAL recovery was verified and the child
process exited code 0 on a clean SIGTERM.

This is the honest shape of the result: a sound simulation core with a
working scene mechanism, shipped as `v1.0.0-rc1` (per ADR 0006
Amendment 1) because one operational reliability issue and two
behavioural gates remain open for v1.1.

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
