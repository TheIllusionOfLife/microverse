# Action-economy Stage 4 runbook — honest per-agent scarcity hint (`adv` mode)

Operator handoff for the **deferred** Stage 4 live A/B that tests the ADR 0009
follow-up intervention. The ADR 0008/0009 HALT stays in force until a PASS read.
This PR ships the mechanism (`adv` mode) + offline tests only; the live run below
is a separate scheduled compute job (~9 runs × 5-6 h on `gemma4:26b`).

## Why

Stage 3 (ADR 0009) found the action economy breaks the `contribute` monoculture
(society entropy 0.27 → 0.57) but **fails Gate 9 cross-agent specialization**:
`jsd_norm` topped out at 0.185 (< 0.25 floor). Root cause (verified in code): Gate 9
reads the **chosen** (`parsed_verb`, model-pick) stream, and the only channel the
economy has into that stream is the per-agent `energy_hint`. That hint named
`cheapest_affordable_productive`, which excludes the payload verbs `{contribute,
craft}` — so the **artisan**, whose cheap specialty *is* `craft`, was told to fall
back to `study` (the **scholar's** specialty). Both agents' scarcity hints pointed
at the same payload-free escape → co-drift, not differentiation.

`adv` mode fixes the hint: `EnergyLedger.cheapest_affordable_perceived` names the
agent's **true** cheapest affordable verb including its payload specialty (excludes
only `rest`), while the blind executor's substitution target stays payload-free
(it cannot fabricate a `craft` artifact). Intended effect: artisan hint → "craft
comes easily", scholar hint → "study comes easily", pushing the chosen
distributions apart.

### This is a correctness fix, not prompt-tuning (ADR 0008 constraint)

No persona template (`prompts/persona_*.j2`) is touched; the hint string format and
firing condition are unchanged. Only *which verb the existing scarcity signal names*
changes — from a verb the agent cannot cheaply do to its true cheapest affordable
one, computed mechanically from the cost table. It is symmetric across agents and
**inert under the `flat` control** (which removes specialties, so there is no
specialty to name). The artisan getting `craft` is emergent from `craft` costing 6
for it, not an authored preference. It also makes code match the `economy.py`
comment that already claimed the artisan "diversifies through the perception channel
(energy_hint lets the model choose craft)".

## A/B matrix

| mode   | scene_gate | substitute | hint selector                     | specialty | honest hint |
|--------|-----------|-----------|-----------------------------------|-----------|-------------|
| `0`    | no        | no        | —                                 | n/a       | no          |
| `flat` | yes       | yes       | (inert — no specialty)            | removed   | n/a         |
| `sub`  | no        | yes       | `cheapest_affordable_productive`  | present   | no          |
| `adv`  | no        | yes       | `cheapest_affordable_perceived`   | present   | **yes**     |

`adv` vs `sub` isolates the hint fix as the single changed variable; `adv` vs `flat`
isolates specialization given the honest hint; `adv` vs `0` is the total economy effect.

## Run (deferred — separate compute job)

Run `{0, sub, adv}` × seeds `{42, 38, 7}` × 3000 ticks **in the same sweep**. Do NOT
reuse the archived Stage-3 `sub`/`0` reads as the comparator: `gemma4:26b` sampling is
unseeded, so even a byte-identical harness produces different draws — the only causally
clean `adv`-vs-`sub` contrast is paired in the same sweep.

`flat` is in the A/B matrix above but is **intentionally omitted from this run**: the
hint fix is inert under `flat` (it removes specialties, so there is nothing to name —
proven by `test_cheapest_affordable_perceived_under_flat_table_is_role_symmetric`), so
`flat` is identical to its Stage-3 read and adds no information about the `adv`
intervention. Reuse the Stage-3 `flat` numbers (ADR 0009) only as a static reference for
"specialty removed"; do not treat it as a paired comparator. Add it back to the loop
only if a reviewer wants a fresh `flat` baseline at the same wall-clock as `adv`.

```bash
for SEED in 42 38 7; do
  for M in 0 sub adv; do
    TAG="${M}-s${SEED}"
    MICROVERSE_ECONOMY=$M \
    MICROVERSE_DATA=data/econ-stage4-$TAG \
    MICROVERSE_HARVEST=harvest/econ-stage4-$TAG \
      uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
    uv run python scripts/spike_workshop_measure.py \
      --data data/econ-stage4-$TAG --harvest harvest/econ-stage4-$TAG
  done
done
```

## Read & acceptance

**Primary bar (Gate 9, chosen stream):** `jsd_norm ≥ 0.25` AND `entropy_norm ≥ 0.35`
across all three seeds (Stage-3 `sub` topped at 0.185). Pre-register before reading:
*pass iff Aki's modal chosen verb relocates to `craft` OR Aki/Cy secondary mass goes
disjoint* (per the Gate 9 calibration tests in `tests/test_gate9_verb_diversity.py`).

**Quality gate (JSD alone is insufficient).** Also record, per run, so a
`parsed_verb="craft"` cannot inflate chosen JSD without real work product:
- Aki chosen-craft share and executed-craft share,
- `artisan_empty_craft_coerced` count (hollow crafts the guard rewrote),
- parse-fallback rate,
- `novelty_energy_hint_conflict` count (R4 — how often the two hints opposed; see below).

## Risks (the offline fix cannot resolve these — live run decides)

- **R1 — model ignores the soft hint.** `gemma4:26b` may not shift its chosen verb.
  Crossing 0.25 needs Aki's *chosen* mass to move to `craft` (~hundreds of ticks);
  Cy → study alone is insufficient.
- **R2 — free (non-scene) craft (de-risked offline).** The mocked-chat go/no-go
  (`tests/test_run_economy.py::test_adv_model_chosen_craft_survives_to_chosen_stream_unsubstituted`)
  confirms a model-chosen `craft` under `adv` lands in the chosen stream and executes
  un-substituted. The channel is mechanically open.
- **R3 — chosen/executed inconsistency.** Hint says craft but the model picks an
  unaffordable verb, executor rewrites to study, reinforcing co-drift. `craft` (cost 6)
  is cheaply affordable, so this should be rare; the per-event override rate bounds it.
- **R4 — novelty hint FIGHTS the energy hint (instrumented).** `_compute_novelty_hint`
  (`dominance_threshold=0.50`) steers an agent away from whatever verb dominates its
  recent mix. The moment Aki specializes into `craft`, novelty pushes it back off, and
  the diversity lever (30% substitution of a dominant non-contribute verb) rewrites
  executed craft. The run loop now counts `novelty_energy_hint_conflict` per tick (the
  honest energy hint naming the very verb novelty discourages). If that count is high
  in the live read, a follow-up may need to suppress the novelty hint for an agent's
  *own* cheap specialty under `adv` — a known limitation, recorded here rather than
  silently patched.

After the live read, write the ADR 0009 follow-up with the per-seed pass/fail table and
the decision (PASS unblocks Phase 2; HALT stays otherwise).
