# Action-economy Phase 2 item 3 findings — held-out replication of the `bal@30` Gate 9 PASS

Operator note recording the live held-out replication sweep that feeds **ADR 0014**. This is the
sweep pre-registered in `docs/economy-phase2-replication-runbook.md` (locked at commit `2d4ef8b`,
before the sweep's first tick). It tests whether the Stage 6 Gate 9 PASS (ADR 0012, seeds
{42, 38, 7}) is **stable under replication**: the balanced contribute target stays fixed at
**T = 30** (nothing is re-tuned), only the seeds are fresh ({101, 202, 303}, disjoint from every
prior economy read) and each run is a new unseeded sampling draw of `gemma4:26b`.

**Headline: REPLICATED.** Instrument gate PASS, Layer 1 (gate replication) PASS, Layer 2 (role
stability) PASS — all three at every fresh seed, against the locked two-layer rule, with no
post-hoc amendment. The arc's first Gate 9 PASS holds on held-out seeds.

## Setup

- Matrix `{bal@22 in-sweep control, bal@30}` × seeds `{101, 202, 303}` × 3000 ticks, one sweep,
  seed-outer / arm-inner. Model `gemma4:26b`, energy knobs at the live defaults (max 100,
  regen 8). State dirs `data/econ-rep30-<arm>-s<seed>`.

  ```bash
  env MICROVERSE_ECONOMY=bal [MICROVERSE_BAL_CONTRIBUTE=30] \
    MICROVERSE_DATA=data/econ-rep30-<arm>-s<seed> \
    MICROVERSE_HARVEST=harvest/econ-rep30-<arm>-s<seed> \
    uv run python -m microverse.run --ticks 3000 --tempo 0 --seed <seed>
  ```

- Arms: `bal@22` (`bal` at the natural dearest contribute cost 22, `MICROVERSE_BAL_CONTRIBUTE`
  unset — the **in-sweep control**, isolating the raised target from unseeded sampling drift);
  `bal@30` (the replicated arm, `MICROVERSE_BAL_CONTRIBUTE=30`, T fixed not re-tuned). No `adv`
  arm: its Stage-6 role (reproducing the one-sided Stage-4/5 mechanism) is settled and re-running
  it buys nothing for the replication claim.
- Run detached 2026-06-12 20:38 → 2026-06-14 11:02 JST (pid 59409), ~6.3 h / ~4540 agent events
  per run, dead-steady pacing. Each seed's `bal@22` control is paired temporally adjacent to its
  `bal@30`, so the archived Stage-6 control is a reference only, not the comparator.
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen** (`parsed_verb`)
  stream, exactly as Stage 6. Gate 9 PASS = chosen `entropy_norm ≥ 0.35` AND chosen
  `jsd_norm ≥ 0.25`. Mechanism audit via `scripts/replay_economy.py --audit MODE[@TARGET]=PATH`
  over all six dirs.

## What was new in the instrument (and why it does not break comparability)

ADR 0013 Decision 3: this PR stamps the live scarcity-hint state into each free-turn payload —
`energy_hint_fired` and `energy_hint_verb` — computed once per free turn and threaded to the
prompt, the R4 conflict counter, and the payload so they cannot diverge. The audit's new
`fidelity.hint_logged` block compares its offline reconstruction against this logged ground truth,
retiring the ADR 0013 reconstruction caveat. The keys are observation-only (pinned by
`tests/test_run_economy.py` and by the audit re-read of `data/econ-stage6-bal30-s42`, numerically
identical to the PR #56 report except for the added null `hint_logged` block); Gate 9 filters
only `parsed_verb` / `scene_id` / `parse_fallback` / `economy_substituted`, so it is inert to the
new keys. The replication runs therefore remain comparable to Stage 6.

## Instrument gate (checked before any behavioral verdict)

Per run: substitution fidelity (the audit's C5) ≥ 0.90 on both the hint-off and hint-on subsets;
`hint_logged` agreement ≥ 0.90; coverage ≥ 10 events on the hint-on and `hint_logged` blocks.

| run | hint-off | hint-on | hint-on n | hint_logged | hint_logged n |
|-----|---------:|--------:|----------:|------------:|--------------:|
| `bal@22`-s101 | 1.000 | 0.924 | 1499 | 1.000 | 2569 |
| `bal@30`-s101 | 1.000 | 0.939 | 1974 | 1.000 | 2560 |
| `bal@22`-s202 | 1.000 | 0.924 | 1519 | 1.000 | 2561 |
| `bal@30`-s202 | 1.000 | 0.934 | 1959 | 1.000 | 2564 |
| `bal@22`-s303 | 1.000 | 0.932 | 1528 | 0.9992 | 2516 |
| `bal@30`-s303 | 1.000 | 0.935 | 1955 | 1.000 | 2529 |

**Instrument gate PASS.** Hint-off exact in all six runs (no phantom scarcity); hint-on
0.924–0.939 (floor 0.90); `hint_logged` 0.9992–1.000 — the offline reconstruction matches the
live ground truth almost exactly, so the audit's mechanism reads below stand on logged truth, not
reconstruction. Coverage is two-to-three orders of magnitude above the 10-event floor.

## Layer 1 — gate replication (hard)

At all three fresh seeds: `bal@30` chosen `entropy_norm ≥ 0.35` AND `jsd_norm ≥ 0.25`; AND
`jsd_norm(bal@30) > jsd_norm(bal@22) + 0.02` at every matched seed.

| seed | jsd `bal@22` | jsd `bal@30` | margin | > +0.02 | entropy `bal@30` | ≥ 0.35 | jsd ≥ 0.25 |
|------|-------------:|-------------:|-------:|:-------:|-----------------:|:------:|:----------:|
| 101 | 0.2714 | 0.3369 | 0.0655 | ✅ | 0.6226 | ✅ | ✅ |
| 202 | 0.2488 | 0.3715 | 0.1227 | ✅ | 0.6342 | ✅ | ✅ |
| 303 | 0.2583 | 0.3873 | 0.1290 | ✅ | 0.6354 | ✅ | ✅ |

**Layer 1 PASS.** Every `bal@30` seed clears both floors with margin, and beats its matched
`bal@22` control by 0.066–0.129 (well past the 0.02 noise margin). The fresh-seed `bal@30` jsd
values (0.337 / 0.372 / 0.387) bracket and slightly exceed the Stage 6 results
(0.307 / 0.344 / 0.376 at seeds 42 / 38 / 7) — replication is not a marginal re-pass. The matched
`bal@22` controls sit at 0.249–0.271, below or at the 0.25 floor, exactly the near-miss Stage 6
diagnosed as residual R2.

## Layer 2 — role stability (hard, the ADR 0013 Decision 2 corrected criterion)

At all three `bal@30` seeds: Aki's top NON-contribute chosen verb is `craft` AND Cy's is `study`;
no cross-role bleed (Aki chosen-`study` ≤ 0.05 AND Cy chosen-`craft` ≤ 0.05). Phrased on the top
non-contribute verb because `contribute` tops both agents' raw chosen streams by design (the
ADR 0013 C4 lesson).

| seed | Aki top non-contrib | Aki `study` | Cy top non-contrib | Cy `craft` |
|------|--------------------:|------------:|-------------------:|-----------:|
| 101 | `craft` | 0.0213 | `study` | 0.0189 |
| 202 | `craft` | 0.0167 | `study` | 0.0198 |
| 303 | `craft` | 0.0108 | `study` | 0.0267 |

**Layer 2 PASS.** The specialization is the same one Stage 6 produced: the artisan keeps `craft`,
the scholar keeps `study`, and neither drifts toward the other's specialty (max cross-bleed 0.027,
floor 0.05). No identity collapse at fresh seeds.

## Verdict

**REPLICATED.** Instrument gate PASS, Layer 1 PASS, Layer 2 PASS, all in full at every fresh seed.
By the locked rule (REPLICATED iff Layer 1 AND Layer 2 hold, given the instrument gate passed),
the Stage 6 Gate 9 PASS is stable under replication on held-out seeds with T fixed at 30.

## Secondary attribution metrics (reported, NOT gating)

Per matched seed vs the in-sweep `bal@22` control. Stage 6's conditions 2/4, demoted to
confirmatory because the lever attribution was already established in ADR 0013; replication tests
stability, not attribution.

| seed | Cy contribute `bal@22`→`bal@30` | drop | ≥ 0.10 | Cy study `bal@22`→`bal@30` | rise |
|------|--------------------------------:|-----:|:------:|---------------------------:|-----:|
| 101 | 0.656 → 0.497 | 0.159 | ✅ | 0.276 → 0.417 | 0.141 |
| 202 | 0.613 → 0.499 | 0.114 | ✅ | 0.303 → 0.419 | 0.116 |
| 303 | 0.599 → 0.471 | 0.128 | ✅ | 0.319 → 0.438 | 0.119 |

Cy contribute drops ≥ 0.10 at every seed; mean Cy study rise across the three fresh seeds is
**0.125** (floor 0.05), with no seed below 0.116 — the aggregate does not hide a zero-rise seed.
The scholar reallocates from contribute to study under the raised target, the same substitution
Stage 6 measured.

## Advisory mechanism diagnostics (directional, deliberately no numeric bands)

From `--audit` over the six dirs (the audit now stands on logged hint ground truth):

- **Cy hint firing materially higher in `bal@30`** than its matched `bal@22` at every seed:
  0.433 → 0.672 (s101), 0.465 → 0.647 (s202), 0.477 → 0.674 (s303). Same dose direction Stage 6
  and ADR 0013 reported.
- **`P(Cy study | hint fired) > P(Cy study | hint absent)`** at every `bal@30` seed: 0.619 / 0.647
  / 0.649 conditional on the hint, vs ≤ 0.003 absent. The `absent_low` deconfound stratum
  (hint-absent turns energetically adjacent to the threshold) stays flat at ≤ 0.007 — the hint
  TEXT moves the model, not the bare energy level, replicating ADR 0013's C2.
- **Cy obedience to the named verb ≥ 0.30** (advisory floor): 0.619 / 0.647 / 0.649 across seeds.
- **Per-agent specialty hint targeting persists:** Aki's fired turns name `craft`, Cy's name
  `study`, at all three seeds (Aki firing itself rises 0.689 → 0.842, 0.688 → 0.848, 0.703 → 0.843
  — `bal` raises the artisan's own contribute cost too, the same self-economy ADR 0013 noted).

## Quality counters (reported MAX across runs, R3 scene-collapse watch)

| run | scene_completed | scene_aborted | novelty_energy_hint_conflict | json_fallback_rest |
|-----|----------------:|--------------:|-----------------------------:|-------------------:|
| `bal@22`-s101 | 325 | 106 | 455 | 1 |
| `bal@30`-s101 | 324 | 116 | 706 | 2 |
| `bal@22`-s202 | 334 | 105 | 504 | 1 |
| `bal@30`-s202 | 318 | 118 | 685 | 2 |
| `bal@22`-s303 | 360 | 124 | 505 | 2 |
| `bal@30`-s303 | 338 | 133 | 706 | 0 |

**Scenes do not collapse at fresh seeds (R3 clears):** scene_completed is 318–360 across all six
runs, `bal@30` ≈ `bal@22` (no scene attrition from the raised target). `json_fallback_rest` is
0–2 (parse health intact). `novelty_energy_hint_conflict` is higher in `bal@30`
(685–706 vs 455–505) — expected, since the hint fires more often, so the novelty/scarcity
co-fire opportunity rises; the conflict resolver favors the scarcity hint by design and the Layer 2
role stability shows no resulting drift.

## Honesty note

The Stage 6 results at seeds {42, 38, 7} were known. Everything this sweep measures — all six
fresh-seed runs — was genuinely unseen at pre-registration time, and the two-layer rule, instrument
gate, and verdict mapping were committed to git (`2d4ef8b`) before the sweep's first tick. No
criterion was amended after the read.

## Scope and limitations

- Two-resident roster, single model (`gemma4:26b`), T fixed at 30. Replication is over fresh
  unseeded sampling draws at three new seeds; the seed controls weather/scheduler RNG only, so
  this design supports "stable across fresh stochastic runs" but does NOT decompose seed effects
  vs sampling noise (that needs fixed-seed repeats, out of scope).
- Roster generality (more residents, different weights/pairings) remains Phase 2 item 4 — this
  sweep does not speak to it.
- No mid-run restarts; the instrument gate was re-checked on logged ground truth and passes.

## Reproduction

Runbook: `docs/economy-phase2-replication-runbook.md` (pre-registration, locked `2d4ef8b`). Sweep
driver: `scripts/run_rep30_sweep.sh`. Per-run gate reports:
`data/econ-rep30-<arm>-s<seed>/gate-report.json`. Audit:
`uv run python scripts/replay_economy.py --audit bal=data/econ-rep30-bal22-s<seed> --audit bal@30=data/econ-rep30-bal30-s<seed>`
(run dirs untracked, kept locally).
