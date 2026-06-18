# Findings — R3 strong-specializer retest (ADR 0017)

Fresh live read (seed 101 pilot, `gemma4:26b`, 3000 ticks) of the R3 roster under the stronger-travel
Stranger persona (`MICROVERSE_STRANGER_PERSONA=travel`). Pre-registration:
`docs/economy-phase2-r3-strong-specializer-runbook.md` (locked before launch). **Verdict: LEVER INERT
(counterproductive) — full sweep declined at the pilot per the locked continuation rule.**

The persona lever did not specialize the Stranger. It moved the targeted mechanism **backward**:
Vesna's `travel` share more than halved and her role stability (Layer 2) broke. R3 still fails.

## Setup

Identical to the ADR 0015 R3 baseline except the one IV: `MICROVERSE_STRANGER_PERSONA=travel`
(selects `persona_stranger_travel.j2`). Roster `artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70`,
`MICROVERSE_ECONOMY=bal`, `MICROVERSE_BAL_CONTRIBUTE=30`, energy knobs at live defaults, seed 101.
Cost table untouched (Stranger `travel` stays cost 6, cheapest).

## Instrument gate — PASS

| metric | value | gate |
|---|---:|:---:|
| `fidelity.rate` | 0.9731 | ≥ 0.90 ✅ |
| `fidelity.hint_on.rate` (ev 1363) | 0.9494 | ≥ 0.90 ✅ |
| `fidelity.hint_logged.rate` | 0.9996 | ≥ 0.90 ✅ |

The read is valid: the backfire is real behavior, not an instrument artifact.

## Layer 1 — society divergence: FAIL

| run | `mean_pairwise_jsd` | `jsd_norm` | Layer 1 @0.25 | continuation @0.235 |
|---|---:|---:|:---:|:---:|
| R3 baseline (default persona) | 0.2073 | 0.1964 | FAIL | — |
| **R3-strong (travel persona)** | **0.2179** | 0.2030 | **FAIL** | **below (stop)** |

`mean_pairwise_jsd` ticked up +0.0106 but stayed far under 0.25 and under the 0.235 continuation bar.
Crucially the rise is **not** specialization: it tracks Vesna's distribution becoming *more lopsided
toward `contribute`* (a shape change that nudges pairwise JSD), while her actual specialty collapsed
(below). The metric moved up for the wrong reason.

## The backfire — Vesna's chosen-verb distribution (the headline)

| Vesna chosen verb | baseline | strong (travel) | Δ |
|---|---:|---:|---:|
| contribute | 0.720 | **0.787** | +0.067 |
| **travel (specialty)** | **0.156** | **0.054** | **−0.102** |
| speak | 0.063 | 0.086 | +0.023 |
| rest | 0.027 | 0.060 | +0.033 |
| study | 0.030 | 0.009 | −0.021 |
| craft | 0.004 | 0.004 | 0.000 |

Travel-obedience (audit, fraction of free turns choosing the hint-named `travel`): **0.314 → 0.114**.
Hint firing was unchanged-adequate (0.499 → 0.473), so dose is not the issue — the Stranger simply
chose `travel` far less.

**Interpretation:** the wayfarer/road identity framing made Vesna narrate road-stories *into the
workshop* (contribute) rather than take the `travel` action. The persona's "bring contrasts from the
road" + "your outsider perspective is welcome here" pull won out over literal travel. Reframing
identity toward movement amplified contribution, not specialization.

## Layer 2 — per-agent role stability: FAIL (worse than baseline)

Top NON-contribute chosen verb per resident:

| resident | baseline top non-contribute | strong top non-contribute | Layer 2 |
|---|---|---|:---:|
| Aki (craft) | craft 0.358 | craft 0.393 | ✅ |
| Cy (study) | study 0.277 | study 0.287 | ✅ |
| **Vesna (travel)** | **travel 0.156** | **speak 0.086** | **❌** |

The baseline R3 actually *held* Layer 2 for Vesna (travel was her top non-contribute verb). The
travel persona **broke** it: `speak` (0.086) now outranks her specialty `travel` (0.054). Aki and Cy
are unmoved (their personas unchanged), confirming the effect is the Stranger-persona IV.

## Diagnostics (reported, not gating)

| run | `society_entropy_norm` | `specialization_ratio` | `identity_verb_nmi` |
|---|---:|---:|---:|
| R3 baseline | 0.6507 | 1.061 | 0.190 |
| R3-strong | 0.6330 | 1.032 | 0.199 |

`identity_verb_nmi` is essentially flat (0.190 → 0.199): cross-agent distinguishability-by-verb did
not improve. `specialization_ratio` stays ≈ 1.0 (the society still sits near the 3-specialist entropy
ceiling) — the R3 signature ADR 0016 described (diverse society, undifferentiated agents) is
unchanged.

## Continuation decision (locked rule applied)

Pilot `mean_pairwise_jsd` 0.2179 < 0.235 → **STOP; seeds 202/303 not run.** The pre-registration
authorized declining the remaining ~12 h on a single sub-threshold pilot, and the result is not
merely null but counterproductive (Vesna's specialty suppressed, Layer 2 broken), so a second/third
seed is not needed to read "this lever is the wrong tool."

## Honesty note — the committed prediction was wrong

The runbook pre-committed "ACHIEVABLE more likely than STILL FAILS; Vesna's travel obedience should
rise above ~0.31 toward the scholar-like ~0.5." **This was wrong in direction:** obedience *fell* to
0.114. Recorded as a failed prediction, not rationalized. The mechanism (identity prose that
foregrounds the workshop/contribution affordance can crowd out the literal specialty verb) is the
lesson; it was not anticipated.

## Conclusion

A travel-leaning persona, calibrated to the Scholar's identity-led study lean, does **not** make the
Stranger specialize — it suppresses `travel` and amplifies `contribute`. R3 still fails Gate 9 (both
layers) under the size-invariant metric. This does **not** establish "intrinsic roster-size
difficulty" (that would require a lever that *successfully* raised obedience yet still failed
divergence); it establishes that **this** lever is counterproductive and that R3's contribute-
dominance is sticky against identity prose. The why-does-N=3-fail question (ADR 0015 Decision 3(a))
remains open; the next candidate levers are economic (suppress the Stranger's `contribute`, not
amplify its `travel` narrative) or the deferred weights axis — see ADR 0017.

## Reproduction

```bash
MICROVERSE_STRANGER_PERSONA=travel ./scripts/run_r3_strong_sweep.sh 101
uv run python scripts/spike_workshop_measure.py --data data/econ-r3strong-s101 \
  --harvest harvest/econ-r3strong-s101            # mean_pairwise_jsd, Layer 2, diagnostics
uv run python scripts/replay_economy.py --audit bal@30=data/econ-r3strong-s101   # fidelity + obedience
```
Run dir `data/econ-r3strong-s101` (untracked, kept locally). Baseline `data/econ-roster-r3-s101`.
