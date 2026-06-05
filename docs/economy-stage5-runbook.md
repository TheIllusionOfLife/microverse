# Action-economy Stage 5 runbook — balanced contribute cost (`bal` mode)

Operator handoff for the **deferred** Stage 5 live A/B testing the ADR 0010 follow-up: does
making the scholar's `contribute` dear push it to its `study` specialty and finally clear
Gate 9? This PR ships the `bal` mechanism + offline tests only; the live run is a separate
scheduled compute job (~6 runs × ~6 h on `gemma4:26b`). The ADR 0008/0009/0010 HALT stays in
force until a PASS read.

## Why

Stage 4 (ADR 0010) found the honest hint (`adv`) robustly specializes the **artisan** (Aki
chosen-craft 0.025→0.33, all three seeds) but leaves the **scholar** a ~0.92–0.94 `contribute`
monoculture (Cy chosen-study flat at ~0.03). Root cause: the scholar's `contribute` costs only
14, so it is almost always affordable and the scarcity hint never fires for Cy. Cross-agent jsd
therefore lands a stable ~0.16, short of the 0.25 floor — one-sided specialization.

Gate 9's calibration says the *target* is reachable on two agents: distinct specialists with
~40% shared `contribute` each → jsd 0.6 (PASS); fully disjoint → 1.0. So if a lever could push
the scholar to `study`, the two-agent world plausibly clears 0.25.

`bal` mode does exactly that: `derive_balanced_table` raises every role's `contribute` to the
dearest in the table (the artisan's 22) while leaving each role's cheap specialty untouched.
A contribute-heavy scholar now drains, so its (already honest, `adv`) hint fires and names
`study`, and the executor substitutes its free contributes toward `study`. `bal` keeps the
`adv` hint selector, so the artisan still specializes into `craft`.

## A/B matrix

| mode  | contribute cost | hint selector | artisan | scholar hint fires? |
|-------|-----------------|---------------|---------|---------------------|
| `0`   | role (off)      | —             | n/a     | n/a                 |
| `adv` | role (art 22, sch 14) | perceived | craft   | rarely (cheap contribute) |
| `bal` | **22 for every role** | perceived | craft   | **yes** (dear contribute) |

`bal` vs `adv` isolates exactly one variable: the scholar's contribute cost. `adv` vs `0` is
the prior Stage-4 result (carried as context).

## Run (deferred — separate compute job)

Run `{0, adv, bal}` × seeds `{42, 38, 7}` × 3000 ticks **in one sweep** (concurrent
comparators — `gemma4:26b` sampling is unseeded). Reuse the Stage-3 runner pattern:

```bash
for SEED in 42 38 7; do
  for M in 0 adv bal; do
    TAG="${M}-s${SEED}"
    MICROVERSE_ECONOMY=$M \
    MICROVERSE_DATA=data/econ-stage5-$TAG \
    MICROVERSE_HARVEST=harvest/econ-stage5-$TAG \
      uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
    uv run python scripts/spike_workshop_measure.py \
      --data data/econ-stage5-$TAG --harvest harvest/econ-stage5-$TAG
  done
done
```

## Read & acceptance

**Primary bar (Gate 9, chosen stream):** `jsd_norm ≥ 0.25` AND `entropy_norm ≥ 0.35` across all
three seeds. Pre-register before reading: **pass iff Cy's chosen-study share rises materially
under `bal` (vs ~0.03 under `adv`) AND its chosen-contribute share falls**, i.e. the scholar
actually specializes. If Cy stays ~93% contribute, `bal` failed even if jsd wobbles.

**Per-agent specialty shares (the load-bearing read — measure each agent against its OWN
specialty):**
- `aki_craft` = Aki chosen `parsed_verb=craft` share (should stay ~0.33, as under `adv`).
- `cy_study` = Cy chosen `parsed_verb=study` share (the test: does it rise above ~0.03?).
- `cy_contribute` = Cy chosen contribute share (should fall below ~0.93 if `bal` works).

Extract with:
```sql
SELECT printf('%.3f', AVG(CASE WHEN json_extract(payload_json,'$.parsed_verb')='study'
  THEN 1.0 ELSE 0.0 END)) FROM events WHERE actor='Cy'
  AND json_extract(payload_json,'$.parsed_verb') IS NOT NULL;
```

**Quality counters (use `MAX`, not `SUM` — the metric is a cumulative time-series):**
`novelty_energy_hint_conflict`, `artisan_empty_craft_coerced`, `parse_fallback`. Expect the
conflict count to rise under `bal` (now both agents are pushed off their dominant verbs, so
novelty fights both).

## Risks

- **R1 — the scholar still won't move.** Even hinted and drained, `gemma4:26b` may keep choosing
  `contribute` (persona/sampling inertia; the scholar's factual sampling at temp 0.6 concentrates
  on its modal verb). The hint is a soft nudge; ADR 0010 showed the artisan took it but the
  scholar is a different persona. This is the central unknown and only the live run answers it.
- **R2 — cost 22 may not drain the lower-weight scholar enough.** Cy has `soul_tokens=70` (vs
  Aki 100) so it is scheduled less often and regenerates more between actions; the per-action
  drain margin at contribute 22 is thin. It still net-drains under sustained 93% contribute, but
  if the live `cy_contribute` barely falls, raise `derive_balanced_table`'s target above 22 (make
  it a config knob) and re-run. Check the live `cy` energy trajectory before concluding R1 vs R2.
- **R3 — over-correction.** If both agents are pushed hard off contribute, scenes (which need a
  `contribute` initiator) may thin out; watch `scene_completed`. `bal` is not a scene-gate mode,
  so scene *initiation* is not energy-gated, but a drained roster contributes less freely.
- **R4 — novelty vs energy conflict, now two-sided.** With both agents nudged to specialties,
  the novelty hint fights both; conflict counts will rise. If high, the next step is to suppress
  the novelty hint for an agent's own specialty under `bal` (flagged in ADR 0010 R4).

After the read, write the Stage 5 findings + ADR follow-up with the per-seed pass/fail table
and the PASS/HALT decision. If `bal` also fails, the economy approach is exhausted (insufficient
three times) and the arc should be parked for a different mechanism.
