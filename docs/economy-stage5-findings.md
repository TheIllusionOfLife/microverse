# Action-economy Stage 5 findings — balanced contribute cost (`bal` mode)

Operator note recording the Stage 5 live A/B that feeds **ADR 0011**. The ADR 0008/0009/0010
HALT stays in force; this records the read that informs it. Mechanism + offline tests shipped
in PR #54; this is the deferred live run from `docs/economy-stage5-runbook.md`.

## Setup

- Runner (per mode `{0, adv, bal}` × seed `{42, 38, 7}`), model `gemma4:26b`, isolated
  `MICROVERSE_DATA`/`MICROVERSE_HARVEST` per run, ~6.3 h / ~3760 agent events each:

  ```bash
  MICROVERSE_ECONOMY=$MODE uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
  ```
- Modes: `0` (economy off / baseline), `adv` (substitution lever + honest hint, the Stage-4
  state where only the artisan specializes), `bal` (`adv` + `derive_balanced_table` raising
  every role's `contribute` to the dearest cost, 22, so the scholar's contribute drains and its
  scarcity hint fires — ADR 0010 fix).
- Seeds all run as concurrent comparators — `gemma4:26b` sampling is unseeded, so `0`/`adv`/`bal`
  were paired in the same sweep, not compared to archived Stage-4 reads.
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen**
  (`parsed_verb`) stream. **PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥ 0.25`.**
  Primary read is `jsd_norm` (cross-agent divergence — ADR 0007's real target).
- Pre-registered mechanism criterion (runbook, before reading): pass iff Cy's chosen-study share
  rises materially under `bal` (vs ~0.03 under `adv`) AND its chosen-contribute share falls.

## Results (all 9 runs)

`aki_craft` = Aki's **chosen** (`parsed_verb`) share of its OWN specialty (artisan: craft).
`cy_study` = Cy's chosen share of its OWN specialty (scholar: study); `cy_contrib` = Cy's chosen
contribute share. `conflict` = true `novelty_energy_hint_conflict` count (the cumulative metric's
MAX; the per-run progress log printed a SUM, ~1000× inflated — disregard that column there).
`econ_sub` = economy substitution rate. For mode `0` the economy is off so `parsed_verb` is
unstamped; its shares are executed (chosen ≡ executed when nothing substitutes).

| mode  | seed | entropy_norm | jsd_norm | aki_craft | cy_study | cy_contrib | conflict | econ_sub | Gate 9 |
|-------|------|-------------:|---------:|----------:|---------:|-----------:|---------:|---------:|:------:|
| `0`   | 42   | 0.253        | 0.014    | 0.028     | 0.012    | 0.930      | 0        | 0.000    | FAIL   |
| `adv` | 42   | 0.381        | 0.188    | 0.258     | 0.021    | 0.960      | 312      | 0.176    | FAIL   |
| `bal` | 42   | 0.544        | **0.235**| 0.258     | **0.168**| **0.770**  | 432      | 0.233    | FAIL   |
| `0`   | 38   | 0.256        | 0.016    | 0.023     | 0.020    | 0.923      | 0        | 0.000    | FAIL   |
| `adv` | 38   | 0.358        | 0.169    | 0.230     | 0.011    | 0.966      | 124      | 0.214    | FAIL   |
| `bal` | 38   | 0.543        | **0.244**| 0.239     | **0.170**| **0.772**  | 431      | 0.259    | FAIL   |
| `0`   | 7    | 0.253        | 0.012    | 0.021     | 0.014    | 0.923      | 0        | 0.000    | FAIL   |
| `adv` | 7    | 0.357        | 0.157    | 0.220     | 0.018    | 0.961      | 73       | 0.206    | FAIL   |
| `bal` | 7    | 0.544        | **0.232**| 0.258     | **0.170**| **0.773**  | 424      | 0.249    | FAIL   |

Means: `0`/`adv`/`bal` jsd 0.014 / 0.171 / **0.237**. `cy_study` 0.015 / 0.017 / **0.169** —
the scholar moves only under `bal`. `cy_contrib` 0.925 / 0.962 / **0.772** — the scholar's
contribute monoculture breaks only under `bal`. `aki_craft` 0.024 / 0.236 / 0.252 — the artisan
holds its `adv` specialty. Entropy 0.254 / 0.365 / **0.544**.

## Read

1. **`bal` robustly specializes the SCHOLAR — the missing half of Stage 4.** Cy's chosen-study
   share is ~0.017 (adv) → **~0.169 (bal)** and chosen-contribute ~0.962 (adv) → **~0.772 (bal)**,
   near-identical at all three seeds (study 0.168 / 0.170 / 0.170; contribute 0.770 / 0.772 /
   0.773). The pre-registered mechanism criterion passes cleanly and reproducibly. Making the
   scholar's contribute dear fires its scarcity hint and the executor substitutes toward `study`,
   exactly as ADR 0010 predicted. The scholar inertia named the Stage-4 binding constraint *was*
   breakable by the cost table alone — no persona tuning.

2. **Both agents now specialize — the world is genuinely two-profile.** `bal` holds the artisan
   on craft (~0.25) AND moves the scholar onto study (~0.17), over a shared but *reduced*
   contribute floor (~0.77 vs ~0.96 under `adv`). Entropy jumps to 0.544 (clears the 0.35 floor at
   every seed). This is the first time in the arc that *both* residents hold distinct specialties.

3. **But Gate 9 still FAILS — on `jsd` alone, by a hair.** Cross-agent `jsd_norm` is 0.235 / 0.244
   / 0.232 (mean **0.237**), a stable ~0.013 below the 0.25 floor at all three seeds. Stage 4
   missed by a wide ~0.09 with a flat scholar; Stage 5 misses by a hair with both agents moved.
   The binding term is the residual ~0.77 shared contribute mass: with each agent still ~0.77
   contribute, the two distributions overlap too much for jsd to clear 0.25. The lever moved the
   scholar but not *far enough*.

4. **The `adv`/`0` comparators reproduce Stage 4 qualitatively.** Fresh unseeded `adv` runs give
   jsd 0.188 / 0.169 / 0.157 (Stage-4: 0.175 / 0.168 / 0.143) with the scholar stuck on contribute
   (~0.96) — artisan-only specialization reproduces even though point values drift. This confirms
   the `bal` effect is causal to the balanced cost, not a sweep artifact.

5. **R4 (novelty vs energy hint conflict) rises but is not the binding cap.** Conflict climbs to
   ~424–432 under `bal` (vs `adv` 73–312) — expected, since both agents are pushed off their
   dominant verbs so novelty fights both — but at ~430 over ~3760 events (~11%) it is not what
   holds jsd under 0.25. The binding term is residual shared contribute (Read 3), not the hint
   fight. R2 (drain margin), not R4, is the lever.

## Decision: HALT stays (see ADR 0011)

`bal` is **necessary and effective for the scholar — the first time both agents specialize** —
but the society lands a consistent near-miss (jsd ~0.237 vs the 0.25 floor). No PASS, so the ADR
0008/0009/0010 HALT stays in force.

The Stage-5 pre-registration said a failed `bal` would mean "the economy approach is exhausted,
park the arc." That framing assumed `bal` would fail *because the scholar wouldn't move*. It moved,
robustly — so the strict "exhausted" verdict does not cleanly apply. The honest read is a near-pass
with a single pre-identified residual cause:

- **R2 (pre-registered in the runbook): balanced contribute = 22 does not drain the lower-weight
  scholar enough.** Cy (`soul_tokens=70`, vs Aki 100) is scheduled less often and regenerates more
  between actions, so its contribute net-drains only to ~0.77. The runbook already flagged the fix:
  make `derive_balanced_table`'s target a config knob and raise it above 22 → lower `cy_contrib` →
  higher `jsd`. One well-motivated tune, not a new mechanism.

**This is a genuine operator fork, not a unilateral call** — it is live-compute spend (~6 runs ×
~6 h) and chasing a 0.013 gap risks goalpost-tuning the threshold:

- **Tune-to-clear (R2):** raise balanced contribute (e.g. 26–30), re-run `{adv, bal}` × `{42, 38,
  7}`; pre-register the new target and pass rule before reading. If `cy_contrib` falls below ~0.6
  and jsd crosses 0.25 at all three seeds, Gate 9 PASSes and Phase 2 is unblocked.
- **Park the arc:** treat the consistent ~0.237 near-miss as the economy lever's two-resident
  ceiling and pursue a different mechanism (e.g. a larger heterogeneous roster — the two-agent JSD
  coarseness may itself be the constraint).

Recommended: one R2 tune-to-clear run (specific, pre-identified, mechanical cause; world already
two-profile) — but the call is the operator's. See
`docs/adr/0011-action-economy-stage5-bal-read.md`.
