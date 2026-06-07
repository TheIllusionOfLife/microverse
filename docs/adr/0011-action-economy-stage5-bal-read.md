# ADR 0011: Action-economy Stage 5 — balanced-contribute (`bal`) verb-diversity A/B read (Gate 9)

**Status:** Accepted (measurement record) — records the falsifiable Stage 5 read that tests
the ADR 0010 follow-up intervention (the balanced `contribute` cost that makes the scholar's
contribute dear so its scarcity hint fires).
**Halt decision: HALT stays.** `bal` robustly specializes the *scholar* (the missing half of
Stage 4) at all three seeds, but cross-agent `jsd_norm` lands a stable ~0.237, ~0.013 short of
the 0.25 Gate 9 floor. No PASS, so the ADR 0007 substrate thesis remains unproven. The miss is
small, consistent, and has a single pre-identified cause (R2); see the decision below for the
one remaining well-motivated lever vs. park.

**Date:** 2026-06-08

## Context

ADR 0010 (Stage 4) found the honest hint (`adv`) robustly specializes the **artisan** (Aki
chosen-craft → ~0.33) but leaves the **scholar** a ~0.92–0.96 `contribute` monoculture (Cy
chosen-study flat at ~0.03). Root cause: the scholar's `contribute` costs only 14, so it is
almost always affordable and the scarcity hint never fires for Cy. Cross-agent `jsd_norm`
therefore topped out at a stable ~0.16 — one-sided specialization, which cannot clear a
*cross-agent* divergence floor.

PR #54 shipped the fix as a new feature-flagged mode **`bal`**: `derive_balanced_table` raises
every role's `contribute` to the dearest in the table (the artisan's 22) while leaving each
role's cheap specialty untouched. A contribute-heavy scholar now drains, so its (already
honest, `adv`) hint fires and names `study`, and the executor substitutes its free contributes
toward `study`. `bal` keeps the `adv` hint selector, so the artisan still specializes into
`craft`. `bal` vs `adv` isolates exactly one variable: the scholar's contribute cost.

Gate 9 PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥ 0.25`. The headline is
`jsd_norm`. Pre-registered before reading (runbook): pass iff Cy's chosen-study share rises
materially under `bal` (vs ~0.03 under `adv`) AND its chosen-contribute share falls.

## Method

- Runner (per mode `{0, adv, bal}` × seed `{42, 38, 7}`) into isolated state dirs, model
  `gemma4:26b`, ~6.3 h / ~3760 agent events each:

  ```bash
  MICROVERSE_ECONOMY=$MODE uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
  ```
- All run in **one sweep** (concurrent comparators; `gemma4:26b` sampling is unseeded, so
  `adv`/`0` are paired with `bal`, not reused from Stage 4 — the `adv`/`0` point values differ
  from ADR 0010 while the qualitative verdict reproduces, see Findings 4).
- Measurement: `scripts/spike_workshop_measure.py` `gate9_verb_diversity`, chosen stream;
  scene-forced contributes and parse-fallback rests excluded as non-choices.
- Quality gate (per-agent OWN specialty): `aki_craft`, `cy_study`, `cy_contrib`, and
  `novelty_energy_hint_conflict` (cumulative-metric MAX, not SUM) recorded per run.

## Results (chosen stream)

`aki_craft` = Aki's chosen share of the artisan specialty (craft); `cy_study` = Cy's chosen
share of the scholar specialty (study); `cy_contrib` = Cy's chosen contribute share. Mode `0`
shares are executed (`parsed_verb` unstamped with the economy off; chosen ≡ executed when
nothing substitutes). `econ_sub` = economy substitution rate.

| mode  | seed | entropy_norm | jsd_norm | aki_craft | cy_study | cy_contrib | conflict | econ_sub | Gate 9 |
|-------|------|-------------:|---------:|----------:|---------:|-----------:|---------:|---------:|:------:|
| `0`   | 42   | 0.253        | 0.014    | 0.028     | 0.012    | 0.930      | 0        | 0.000    | FAIL   |
| `adv` | 42   | 0.381        | 0.188    | 0.258     | 0.021    | 0.960      | 312      | 0.176    | FAIL   |
| `bal` | 42   | 0.544        | 0.235    | 0.258     | 0.168    | 0.770      | 432      | 0.233    | FAIL   |
| `0`   | 38   | 0.256        | 0.016    | 0.023     | 0.020    | 0.923      | 0        | 0.000    | FAIL   |
| `adv` | 38   | 0.358        | 0.169    | 0.230     | 0.011    | 0.966      | 124      | 0.214    | FAIL   |
| `bal` | 38   | 0.543        | 0.244    | 0.239     | 0.170    | 0.772      | 431      | 0.259    | FAIL   |
| `0`   | 7    | 0.253        | 0.012    | 0.021     | 0.014    | 0.923      | 0        | 0.000    | FAIL   |
| `adv` | 7    | 0.357        | 0.157    | 0.220     | 0.018    | 0.961      | 73       | 0.206    | FAIL   |
| `bal` | 7    | 0.544        | 0.232    | 0.258     | 0.170    | 0.773      | 424      | 0.249    | FAIL   |

Means: jsd `0`/`adv`/`bal` 0.014 / 0.171 / **0.237**. `cy_study` 0.015 / 0.017 / **0.169**;
`cy_contrib` 0.925 / 0.962 / **0.772**; `aki_craft` 0.024 / 0.236 / 0.252; entropy 0.254 /
0.365 / **0.544**.

## Findings

1. **`bal` robustly specializes the scholar — the missing half of Stage 4.** Cy chosen-study
   ~0.017(adv)→ **~0.169(bal)** and chosen-contribute ~0.962(adv)→ **~0.772(bal)**, near-identical
   at all three seeds (study 0.168/0.170/0.170; contribute 0.770/0.772/0.773). The pre-registered
   mechanism criterion (scholar study rises materially AND contribute falls) **passes cleanly and
   reproducibly**. The lever does exactly what ADR 0010 predicted; the scholar's inertia named as
   the Stage-4 binding constraint *was* breakable by making its contribute dear.

2. **Gate 9 still FAILS at every seed — but only on `jsd`, and only just.** Cross-agent
   `jsd_norm` is 0.235 / 0.244 / 0.232 (mean **0.237**), a stable ~0.013 below the 0.25 floor.
   Entropy clears the 0.35 floor easily and consistently (0.544). Unlike Stage 4 (which missed by
   a wide ~0.09 margin with a flat scholar), Stage 5 misses by a hair with *both* agents
   specialized. This is a near-pass, not a structural failure.

3. **The world is now genuinely two-profile.** `bal` lifts entropy to 0.544 (vs `adv` 0.365,
   `0` 0.254) with both agents holding distinct specialties (Aki craft ~0.25, Cy study ~0.17)
   over a *shared, reduced* contribute floor (~0.77). The residual contribute mass is the binding
   term: with each agent still ~0.77 contribute, the distributions overlap too much for jsd to
   clear 0.25. The lever moved the scholar but did not move it *far enough*.

4. **The `adv`/`0` comparators reproduce Stage 4 qualitatively.** Fresh unseeded `adv` runs give
   jsd 0.188/0.169/0.157 (Stage-4 was 0.175/0.168/0.143) with the scholar stuck on contribute
   (~0.96) — the artisan-only specialization reproduces even though point values drift. This
   confirms the `bal` effect (Finding 1) is causal to the balanced cost, not a sweep artifact.

5. **R4 (novelty vs energy hint) rises but is still not the binding cap.** Conflict counts climb
   to ~424–432 under `bal` (vs `adv` 73–312) — expected, since both agents are now pushed off
   their dominant verbs so novelty fights both — but at ~430 over ~3760 events (~11%) it is not
   what holds jsd under 0.25. The binding term is residual shared contribute (Finding 3), not the
   hint fight. R2, not R4, is the lever.

## Decision

**`bal` is necessary and effective for the scholar — the first time both agents specialize — but
the society lands a near-miss. HALT stays (no PASS).**

Stage 4 left the scholar as the missing ingredient; `bal` supplies it, robustly and reproducibly.
That is a real, falsifiable advance: an identity-independent economy change moved *both* roles to
their own specialties. But Gate 9 requires the two distributions to diverge past 0.25, and a
residual ~0.77 shared contribute floor holds the mean at 0.237. Per the Stage-5 pre-registration,
a failed `bal` would mean "the economy approach is exhausted, park the arc." That framing assumed
`bal` would fail *because the scholar wouldn't move*. It did move — so the strict "exhausted"
conclusion does not cleanly apply, and the honest read is a near-pass with a single,
pre-identified residual cause:

- **R2 (pre-registered in the runbook): cost 22 does not drain the lower-weight scholar enough.**
  Cy has `soul_tokens=70` (vs Aki 100), is scheduled less often, and regenerates more between
  actions, so balanced contribute = 22 net-drains it only to ~0.77, not lower. The runbook already
  flagged the fix: make `derive_balanced_table`'s target a config knob and raise it above 22 to
  drain the scholar harder → lower `cy_contrib` → higher `jsd`. This is one well-motivated tune,
  not a new mechanism, and the Stage-5 data (near-identical 3-seed near-miss with a known residual
  term) makes it the clearest remaining path to a PASS.

**This is a genuine fork for the operator, not a unilateral call**, because (a) it is live-compute
spend (~6 runs × ~6 h) and (b) chasing a 0.013 gap risks goalpost-tuning the threshold. The two
honest options:

- **Tune-to-clear (R2):** raise balanced contribute (e.g. 26–30), re-run `{adv, bal}` × `{42, 38,
  7}`. If `cy_contrib` falls below ~0.6 and jsd crosses 0.25 at all three seeds, Gate 9 PASSes and
  Phase 2 is unblocked. Pre-register the new target and pass rule before reading.
- **Park the arc:** treat the consistent ~0.237 near-miss as the economy lever's ceiling on a
  two-resident roster and pursue a different mechanism (e.g. a larger heterogeneous roster, where
  the two-agent JSD coarseness noted in Limitations may itself be the constraint).

Recommended: one R2 tune-to-clear run, because the residual cause is specific, pre-identified, and
mechanical, and the world is already two-profile — but the call is the operator's.

## Limitations

- Three seeds at scale; the qualitative verdict (both agents specialize robustly; jsd misses the
  floor by a hair at every seed) is stable, but the numeric values are 3-point estimates.
- Two-resident roster (Aki/artisan, Cy/scholar). JSD across two agents is a coarse divergence
  estimate; a residual shared-contribute floor caps it. A larger, more heterogeneous roster could
  change the dynamics and is untested.
- The spike deliberately did not tune persona prompts (ADR 0008 constraint carried forward); the
  scholar's residual contribute floor is a property of the unmodified persona + balanced cost
  table at contribute = 22.

## Reproduction

```bash
# per mode in {0, adv, bal}, seed in {42, 38, 7}:
MICROVERSE_ECONOMY=$MODE \
MICROVERSE_DATA=data/econ-stage5-$MODE-s$SEED \
MICROVERSE_HARVEST=harvest/econ-stage5-$MODE-s$SEED \
  uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
uv run python scripts/spike_workshop_measure.py \
  --data data/econ-stage5-$MODE-s$SEED --harvest harvest/econ-stage5-$MODE-s$SEED
# read .gate_9_verb_diversity.chosen.{society_entropy_norm,jsd_norm} and .pass
```

Runbook: `docs/economy-stage5-runbook.md`. Findings: `docs/economy-stage5-findings.md`.
Mechanism + offline tests: PR #54. Stage 4 read: ADR 0010 / `docs/economy-stage4-findings.md`.
