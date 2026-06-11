# ADR 0013: Action-economy mechanism audit — the scarcity hint IS the operative channel

**Status:** Accepted (measurement record) — records the offline mechanism audit (ADR 0012
Phase 2 item 2) of the Stage 6 Gate 9 PASS. **Verdict: CONFIRMED, under one disclosed
deviation from the pre-registered rule.** C5 (reconstruction fidelity), C1 (dose), C2
(conditional shift, with the deconfound stratum), and C3 (decomposition consistency) all pass
at all three seeds with wide margins. C4 failed as literally drafted; since the rule read
"confirmed iff all of C1–C4 hold", the strict literal reading is INCONCLUSIVE. The verdict is
issued under an amended rule — C4's predicate was mis-specified against already-published facts
(it tested a baseline that never existed), and the corrected falsification (no cross-role
identity bleed) passes everywhere. The amendment is disclosed, not silently substituted; see
the findings doc's "Verdict under the rule". The Stage 6 causal narrative survives.

**Date:** 2026-06-12

## Context

ADR 0012 lifted the arc's HALT on the first Gate 9 PASS (`bal@30`, all three seeds) but scoped
the claim deliberately: the PASS read only aggregate verb distributions, while the causal story —
dear contribute drains the scholar → the scarcity hint fires and names `study` → the model obeys
— had its middle links unmeasured. Phase 2 item 2 demanded a cheap, offline audit on the existing
nine Stage 6 runs before investing in held-out replication (item 3) and roster generality
(item 4): Cy energy traces, hint firing at 22 vs 30, chosen verb conditional on hint
present/absent, Aki top-verb stability.

The hint text is never persisted. The audit reconstructs it deterministically: replay the
EnergyLedger over the LOGGED executed-verb stream (live deducts the committed verb, run.py:1222;
whole-roster regen once per tick with scenes collapsed, run.py:1127) and re-evaluate the live
hint predicate pre-deduct at each free turn (live computes it pre-think, run.py:983). The logged
`economy_substituted` flag gives an in-band fidelity check (C5); a parity unit test pins the
offline predicate to `run._compute_energy_hint` / `_energy_hint_verb` so they cannot drift.

## Method

`scripts/replay_economy.py --audit MODE[@TARGET]=PATH` (this PR), one pass over the nine frozen
Stage 6 dirs at the live energy knobs (max 100, regen 8). Streams mirror `gate9_verb_diversity`
filters. Decision criteria C1–C5 were committed to git **before** the instrument first read a
real event table (`docs/economy-phase2-mechanism-audit.md`, the full write-up).

## Results (summary — full tables in the findings doc)

- **C5 PASS.** Predicted-vs-logged substitution agreement: hint-off **1.000 in all nine runs**
  (no phantom scarcity), hint-on 0.919–0.948 (floor 0.90).
- **C1 PASS.** Cy hint firing: `adv` 0.014–0.038, `bal@22` 0.39–0.42, `bal@30` 0.66 at every
  seed; bal-pair gaps +0.24 to +0.27 (floor 0.15). The offline rule's prediction (~0.24 → ~0.56)
  was directionally right and conservative.
- **C2 PASS.** `P(Cy study | hint)` 0.56–0.67 vs `P(study | absent)` ≤ 0.003 in the bal arms;
  contribute suppressed under the hint in every run. **Deconfound stratum:** on hint-absent turns
  energetically adjacent to the threshold (`absent_low`, n = 149–200 per bal run) study stays at
  ~0, identical to comfortable turns — the hint TEXT moves the model, not the energy level.
- **C3 PASS.** Observed ΔCy-study (bal@22→bal@30) +0.169 vs predicted Δfiring × conditional
  effect +0.153; fit ratio 1.10 (band 0.5–1.5).
- **C4 FAIL as drafted.** The criterion assumed `craft` was Aki's top chosen verb; `contribute`
  tops all nine runs (0.56–0.68), a pre-Stage-6 fact the drafting missed, and Aki's mix moved
  across arms because `bal` raises AKI's contribute cost too (its own firing 0.70→0.85, craft
  0.32→0.42 — same mechanism, decomposition fit 0.80). The intent (no cross-role bleed) holds:
  the hint named `craft` on 100% of Aki's fired turns and `study` on 100% of Cy's; Aki-study
  ≤ 0.02 and Cy-craft ≤ 0.01 everywhere.
- **Obedience (advisory) PASS.** Cy follows the named verb at 0.50–0.67 per run (floor 0.30).

## Decision

1. **The mechanism claim is confirmed (under the disclosed C4 amendment).** ADR 0012's bounded
   PASS is upgraded from "the tuned
   economy produced divergence" to "the tuned economy produced divergence THROUGH the measured
   hint channel, at quantified firing/obedience rates, with the raw-energy confound excluded."
2. **Phase 2 item 3 (held-out replication, T fixed at 30) proceeds** on this foundation. Its
   pre-registration must carry the corrected stability criterion: Aki's top NON-contribute
   chosen verb is `craft` and neither agent drifts toward the other's specialty — not the
   mis-drafted C4.
3. **No live hint logging is needed for the audit's sake** (C5 passed without it), but stamping
   `energy_hint_fired` / named verb into the payload remains worth doing in the replication
   runs to retire the reconstruction caveat entirely. Decide in the item 3 PR.
4. C4's literal failure is recorded, not patched away: the pre-registered criterion text stands
   as written and scored FAIL in the findings doc, per the arc's pre-registration discipline.

## Scope and limitations

- The reconstruction is exact for ledger arithmetic but approximate for lever interleaving (the
  diversity lever and engagement gate sit between `parsed_verb` and the economy lever); C5
  bounds that slack at ≤ 8% of hint-on turns, hint-off exact.
- Everything ADR 0012 carries forward still binds: two-resident roster, single model
  (`gemma4:26b`), three seeds, target-selected T = 30. This audit settles the *mechanism*
  question only; generality is items 3–4.
- The nine runs had no mid-run restarts; a restarted run would weaken the reconstruction and
  needs the fidelity gate re-checked.

## Reproduction

See `docs/economy-phase2-mechanism-audit.md` (Setup + Reproduction). Raw report:
`data/econ-phase2-mechanism-audit.json` (untracked, with the frozen run dirs).
