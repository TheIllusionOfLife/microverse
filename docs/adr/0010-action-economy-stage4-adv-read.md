# ADR 0010: Action-economy Stage 4 — honest-hint (`adv`) verb-diversity A/B read (Gate 9)

**Status:** Accepted (measurement record) — records the falsifiable Stage 4 read that tests
the ADR 0009 follow-up intervention (the honest per-agent scarcity hint).
**Halt decision: HALT stays.** The honest hint robustly specializes the *artisan* but not the
*scholar*, so cross-agent specialization (Gate 9 JSD) does not emerge; the ADR 0007 substrate
thesis remains unproven.

**Date:** 2026-06-05

## Context

ADR 0009 (Stage 3) found the action economy breaks the `contribute` monoculture (society
entropy rises) but FAILS Gate 9 cross-agent specialization: the two residents co-drift onto
the *same* alternative verbs, jsd_norm topping out at 0.185. Root cause (verified in code):
Gate 9 reads the **chosen** (`parsed_verb`) stream, and the only economy→chosen channel is the
per-agent `energy_hint`, which named `cheapest_affordable_productive` — a selector that excludes
the payload verbs (`contribute`/`craft`). So the **artisan**, whose cheap specialty *is*
`craft`, was pointed at the shared payload-free escape `study` (the **scholar's** specialty),
producing co-drift instead of differentiation.

PR #52 shipped the fix as a new feature-flagged mode **`adv`**: the `energy_hint` names
`cheapest_affordable_perceived` (excludes only `rest`, so it may name a role's payload
specialty), while the blind executor's substitution target stays payload-free. `adv` is
otherwise identical to `sub`. This ADR records the deferred live A/B that tests whether the
honest hint clears Gate 9.

Gate 9 PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥ 0.25`. The headline is
`jsd_norm`.

## Method

- Runner: `MICROVERSE_ECONOMY={mode} uv run python -m microverse.run --ticks 3000 --tempo 0
  --seed {seed}` into isolated state dirs, model `gemma4:26b`. ~5.7–6.3 h / ~3760 agent events.
- Modes `{0, sub, adv}` × seeds `{42, 38, 7}`, all run in **one sweep** (concurrent comparators;
  `gemma4:26b` sampling is unseeded, so `sub`/`0` are paired with `adv`, not reused from Stage 3).
- Measurement: `scripts/spike_workshop_measure.py` `gate9_verb_diversity`, chosen stream;
  scene-forced contributes and parse-fallback rests excluded as non-choices.
- Quality gate (ADR 0009 R-mitigations): per-agent chosen-craft share, `artisan_empty_craft_
  coerced`, `parse_fallback`, and `novelty_energy_hint_conflict` recorded per run.

## Results (chosen stream)

| mode  | seed | entropy_norm | jsd_norm | aki_craft | cy_craft | conflict | Gate 9 |
|-------|------|-------------:|---------:|----------:|---------:|---------:|:------:|
| `0`   | 42   | 0.259        | 0.019    | 0.000     | 0.000    | 0        | FAIL   |
| `sub` | 42   | 0.426        | 0.119    | 0.024     | 0.014    | 5        | FAIL   |
| `adv` | 42   | 0.380        | 0.175    | 0.350     | 0.011    | 177      | FAIL   |
| `0`   | 38   | 0.257        | 0.010    | 0.000     | 0.000    | 0        | FAIL   |
| `sub` | 38   | 0.497        | 0.171    | 0.020     | 0.013    | 25       | FAIL   |
| `adv` | 38   | 0.378        | 0.168    | 0.336     | 0.011    | 51       | FAIL   |
| `0`   | 7    | 0.267        | 0.014    | 0.000     | 0.000    | 0        | FAIL   |
| `sub` | 7    | 0.456        | 0.119    | 0.027     | 0.009    | 5        | FAIL   |
| `adv` | 7    | 0.374        | 0.143    | 0.312     | 0.015    | 79       | FAIL   |

Means: jsd `0` 0.014 / `sub` 0.136 / `adv` 0.162. `adv` aki_craft 0.333, cy_craft 0.012.

`conflict` is the true `novelty_energy_hint_conflict` count (cumulative metric MAX). The
per-run progress log printed a SUM over the time-series snapshots (~1000× inflated); use MAX.

## Findings

1. **The honest hint robustly specializes the artisan.** Aki chosen-craft 0.00→0.024(sub)→
   **0.33(adv)**, stable across all three seeds. The ADR 0009 fix does exactly what it was
   designed to: an identity-independent change to the perception channel moves the artisan to
   its own cheap specialty. `sub` does not (Aki craft ~0.024), so the effect is hint-specific.

2. **It does not specialize the scholar — Gate 9 FAILS in every run.** Cy chosen-craft ~0.012
   everywhere; Cy stays contribute-dominated. Cross-agent `jsd_norm` lands a stable ~0.16
   (0.175/0.168/0.143), below the 0.25 floor at all three seeds. `adv` edges `sub` on the mean
   (0.162 vs 0.136) but within the seed-variance band and never near the floor.

3. **The move is specialization, not just diversity.** `adv` entropy (0.377) < `sub` (0.460)
   while `adv` cxe (~0.11) > `sub` (~0.06): `adv` concentrates each agent toward a profile
   rather than spreading both. That is the right qualitative direction — it just stops at one
   agent. One-sided specialization cannot clear a *cross-agent* divergence floor.

4. **R4 (novelty vs energy hint) is real but not the binding cap.** True conflict counts are
   higher under `adv` (51–177) than `sub` (5–25) — the novelty hint does fight craft once it
   dominates — but at ~50–180 ticks over ~3760 events it is modest. The binding constraint is
   the scholar's inertia, not the novelty/energy fight.

## Decision

**The honest hint is necessary and effective for the artisan but insufficient for the society.
HALT stays.**

ADR 0009 diagnosed the co-drift as the hint laundering both roles' specialties into the same
payload-free escape. The fix removes that for the artisan (robustly, across seeds), proving the
chosen stream *can* be moved toward a role's specialty by an identity-independent economy
change. But Gate 9 requires *both* agents to put mass on *different* verbs, and the scholar does
not move: its `contribute` is cheap (cost 14) so it rarely triggers the scarcity hint, and its
persona/sampling keep it on contribute even when hinted. Do not start Phase 2 on the strength of
the economy lever; the missing ingredient is a force that differentiates the *scholar* (and, more
generally, every non-artisan role), not merely the artisan.

## Limitations

- Three seeds at scale; the qualitative verdict (artisan specializes robustly, jsd misses the
  floor by a wide margin at every seed) is stable, but the numeric values are 3-point estimates.
- Two-resident roster (Aki/artisan, Cy/scholar). JSD across two agents is a coarse divergence
  estimate; a larger, more heterogeneous roster could change the dynamics and is untested.
- The spike deliberately did not tune persona prompts (ADR 0008 constraint carried forward); the
  scholar's contribute-inertia is therefore a property of the unmodified persona + cost table.

## Reproduction

```bash
# per mode in {0, sub, adv}, seed in {42, 38, 7}:
MICROVERSE_ECONOMY=$MODE \
MICROVERSE_DATA=data/econ-stage4-$MODE-s$SEED \
MICROVERSE_HARVEST=harvest/econ-stage4-$MODE-s$SEED \
  uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
uv run python scripts/spike_workshop_measure.py \
  --data data/econ-stage4-$MODE-s$SEED --harvest harvest/econ-stage4-$MODE-s$SEED
# read .gate_9_verb_diversity.chosen.{society_entropy_norm,jsd_norm} and .pass
```

Runbook: `docs/economy-stage4-runbook.md`. Findings: `docs/economy-stage4-findings.md`.
Mechanism + offline tests: PR #52. Stage 3 read: ADR 0009 / `docs/economy-stage3-findings.md`.
