# Action-economy Stage 6 findings — tune-to-clear the balanced contribute target (`bal@30`)

Operator note recording the Stage 6 live A/B that feeds **ADR 0012**. This is the deferred live
run pre-registered in `docs/economy-stage6-runbook.md` (the lever + offline instrument shipped in
PR #55). It acts on the pre-identified residual cause **R2** from ADR 0011: at the natural
balanced contribute cost (22) the lower-weight scholar does not drain far enough, so its scarcity
hint rarely fires and cross-agent `jsd_norm` stalls at a stable ~0.237 near-miss. Stage 6 raises
the balanced contribute target to the pre-registered **T\* = 30** and re-reads Gate 9.

**Headline: Gate 9 PASSES at `bal@30`, all three seeds, against the locked four-condition rule.**
First PASS in the entire action-economy arc; the ADR 0008/0009/0010/0011 HALT is lifted by this
read and Phase 2 unblocks.

## Setup

- Runner (per arm `{adv, bal@22, bal@30}` × seed `{42, 38, 7}`), model `gemma4:26b`, isolated
  `MICROVERSE_DATA`/`MICROVERSE_HARVEST` per run, ~6.3 h / ~4580 agent events each:

  ```bash
  MICROVERSE_ECONOMY=$MODE MICROVERSE_BAL_CONTRIBUTE=$BAL \
    uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
  ```
- Arms: `adv` (substitution + honest hint, the one-sided Stage-4 state where only the artisan
  specializes); `bal@22` (`bal` at the natural dearest 22 — the **in-sweep control**, isolating
  the raised target from unseeded sampling drift); `bal@30` (the tune-to-clear arm,
  `MICROVERSE_BAL_CONTRIBUTE=30`).
- One sweep, seed-outer / arm-inner, run detached 2026-06-09 → 2026-06-11. `gemma4:26b` sampling
  is unseeded, so each seed's `bal@22` control is paired temporally adjacent to its `bal@30`; the
  archived Stage-5 `bal@22` is a weak reference, not a control.
- Metric: `gate9_verb_diversity` (`scripts/spike_workshop_measure.py`), **chosen** (`parsed_verb`)
  stream. **Gate 9 PASS = chosen `entropy_norm ≥ 0.35` AND chosen `jsd_norm ≥ 0.25`.** Headline is
  `jsd_norm` (cross-agent divergence — ADR 0007's real target).
- **T\* = 30 was selected offline** from a pre-registered mechanical rule (`replay_economy.py`,
  smallest grid target with contribute-out/study-ok ≥ 0.55 at all three seeds, rest-only ≤ 0.05),
  with zero knowledge of the live jsd it would produce. See the runbook for the selection table.

## Pre-registered pass rule (locked before reading)

`bal@30` PASSES iff, **at all three seeds**: (1) `entropy_norm ≥ 0.35` AND `jsd_norm ≥ 0.25`; AND,
attributed to the lever vs the in-sweep `bal@22` at the same seed — (2) Cy chosen-contribute drops
≥ 0.10 absolute, (3) study is Cy's top non-contribute productive verb, (4) mean (over seeds) Cy
chosen-study share rises ≥ 0.05. No relaxing 0.25, no re-picking T after the read.

## Results (all 9 runs, chosen stream)

`aki_craft` = Aki's chosen (`parsed_verb`) share of its OWN specialty (craft); `cy_study` /
`cy_contrib` = Cy's chosen study / contribute shares. `conflict` = `novelty_energy_hint_conflict`
(cumulative-metric MAX). `scene` = `scene_completed` (MAX; R3 watch).

| arm     | seed | entropy_norm | jsd_norm  | aki_craft | cy_study | cy_contrib | conflict | scene | Gate 9 |
|---------|------|-------------:|----------:|----------:|---------:|-----------:|---------:|------:|:------:|
| `adv`   | 42   | 0.377        | 0.150     | 0.219     | 0.028    | 0.946      | 80       | 338   | FAIL   |
| `bal@22`| 42   | 0.550        | 0.252     | 0.270     | 0.172    | 0.776      | 440      | 341   | ctrl   |
| `bal@30`| 42   | 0.628        | **0.307** | 0.271     | **0.260**| **0.674**  | 703      | 335   | **PASS** |
| `adv`   | 38   | 0.347        | 0.160     | 0.216     | 0.015    | 0.961      | 65       | 362   | FAIL   |
| `bal@22`| 38   | 0.525        | 0.240     | 0.251     | 0.164    | 0.786      | 425      | 344   | ctrl   |
| `bal@30`| 38   | 0.623        | **0.344** | 0.301     | **0.258**| **0.683**  | 681      | 332   | **PASS** |
| `adv`   | 7    | 0.363        | 0.175     | 0.246     | 0.016    | 0.963      | 63       | 353   | FAIL   |
| `bal@22`| 7    | 0.539        | 0.220     | 0.256     | 0.145    | 0.785      | 405      | 322   | ctrl   |
| `bal@30`| 7    | 0.630        | **0.376** | 0.306     | **0.289**| **0.663**  | 685      | 320   | **PASS** |

Means: jsd `adv`/`bal@22`/`bal@30` 0.162 / 0.237 / **0.342**; `cy_contrib` 0.957 / 0.782 /
**0.673**; `cy_study` 0.020 / 0.160 / **0.269**; entropy 0.362 / 0.538 / **0.627**.

### Pass-rule scorecard

| condition | s42 | s38 | s7 | verdict |
|-----------|-----|-----|-----|:------:|
| 1. jsd ≥ 0.25 ∧ entropy ≥ 0.35 | 0.307 / 0.628 | 0.344 / 0.623 | 0.376 / 0.630 | PASS |
| 2. Cy contribute drop ≥ 0.10 vs `bal@22` | −0.102 | −0.103 | −0.122 | PASS |
| 3. study is Cy's top non-contribute verb | 0.260 vs speak 0.034 | 0.258 vs 0.036 | 0.289 vs 0.027 | PASS |
| 4. mean Cy study rise ≥ 0.05 vs `bal@22` | +0.088 | +0.094 | +0.144 → mean **+0.109** | PASS |

**All four conditions clear at all three seeds. Gate 9 PASSES.**

## Findings

1. **`bal@30` clears Gate 9 with margin at every seed.** jsd 0.307 / 0.344 / 0.376 (worst-case
   +0.057 over the 0.25 floor), entropy 0.623–0.630 (far over 0.35). Unlike Stage 5's hair-width
   ~0.237 near-miss, this is a clean pass, not a boundary read.

2. **The two agents now specialize onto *disjoint* verbs — the cross-agent property Gate 9 exists
   to measure.** In `bal@30` Aki's non-contribute mass is ~98% `craft` (craft 0.271/0.301/0.306,
   study 0.010–0.018) while Cy's is ~90% `study` (study 0.260/0.258/0.289, craft 0.010–0.017).
   Near-zero cross-leakage: each resident retreats to its *own* specialty over a reduced shared
   contribute floor. jsd crossing 0.25 is therefore real divergence, not one agent's noise.

3. **Clean monotone dose-response 22 → 30 confirms the lever is causal, not threshold-fit.** As the
   balanced contribute target rises, Cy contribute falls 0.782 → 0.673, Cy study rises 0.160 →
   0.269, and jsd rises 0.237 → 0.342 — monotone at every seed. Because T\* = 30 was fixed offline
   from a mechanical executor rule with no sight of the live jsd, the pass is confirmatory of R2,
   not a post-hoc goalpost move. The offline scarcity probe (contribute-out/study-ok ~0.24 at 22 vs
   ~0.56 at 30) predicted exactly this firing-rate increase; **R1 (would the model obey the louder
   hint?) is answered yes** — the chosen stream moves, not just the executed one.

4. **The `adv` controls reproduce the one-sided Stage-4/5 mechanism.** Fresh unseeded `adv` runs
   give jsd 0.150 / 0.160 / 0.175 with Cy stuck on contribute (~0.95–0.96) — artisan-only
   specialization, confirming the `bal@30` effect is causal to the raised target, not a sweep
   artifact. The `bal@22` controls straddle the 0.25 line (0.252 / 0.240 / 0.220), reproducing the
   Stage-5 near-miss and confirming the metric is sampling-noise-dominated *at* the threshold —
   which is exactly why the verdict rests on lever attribution (conditions 2–4), not raw pass/fail
   of any single boundary run.

5. **R3 (over-drain thins scenes) does not fire.** `scene_completed` under `bal@30` is 335 / 332 /
   320 — alongside `bal@22` (341 / 344 / 322) and only modestly below `adv` (338 / 362 / 353). The
   harder-drained roster still completes scenes at a comparable rate; the lever does not collapse
   collaborative work.

6. **The novelty/energy hint conflict rose as predicted but is non-binding.** Conflict climbs to
   ~681–703 under `bal@30` (vs `bal@22` ~405–440, `adv` ~63–80) — expected, since Cy is pushed
   harder off contribute so novelty pressure fights the energy hint more often. Specialization
   happened anyway. The conflict is a recorded cost of harder draining, not a cap on the result.

## Limitations (scope of the claim)

- **Two-resident roster (Aki/artisan, Cy/scholar).** JSD across two agents is a coarse divergence
  estimate; generality to larger or differently-weighted rosters is untested.
- **Hint-mediated, not a hard mechanical filter.** The only economy → chosen-verb channel is the
  per-agent persona scarcity hint. The claim is "a hint-mediated action economy shifts the model's
  *chosen* verbs toward role specialties," NOT "energy costs mechanically force actions." A
  different base model may obey the hint differently.
- **T\* = 30 was target-selected** (offline mechanical rule, raise-only). The pass is confirmatory
  of R2 at this roster/model, not evidence that 30 is a universal constant.
- **Single model, three seeds.** `gemma4:26b`, unseeded sampling; the in-sweep `bal@22` control is
  valuable but is one paired comparator per seed, not many repeated live runs. Held-out replication
  on fresh seeds (T fixed) is the natural confirmation.
- Persona prompts were not tuned (ADR 0008 carried forward); the result is a property of the
  unmodified personas + the balanced cost table at contribute = 30.

## Reproduction

```bash
# per arm in {adv, bal22, bal30}, seed in {42, 38, 7}:
#   adv   -> MODE=adv BAL=""    bal22 -> MODE=bal BAL=""    bal30 -> MODE=bal BAL=30
MICROVERSE_ECONOMY=$MODE MICROVERSE_BAL_CONTRIBUTE=$BAL \
MICROVERSE_DATA=data/econ-stage6-$ARM-s$SEED \
MICROVERSE_HARVEST=harvest/econ-stage6-$ARM-s$SEED \
  uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
uv run python scripts/spike_workshop_measure.py \
  --data data/econ-stage6-$ARM-s$SEED --harvest harvest/econ-stage6-$ARM-s$SEED
# read .gate_9_verb_diversity.chosen.{society_entropy_norm,jsd_norm} and .pass

# offline T* selection (zero live compute):
for T in 22 24 26 28 30 32; do for S in 42 38 7; do
  uv run python scripts/replay_economy.py --data data/econ-stage5-0-s$S --mode bal --bal-contribute $T
done; done
```

Pre-registration: `docs/economy-stage6-runbook.md`. Decision record: ADR 0012. Lever + offline
tests: PR #55. Stage 5 read: ADR 0011 / `docs/economy-stage5-findings.md`.
