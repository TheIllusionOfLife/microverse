# Action-economy Stage 2 findings (post-#45, re-diagnosis of the ADR 0008 halt)

Short operator note (not an ADR). Records the Stage 2 read that gates Stage 3.
ADR 0009 is still written *after* Stage 3, per the #45 plan. The ADR 0008 HALT
stays in force.

## Setup

- Knobs: `ENERGY_REGEN_PER_TICK = 8` (Stage 0/1 tune, this PR), everything else default.
- Runs: `--ticks 200 --tempo 0 --seed 42`, model `gemma4:26b`, one run per mode.
- Five modes: `0` (economy off / baseline), `1` (scene-gate + comparative-advantage
  substitution), `flat` (role-agnostic control — substitution without comparative
  advantage), `throttle` (scene-gate only, no substitution), `sub` (substitution only,
  no scene gate).
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`). The **chosen**
  (`parsed_verb`) stream is the headline; scene-forced contributes (ADR 0006) and
  parse-fallback rests are excluded as non-choices. Single seed, ~165–182 free-choice
  events/mode — directional, not definitive.

## Results

| mode     | chosen contribute share | chosen entropy_norm | chosen JSD_norm | econ_sub_rate | Gate 9 PASS |
|----------|------------------------:|--------------------:|----------------:|--------------:|:-----------:|
| 0        | 88.7% (149/168)         | 0.258               | 0.031           | 0.000         | no          |
| 1        | 78.4% (138/176)         | 0.405               | 0.074           | 0.210         | no          |
| flat     | 60.7% (108/178)         | 0.642               | 0.168           | 0.124         | no          |
| throttle | 89.6% (163/182)         | 0.250               | 0.013           | 0.000         | no          |
| sub      | 66.7% (110/165)         | 0.534               | 0.222           | 0.133         | no          |

Gate 9 PASS = chosen `entropy_norm ≥ 0.35` **AND** chosen `jsd_norm ≥ 0.25`.
`chosen_vs_executed_divergence` stayed small (0.03–0.07) in every mode.

## Read

1. **Chosen verbs do shift away from `contribute`** under every substitution-bearing
   mode (`1`, `flat`, `sub`): the free-choice contribute share drops from 88.7% (baseline)
   to 60.7–78.4%, and chosen entropy roughly doubles. Because
   `chosen_vs_executed_divergence` is tiny and `economy_substitution_rate` (0.12–0.21) is
   *below* the total chosen shift, this is the model genuinely **choosing** other verbs via
   the `energy_hint` perception channel — not the executor forcing them. This directly
   contradicts the ADR 0008 framing that "no identity layer can diversify verbs without
   changing the action economy" only insofar as it shows the action-economy lever *can*
   move chosen verbs.
2. **The scene-gate alone does nothing.** `throttle` (89.6%, entropy 0.250) is
   indistinguishable from baseline. The substitution lever + `energy_hint`, not the
   scene-initiation throttle, is what moves choices.
3. **Divergence is the remaining gap.** No mode clears Gate 9: modes `1`/`flat`/`sub` pass
   the entropy floor but **fail the chosen JSD floor** (max 0.222, mode `sub`). Agents
   reduce the monoculture but drift toward the *same* alternatives (study/rest) rather than
   specializing into *distinct* verbs — and cross-agent divergence is exactly ADR 0007's
   real target.
4. **`flat` ≈ `1` on the contribute axis** (and beats it on chosen entropy), while `sub`
   leads on divergence. Comparative advantage is not the driver here; the substitution
   pressure + perception is.

## Decision

**Escalate to Stage 3.** The Stage 2 criterion ("escalate only if the chosen verbs shift
away from contribute") is met. Recommended adjustments for the Stage 3 design:

- Modes `{0, sub, flat}` (swap `1` → `sub`: `sub` led divergence and isolates the lever
  from the scene gate, which Stage 2 showed inert). Keep `flat` as the comparative-advantage
  control.
- Seeds `{42, 38, 7}`, 3000 ticks, per the #45 plan — needed because the single-seed
  ~170-event reads here are noisy and the JSD signal is small.
- Primary read: **chosen JSD_norm** (divergence), not just contribute share. The open
  question Stage 3 must answer is whether more events let agents settle into *distinct*
  specialties (JSD ≥ 0.25), or whether they keep co-drifting (the lever lowers monoculture
  but does not produce ADR 0007 divergence).
- Write **ADR 0009** from the Stage 3 read. HALT remains until a PASS.
