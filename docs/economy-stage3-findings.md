# Action-economy Stage 3 findings (post-#45, ADR 0008 re-diagnosis)

Operator note recording the Stage 3 live A/B that feeds **ADR 0009**. The
ADR 0008 HALT stays in force; this records the read that informs it.

## Setup

- Knobs: `ENERGY_REGEN_PER_TICK = 8` (Stage 0/1 tune, #46), everything else default.
- Runner: `MICROVERSE_ECONOMY={mode} python -m microverse.run --ticks 3000 --tempo 0
  --seed {seed}`, model `gemma4:26b`, isolated `MICROVERSE_DATA`/`MICROVERSE_HARVEST`
  per run. Each run ~5.3–6.2 h / ~3500–3800 agent events.
- Modes: `0` (economy off / baseline), `sub` (substitution lever only, no scene gate —
  Stage 2 showed the scene gate inert, so it is dropped here), `flat` (role-agnostic
  substitution control: substitution pressure without comparative advantage).
- Planned seeds `{42, 38, 7}`; **stopped after seed 42** by operator decision (below).
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen**
  (`parsed_verb`) stream. Scene-forced contributes (ADR 0006) and parse-fallback rests are
  excluded as non-choices. **Gate 9 PASS = chosen `entropy_norm ≥ 0.35` AND
  chosen `jsd_norm ≥ 0.25`.** Primary read is `jsd_norm` (cross-agent divergence —
  ADR 0007's real target).

## Results (seed 42, 3000 ticks)

| mode   | chosen contribute share | chosen entropy_norm | chosen jsd_norm | econ_sub_rate | cxe   | Gate 9 PASS |
|--------|------------------------:|--------------------:|----------------:|--------------:|------:|:-----------:|
| `0`    | 88.1% (2229/2531)       | 0.2741              | 0.017           | 0.000         | 0.000 | no          |
| `sub`  | 77.3% (1975/2555)       | 0.4090              | 0.1088          | 0.2041        | 0.078 | no          |
| `flat` | 66.9% (1826/2730)       | 0.5738              | **0.1846**      | 0.1573        | 0.030 | no          |

`econ_sub_rate` = `economy_substitution_rate`; `cxe` = `chosen_vs_executed_divergence`
(small in every mode → the executor is not forcing the shift). Per-run elapsed: `0` 6.22 h,
`sub` 5.92 h, `flat` 5.27 h.

### Stage 2 (200 ticks) vs Stage 3 (3000 ticks), seed 42

| mode   | entropy 200t → 3000t | jsd 200t → 3000t | contribute 200t → 3000t |
|--------|:--------------------:|:----------------:|:-----------------------:|
| `0`    | 0.258 → 0.274        | 0.031 → 0.017    | 88.7% → 88.1%           |
| `sub`  | 0.534 → 0.409        | 0.222 → **0.109**| 66.7% → 77.3%           |
| `flat` | 0.642 → 0.574        | 0.168 → 0.185    | 60.7% → 66.9%           |

## Read

1. **The lever breaks the monoculture but never reaches the divergence floor.** Both lever
   modes clear the entropy floor (`sub` 0.409, `flat` 0.574 ≥ 0.35) and drop the chosen
   contribute share from 88% toward 67–77%. The shift is genuine model choice via the
   `energy_hint` channel, not executor forcing (`cxe` ≤ 0.078, `econ_sub` 0.16–0.20 below the
   total chosen shift). **But `jsd_norm` tops out at 0.185 (`flat`), a 26% shortfall on the
   0.25 floor.** No mode passes Gate 9.

2. **At scale the early divergence decays — agents co-drift, they do not specialize.** The
   Stage 2 200-tick `sub` read (jsd 0.222, near the floor) was small-sample optimism: at
   3000 ticks `sub` regresses (jsd 0.222 → 0.109, contribute 66.7% → 77.3%). The
   non-contribute mass piles onto the *same* alternatives for both agents (`sub` → study 412
   / rest 103; `flat` → speak 434 / rest 247 / study 170), which lifts society entropy while
   leaving cross-agent JSD low. More events make the divergence gap look *worse*, not better.

3. **`flat` ≥ `sub` on both axes at scale.** Comparative advantage (role-specific costs) is
   not the driver; flat substitution pressure diversifies at least as well and is more stable
   over the longer run. This is the opposite of what a "specialize into your cheap verb" story
   predicts, and is itself evidence that the agents are not using cost structure to differentiate.

## Decision: stop, HALT stays

**Stopped after seed 42** (3 of 9 runs). Rationale: the within-seed pattern is monotone and
internally consistent (entropy climbs, JSD plateaus far under floor), the mechanism dominates
RNG, and it corroborates the Stage 2 direction at scale. A second/third seed would refine the
variance estimate but is very unlikely to flip a 0.185 → 0.25 JSD verdict. Compute (~36 h for
the remaining 6 runs) was judged not worth that marginal rigor.

**Caveat:** this is a single seed at scale. ADR 0009 records the verdict with that explicit
limitation; seeds 38/7 were not run.

The action-economy lever is **necessary-ish but not sufficient**: it moves verb *diversity*
(refuting "only identity-independent structure, nothing the agent chooses, can move Gate 3")
but does not unlock cross-agent *specialization* (Gate 9 JSD — ADR 0007's actual target). The
ADR 0008 HALT stays in force. See `docs/adr/0009-action-economy-stage3-read.md`.
