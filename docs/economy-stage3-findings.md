# Action-economy Stage 3 findings (post-#45, ADR 0008 re-diagnosis)

Operator note recording the Stage 3 live A/B that feeds **ADR 0009**. The
ADR 0008 HALT stays in force; this records the read that informs it.

## Setup

- Knobs: `ENERGY_REGEN_PER_TICK = 8` (Stage 0/1 tune, #46), everything else default.
- Runner: `MICROVERSE_ECONOMY={mode} uv run python -m microverse.run --ticks 3000
  --tempo 0 --seed {seed}`, model `gemma4:26b`, isolated `MICROVERSE_DATA`/
  `MICROVERSE_HARVEST` per run. Each run ~5.3–6.2 h / ~3500–3800 agent events.
- Modes: `0` (economy off / baseline), `sub` (substitution lever only, no scene gate —
  Stage 2 showed the scene gate inert, so it is dropped here), `flat` (role-agnostic
  substitution control: substitution pressure without comparative advantage).
- Planned seeds `{42, 38, 7}`; **stopped after seed 42** by operator decision (below).
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen**
  (`parsed_verb`) stream. Scene-forced contributes (ADR 0006) and parse-fallback rests are
  excluded as non-choices. **Gate 9 PASS = chosen `entropy_norm ≥ 0.35` AND
  chosen `jsd_norm ≥ 0.25`.** Primary read is `jsd_norm` (cross-agent divergence —
  ADR 0007's real target).

> **Chosen stream, by mode.** `parsed_verb` is stamped only in substitution-enabled
> modes (`run.py:1155-1160`, gated on `_ECONOMY_SUBSTITUTE`). For mode `0` the gate9
> reader (`measure:231`) falls back to the *executed* verb, so the `0` arm's "chosen"
> stream is its executed stream. That is exact for the baseline, not an approximation:
> with the economy off there is no substitution (`econ_sub = 0`), so chosen ≡ executed by
> construction. For `sub`/`flat` the chosen stream is the model's pick **before** the
> executor may rewrite it.

## Results (seed 42, 3000 ticks)

| mode   | chosen contribute share | chosen entropy_norm | chosen jsd_norm | econ_sub_rate | cxe   | Gate 9 PASS |
|--------|------------------------:|--------------------:|----------------:|--------------:|------:|:-----------:|
| `0`    | 88.1% (2229/2531)       | 0.274               | 0.017           | 0.000         | 0.000 | no          |
| `sub`  | 77.3% (1975/2555)       | 0.409               | 0.109           | 0.204         | 0.078 | no          |
| `flat` | 66.9% (1826/2730)       | 0.574               | **0.185**       | 0.157         | 0.030 | no          |

`econ_sub_rate` = `economy_substitution_rate`, the **per-event** rate at which the executor
overrode the model's pick (`measure:222-224`). `cxe` = `chosen_vs_executed_divergence`, the
JSD between the **society-aggregated** chosen and executed verb distributions (`measure:248`) —
a society-level summary, not a per-event rewrite count. Per-run elapsed: `0` 6.22 h, `sub`
5.92 h, `flat` 5.27 h.

### Stage 2 (200 ticks) vs Stage 3 (3000 ticks), seed 42 — two independent runs

These are **separate live runs** at the same config and `--seed`, not two sample lengths of
one trajectory: `--seed` seeds only the Python RNGs (scheduler/weather/clock); the `gemma4:26b`
sampling is unseeded, so the runs diverge from tick 1. Read the pair as two independent draws at
different horizons, not a within-run time series.

| mode   | entropy 200t / 3000t | jsd 200t / 3000t | contribute 200t / 3000t |
|--------|:--------------------:|:----------------:|:-----------------------:|
| `0`    | 0.258 / 0.274        | 0.031 / 0.017    | 88.7% / 88.1%           |
| `sub`  | 0.534 / 0.409        | 0.222 / 0.109    | 66.7% / 77.3%           |
| `flat` | 0.642 / 0.574        | 0.168 / 0.185    | 60.7% / 66.9%           |

## Read

1. **The lever breaks the monoculture but never reaches the divergence floor.** Both lever
   modes clear the entropy floor (`sub` 0.409, `flat` 0.574 ≥ 0.35) and drop the chosen
   contribute share from 88% toward 67–77%. This is a shift in *chosen* verbs: the chosen stream
   is `parsed_verb`, the model's pick **before** the executor may rewrite it, so the entropy/JSD
   numbers reflect model choice by construction, not executor output. The per-event override rate
   (`econ_sub` 0.157–0.204) is real but well below the total chosen shift, and `cxe` (0.03–0.08)
   shows the override barely moves the society-level verb mix. **But `jsd_norm` tops out at 0.185
   (`flat`), a 26% shortfall on the 0.25 floor.** No mode passes Gate 9.

2. **The longer run does not converge to specialization; if anything divergence is lower.** The
   independent 3000-tick `sub` run lands at jsd 0.109 / contribute 77.3% vs the 200-tick run's
   0.222 / 66.7%. Two draws is far too few to call a trend, but the larger-sample run does not
   move *toward* the floor, and the non-contribute mass piles onto the *same* alternatives for
   both agents (`sub` → study 412 / rest 103; `flat` → speak 434 / rest 247 / study 170) — society
   entropy rises while cross-agent JSD stays low. The co-drift, not specialization, is what the
   extra events buy.

3. **`flat` ≥ `sub` on both axes.** Comparative advantage (role-specific costs) is not the
   driver; flat substitution pressure diversifies at least as well. This is the opposite of what a
   "specialize into your cheap verb" story predicts, and is itself evidence the agents are not
   using cost structure to differentiate.

## Decision: stop, HALT stays

**Stopped after seed 42** (3 of 9 runs). Rationale: the within-seed pattern is monotone and
internally consistent (entropy climbs, JSD plateaus far under floor), and it matches the Stage 2
direction. A second/third seed would add a cross-seed variance band but is very unlikely to flip
a 0.185 → 0.25 JSD verdict. Compute (~36 h for the remaining 6 runs) was judged not worth that
marginal rigor.

**Caveat:** this is a single seed at scale. ADR 0009 records the verdict with that explicit
limitation; seeds 38/7 were not run, so no cross-seed variance band exists.

The action-economy lever is **necessary-ish but not sufficient**: it moves verb *diversity*
(refuting "nothing the agent chooses can move Gate 3") but does not unlock cross-agent
*specialization* (Gate 9 JSD — ADR 0007's actual target). The ADR 0008 HALT stays in force. See
`docs/adr/0009-action-economy-stage3-read.md`.

## Metric validation (ADR 0009 follow-up)

Before spending more compute, we checked whether the Stage 3 failure is real or just a metric
that a 2-resident world cannot satisfy. It is real. Feeding synthetic per-agent distributions
through the production `gate9` reader (`_diversity_block`) gives a clean response curve
(`tests/test_gate9_verb_diversity.py`):

| 2-agent scenario | chosen jsd_norm | Gate 9 |
|---|---:|:--:|
| disjoint specialists (craft \| study) | 1.000 | PASS |
| distinct specialties, 40% shared `contribute` each | 0.600 | PASS |
| both keep `contribute` as mode, but disjoint *secondary* mass | 0.400 | PASS |
| one agent's modal verb relocated off `contribute` | 0.335 | PASS |
| both keep `contribute` as mode, one grows a long diverse tail | 0.212 | fail |
| **real `flat-s42` (co-drift)** | 0.185 | fail |
| identical agents | 0.000 | fail (correct) |

The harness reproduces the live `flat-s42` read (entropy 0.574 / jsd 0.185) exactly, so the
calibration is on the same code path as the gate. Two takeaways:

1. **The ruler is sound.** Gate 9 is comfortably satisfiable on two agents (jsd up to 1.0) and
   correctly scores identical agents at 0. The halt is a behavioral finding, not a measurement
   artifact.
2. **Sharper diagnosis than "co-drift."** Gate 9 compares the agents' *full* verb distributions;
   it passes when they put **different mass on different verbs** and fails when they cluster near
   the same corner. The real `flat-s42` agents are not symmetric — Cy stays ~93% `contribute`
   while Aki diversified to ~51% `contribute` + a large speak/rest/study tail — but both keep
   their plurality on `contribute` *and* their tails overlap, so JSD caps at 0.185. The synthetic
   contrast isolates the lever's gap: growing a tail while staying clustered near `contribute`
   fails (0.212), whereas divergence passes via either route — relocating an agent's modal verb
   off `contribute` (0.335) **or** keeping `contribute` modal for both but concentrating disjoint
   *secondary* mass (0.400). The Stage 3 lever did neither; it only thinned one agent's tail.
   The concrete, falsifiable target for any next intervention: make the two agents' distributions
   *diverge* (different verbs carrying their mass), not merely spread a shared one.
