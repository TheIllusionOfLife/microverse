# ADR 0009: Action-economy Stage 3 — verb-diversity A/B read (Gate 9)

**Status:** Accepted (measurement record) — records the falsifiable Stage 3 read that
re-tests the ADR 0008 "verb monoculture is structural" re-diagnosis.
**Halt decision: HALT stays.** The action economy moves verb *diversity* but not cross-agent
*specialization*; the ADR 0007 substrate thesis remains unproven.

**Date:** 2026-06-02

## Context

ADR 0008 halted ADR 0007 Phase 1 (durable identity moved neither Gate 1 nor Gate 3) and offered
a re-diagnosis: **Gate 3 (verb monoculture) is structural** — "the scene/workshop loop funnels
~95% of actions into `contribute`; no identity layer can diversify verbs without changing the
action economy itself" (ADR 0008, re-diagnosis #2). PR #45 built a feature-flagged
**action-economy spike** (finite per-agent stamina `EnergyLedger` + comparative-advantage verb
costs `VERB_COST_BY_ROLE`, gated by `MICROVERSE_ECONOMY`) to test that claim *falsifiably*. Its
operator runbook ran in three stages:

- **Stage 0/1** (offline, #46): tune the cost numbers until the lever actually throttles
  (`ENERGY_REGEN_PER_TICK 12 → 8`); confirm a draining policy is substituted while a sustainable
  specialty is not.
- **Stage 2** (cheap live, 200 ticks × 5 modes): the chosen (`parsed_verb`) stream genuinely
  shifts away from `contribute` under the substitution lever — escalate.
- **Stage 3** (this ADR): long live A/B for the real target, **Gate 9 cross-agent verb
  divergence**.

Gate 9 is the operational form of ADR 0007's "civilization" thesis: agents should not merely emit
varied verbs (society entropy) but settle into **distinct** verb profiles (cross-agent
Jensen-Shannon divergence). **PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥ 0.25`.**
The headline read is `jsd_norm`.

## Method

- Runner: `MICROVERSE_ECONOMY={mode} uv run python -m microverse.run --ticks 3000 --tempo 0
  --seed {seed}` into isolated `MICROVERSE_DATA`/`MICROVERSE_HARVEST` dirs. Model `gemma4:26b`
  (the configured `agent.think()` model). 3000 ticks so the run spans ~5–6 h / ~3500–3800 agent
  events.
- Knobs: Stage 0/1 tune `ENERGY_REGEN_PER_TICK = 8`, all else default. `ENERGY_MAX = 100`,
  `VERB_COST_BY_ROLE` unchanged (preserves cross-mode cost parity + the strict-specialty
  invariant).
- Modes: `0` (economy off, baseline), `sub` (substitution lever only — Stage 2 showed the
  scene-initiation gate inert, so it is dropped), `flat` (role-agnostic substitution control).
- Measurement: `scripts/spike_workshop_measure.py` `gate9_verb_diversity` on the **chosen**
  stream; scene-forced contributes (ADR 0006) and parse-fallback rests excluded as non-choices.
  `parsed_verb` is stamped only in substitution-enabled modes (`run.py:1155-1160`), so for the
  `0` arm the reader falls back to the *executed* verb (`measure:231`). That is exact, not an
  approximation: with the economy off there is no substitution, so chosen ≡ executed for the
  baseline; for `sub`/`flat` the chosen stream is the model's pick **before** the executor may
  rewrite it.
- **Planned seeds `{42, 38, 7}`; the sweep was stopped after seed 42** (operator decision —
  see Limitations).

## Results (seed 42, 3000 ticks)

| mode   | chosen contribute share | chosen entropy_norm | chosen jsd_norm | econ_sub_rate | cxe   | Gate 9 |
|--------|------------------------:|--------------------:|----------------:|--------------:|------:|:------:|
| `0`    | 88.1% (2229/2531)       | 0.274               | 0.017           | 0.000         | 0.000 | FAIL   |
| `sub`  | 77.3% (1975/2555)       | 0.409               | 0.109           | 0.204         | 0.078 | FAIL   |
| `flat` | 66.9% (1826/2730)       | 0.574               | **0.185**       | 0.157         | 0.030 | FAIL   |

`econ_sub_rate` = `economy_substitution_rate`, the **per-event** executor-override rate. `cxe` =
`chosen_vs_executed_divergence`, the JSD between the **society-aggregated** chosen and executed
distributions (`measure:248`) — a society-level summary, not a per-event rewrite count.

### Scale comparison vs Stage 2 (200 ticks, same seed) — two independent runs

The 200-tick and 3000-tick numbers come from **separate live runs**, not two sample lengths of
one trajectory. `--seed` seeds only the Python RNGs (scheduler/weather/clock, `run.py:695,786`);
the `gemma4:26b` sampling is unseeded, so the runs diverge from tick 1. Read the pair as two
independent draws at different horizons.

| mode   | entropy 200t / 3000t | jsd 200t / 3000t | contribute 200t / 3000t |
|--------|:--------------------:|:----------------:|:-----------------------:|
| `0`    | 0.258 / 0.274        | 0.031 / 0.017    | 88.7% / 88.1%           |
| `sub`  | 0.534 / 0.409        | 0.222 / 0.109    | 66.7% / 77.3%           |
| `flat` | 0.642 / 0.574        | 0.168 / 0.185    | 60.7% / 66.9%           |

## Findings

1. **The lever moves verb diversity — ADR 0008's re-diagnosis is half right.** Both lever modes
   clear the entropy floor and cut the chosen contribute share from 88% to 67–77%. The chosen
   stream is `parsed_verb`, the model's pick **before** the executor may rewrite it, so the
   entropy/JSD numbers are model choice by construction; the per-event override rate (`econ_sub`
   0.157–0.204) is real but below the total chosen shift, and `cxe` (0.03–0.08) shows the override
   barely moves the society-level mix. The monoculture is *not* immovable: an
   identity-independent change to the action economy does diversify chosen verbs.

2. **But it does not unlock cross-agent specialization — Gate 9 FAILS in every mode.** `jsd_norm`
   tops out at 0.185 (`flat`), 26% under the 0.25 floor. Agents reduce the monoculture by
   co-drifting toward the *same* alternatives (`sub` → study/rest; `flat` → speak/rest/study)
   rather than splitting into distinct profiles. Cross-agent divergence — the actual ADR 0007
   target — does not emerge.

3. **The longer run does not move toward the floor.** The independent 3000-tick `sub` run lands
   at jsd 0.109 / contribute 77.3% vs the 200-tick run's 0.222 / 66.7%. Two draws is too few to
   assert a trend, but the larger-sample run is no closer to passing, so the near-floor Stage 2
   `sub` read does not survive as evidence that more events produce specialization.

4. **Comparative advantage is not the driver.** `flat` (role-agnostic) matches or beats `sub`
   (role-specific costs) on both entropy and JSD. The agents are not exploiting cost structure to
   differentiate; raw substitution pressure, not comparative advantage, is what little movement
   there is.

## Decision

**The action economy is necessary-ish but not sufficient for the ADR 0007 thesis. HALT stays.**

ADR 0008 framed verb monoculture as the structural blocker and the action economy as the
unlock. Stage 3 shows the action economy unlocks *diversity* (society entropy) but **not
specialization** (cross-agent JSD), and specialization is what "from workshop to civilization"
actually requires. The JSD shortfall is wide (0.185 vs 0.25) and does not close in the longer
run; the missing ingredient is a force that makes agents differentiate from each other, not
merely vary their own output. That is a deeper design question than the spike was built to
answer, and it stays open. Do not start Phase 2 on the strength of the economy lever.

## Limitations

- **Single seed at scale.** Seeds 38 and 7 were planned but not run; the sweep was stopped after
  seed 42 once the within-seed signal was unambiguous and consistent with Stage 2. The numeric
  JSD/entropy values are therefore one-seed point estimates with no cross-seed variance band. The
  *qualitative* verdict (entropy floor cleared, JSD floor missed by a wide margin that does not
  close in the longer run) is robust to plausible seed variance; a specific mode's borderline pass
  on a lucky seed would not constitute a robust Gate 9 PASS.
- **`cxe` is a society-level summary.** A low `chosen_vs_executed_divergence` means the
  *aggregate* chosen and executed verb mixes are close; it does not bound the per-event rewrite
  count (that is `economy_substitution_rate`, 0.16–0.20 here). The "model choice, not executor
  forcing" claim rests on the chosen stream being pre-substitution `parsed_verb`, not on `cxe`.
- Two-resident roster (Aki/artisan, Cy/scholar). JSD across two agents is a coarse divergence
  estimate; a larger roster could change the specialization dynamics and is untested here.
- The spike deliberately did not tune prompts to manufacture movement (ADR 0008 constraint
  carried forward).

## Reproduction

```bash
# per mode in {0, sub, flat}, seed in {42, 38, 7}:
MICROVERSE_ECONOMY=$MODE \
MICROVERSE_DATA=data/econ-stage3-$MODE-s$SEED \
MICROVERSE_HARVEST=harvest/econ-stage3-$MODE-s$SEED \
  uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
uv run python scripts/spike_workshop_measure.py \
  --data data/econ-stage3-$MODE-s$SEED --harvest harvest/econ-stage3-$MODE-s$SEED
# read .gate_9_verb_diversity.chosen.{society_entropy_norm,jsd_norm} and .pass
```

Offline knob sweep (zero LLM compute): `scripts/replay_economy.py --synthetic --regen 8`.
Stage 2 read: `docs/economy-stage2-findings.md`. Stage 3 read: `docs/economy-stage3-findings.md`.
