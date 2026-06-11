# Action-economy Phase 2 — mechanism audit (is the hint the operative channel?)

Operator note for **ADR 0012 Phase 2 item 2**. Stage 6 (`docs/economy-stage6-findings.md`,
ADR 0012) locked the first Gate 9 PASS at `bal@30`, with a causal narrative: the dear balanced
contribute drains the scholar, the scarcity hint fires and names `study`, the model obeys. The
PASS read only the *aggregate* verb distributions; this audit checks the narrative's middle links
offline, on the existing nine Stage 6 runs, with **zero new LLM compute**.

The hint text is never persisted, but its per-turn state is deterministically reconstructable:
replay the energy ledger over the LOGGED executed-verb stream (the exact live arithmetic — live
deducts the committed verb at run.py:1222 and regenerates the whole roster once per tick, scenes
collapsed, run.py:1127) and re-evaluate `run.py`'s hint predicate at each free turn pre-deduct
(live computes the hint before `think()`, run.py:983). The logged `economy_substituted` flag is
an in-band fidelity check on that reconstruction. Parity between the offline predicate and
`run._compute_energy_hint` / `run._energy_hint_verb` is pinned by unit test
(`tests/test_economy_audit.py::test_hint_state_parity_with_run_helpers`).

**Headline: CONFIRMED, with one disclosed deviation. C5 (fidelity), C1 (dose), C2 (conditional
shift), C3 (decomposition) pass at all three seeds with wide margins; the deconfound stratum
rules out a raw-energy channel. C4 fails as literally drafted — a criterion mis-specified
against already-published facts (Findings F5) — so under the strict literal rule the read is
INCONCLUSIVE. The CONFIRMED verdict is issued under an amended rule (C4 replaced by the
corrected no-cross-role-bleed falsification, which passes), with the amendment disclosed in the
scorecard below and carried into the item 3 pre-registration.**

## Setup

- Instrument: `scripts/replay_economy.py --audit MODE[@TARGET]=PATH` (this PR), one process over
  all nine frozen Stage 6 run dirs:

  ```bash
  uv run python scripts/replay_economy.py \
    --audit adv=data/econ-stage6-adv-s42    --audit bal@22=data/econ-stage6-bal22-s42 \
    --audit bal@30=data/econ-stage6-bal30-s42 \
    --audit adv=data/econ-stage6-adv-s38    --audit bal@22=data/econ-stage6-bal22-s38 \
    --audit bal@30=data/econ-stage6-bal30-s38 \
    --audit adv=data/econ-stage6-adv-s7     --audit bal@22=data/econ-stage6-bal22-s7 \
    --audit bal@30=data/econ-stage6-bal30-s7
  ```
- Energy knobs: config defaults (`ENERGY_MAX=100`, `ENERGY_REGEN_PER_TICK=8`), exactly what the
  live sweep ran with. Cost tables rebuilt per arm from the spec (`adv`, `bal@22`, `bal@30`).
- Streams mirror `gate9_verb_diversity` filters: scene turns excluded everywhere (forced
  contributes, hint silenced live); parse-fallback RESTs excluded from the chosen-verb
  conditionals (not free choices) but kept in the hint-rate denominator (the hint is computed
  live regardless of how the output parses).
- Strata per free turn: `hint` (predicate fires), `absent_low` (contribute affordable but within
  one regen of the threshold — the deconfound band: energetically adjacent to hint turns, no hint
  text), `absent_comfortable` (everything else).

## Pre-registered decision criteria (locked before the read)

Honesty note: the Stage 6 *aggregate* verb shifts are already known from the sweep. What is
genuinely unseen here are the reconstructed hint firing rates, the conditional (per-turn)
probabilities, the strata, and the fidelity rates. The criteria below were committed to git
before `--audit` first touched a real `episodic.sqlite`.

The hint is confirmed as the operative channel iff all of C1–C4 hold at **all three seeds**, with
C5 as a validity precondition:

- **C5 (fidelity gate, precondition):** predicted-vs-logged `economy_substituted` agreement
  ≥ 0.90 per run, on BOTH the predicted-hint-on and predicted-hint-off subsets (an overall 0.90
  is too easy when hint turns are rare). Below that the reconstruction cannot carry C1–C3 and the
  audit verdict is **INCONCLUSIVE** (fallback: stamp hint state into the live payload for the
  Phase 2 item 3 replication runs — a follow-up, not this PR).
- **C1 (dose):** Cy hint firing rate ordered `adv ≤ bal@22 < bal@30`, with the gap criterion
  `bal@30 − bal@22 ≥ 0.15` absolute applied ONLY to the bal pair (`adv` firing may be near zero
  by construction; a small adv→bal22 gap is not a failure).
- **C2 (conditional shift):** `P(Cy chosen=study | hint) − P(study | absent) ≥ 0.10` absolute,
  and `P(Cy chosen=contribute | hint) < P(contribute | absent)`. The `absent_low` stratum is
  reported alongside: if `P(study | absent_low) ≈ P(study | absent_comfortable)`, the effect is
  attributable to the hint text rather than the raw energy level.
- **C3 (decomposition consistency):** observed ΔCy-study (bal@30 − bal@22, chosen share) within
  ±50% of Δfiring × (P(study|hint) − P(study|absent)) pooled over the bal arms. A consistency
  check, not a model fit; the fit ratio is reported, not just pass/fail.
- **C4 (Aki spillover falsification):** Aki's top chosen verb is `craft` in all 9 runs and its
  top-verb share shifts < ±0.08 across arms.
- **Obedience floor (reported, advisory):** `P(Cy chosen = named easy verb | hint)`. A value
  < 0.30 undercuts the causal claim even if the correlations hold.

What would weaken Stage 6: conditional probabilities flat while firing rose (the study rise was
unconditional drift — novelty lever or sampling, hint not operative → ADR 0012's causal narrative
needs revision); or bal@30 firing not above bal@22 (the R2 mechanism story itself wrong); or a C5
failure (claim stands but unaudited until live hint logging exists).

## Results

Per run (Cy = scholar, Aki = artisan; all shares on the free chosen stream, gate9 filters;
`fire` = hint firing rate over free turns; `p(v|h)` / `p(v|a)` = chosen-verb probability given
hint present / absent; `fid_on` = predicted-vs-logged substitution agreement on predicted-hint-on
turns — hint-off agreement was **1.0000 in all nine runs**):

| arm    | seed | cy_fire | p(study\|h) | p(study\|a) | cy_obed | aki_fire | p(craft\|h) | p(craft\|a) | fid_on |
|--------|-----:|--------:|------------:|------------:|--------:|---------:|------------:|------------:|-------:|
| adv    | 42   | 0.038   | 0.525       | 0.023       | 0.525   | 0.680    | 0.443       | 0.025       | 0.942  |
| bal@22 | 42   | 0.422   | 0.621       | 0.002       | 0.621   | 0.698    | 0.531       | 0.031       | 0.919  |
| bal@30 | 42   | 0.658   | 0.603       | 0.003       | 0.603   | 0.849    | 0.455       | 0.041       | 0.934  |
| adv    | 38   | 0.023   | 0.542       | 0.011       | 0.542   | 0.715    | 0.426       | 0.026       | 0.948  |
| bal@22 | 38   | 0.420   | 0.602       | 0.002       | 0.602   | 0.727    | 0.478       | 0.036       | 0.929  |
| bal@30 | 38   | 0.663   | 0.608       | 0.000       | 0.608   | 0.853    | 0.493       | 0.045       | 0.928  |
| adv    | 7    | 0.014   | 0.500       | 0.018       | 0.500   | 0.709    | 0.475       | 0.041       | 0.945  |
| bal@22 | 7    | 0.389   | 0.557       | 0.002       | 0.557   | 0.702    | 0.506       | 0.011       | 0.944  |
| bal@30 | 7    | 0.661   | 0.667       | 0.000       | 0.667   | 0.850    | 0.503       | 0.040       | 0.936  |

Deconfound stratum (Cy, study share among hint-ABSENT turns, split by the energy band):
`P(study | absent_low)` is 0.000–0.006 in every bal run (n = 149–200 per run) and
`P(study | absent_comfortable)` is 0.000–0.003 (n = 185–437) — indistinguishable from each
other and from zero, against 0.56–0.67 under the hint.

Decomposition (aggregate, bal@22 → bal@30): Cy observed Δstudy **+0.169** vs predicted
Δfiring × conditional effect = 0.251 × 0.612 = **+0.153**, fit ratio **1.10**. Aki observed
Δcraft +0.053 vs predicted +0.065, fit ratio 0.80.

Cy energy equilibria (mean of last quarter of free turns): ~71–83 in `adv`, ~27–31 in `bal@22`,
~24–27 in `bal@30` — the bal arms hold the scholar hovering at the affordability threshold,
exactly the drain regime the R2 story requires.

Raw report: `data/econ-phase2-mechanism-audit.json` (untracked, alongside the frozen run dirs).

## Criteria scorecard

- **C5 PASS** — hint-on agreement 0.919–0.948, hint-off 1.000, all nine runs (floor 0.90).
- **C1 PASS** — Cy firing ordered `adv ≤ bal@22 < bal@30` at every seed; bal-pair gaps
  +0.237 / +0.243 / +0.272 (floor 0.15).
- **C2 PASS** — conditional study shift +0.600 / +0.608 / +0.667 at bal@30 (floor 0.10);
  contribute suppressed under the hint in every run (0.26–0.31 vs 0.88–0.92). Deconfound
  stratum flat at ~0 (see above): the effect follows the hint TEXT, not the energy level.
- **C3 PASS** — Cy fit ratio 1.10 (band 0.5–1.5; even within ±30%).
- **C4 FAIL as drafted** — see F5. Aki's top chosen verb is `contribute` in all nine runs
  (never `craft`), and its top-verb share moved −0.108 across arms (band ±0.08).
- **Obedience (advisory) PASS** — Cy 0.50–0.67 per run (floor 0.30); the named verb was
  `study` on 100% of Cy's fired turns, `craft` on 100% of Aki's.

**Verdict under the rule.** The pre-registered rule reads "confirmed iff all of C1–C4 hold";
C4 failed, so the strict literal reading is INCONCLUSIVE. The verdict issued here is
**CONFIRMED under a post-hoc amended rule**, and the amendment is disclosed rather than
silently substituted: C4's predicate ("Aki's top chosen verb is craft") was false in the
already-frozen pre-Stage-6 data at the moment it was drafted — it tested a baseline that never
existed, not a hypothesis the audit could inform — and its share-stability clause contradicts
the audited design itself (`bal` raises Aki's contribute cost too, so Aki's mix moving WITH its
own firing rate is the mechanism working, not spillover). The amended C4 — Aki's top
NON-contribute chosen verb is `craft` in all nine runs, and neither agent drifts toward the
other's specialty — passes everywhere (F5). Readers who reject post-hoc amendments on principle
should treat this audit as INCONCLUSIVE-pending-replication; the item 3 replication carries the
corrected criterion as a properly pre-registered test either way (ADR 0013 Decision 2).

## Findings

- **F1 — the dose-response chain is real and quantified.** Raising the balanced contribute
  target 22→30 raises Cy's hint firing 0.41→0.66 (and `adv` explains itself: firing 1–4%,
  which is why the honest hint alone never specialized the scholar — ADR 0010's diagnosis,
  now measured). Firing × obedience reproduces the observed study rise within 10% (C3).
- **F2 — the hint text, not the energy level, moves the model.** On energetically-adjacent
  hint-absent turns (`absent_low`) Cy chooses study at ~0, identical to comfortable turns.
  There is no path from the pool level into the prompt except the hint string, and the
  behavior confirms it.
- **F3 — the model obeys the named verb at ~0.5–0.67**, far above the 0.30 floor; the
  remainder mostly stays on contribute (which the executor then substitutes). Specialization
  is perception-mediated, with the hard lever as backstop — the ADR 0009 design intent.
- **F4 — reconstruction validity.** Hint-off turns agree perfectly (no phantom scarcity);
  hint-on agreement 0.92–0.95, the slack matching the known lever ordering (diversity lever
  and engagement gate sit between `parsed_verb` and the economy lever; see Limitations).
- **F5 — C4 was mis-specified, and the underlying falsification still passes.** The criterion
  assumed `craft` was Aki's top chosen verb; it never was — `contribute` tops every arm
  including `adv` (0.56–0.68), a fact already visible in pre-Stage-6 data. The share movement
  across arms is also not spillover: `bal` raises AKI's contribute cost too (22→30), so Aki's
  own firing rises 0.70→0.85 and its craft share 0.32→0.42 — the same mechanism, the same
  comparative-advantage direction, decomposition-consistent (fit 0.80). The intent of C4
  (no cross-role identity bleed) holds cleanly: the hint named `craft` on 100% of Aki's fired
  turns and `study` on 100% of Cy's; neither agent drifted toward the other's specialty
  (Aki study ≤ 0.02, Cy craft ≤ 0.01 everywhere). Recorded as FAIL-as-drafted per
  pre-registration discipline; the corrected falsification belongs to the Phase 2 item 3
  replication's pre-registration.

## Limitations

- **Reconstruction, not ground truth.** The hint state is recomputed, not logged. The known
  near-miss sources: the diversity lever and engagement gate run between `parsed_verb` capture
  and the economy lever, so the logged `economy_substituted` compares the economy lever's own
  input/output, not `parsed_verb` — C5 measures exactly this slack.
- **No mid-run restarts** occurred in the nine Stage 6 dirs; a restarted run would only support
  approximate reconstruction (`EnergyLedger.reconstruct_from_events` is per-actor, not
  whole-roster) and would need the fidelity gate re-examined.
- Everything ADR 0012 carries forward still binds: two-resident roster, single model, three
  seeds, target-selected T. This audit strengthens (or weakens) the *mechanism* claim only;
  replication and roster generality are Phase 2 items 3–4.

## Reproduction

```bash
git checkout feat/economy-mechanism-audit
uv run pytest -q tests/test_economy_audit.py
# then the Setup command above against the frozen data/econ-stage6-* dirs
```
