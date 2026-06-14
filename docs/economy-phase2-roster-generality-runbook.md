# Phase 2 item 4 runbook — roster generality of the `bal@30` Gate 9 PASS (T fixed at 30)

Operator handoff + **pre-registration** for the ADR 0012 Phase 2 item 4 roster-generality sweep.
Stage 6 (ADR 0012) produced the arc's first Gate 9 PASS at `bal@30`; the mechanism audit (ADR 0013)
confirmed the scarcity hint as the operative channel; the held-out replication (ADR 0014) showed the
PASS is stable on fresh seeds — **all on the default 2-resident dyad** Aki(Artisan, 100) +
Cy(Scholar, 70). ADR 0014 Decision 2 names the open frontier: does the effect **generalize beyond
that dyad**? This sweep is the first held-out test of generality: the dose stays fixed at T = 30
(nothing re-tuned), and the roster changes.

This PR ships the **`MICROVERSE_ROSTER` env hook + the audit's roster-derived ledger seeding + this
pre-registration**; the sweep launches right after the pre-registration commit and the findings +
ADR 0015 land in the same PR after the read (~38 h of live compute later).

## The two rosters (why this matrix)

Both use existing `VERB_COST_BY_ROLE` entries (artisan→`craft`, scholar→`study`, stranger→`travel`,
each specialty cost 6, every role's `contribute` raised to T under `bal`) — **no new cost tables**.

- **R2 (role-swap, same N / weight shape):** `Aki(artisan, 100) + Vesna(stranger, 70)`. Tests a
  different pairing — `craft` vs `travel` — at the dyad's exact weight shape. Isolates "does the
  pairing matter" from "does N matter".
- **R3 (all-roles, more residents):** `Aki(artisan, 100) + Cy(scholar, 70) + Vesna(stranger, 70)`.
  Tests N=3 + a third specialty (`travel`). Each weight-70 agent's scheduling share drops from
  ~0.41 (dyad) to ~0.29.

**Why no `bal@22` in-sweep control:** the item-4 criteria are **absolute** (a society that did not
specialize fails Layer 2's per-agent role-stability outright), so a control arm is not required to
read generality; omitting it keeps the sweep at 6 runs per the approved budget. A per-roster
`bal@22` control is the natural follow-up only if a roster lands borderline.

**Hold T = 30 fixed (the transfer question).** This sweep asks "does the *same discovered knob*
work on a new roster without re-tuning?" — the stronger, cleaner generality question. Re-tuning T
per roster answers a *different* (mechanism-existence) question and is a pre-scoped separate
follow-up, not part of this confirmatory read.

## Offline firing pre-flight (zero LLM, pre-registered — does NOT alter the dose)

A calibrated drain simulation (weighted scheduling + whole-roster +8/tick regen + "attempt
contribute; divert to the cheap specialty when the hint fires") predicts per-agent hint firing at
T = 30. **Calibration:** on the dyad it predicts Aki 0.68 / Cy 0.45 — matching the live rep30 reads
(Aki ~0.69, Cy ~0.41–0.43). Predictions for the new rosters:

| roster | agent (weight) | predicted firing | note |
|--------|----------------|-----------------:|------|
| R2 | Aki (100) | 0.68 | same weight shape as the dyad |
| R2 | Vesna (70) | 0.45 | adequate dose — R2 should behave like the dyad |
| R3 | Aki (100) | 0.45 | scheduled less at N=3 |
| R3 | Cy (70) | 0.20 | **under-fired** — share drops 0.41→0.29 |
| R3 | Vesna (70) | 0.20 | **under-fired** |

**Pre-registered expectation:** R3's weight-70 agents fire ~0.20 at T = 30, less than half the
dyad's ~0.41, because the whole roster regens +8/tick but an agent only spends when scheduled. R3
is therefore the **at-risk cell**: a fixed-dose failure there is the predicted, interpretable
outcome (not a bug to patch by pre-tuning), and the pre-scoped follow-up re-tunes to a
drain-equated ~T = 42. This prediction does NOT change the dose used in the sweep.

## What is new in the instrument (and why it does not break comparability)

The `--audit` path now seeds the offline `EnergyLedger` from the run's **actual residents** (derived
from the committed trace) instead of the hardcoded 2-agent default, so the per-tick whole-roster
regen stays faithful for N>2 / non-default rosters. For the default dyad the derived names are
exactly {Aki, Cy}, so the ADR 0014 reads remain **byte-identical** (verified: a re-audit of
`data/econ-rep30-bal30-s101` is unchanged). `audit_run` and `gate9_verb_diversity` are already
per-event actor/role-keyed and N-agent ready (`_multi_jsd` normalizes by `log2(n)`); the hint
ground-truth logging (`energy_hint_fired` / `energy_hint_verb`, ADR 0014) carries forward unchanged.

---

## ===== DO NOT EDIT BELOW AFTER THE SWEEP LAUNCHES (pre-registered) =====

### Matrix — 6 runs

| roster | `MICROVERSE_ROSTER` | residents |
|--------|---------------------|-----------|
| R2 | `artisan:Aki:100,stranger:Vesna:70` | Artisan(100) + Stranger(70) |
| R3 | `artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70` | Artisan(100) + Scholar(70) + Stranger(70) |

`{R2, R3}` × seeds `{101, 202, 303}` × 3000 ticks, one sweep, roster-outer / seed-inner.
`MICROVERSE_ECONOMY=bal`, `MICROVERSE_BAL_CONTRIBUTE=30` (**fixed**), energy knobs at the live
defaults (max 100, regen 8). State dirs `data/econ-roster-<roster>-s<seed>`, harvest
`harvest/econ-roster-<roster>-s<seed>`. Seeds {101, 202, 303} match the replication and are disjoint
from Stage 6 {42, 38, 7}.

### Run

```bash
nohup ./scripts/run_roster_gen_sweep.sh > econ-roster-sweep.log 2>&1 &
```

(The script writes each run's Gate 1–9 report to `data/econ-roster-<roster>-s<seed>/gate-report.json`
via an atomic `.tmp`+`mv`, refuses to append to a partial run dir, and skips a run whose report
already exists. A crashed run is re-run fresh, never restarted.)

### Instrument gate (checked before any behavioral verdict is read)

Per run, from `scripts/replay_economy.py --audit bal@30=PATH`:

- `fidelity` (predicted-vs-logged substitution, the audit's C5) ≥ 0.90 on both the hint-on and
  hint-off subsets (a value in 0.90–0.91 satisfies the gate but is flagged borderline);
- `fidelity.hint_logged` (reconstruction vs logged ground truth, full `(fired, verb)` agreement)
  ≥ 0.90;
- coverage: the hint-on fidelity subset and the `hint_logged` block each have ≥ 10 events per run.

If any run fails the instrument gate, the verdict is **INSTRUMENT-INVALID**: no behavioral verdict
is read, and the instrument fault is diagnosed before any re-run.

### Pass rule (locked)

Measured on the **chosen** (`parsed_verb`) stream via `scripts/spike_workshop_measure.py`
`gate9_verb_diversity`, exactly as Stage 6 / the replication.

- **Layer 1 — society divergence (hard):** chosen-stream `jsd_norm ≥ 0.25`. This is the
  cross-agent JSD normalized by `log2(n)`, so it is **roster-size invariant** and directly
  comparable across N. `entropy_norm` is **reported** alongside each roster's perfect-specialist
  society-entropy ceiling (`log2(2)/log2(6) ≈ 0.387` at N=2, `log2(3)/log2(6) ≈ 0.613` at N=3) and
  whether it clears the legacy 0.35 mark — but `entropy_norm` is **NOT** a hard gate (the 0.35
  floor was calibrated for N=2 and is too permissive at N=3; re-deriving an entropy floor would
  introduce a contestable threshold). The verdict rests on `jsd_norm` + Layer 2.
- **Layer 2 — per-agent role stability (hard, the mechanism, the ADR 0013 Decision-2 form):** for
  EVERY resident i with specialty verb s_i (artisan→`craft`, scholar→`study`, stranger→`travel`):
  i's top NON-contribute chosen verb is s_i, AND i's chosen-share of every OTHER resident's
  specialty verb ≤ 0.05 (no cross-role bleed; the replication held this at ≤ 0.027). Phrased on the
  top non-contribute verb because `contribute` tops every raw chosen stream by design (the ADR 0013
  C4 lesson). This is the strong, absolute, roster-invariant discriminator and the heart of the
  verdict.

**Per-roster verdict:** a roster **GENERALIZES** iff ≥ 2 of its 3 seeds pass Layer 1 AND Layer 2 in
full (given the instrument gate passed for those seeds).

**Sweep verdict rule (locked):**
- **GENERALIZES** — both R2 and R3 generalize;
- **PARTIAL** — exactly one generalizes (report which axis survived: R2 = role pairing,
  R3 = more residents + third specialty);
- **DOES NOT GENERALIZE** — neither;
- **INSTRUMENT-INVALID** — any run fails the instrument gate.

No partial credit, no post-hoc amendment: a Layer that fails as drafted is reported as drafted.

### Secondary attribution metrics (reported, NOT gating)

Per agent, per roster: chosen-`contribute` share; chosen-specialty share; the live hint firing rate
vs the **offline pre-flight prediction** above (does the predicted R3 under-firing materialize?).
No in-sweep `bal@22` control exists, so these are absolute per-agent reads, not deltas.

### Advisory mechanism diagnostics (directional, deliberately no numeric bands)

From `--audit` over the six dirs:

- per-agent hint firing rate (and whether R3's weight-70 agents land near the predicted ~0.20);
- obedience to the named verb ≥ 0.30 (the audit's advisory floor), per agent;
- `P(i specialty | hint fired) > P(i specialty | hint absent)` per resident, with the `absent_low`
  deconfound stratum flat as in ADR 0013;
- per-agent specialty hint targeting: Aki's fired turns name `craft`, Cy's name `study`, Vesna's
  name `travel`.

### Quality counters (report, `MAX` not `SUM`)

`scene_completed` / `scene_aborted` (the **R3 collapse watch**: confirm 3-author scenes do not
collapse at N=3), `novelty_energy_hint_conflict`, `json_fallback_rest`,
`artisan_empty_craft_coerced`.

### Honesty note

Seeds {101, 202, 303} were used by the replication on the dyad, but **no run on the R2 or R3 roster
has ever been measured** — every behavioral number this sweep produces is genuinely unseen at
pre-registration time. The offline firing pre-flight above is a prediction, committed here before
the first tick; the live firing is read against it, not fitted to it. This rule is committed to git
before the sweep's first tick.

---

After the read: `docs/economy-phase2-roster-generality-findings.md` with the per-roster / per-seed
table and the GENERALIZES / PARTIAL / DOES NOT GENERALIZE / INSTRUMENT-INVALID verdict, plus
`docs/adr/0015-action-economy-roster-generality.md` recording it, appended to this PR. If a roster
does not generalize, the findings name which Layer failed and (for R3) whether the offline-predicted
dose under-shoot is the cause, scoping the re-tune follow-up.
