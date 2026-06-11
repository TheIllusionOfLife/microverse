# ADR 0012: Action-economy Stage 6 — balanced contribute target tune-to-clear (`bal@30`) PASSES Gate 9

**Status:** Accepted (measurement record) — records the falsifiable Stage 6 read that tests the
ADR 0011 follow-up intervention (raising the balanced `contribute` target to drain the
lower-weight scholar harder so its scarcity hint fires often enough to push it off contribute).
**Halt decision: HALT LIFTED.** `bal@30` PASSES Gate 9 at all three seeds against the locked
four-condition pre-registration. This is the first PASS in the action-economy arc; the ADR 0007
substrate thesis has its first confirming live read and **Phase 2 unblocks.**

**Date:** 2026-06-11

## Context

ADR 0011 (Stage 5) found that the balanced cost table (`bal`) robustly specializes *both*
residents for the first time — the scholar (Cy) moved off its `contribute` monoculture — but
cross-agent `jsd_norm` landed a stable ~0.237, ~0.013 under the 0.25 Gate 9 floor, held there by a
residual ~0.77 shared `contribute` floor. ADR 0011 named the single pre-identified residual cause
**R2**: at the natural balanced contribute cost (22) the lower-weight scholar (`soul_tokens=70` vs
Aki 100, scheduled ~41% of ticks) regenerates ~19 energy between its own turns, so net drain is
only ~−2.6; its `contribute` stays affordable, its scarcity hint rarely fires, and it keeps
choosing `contribute`. ADR 0011 left a genuine operator fork: tune-to-clear R2, or park the arc.

PR #55 shipped the lever for the tune: `ECONOMY_BALANCED_CONTRIBUTE` (env `MICROVERSE_BAL_CONTRIBUTE`),
a raise-only knob on `derive_balanced_table`'s contribute target (a target below the natural
dearest raises `ValueError`, never silently clamps). The target **T\* = 30** was selected OFFLINE
from a pre-registered mechanical rule on `replay_economy.py` (the smallest grid target with
contribute-out/study-ok rate ≥ 0.55 at all three seeds and rest-only ≤ 0.05), modelling only the
executor — with zero knowledge of the live jsd it would produce. The offline probe measured the
scarcity-hint *firing* rate (~0.24 at 22 → ~0.56 at 30); whether `gemma4:26b` would *obey* the
louder hint (R1) was the live unknown this stage resolves.

Gate 9 PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥ 0.25`. The headline is `jsd_norm`.

## Method

- Three arms `{adv, bal@22, bal@30}` × seeds `{42, 38, 7}`, model `gemma4:26b`, isolated state
  dirs, ~6.3 h / ~4580 agent events each, one sweep (seed-outer / arm-inner), run detached
  2026-06-09 → 2026-06-11:

  ```bash
  MICROVERSE_ECONOMY=$MODE MICROVERSE_BAL_CONTRIBUTE=$BAL \
    uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
  ```
- `bal@22` is the **in-sweep control**: `gemma4:26b` sampling is unseeded, so the only causally
  clean baseline for the raised target is a `bal` run at the natural cost paired in the same sweep.
  `adv` reproduces the one-sided Stage-4/5 mechanism.
- Measurement: `scripts/spike_workshop_measure.py` `gate9_verb_diversity`, chosen
  (`parsed_verb`) stream; scene-forced contributes and parse-fallback rests excluded as non-choices.
- Pre-registration locked before any live read: `docs/economy-stage6-runbook.md` (target, matrix,
  pass rule, risks). The four-condition pass rule is reproduced under Decision.

## Results (chosen stream)

| arm     | seed | entropy_norm | jsd_norm  | aki_craft | cy_study | cy_contrib | conflict | scene | Gate 9 |
|---------|------|-------------:|----------:|----------:|---------:|-----------:|---------:|------:|:------:|
| `adv`   | 42   | 0.377        | 0.150     | 0.219     | 0.028    | 0.946      | 80       | 338   | FAIL   |
| `bal@22`| 42   | 0.550        | 0.252     | 0.270     | 0.172    | 0.776      | 440      | 341   | ctrl   |
| `bal@30`| 42   | 0.628        | **0.307** | 0.271     | 0.260    | 0.674      | 703      | 335   | **PASS** |
| `adv`   | 38   | 0.347        | 0.160     | 0.216     | 0.015    | 0.961      | 65       | 362   | FAIL   |
| `bal@22`| 38   | 0.525        | 0.240     | 0.251     | 0.164    | 0.786      | 425      | 344   | ctrl   |
| `bal@30`| 38   | 0.623        | **0.344** | 0.301     | 0.258    | 0.683      | 681      | 332   | **PASS** |
| `adv`   | 7    | 0.363        | 0.175     | 0.246     | 0.016    | 0.963      | 63       | 353   | FAIL   |
| `bal@22`| 7    | 0.539        | 0.220     | 0.256     | 0.145    | 0.785      | 405      | 322   | ctrl   |
| `bal@30`| 7    | 0.630        | **0.376** | 0.306     | 0.289    | 0.663      | 685      | 320   | **PASS** |

Means: jsd `adv`/`bal@22`/`bal@30` 0.162 / 0.237 / **0.342**; `cy_contrib` 0.957 / 0.782 /
**0.673**; `cy_study` 0.020 / 0.160 / **0.269**; entropy 0.362 / 0.538 / **0.627**.

Per-agent non-contribute mass under `bal@30` is **disjoint**: Aki ~98% craft (study 0.010–0.018),
Cy ~90% study (craft 0.010–0.017). Each resident retreats to its OWN specialty.

## Findings

1. **Gate 9 PASSES at every seed with margin.** jsd 0.307 / 0.344 / 0.376 (worst-case +0.057 over
   the floor), entropy 0.623–0.630. A clean pass, not a boundary read like Stage 5.

2. **The pass is real cross-agent divergence.** Aki and Cy hold disjoint specialties (craft vs
   study) with near-zero cross-leakage over a reduced shared contribute floor. jsd crossing 0.25 is
   driven by both agents occupying *different* verbs, which is precisely what a cross-agent gate
   should reward.

3. **Monotone dose-response confirms causation, refutes threshold-fitting.** 22 → 30 moves Cy
   contribute 0.782 → 0.673, study 0.160 → 0.269, jsd 0.237 → 0.342, monotone at every seed. T\*
   was fixed offline from the executor model with no sight of live jsd, so this is confirmatory of
   R2, not a goalpost move. **R1 is answered: the model obeys the louder hint** (the chosen stream
   moves, not only the executed one).

4. **Controls behave.** `adv` reproduces one-sided specialization (jsd 0.150/0.160/0.175, Cy
   contribute ~0.95); `bal@22` straddles 0.25 (0.252/0.240/0.220), confirming the metric is
   noise-dominated at the threshold — which is why the verdict rests on lever attribution
   (conditions 2–4), not any single boundary run's pass/fail.

5. **R3 does not fire; conflict rises but is non-binding.** `scene_completed` under `bal@30`
   (320–335) is comparable to `bal@22` (322–344): no scene collapse. `novelty_energy_hint_conflict`
   rose to ~681–703 (vs `bal@22` ~405–440) as predicted, yet specialization still happened — a
   recorded cost of harder draining, not a cap.

## Decision

**`bal@30` PASSES Gate 9 at all three seeds against the locked four-condition rule. The HALT is
lifted and Phase 2 unblocks.**

Pass rule (locked before the read, met in full): at all three seeds (1) `entropy_norm ≥ 0.35` AND
`jsd_norm ≥ 0.25`; AND attributed to the lever vs in-sweep `bal@22` — (2) Cy contribute drop ≥ 0.10
(−0.102 / −0.103 / −0.122), (3) study is Cy's top non-contribute verb (0.260/0.258/0.289 vs next
verb ~0.03), (4) mean Cy study rise ≥ 0.05 (mean +0.109). No condition relaxed; T not re-picked.

This is the first falsifiable confirming read for the ADR 0007 substrate thesis: an
identity-independent economy change, with personas untouched (ADR 0008), drives two residents onto
distinct role specialties past the cross-agent divergence floor.

## Scope and limitations (what this does NOT prove)

The claim is bounded and should be carried forward as such:

- **Two-resident roster (Aki/artisan, Cy/scholar) only.** Two-agent JSD is a coarse divergence
  estimate; generality to larger or differently-weighted rosters is untested.
- **Hint-mediated, not a mechanical filter.** The sole economy → chosen-verb channel is the
  per-agent persona scarcity hint. The result is "a hint-mediated action economy shifts *chosen*
  verbs toward specialties," not "energy costs force actions." A different base model may obey
  differently.
- **T\* = 30 was target-selected** (offline mechanical rule, raise-only). The pass is confirmatory
  at this roster/model, not evidence that 30 is universal.
- **Single model, three seeds, unseeded sampling.** The in-sweep `bal@22` control is one paired
  comparator per seed, not many repeated live runs.
- **Tightest attribution margin:** condition 2 clears narrowly at s42/s38 (−0.102 / −0.103 vs the
  0.10 floor). The headline gate is robust; the attribution check is the fragile part. Recorded so
  a future reviewer does not mistake the margin for slack.

## Next actions (Phase 2)

1. **Freeze this ADR + findings as the confirming artifact** — exact commands, seeds, model,
   parser version, metric definitions, raw 9-run table, pass rule, artifact paths (done here).
2. **Mechanism audit** (cheap, offline on existing data): Cy energy traces, scarcity-hint firing
   rate at 22 vs 30, chosen verb conditional on hint present/absent, Aki top-verb stability — to
   show the hint is the operative channel, not a coincidence.
3. **Held-out replication** with T fixed at 30: fresh seeds plus multiple unseeded live repeats per
   condition, to move from "first locked pass" to "stable under replication."
4. **Roster generality**: more residents, alternate schedule weights, at least one different
   scholar/artisan pairing — the limitation most likely to bound the claim.
5. **Automate Gate 9 reporting** from frozen artifacts so future reads are not hand-reconstructed.

The honest framing: this is the **first locked live PASS under a tuned, hint-mediated two-agent
setup** — credible and pre-registered, but to be replicated and generalized in Phase 2 before it is
treated as settled general evidence.

## Limitations carried from prior stages

- Three seeds at scale; the qualitative verdict is stable but numeric values are 3-point estimates.
- Persona prompts were not tuned (ADR 0008 constraint carried forward); the result is a property of
  the unmodified personas + the balanced cost table at contribute = 30.

## Reproduction

```bash
# per arm in {adv, bal22, bal30}, seed in {42, 38, 7}:
#   adv -> MODE=adv BAL="" ; bal22 -> MODE=bal BAL="" ; bal30 -> MODE=bal BAL=30
MICROVERSE_ECONOMY=$MODE MICROVERSE_BAL_CONTRIBUTE=$BAL \
MICROVERSE_DATA=data/econ-stage6-$ARM-s$SEED \
MICROVERSE_HARVEST=harvest/econ-stage6-$ARM-s$SEED \
  uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
uv run python scripts/spike_workshop_measure.py \
  --data data/econ-stage6-$ARM-s$SEED --harvest harvest/econ-stage6-$ARM-s$SEED
# read .gate_9_verb_diversity.chosen.{society_entropy_norm,jsd_norm} and .pass
```

Pre-registration: `docs/economy-stage6-runbook.md`. Findings: `docs/economy-stage6-findings.md`.
Lever + offline tests: PR #55. Stage 5 read: ADR 0011 / `docs/economy-stage5-findings.md`.
