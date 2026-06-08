# Action-economy Stage 6 runbook — tune-to-clear the `bal` near-miss (R2)

Operator handoff + **pre-registration** for the Stage 6 live A/B. Stage 5 (`bal`, ADR 0011)
specialized the scholar for the first time but landed a stable near-miss: cross-agent
`jsd_norm ≈ 0.237`, ~0.013 under the 0.25 Gate-9 floor, because the scholar still emits ~0.77
`contribute`. This stage acts on the pre-identified residual cause **R2** (the balanced
contribute target 22 does not drain the lower-weight scholar far enough) by raising the target.

This PR ships the **mechanism + the offline target-selection instrument + this pre-registration**
only. The live run is a separate scheduled compute job (~9 runs × ~6 h on `gemma4:26b`). The ADR
0008/0009/0010/0011 HALT stays in force until a PASS read. **Everything below the "DO NOT EDIT
AFTER THE LIVE READ BEGINS" line is locked before any live measurement** — that is what keeps a
0.013 chase from becoming threshold-fitting (Codex review).

## Why R2, and why this is not goalpost-tuning

Stage 5's binding term was the residual ~0.77 shared `contribute` mass, not entropy (0.544,
clears 0.35) and not the novelty/energy conflict (R4, ~11%, not binding). The scholar (Cy,
`soul_tokens=70`) is scheduled ~41% of ticks (vs Aki 100), so it regenerates ~19.4 energy
between its own actions. At `contribute=22` the per-action net drain is only ~19.4 − 22 ≈ −2.6,
so its pool barely falls below affordability, its scarcity hint rarely fires, and it keeps
choosing `contribute`. Raising the target raises the drain: −6.6 at 26, −10.6 at 30.

The risk of raising a metric-adjacent knob is threshold-fitting. Two guards make this honest:
1. **The target is selected OFFLINE from a pre-registered rule, with zero knowledge of the live
   jsd it produces** (the offline probe measures mechanical scarcity, not Gate 9 — they are
   different causal channels: the executor vs the persona hint).
2. **The pass rule below is locked before the live read and includes mechanism attribution
   against an in-sweep `bal@22` control**, so "cleared by raising a knob" cannot be confused with
   unseeded-sampling drift.

## Offline target selection (zero live compute) — DONE, locked

Instrument: `scripts/replay_economy.py --data <econ-off-run> --mode bal --bal-contribute <T>`.
It replays each locked **economy-OFF** Cy trace (the model's hint-free choices, ~92% contribute)
through the live `EnergyLedger` executor with **faithful whole-roster per-tick regen**
(`regen_all`; the prior per-actor approximation under-regenerated the 41%-scheduled scholar
~2.4× and saturated the probe — fixed this stage) and reports, per Cy free turn, the rate at
which `contribute` is out of reach while `study` is still affordable (the mechanical proxy for
the hint firing toward the specialty), and the rest-only rate.

**Pre-registered selection rule (set before reading the sweep):** `T* =` the smallest target on
the grid `{22,24,26,28,30,32}` whose **contribute-out / study-ok rate ≥ 0.55 at all three seeds**
AND **rest-only ≤ 0.05** (no starvation). Replay ranks mechanical pressure only; it does NOT
read Gate 9.

Result (3000-tick economy-off traces `data/econ-stage5-0-s{42,38,7}`):

| target T | contribute-out / study-ok (s42 / s38 / s7) | rest-only | ≥0.55 all seeds |
|---------:|:-------------------------------------------|:---------:|:---------------:|
| 22 | 0.249 / 0.234 / 0.222 | 0.000 | no |
| 24 | 0.346 / 0.334 / 0.330 | 0.000 | no |
| 26 | 0.430 / 0.435 / 0.420 | 0.000 | no |
| 28 | 0.507 / 0.506 / 0.509 | 0.000 | no |
| **30** | **0.557 / 0.569 / 0.562** | 0.000 | **yes** |
| 32 | 0.597 / 0.619 / 0.614 | 0.000 | yes |

The `T=22` row (~0.235) is the Stage-5 baseline and matches the live observation that the hint
fired too rarely to move Cy off contribute. **`T* = 30`** is the smallest grid step clearing the
pre-registered threshold at all three seeds — ~2.3× the scarcity pressure of 22, no rest
starvation. Reproduce:
`for T in 22 24 26 28 30 32; do for S in 42 38 7; do uv run python scripts/replay_economy.py --data data/econ-stage5-0-s$S --mode bal --bal-contribute $T; done; done`

---

## ===== DO NOT EDIT BELOW AFTER THE LIVE READ BEGINS (pre-registered) =====

### Locked target

`MICROVERSE_BAL_CONTRIBUTE=30` for the `bal@T*` arm.

### A/B matrix — 9 runs

| arm | mode | `MICROVERSE_BAL_CONTRIBUTE` | role in the design |
|-----|------|----------------------------|--------------------|
| `adv`    | `adv` | unset | reproduces the one-sided (artisan-only) Stage-4/5 mechanism |
| `bal@22` | `bal` | unset (→ natural dearest 22) | **in-sweep control**: isolates the raised target from sampling drift |
| `bal@30` | `bal` | `30` | the tune-to-clear arm |

`{adv, bal@22, bal@30}` × seeds `{42, 38, 7}` × 3000 ticks, **one sweep** (concurrent
comparators — `gemma4:26b` sampling is unseeded, so an in-sweep `bal@22` is the only causally
clean control; the archived Stage-5 `bal@22` is a weak reference, not a control).

### Run (deferred — separate compute job)

```bash
for SEED in 42 38 7; do
  for ARM in adv bal22 bal30; do
    case $ARM in
      adv)   M=adv; BAL="" ;;
      bal22) M=bal; BAL="" ;;
      bal30) M=bal; BAL=30 ;;
    esac
    TAG="${ARM}-s${SEED}"
    MICROVERSE_ECONOMY=$M MICROVERSE_BAL_CONTRIBUTE=$BAL \
    MICROVERSE_DATA=data/econ-stage6-$TAG \
    MICROVERSE_HARVEST=harvest/econ-stage6-$TAG \
      uv run python -m microverse.run --ticks 3000 --tempo 0 --seed $SEED
    uv run python scripts/spike_workshop_measure.py \
      --data data/econ-stage6-$TAG --harvest harvest/econ-stage6-$TAG
  done
done
```

### Pass rule (locked)

Measured on the **chosen** (`parsed_verb`) stream via `scripts/spike_workshop_measure.py`
`gate9_verb_diversity`. `bal@30` **PASSES Gate 9** iff, **at all three seeds**:

1. `entropy_norm ≥ 0.35` AND `jsd_norm ≥ 0.25`.

AND the pass is attributed to the lever (not sampling drift), **paired against the in-sweep
`bal@22` at the same seed**, at all three seeds:

2. Cy chosen-`contribute` share drops **≥ 0.10 absolute** vs `bal@22`.
3. Cy's largest non-`contribute` productive verb **is `study`**.
4. Mean (over seeds) Cy chosen-`study` share rises **≥ 0.05** vs `bal@22`.

If any condition fails at any seed, **Gate 9 stays FAILED** and the HALT stays. Any further
target needs a fresh pre-registered sweep — no relaxing 0.25, no re-picking T after the read.

### Quality counters (report, use `MAX` not `SUM` — cumulative time-series)

`novelty_energy_hint_conflict` (expect ≥ Stage-5 ~430, since Cy is pushed harder off contribute),
`artisan_empty_craft_coerced`, `parse_fallback`, and `scene_completed` (R3 watch: a harder-drained
roster contributes less freely; confirm scenes do not collapse).

### Risks

- **R1 — the scholar still won't obey the hint.** The offline probe measures only that the hint
  *fires* (contribute out of reach) more often at 30 (~0.56 vs ~0.24 of Cy turns); whether
  `gemma4:26b` *acts* on the louder signal is the live unknown. At 22 a ~0.24 firing rate left
  cy_contribute ~0.77; 30 roughly doubles the firing rate. If chosen-contribute barely moves, the
  binding constraint is hint obedience, not drain margin — which raising the target cannot fix,
  and the arc should be parked (R2 would then be refuted, not merely un-cleared).
- **R3 — over-drain thins scenes.** Watch `scene_completed`; `bal` does not energy-gate scene
  initiation, but a harder-drained roster contributes less freely.
- **Starvation** is pre-excluded: `study` (cost 6) stays affordable at every probed target
  (rest-only 0.000), so 30 pressures Cy off contribute without collapsing it onto rest.

After the read, write `docs/economy-stage6-findings.md` + the ADR 0012 follow-up with the
per-seed table and the PASS/HALT decision. **If `bal@30` clears all four conditions, Gate 9
PASSes and Phase 2 unblocks. If it misses, the economy lever's two-resident ceiling stands and
the arc is parked** for a different mechanism (e.g. a larger heterogeneous roster).
