# Action-economy Phase 2 item 4 findings — roster generality of the `bal@30` Gate 9 PASS

Operator note recording the live roster-generality sweep that feeds **ADR 0015**. This is the sweep
pre-registered in `docs/economy-phase2-roster-generality-runbook.md` (locked at commit `4388a64`,
before the sweep's first tick). It tests whether the replicated `bal@30` Gate 9 PASS (ADR 0014,
default dyad Aki/Artisan + Cy/Scholar) **generalizes beyond that dyad**: the dose stays fixed at
T = 30 (nothing re-tuned), only the roster changes.

**Headline: PARTIAL.** Role-pairing transfers — **R2 (Artisan + Stranger) GENERALIZES** (3/3 seeds,
clean specialization). Adding a third resident does not — **R3 (Artisan + Scholar + Stranger) DOES
NOT GENERALIZE** (0/3 seeds clear Layer 1). The instrument gate passed on all six runs, so the
behavioral verdict is read against the locked two-layer rule with no post-hoc amendment.

The most important diagnostic: **R3's failure is NOT the pre-registered dose under-shoot.** The
offline pre-flight predicted R3's weight-70 agents would under-fire (~0.20) at fixed T = 30; in fact
they fired ~0.40–0.50 — adequate, comparable to the dyad's ~0.41. R3 failed for a different reason
(below), so the dose re-tune follow-up is refuted, not confirmed.

## Setup

- Matrix `{R2 role-swap, R3 all-roles}` × seeds `{101, 202, 303}` × 3000 ticks, one sweep,
  roster-outer / seed-inner. `MICROVERSE_ECONOMY=bal`, `MICROVERSE_BAL_CONTRIBUTE=30` (fixed),
  energy knobs at the live defaults (max 100, regen 8). State dirs `data/econ-roster-<r>-s<seed>`.
- **R2:** `Aki(Artisan, 100) + Vesna(Stranger, 70)` — different pairing (`craft` vs `travel`), the
  dyad's weight shape. **R3:** `Aki(Artisan, 100) + Cy(Scholar, 70) + Vesna(Stranger, 70)` — N=3 +
  a third specialty.
- Run detached 2026-06-14 13:10 → 2026-06-16 04:48 JST; R2 runs ~6h15m, R3 runs ~6h50m each.
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen** (`parsed_verb`)
  stream, exactly as Stage 6 / the replication. Audit via `replay_economy.py --audit bal@30=PATH`.
- No in-sweep `bal@22` control (item-4 criteria are absolute; kept the sweep at 6 runs).

## Instrument gate — PASS (all six runs)

Substitution fidelity hint-off / hint-on ≥ 0.90; `hint_logged` ≥ 0.90; coverage ≥ 10.

| run | hint-off | hint-on | hint-on n | hint_logged | hint_logged n |
|-----|---------:|--------:|----------:|------------:|--------------:|
| R2-s101 | 1.000 | 0.936 | 1978 | 1.000 | 2515 |
| R2-s202 | 1.000 | 0.921 | 1998 | 1.000 | 2564 |
| R2-s303 | 1.000 | 0.933 | 1997 | 1.000 | 2538 |
| R3-s101 | 1.000 | 0.949 | 1389 | 1.000 | 2549 |
| R3-s202 | 1.000 | 0.953 | 1385 | 0.9996 | 2555 |
| R3-s303 | 1.000 | 0.940 | 1376 | 0.9980 | 2532 |

Hint-off exact, hint-on 0.921–0.953, `hint_logged` ≥ 0.998 — the audit stands on logged ground
truth for both rosters, incl. the Stranger resident. **Instrument gate PASS** → the behavioral
verdict is valid.

## Layer 1 — society divergence (hard: chosen `jsd_norm ≥ 0.25`)

| run | n | jsd_norm | ≥ 0.25 | entropy_norm | ≥ 0.35 (legacy) | N-ceiling |
|-----|---|---------:|:------:|-------------:|:---------------:|----------:|
| R2-s101 | 2 | 0.3322 | ✅ | 0.6749 | ✅ | 0.387 |
| R2-s202 | 2 | 0.3745 | ✅ | 0.7096 | ✅ | 0.387 |
| R2-s303 | 2 | 0.3539 | ✅ | 0.6690 | ✅ | 0.387 |
| R3-s101 | 3 | 0.1964 | ❌ | 0.6507 | ✅ | 0.613 |
| R3-s202 | 3 | 0.1940 | ❌ | 0.6133 | ✅ | 0.613 |
| R3-s303 | 3 | 0.2115 | ❌ | 0.6675 | ✅ | 0.613 |

**R2 PASS all seeds** (jsd 0.33–0.37, bracketing the dyad's replicated 0.34–0.39). **R3 FAIL all
seeds** (jsd 0.19–0.21). Note the R3 signature: **society entropy is high** (0.61–0.67, near the
N=3 perfect-specialist ceiling 0.613 and far above 0.35) **while cross-agent jsd is low**. The
society uses many verbs, but the three agents are not differentiated *from each other* — the hallmark
of weak specialization, not a monoculture.

## Layer 2 — per-agent role stability (hard: top non-contribute verb = own specialty, bleed ≤ 0.05)

| run | result | detail |
|-----|--------|--------|
| R2-s101 | ✅ PASS | Aki→craft (bleed 0.000), Vesna→travel (0.001) |
| R2-s202 | ✅ PASS | Aki→craft (0.000), Vesna→travel (0.001) |
| R2-s303 | ✅ PASS | Aki→craft (0.000), Vesna→travel (0.003) |
| R3-s101 | ❌ FAIL | Aki→craft bleed **0.058** (>0.05); Cy/Vesna OK |
| R3-s202 | ✅ PASS | Aki 0.025, Cy 0.043, Vesna 0.025 |
| R3-s303 | ❌ FAIL | Vesna→travel bleed **0.058** (>0.05); Aki/Cy OK |

Across every run the specialization **direction** is correct — each agent's top non-contribute verb
is its own specialty. R2 is clean (bleed ≤ 0.003). R3 hovers at the 0.05 cross-bleed line: s202
passes, s101 and s303 just miss (0.058), consistent with weaker differentiation at N=3.

## Verdict

Per the locked rule (a roster GENERALIZES iff ≥ 2/3 seeds pass Layer 1 AND Layer 2, given the
instrument gate passed):

- **R2 — GENERALIZES.** 3/3 seeds pass both layers.
- **R3 — DOES NOT GENERALIZE.** 0/3 seeds pass Layer 1 (and only 1/3 pass Layer 2).

**Sweep verdict: PARTIAL.** Exactly one roster generalizes. **Surviving axis: role pairing** —
swapping the Scholar for a Stranger (craft vs travel) at the dyad's weight shape reproduces the
divergence cleanly. **Failing axis: more residents** — the three-resident roster does not clear the
cross-agent divergence floor. No post-hoc amendment: R3 fails as drafted.

## Why R3 failed — it is NOT the pre-registered dose under-shoot

The runbook pre-registered R3 as the at-risk cell on the hypothesis that its weight-70 agents would
under-fire (~0.20) at fixed T = 30. **That hypothesis is refuted by the data:**

| roster | agent | predicted firing | live firing | obedience to named verb |
|--------|-------|-----------------:|------------:|------------------------:|
| R2 | Aki (artisan) | 0.68 | 0.83–0.85 | 0.45–0.50 |
| R2 | Vesna (stranger) | 0.45 | 0.70–0.71 | **0.31–0.41** |
| R3 | Aki (artisan) | 0.45 | 0.67–0.71 | 0.50–0.55 |
| R3 | Cy (scholar) | 0.20 | 0.40–0.45 | 0.62–0.72 |
| R3 | Vesna (stranger) | 0.20 | 0.42–0.50 | **0.31–0.33** |

Live firing ran ~1.5–2× the offline prediction across the board (the "prefer-contribute" pre-flight
under-drained — real agents also spend on other dear verbs). R3's weight-70 agents fired ~0.40–0.50,
**adequate and comparable to the dyad's ~0.41.** Firing is not the bottleneck, so re-tuning T upward
(the pre-scoped ~T = 42 follow-up) would not rescue R3. **That follow-up is refuted.**

The actual drivers of the R3 failure:

1. **The Stranger specializes weakly.** Vesna's obedience to its `travel` hint is ~0.31–0.33 across
   BOTH rosters — far below the Scholar's `study` obedience (0.62–0.72) and the Artisan's `craft`
   (0.45–0.55), and barely above the 0.30 advisory floor. So even when the hint fires, Vesna picks
   `travel` only ~1/3 of the time and stays contribute-dominant (chosen-contribute 0.70–0.78 in R3).
   A weakly-specializing third agent that stays contribute-heavy looks similar to the others →
   little cross-agent divergence. `travel` appears to be a less "natural" LLM action than `craft` or
   `study`. In R2 (two agents) this weak third specialty is still enough to clear jsd; in R3 it is
   not.
2. **The log2(n)-normalized multi-JSD is a harder bar at N=3.** R3's high society entropy
   (0.61–0.67) shows the society IS diverse, but the cross-AGENT divergence is diluted because all
   three share `contribute` as the dominant verb and the normalization by log2(3) lowers the score
   for comparable specialization. The pre-registered jsd ≥ 0.25 floor — declared roster-invariant —
   turns out to demand stronger per-agent differentiation at N=3 than two strong specialists need at
   N=2.

The deconfound holds throughout: `P(specialty | hint)` is 0.31–0.72 vs `P(specialty | absent)`
≤ 0.038, with the `absent_low` stratum flat (≤ 0.029) — the hint TEXT drives the choice, the same
channel ADR 0013 established, on both rosters.

## Secondary / quality

- **Per-agent contribute vs specialty (chosen share):** R2 — Aki craft 0.38–0.43 / contribute
  0.52–0.54; Vesna travel 0.22–0.29 / contribute 0.56–0.63. R3 — Aki craft 0.36–0.38; Cy study
  0.25–0.33; Vesna travel **0.13–0.16** / contribute **0.70–0.78** (the weak third specializer).
- **R3 scene-collapse watch CLEARS.** `scene_completed` R3 339–349 vs R2 317–349 — comparable, no
  collapse at N=3 (3-author scenes completed normally). `json_fallback_rest` 3–6.
  `novelty_energy_hint_conflict` lower in R3 (287–381) than R2 (705–778), as expected: each agent is
  scheduled less at N=3 so fires less often.

## Honesty note

Seeds {101, 202, 303} were used by the replication on the dyad, but no run on the R2 or R3 roster
had ever been measured — every number here was unseen at pre-registration time. The two-layer rule,
instrument gate, verdict mapping, and the firing pre-flight were committed to git (`4388a64`) before
the sweep's first tick. The verdict is reported as drafted: R3 fails Layer 1, and the pre-registered
dose hypothesis is recorded as refuted rather than quietly replaced.

## Scope and limitations

- Single model (`gemma4:26b`), fixed dose T = 30, three fresh seeds per roster. Generality tested on
  two new rosters; the seed controls weather/scheduler RNG only (sampling is unseeded).
- This sweep does not decompose the R3 failure between the two drivers (weak Stranger specialization
  vs the N=3 JSD normalization). The follow-ups below target each.
- No mid-run restarts; the instrument gate passed on logged ground truth.
- **Roster preserved end-to-end (checked).** The Watchdog can register extra Stranger immigrants
  mid-run (echo-chamber rehab, `max_strangers=3`), which would silently change the roster under
  test. This was not suppressed during the sweep, but it **did not fire**: the distinct actor set in
  every run is exactly its registered roster (R2 = {Aki, Vesna}, R3 = {Aki, Cy, Vesna}), and gate9
  `n_agents` is 2 / 3 accordingly. So the measured roster equals the registered roster in all six
  runs. Future roster sweeps should pass an explicit Watchdog suppression to make this a guarantee
  rather than a post-hoc check.

## Follow-ups (re-scoped by this result)

1. **The dose re-tune (~T = 42) is shelved** — firing was already adequate; dose is not the R3
   bottleneck.
2. **Stronger third specialization:** retest R3 with a third resident whose specialty the model obeys
   as strongly as study/craft (e.g. a second Scholar or Artisan with a distinct verb), or raise the
   third agent's weight so it drains harder, to test whether a *strongly*-specializing third agent
   clears the N=3 jsd floor.
3. **Revisit the N-agent divergence criterion:** decide whether jsd ≥ 0.25 (log2(n)-normalized) is
   the right invariant bar at N=3, or whether the society-entropy-relative reading (R3 reached
   ~0.65 of a 0.613-ceiling-high entropy) better captures "broke monoculture + specialized" for
   N > 2. This is a measurement-design question for a future ADR, not a post-hoc rescue of this one.

## Reproduction

Runbook: `docs/economy-phase2-roster-generality-runbook.md` (pre-registration, locked `4388a64`).
Driver: `scripts/run_roster_gen_sweep.sh`. Per-run gate reports:
`data/econ-roster-<r>-s<seed>/gate-report.json`. Audit:
`uv run python scripts/replay_economy.py --audit bal@30=data/econ-roster-<r>-s<seed>` (run dirs
untracked, kept locally).
