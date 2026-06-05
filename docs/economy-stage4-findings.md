# Action-economy Stage 4 findings — honest per-agent scarcity hint (`adv` mode)

Operator note recording the Stage 4 live A/B that feeds **ADR 0010**. The ADR 0008/0009
HALT stays in force; this records the read that informs it. Mechanism + offline tests
shipped in PR #52; this is the deferred live run from `docs/economy-stage4-runbook.md`.

## Setup

- Runner: `MICROVERSE_ECONOMY={mode} uv run python -m microverse.run --ticks 3000
  --tempo 0 --seed {seed}`, model `gemma4:26b`, isolated `MICROVERSE_DATA`/
  `MICROVERSE_HARVEST` per run. Each run ~5.7–6.3 h / ~3760 agent events.
- Modes: `0` (economy off / baseline), `sub` (substitution lever, legacy hint naming
  `cheapest_affordable_productive`), `adv` (substitution lever, **honest** hint naming
  `cheapest_affordable_perceived` — includes the agent's payload specialty, ADR 0009 fix).
- Seeds `{42, 38, 7}`, all run (concurrent comparators — `gemma4:26b` sampling is unseeded,
  so `0`/`sub`/`adv` were paired in the same sweep, not compared to archived Stage-3 reads).
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen**
  (`parsed_verb`) stream. **PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥
  0.25`.** Primary read is `jsd_norm` (cross-agent divergence — ADR 0007's real target).

## Results (all 9 runs)

`aki_craft`/`cy_craft` = each agent's **chosen** (`parsed_verb`) craft share. `conflict` =
true `novelty_energy_hint_conflict` count (the cumulative metric's MAX; the per-run progress
log printed a SUM over the time-series, ~1000× inflated — disregard that column there).

| mode  | seed | entropy_norm | jsd_norm | aki_craft | cy_craft | conflict | Gate 9 |
|-------|------|-------------:|---------:|----------:|---------:|---------:|:------:|
| `0`   | 42   | 0.259        | 0.019    | 0.000     | 0.000    | 0        | FAIL   |
| `sub` | 42   | 0.426        | 0.119    | 0.024     | 0.014    | 5        | FAIL   |
| `adv` | 42   | 0.380        | **0.175**| **0.350** | 0.011    | 177      | FAIL   |
| `0`   | 38   | 0.257        | 0.010    | 0.000     | 0.000    | 0        | FAIL   |
| `sub` | 38   | 0.497        | 0.171    | 0.020     | 0.013    | 25       | FAIL   |
| `adv` | 38   | 0.378        | **0.168**| **0.336** | 0.011    | 51       | FAIL   |
| `0`   | 7    | 0.267        | 0.014    | 0.000     | 0.000    | 0        | FAIL   |
| `sub` | 7    | 0.456        | 0.119    | 0.027     | 0.009    | 5        | FAIL   |
| `adv` | 7    | 0.374        | **0.143**| **0.312** | 0.015    | 79       | FAIL   |

Means: `0` jsd 0.014 / `sub` 0.136 / `adv` 0.162. `adv` aki_craft mean **0.333**;
`adv` cy_craft mean **0.012**. `adv` entropy mean 0.377; `sub` entropy mean 0.460.

## Read

1. **The honest hint robustly specializes the ARTISAN.** Aki's chosen-craft share is
   0.00 (off) → 0.024 (sub) → **0.33 (adv)**, stable across all three seeds (0.350 / 0.336 /
   0.312). The ADR 0009 fix works exactly as designed: naming `craft` as "comes easily" moves
   the artisan to its own cheap specialty instead of the shared payload-free escape (`study`).
   `sub` never does this (Aki craft ~0.024) — it is specific to the honest hint.

2. **But it does NOT specialize the SCHOLAR.** Cy's chosen-craft is ~0.012 everywhere, and Cy
   stays contribute-dominated under `adv` (its plurality remains `contribute`). The
   differentiation is one-sided: one role retreats to its specialty, the other does not.

3. **So cross-agent JSD never clears the floor — Gate 9 FAILS in every run.** `adv` jsd lands a
   stable ~0.16 (0.175 / 0.168 / 0.143), below the 0.25 floor at all three seeds. `adv` edges
   `sub` on the mean (0.162 vs 0.136) but the gap is inside the seed-variance band (`sub` itself
   swings 0.119–0.171), and `adv` never approaches 0.25. One-sided specialization is not enough:
   Gate 9 needs **both** agents to put mass on **different** verbs, and only Aki moved.

4. **The signature is specialization, not just diversity.** `adv` entropy (0.377) is *lower*
   than `sub` (0.460) while `adv` cxe (~0.11) is *higher* than `sub` (~0.06): `adv` concentrates
   each agent toward a profile (Aki→craft) rather than spreading both across many verbs. That is
   the right qualitative move — it just stops at one agent.

5. **R4 (novelty vs energy hint conflict) is real but modest.** True conflict counts are higher
   under `adv` (51–177) than `sub` (5–25) — the novelty hint does push back once Aki leans into
   craft — but at ~50–180 ticks over a ~3760-event run it is not the dominant cap. The binding
   constraint is the scholar's inertia, not the novelty/energy fight.

## Decision: HALT stays (see ADR 0010)

The honest hint is **necessary and effective for the artisan but insufficient for the
society**. It refutes "the economy cannot move the chosen stream toward a role's specialty"
(it clearly can — robustly, across seeds), but it does not unlock cross-agent specialization
(Gate 9 JSD), because the scholar stays a contribute monoculture. The ADR 0008/0009 HALT
stays in force. The next lever must move the **scholar** specifically: its `contribute` is
cheap (cost 14) so it rarely triggers the scarcity hint, and its persona/sampling keep it on
contribute even when hinted. See `docs/adr/0010-action-economy-stage4-adv-read.md`.
