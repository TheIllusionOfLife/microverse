# ADR 0017: Action-economy R3 strong-specializer retest — lever inert (counterproductive), R3 still fails

**Status:** Accepted (measurement record) — records the ADR 0015 Decision 3(a) retest: does R3
(Artisan + Scholar + Stranger, N=3) clear Gate 9 with a strongly-specializing third agent? The lever
chosen was a **stronger-travel Stranger persona**. **Verdict: LEVER INERT (counterproductive).** The
persona reframe did not specialize the Stranger; it moved the targeted mechanism backward (travel
share 0.156 → 0.054, travel-obedience 0.314 → 0.114) and broke the Stranger's Layer-2 role stability.
R3 still FAILS Gate 9. The ADR 0015 PARTIAL roster-generality verdict stands.

**Date:** 2026-06-17

## Context

ADR 0015 read R3 as DOES NOT GENERALIZE; ADR 0016 confirmed the FAIL survives the size-invariant
`mean_pairwise_jsd` (0.207 < 0.25), so it is not a normalization artifact. The audit pinned the
residual cause on the **Stranger's weak travel-obedience (~0.31)**: Vesna fired the scarcity hint
~0.50 of the time (adequate dose) but obeyed `travel` only 0.31, vs the Scholar's 0.69 study-
obedience. ADR 0015 Decision 3(a) scoped a causal test: retest R3 with a third agent that specializes
strongly ("role/verb obeyed like study/craft"), to separate "the weak stranger" from "intrinsic
roster-size difficulty."

## Method

One fresh live read (seed 101 pilot, `gemma4:26b`, 3000 ticks), pilot-then-continue. The independent
variable is a default-off env toggle `MICROVERSE_STRANGER_PERSONA=travel` selecting
`persona_stranger_travel.j2`, which reframes the Stranger's identity around movement/the road —
**calibrated to the Scholar's identity-led study lean, with no verb-forcing rule**, and the cost table
**untouched** (so the test isolates obedience, not economics). The metric, the 0.25 floor (ADR 0016
`mean_pairwise_jsd`), Layer 2 with Vesna→`travel` as the positive control, the continuation rule, the
verdict mapping, and the (subsequently-falsified) prediction were committed before launch
(`docs/economy-phase2-r3-strong-specializer-runbook.md`). The toggle is default-off; unset keeps the
default persona, the Watchdog rehab path, and the frozen R2/dyad conditions byte-identical.

## Results (summary — full tables in the findings doc)

- **Instrument gate PASS** (fidelity 0.973, hint_on 0.949 over 1363 ev, hint_logged 0.9996): a valid
  behavioral read.
- **Layer 1 FAIL.** `mean_pairwise_jsd` 0.2073 → 0.2179 — up +0.011 but far below 0.25 and below the
  0.235 continuation bar. The small rise tracks Vesna's distribution becoming *more lopsided toward
  `contribute`*, not specialization.
- **The lever backfired.** Vesna's `travel` share fell 0.156 → 0.054 (below `speak` and `rest`) while
  `contribute` rose 0.720 → 0.787; travel-obedience fell 0.314 → 0.114 at unchanged-adequate firing.
  The wayfarer/road framing increased her drive to contribute road-stories into the workshop rather
  than take the `travel` action.
- **Layer 2 FAIL — worse than baseline.** The baseline R3 *held* Layer 2 for Vesna (travel was her
  top non-contribute verb, 0.156); under the travel persona her top non-contribute verb is now `speak`
  (0.086). Aki and Cy are unmoved, confirming the effect is the Stranger-persona IV.
- **Diagnostics flat.** `identity_verb_nmi` 0.190 → 0.199 (no gain in cross-agent
  distinguishability); `specialization_ratio` ≈ 1.0 unchanged.

## Decision

1. **The stronger-travel persona lever is rejected as a path to N=3 specialization.** It is
   counterproductive: identity prose that foregrounds the workshop/contribution affordance crowds out
   the literal specialty verb. Per the locked continuation rule (pilot `mean_pairwise_jsd` < 0.235),
   the sweep stopped at the pilot; seeds 202/303 were not run.
2. **The ADR 0015 R3 verdict is UNCHANGED.** R3 still fails Gate 9 (both layers). This retest does
   **not** convert the PARTIAL verdict, and it does **not** establish intrinsic roster-size difficulty
   — that conclusion would require a lever that *successfully* raised the Stranger's obedience yet
   still failed divergence. It establishes only that this lever is the wrong tool and that R3's
   contribute-dominance is sticky against identity prose.
3. **The committed prediction was falsified and is recorded as such.** The runbook predicted obedience
   would rise toward ~0.5; it fell to 0.11. Reported, not rationalized — the crowd-out mechanism was
   not anticipated.
4. **The why-does-N=3-fail frontier remains open**, re-scoped by this result toward levers that act on
   the *contribute* pull rather than the *travel* narrative: (a) economic — raise the Stranger's
   `contribute` cost specifically, or lower regen, so contribute is not the cheap default; (b) the
   deferred weights axis (ADR 0015 Decision 3(a)'s other named lever); (c) a persona that *de-*
   emphasizes workshop contribution for the Stranger rather than amplifying travel.

## Scope and limitations

- **Single-seed read.** The pre-registration authorized declining the full sweep on one sub-threshold
  pilot, and the effect is large and mechanistically coherent (travel more than halved, Layer 2
  broken). But a single seed cannot fully exclude seed-specific behavior; the verdict is "this lever
  is counterproductive at seed 101," not a 3-seed statistical claim. A confirmatory 202/303 pair was
  deliberately not spent, per the locked rule.
- The toggle and variant persona remain in the tree (default-off) for any future persona-lever work;
  the cost table and all gates are unchanged.
- This ADR resolves only Decision 3(a)'s persona branch. The weights axis and economic-suppression
  levers are untested.

## Reproduction

Findings: `docs/economy-phase2-r3-strong-specializer-findings.md`. Pre-registration:
`docs/economy-phase2-r3-strong-specializer-runbook.md`. Toggle + persona:
`src/microverse/config.py` (`_parse_stranger_persona`), `src/microverse/agents/stranger.py`,
`src/microverse/prompts/persona_stranger_travel.j2`. Runner: `scripts/run_r3_strong_sweep.sh`. Run
dir `data/econ-r3strong-s101`, baseline `data/econ-roster-r3-s101` (untracked, kept locally).
