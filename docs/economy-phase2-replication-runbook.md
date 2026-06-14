# Phase 2 item 3 runbook — held-out replication of the `bal@30` Gate 9 PASS (T fixed at 30)

Operator handoff + **pre-registration** for the ADR 0012 Phase 2 item 3 replication sweep.
Stage 6 (ADR 0012) produced the arc's first Gate 9 PASS at `bal@30`, seeds {42, 38, 7}; the
mechanism audit (ADR 0013) confirmed the scarcity hint as the operative channel. Both records
scope the claim as "first locked pass, to be replicated." This sweep tests whether the PASS is
**stable under replication**: the target stays fixed at T = 30 (nothing is re-tuned), only the
seeds are fresh and the runs are new draws of `gemma4:26b`'s unseeded sampling.

This PR ships the **ground-truth hint logging + the audit's logged-hint fidelity check + this
pre-registration**; the sweep launches right after the pre-registration commit and the findings +
ADR 0014 land in the same PR after the read (~38 h of live compute later).

## What is new in the instrument (and why it does not break comparability)

ADR 0013 Decision 3: the replication runs stamp the live scarcity-hint state into each free-turn
payload — `energy_hint_fired` (was the hint in the prompt this turn) and `energy_hint_verb` (the
verb it named, `null` when it named none). This retires the mechanism audit's reconstruction
caveat: the audit now compares its offline reconstruction against logged ground truth
(`fidelity.hint_logged`).

**Instrument-inertness clause (pre-registered):** the new keys are observation-only. The values
are the very ones already computed for the prompt and the R4 conflict counter (run.py threads one
computation to all three consumers); nothing about prompts, scheduling, energy arithmetic, or
action selection changes. Pinned by unit tests (`tests/test_run_economy.py`: stamped-when-drained,
false-when-ample, absent-when-off) and by the audit re-read of `data/econ-stage6-bal30-s42`, which
is numerically identical to the PR #56 report except for the new (null) `hint_logged` block. Gate 9
is inert to unknown payload keys (it filters only `parsed_verb` / `scene_id` / `parse_fallback` /
`economy_substituted`). The replication runs therefore remain comparable to Stage 6.

## Design notes (why this matrix)

- **In-sweep `bal@22` control, not Stage 6's archived control.** Model/runtime behavior can drift
  between sweeps separated by days; the only causally clean comparator for `bal@30` is a paired
  `bal@22` run in the same sweep (same reasoning as Stage 6, reaffirmed by external design review).
- **No `adv` arm.** Its role in Stage 6 was reproducing the one-sided Stage-4/5 mechanism; that
  is settled and re-running it buys nothing for the replication claim (~12.6 h saved).
- **Fresh seeds {101, 202, 303}**, disjoint from every prior economy read ({42, 38, 7}). The seed
  controls weather/scheduler RNG only; `gemma4:26b` sampling is unseeded, so each run is also an
  independent sampling draw. This design supports "stable across fresh stochastic runs"; it does
  NOT decompose seed effects vs sampling noise (that would need fixed-seed repeats, out of scope).
- **Known C4 lesson applied:** every role-stability criterion below is phrased on the top
  NON-contribute verb, because `contribute` tops BOTH agents' raw chosen streams by design in all
  nine Stage 6 runs (ADR 0013 Decision 2).

---

## ===== DO NOT EDIT BELOW AFTER THE SWEEP LAUNCHES (pre-registered) =====

### Matrix — 6 runs

| arm | mode | `MICROVERSE_BAL_CONTRIBUTE` | role |
|-----|------|----------------------------|------|
| `bal@22` | `bal` | unset (→ natural dearest 22) | in-sweep control |
| `bal@30` | `bal` | `30` | the replicated arm (T fixed, not re-tuned) |

`{bal@22, bal@30}` × seeds `{101, 202, 303}` × 3000 ticks, one sweep, seed-outer/arm-inner.
Model `gemma4:26b`, energy knobs at the live defaults (max 100, regen 8). State dirs
`data/econ-rep30-<arm>-s<seed>`, harvest `harvest/econ-rep30-<arm>-s<seed>`.

### Run

```bash
nohup ./scripts/run_rep30_sweep.sh > econ-rep30-sweep.log 2>&1 &
```

(The script writes each run's Gate 1–9 report to `data/econ-rep30-<arm>-s<seed>/gate-report.json`
and refuses to append to a partial run dir — a crashed run is re-run fresh, never restarted,
because a mid-run restart would both weaken the audit reconstruction and make the run
non-comparable. Path template corrected post-launch — a doubled-prefix typo flagged in review;
no criterion touched.)

### Instrument gate (checked before any behavioral verdict is read)

Per run, from `scripts/replay_economy.py --audit bal=PATH` / `bal@30=PATH`:

- `fidelity` (predicted-vs-logged substitution, the audit's C5) ≥ 0.90 on both the hint-on and
  hint-off subsets (Stage 6 landed hint-on 0.92–0.95; a value in 0.90–0.91 satisfies the gate but
  is flagged as borderline in the findings);
- `fidelity.hint_logged` (reconstruction vs logged ground truth, full `(fired, verb)` agreement)
  ≥ 0.90;
- coverage: the hint-on fidelity subset and the `hint_logged` block each have ≥ 10 events per run
  (fewer means the rate is too unstable to gate on → INSTRUMENT-INVALID).

If any run fails the instrument gate, the verdict is **INSTRUMENT-INVALID**: no behavioral verdict
is read, and the instrument fault is diagnosed before any re-run.

### Pass rule (locked)

Measured on the **chosen** (`parsed_verb`) stream via `scripts/spike_workshop_measure.py`
`gate9_verb_diversity`, exactly as Stage 6.

- **Layer 1 — gate replication (hard):** at ALL three fresh seeds, `bal@30` has
  `entropy_norm ≥ 0.35` AND `jsd_norm ≥ 0.25`; AND
  `jsd_norm(bal@30) > jsd_norm(bal@22) + 0.02` at every matched seed (the margin excludes a
  noise-level ordering win; Stage 6's per-seed gaps were 0.055–0.156, so the margin is
  conservative against the published facts).
- **Layer 2 — role stability (hard, the ADR 0013 Decision 2 corrected criterion):** at ALL three
  `bal@30` seeds, Aki's top NON-contribute chosen verb is `craft` AND Cy's top NON-contribute
  chosen verb is `study`; and no cross-role bleed: Aki chosen-`study` share ≤ 0.05 AND Cy
  chosen-`craft` share ≤ 0.05.

**Verdict rule: REPLICATED iff Layer 1 AND Layer 2 hold in full (given the instrument gate
passed); otherwise NOT REPLICATED.** No partial credit, no post-hoc amendment: a Layer that fails
as drafted is reported as drafted (the ADR 0013 C4 lesson is why the layers above are phrased
against the published Stage 6 facts).

### Secondary attribution metrics (reported, NOT gating)

Per matched seed vs in-sweep `bal@22`: Cy chosen-`contribute` drop ≥ 0.10; Cy chosen-`study` rise
≥ 0.05 on the mean across the three fresh seeds (per-seed values reported individually so the
aggregate cannot hide a zero-rise seed). These are Stage 6's conditions 2/4, demoted to
confirmatory because lever attribution was already established there; replication tests
stability, not attribution.

### Advisory mechanism diagnostics (directional, deliberately no numeric bands)

From `--audit` over the six dirs (numeric bands are avoided because firing/obedience rates vary
with seed weather and opportunity mix for boring reasons; direction is the claim):

- Cy hint firing rate materially higher in `bal@30` than its matched `bal@22`;
- P(Cy `study` | hint fired) > P(Cy `study` | hint absent), with the `absent_low` deconfound
  stratum flat as in ADR 0013;
- Cy obedience to the named verb ≥ 0.30 (the audit's advisory floor);
- per-agent specialty hint targeting persists (Aki's fired turns name `craft`, Cy's name `study`).

### Quality counters (report, `MAX` not `SUM`)

`novelty_energy_hint_conflict`, `artisan_empty_craft_coerced`, `parse_fallback`,
`scene_completed` (the R3 watch: confirm scenes do not collapse at fresh seeds).

### Honesty note

The Stage 6 results at seeds {42, 38, 7} are known. Everything this sweep measures — all six
fresh-seed runs — is genuinely unseen at pre-registration time. This rule is committed to git
before the sweep's first tick.

---

After the read: `docs/economy-phase2-replication-findings.md` with the per-seed table and the
REPLICATED / NOT REPLICATED / INSTRUMENT-INVALID verdict, plus `docs/adr/0014-*` recording it,
appended to this PR. If NOT REPLICATED, the arc's claim reverts to ADR 0012's bounded "first
locked pass" framing and Phase 2 item 4 (roster generality) is re-scoped in light of which layer
failed.
