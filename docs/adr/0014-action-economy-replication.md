# ADR 0014: Action-economy held-out replication — the `bal@30` Gate 9 PASS is stable

**Status:** Accepted (measurement record) — records the held-out replication sweep (ADR 0012
Phase 2 item 3) of the Stage 6 Gate 9 PASS. **Verdict: REPLICATED.** The instrument gate, Layer 1
(gate replication), and Layer 2 (role stability) all pass in full at all three fresh seeds, against
the two-layer rule locked before the sweep's first tick, with no post-hoc amendment.

**Date:** 2026-06-14

## Context

ADR 0012 lifted the arc's HALT on the first Gate 9 PASS (`bal@30`, seeds {42, 38, 7}); ADR 0013
confirmed the scarcity hint as the operative channel. Both records scoped the claim deliberately as
"first locked pass, to be replicated" — a single sweep at three seeds cannot tell a stable effect
from a lucky draw of `gemma4:26b`'s unseeded sampling. Phase 2 item 3 tests stability: hold the
balanced contribute target fixed at **T = 30** (re-tune nothing), draw fresh seeds {101, 202, 303}
disjoint from every prior economy read, and re-read Gate 9 against a pre-registered rule.

ADR 0013 handed this PR two obligations: **Decision 2** (the replication pre-registration must use
the corrected stability criterion — Aki's top NON-contribute chosen verb, not the mis-drafted C4
that tested a `craft`-tops baseline which never existed) and **Decision 3** (stamp ground-truth
hint state into the live payload to retire the reconstruction caveat). Both are discharged here.

## Method

One sweep, `{bal@22 in-sweep control, bal@30}` × seeds `{101, 202, 303}` × 3000 ticks,
seed-outer / arm-inner, `gemma4:26b`, energy knobs at the live defaults (max 100, regen 8). Run
detached 2026-06-12 → 2026-06-14 (~6.3 h / ~4540 events per run). The control is in-sweep `bal@22`,
not Stage 6's archived control, because model/runtime behavior can drift between sweeps separated by
days; the only causally clean comparator is a `bal@22` paired temporally adjacent to its `bal@30`.

The two-layer pass rule, instrument gate, verdict mapping, and secondary/advisory metrics were
committed to git (`docs/economy-phase2-replication-runbook.md`, commit `2d4ef8b`) **before** the
sweep's first tick. The ground-truth hint logging (`energy_hint_fired` / `energy_hint_verb`,
observation-only, pinned by unit tests and a byte-identical re-read of a Stage 6 dir) and the
audit's `fidelity.hint_logged` block shipped in the same PR.

## Results (summary — full tables in the findings doc)

- **Instrument gate PASS (all six runs).** Substitution fidelity hint-off 1.000, hint-on
  0.924–0.939 (floor 0.90); `hint_logged` reconstruction-vs-logged agreement 0.9992–1.000 (the
  reconstruction matches live truth almost exactly, so the mechanism reads stand on logged ground
  truth); coverage 1499–2569 events per block (floor 10).
- **Layer 1 PASS (gate replication).** `bal@30` chosen `jsd_norm` 0.337 / 0.372 / 0.387 at seeds
  101 / 202 / 303 (floor 0.25), `entropy_norm` 0.623 / 0.634 / 0.635 (floor 0.35), each beating its
  matched `bal@22` control (0.271 / 0.249 / 0.258) by 0.066–0.129 — past the pre-registered 0.02
  noise margin at every seed. The fresh-seed values bracket and slightly exceed Stage 6's
  0.307 / 0.344 / 0.376.
- **Layer 2 PASS (role stability).** At all three `bal@30` seeds Aki's top non-contribute chosen
  verb is `craft` and Cy's is `study`; cross-role bleed ≤ 0.027 (floor 0.05). No identity collapse.
- **Secondary attribution (reported, confirmatory).** Cy contribute drop 0.114–0.159 (≥ 0.10 every
  seed); mean Cy study rise 0.125 across seeds (floor 0.05), no seed below 0.116.
- **Advisory mechanism diagnostics (directional).** Cy hint firing rises 0.43–0.48 → 0.65–0.67
  bal@22→bal@30; `P(Cy study | hint)` 0.62–0.65 vs ≤ 0.003 absent, with the `absent_low` deconfound
  flat at ≤ 0.007; Cy obedience 0.62–0.65 (floor 0.30); the hint names `craft` for Aki and `study`
  for Cy at every seed.
- **R3 scene-collapse watch clears.** scene_completed 318–360 across all runs, `bal@30` ≈ `bal@22`;
  `json_fallback_rest` ≤ 2.

## Decision

1. **The Stage 6 Gate 9 PASS is upgraded from "first locked pass" to "replicated."** The claim's
   scope moves from "the tuned economy produced divergence at three seeds" to "the divergence is
   stable across fresh unseeded sampling draws at held-out seeds, with T fixed at 30." The
   ADR 0012/0013 causal narrative survives unchanged on new data.
2. **Phase 2 item 4 (roster generality) is now the live frontier.** Replication is settled for the
   two-resident roster; the open question is whether the effect generalizes across more residents,
   different soul-token weights, and different role pairings.
3. **The ground-truth hint logging stays in the live loop.** It retired the ADR 0013 reconstruction
   caveat (hint_logged ≥ 0.9992 everywhere) and is observation-only, so it carries forward as the
   audit's truth source for item 4 with no comparability cost.
4. **The pre-registration discipline is recorded as a pass, not a patch.** Unlike ADR 0013's C4,
   every layer here scored as drafted; the verdict needed no amendment. The corrected Decision-2
   criterion (top non-contribute verb) held cleanly on held-out data, validating the ADR 0013
   diagnosis of the C4 mis-drafting.

## Scope and limitations

- Two-resident roster, single model (`gemma4:26b`), T fixed at 30, three fresh seeds. The seed
  controls weather/scheduler RNG only; `gemma4:26b` sampling is unseeded, so this supports "stable
  across fresh stochastic runs" but does NOT decompose seed effects vs sampling noise (fixed-seed
  repeats would, out of scope).
- Roster generality (item 4) is untouched by this sweep.
- No mid-run restarts; the instrument gate was re-checked on logged ground truth and passes.

## Reproduction

Findings: `docs/economy-phase2-replication-findings.md`. Pre-registration:
`docs/economy-phase2-replication-runbook.md` (locked `2d4ef8b`). Sweep driver:
`scripts/run_rep30_sweep.sh`. Audit:
`scripts/replay_economy.py --audit bal=data/econ-rep30-bal22-s<seed> --audit bal@30=data/econ-rep30-bal30-s<seed>`.
Run dirs `data/econ-rep30-*` (untracked, kept locally).
