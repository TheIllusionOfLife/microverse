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

**Headline: TBD (criteria below committed before the audit read any real event table).**

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

TBD — filled in after the locked run of the command above.

## Findings

TBD.

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
