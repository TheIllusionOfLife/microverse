# Action-economy Stage 4 findings — honest per-agent scarcity hint (`adv` mode)

Operator note recording the Stage 4 live A/B that feeds **ADR 0010**. The ADR 0008/0009
HALT stays in force; this records the read that informs it. Mechanism + offline tests
shipped in PR #52; this is the deferred live run from `docs/economy-stage4-runbook.md`.

## Setup

- Runner (per mode `{0, sub, adv}` × seed `{42, 38, 7}`), model `gemma4:26b`, isolated
  `MICROVERSE_DATA`/`MICROVERSE_HARVEST` per run, ~5.7–6.3 h / ~3760 agent events each:

  ```bash
  MICROVERSE_ECONOMY=$MODE uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
  ```
- Modes: `0` (economy off / baseline), `sub` (substitution lever, legacy hint naming
  `cheapest_affordable_productive`), `adv` (substitution lever, **honest** hint naming
  `cheapest_affordable_perceived` — includes the agent's payload specialty, ADR 0009 fix).
- Seeds all run as concurrent comparators — `gemma4:26b` sampling is unseeded, so `0`/`sub`/`adv`
  were paired in the same sweep, not compared to archived Stage-3 reads.
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen**
  (`parsed_verb`) stream. **PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥ 0.25`.**
  Primary read is `jsd_norm` (cross-agent divergence — ADR 0007's real target).

## Results (all 9 runs)

`aki_craft` = Aki's **chosen** (`parsed_verb`) share of its OWN specialty (artisan: craft).
`cy_study` = Cy's chosen share of its OWN specialty (scholar: study); `cy_contrib` = Cy's
chosen contribute share. (Each agent measured against the verb it *should* specialize into,
not the other's — the scholar's specialty is study, not craft.) `conflict` = true
`novelty_energy_hint_conflict` count (the cumulative metric's MAX; the per-run progress log
printed a SUM over the time-series, ~1000× inflated — disregard that column there). For mode
`0` the economy is off so `parsed_verb` is unstamped; its shares are executed (chosen ≡
executed when nothing substitutes).

| mode  | seed | entropy_norm | jsd_norm | aki_craft | cy_study | cy_contrib | conflict | Gate 9 |
|-------|------|-------------:|---------:|----------:|---------:|-----------:|---------:|:------:|
| `0`   | 42   | 0.259        | 0.019    | 0.028     | 0.018    | 0.923      | 0        | FAIL   |
| `sub` | 42   | 0.426        | 0.119    | 0.024     | 0.022    | 0.934      | 5        | FAIL   |
| `adv` | 42   | 0.380        | **0.175**| **0.350** | 0.031    | 0.938      | 177      | FAIL   |
| `0`   | 38   | 0.257        | 0.010    | 0.025     | 0.011    | 0.926      | 0        | FAIL   |
| `sub` | 38   | 0.497        | 0.171    | 0.020     | 0.032    | 0.925      | 25       | FAIL   |
| `adv` | 38   | 0.378        | **0.168**| **0.336** | 0.030    | 0.929      | 51       | FAIL   |
| `0`   | 7    | 0.267        | 0.014    | 0.023     | 0.014    | 0.915      | 0        | FAIL   |
| `sub` | 7    | 0.456        | 0.119    | 0.027     | 0.037    | 0.923      | 5        | FAIL   |
| `adv` | 7    | 0.374        | **0.143**| **0.312** | 0.030    | 0.926      | 79       | FAIL   |

Means: `0`/`sub`/`adv` jsd 0.014 / 0.136 / 0.162. `aki_craft` 0.025 / 0.024 / **0.333** —
the artisan moves only under `adv`. `cy_study` 0.014 / 0.030 / 0.030 — flat; the scholar
never moves toward its specialty, and stays ~0.92–0.94 `contribute` in every mode. `adv`
entropy mean 0.377; `sub` 0.460.

## Read

1. **The honest hint robustly specializes the ARTISAN.** Aki's chosen-craft share is
   ~0.025 (off/sub) → **0.33 (adv)**, stable across all three seeds (0.350 / 0.336 / 0.312).
   The ADR 0009 fix works exactly as designed: naming `craft` as "comes easily" moves the
   artisan to its own cheap specialty instead of the shared payload-free escape (`study`).
   `sub` never does this (Aki craft ~0.024) — it is specific to the honest hint.

2. **But it does NOT specialize the SCHOLAR.** Measured against the scholar's *own* specialty
   (`study`, not craft): Cy's chosen-study share is flat at ~0.03 under `adv`, indistinguishable
   from `sub` (~0.03) and barely above baseline (~0.014) — the honest hint does not move the
   scholar at all. Cy stays a ~0.92–0.94 `contribute` monoculture in every mode. The
   differentiation is one-sided: the artisan retreats to its specialty, the scholar does not.
   (The scholar's `contribute` costs only 14, so it is usually affordable and the scarcity hint
   rarely fires for Cy; even when it does, its persona/sampling keep it on contribute.)

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
