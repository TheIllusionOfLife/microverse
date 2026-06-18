# Phase 2 item 4(a) runbook — R3 retest with a strongly-specializing Stranger (ADR 0017)

Operator handoff + **pre-registration** for ADR 0015 Decision 3(a). The roster-generality sweep
(ADR 0015) found **PARTIAL**: R2 (Artisan+Stranger) generalizes, but **R3 (Artisan+Scholar+Stranger,
N=3) FAILS** the divergence floor at all three seeds. ADR 0016 then removed the `log2(n)` penalty
(size-invariant `mean_pairwise_jsd`) and **R3 still failed** — 0.207 / 0.206 / 0.223 < 0.25 — so the
failure is not a normalization artifact. The findings + audit pinned the residual cause on the
**Stranger's weak travel-obedience (~0.31)**: Aki obeys `craft` and Cy obeys `study` (identity-led),
but Vesna's `travel` tail is the weakest, all three stay contribute-dominant, and the cross-agent
divergence is dragged down by Vesna's thin specialty.

ADR 0015 Decision 3(a) scopes this retest: does R3 clear the bar with a **strongly-specializing 3rd
agent** ("role/verb obeyed like study/craft")? This is a **causal test of *why* N=3 fails** — the
weak stranger, or intrinsic roster-size difficulty.

This PR ships the **`MICROVERSE_STRANGER_PERSONA` toggle + the stronger-travel persona variant + this
pre-registration**; the sweep launches right after the pre-registration commit and the findings +
ADR 0017 land in the same PR after the read.

## The intervention (why the persona lever, calibrated not forced)

The independent variable is a **travel-leaning Stranger persona** (`persona_stranger_travel.j2`,
selected by `MICROVERSE_STRANGER_PERSONA=travel`). The default Stranger frames its identity around
*being an outsider* (contrast, fresh perspective) — which points at no verb in particular, so
`travel` gets no identity pull and obedience stays ~0.31. The variant reframes the identity around
**movement / the road** (a wayfarer between settlements), so `travel` becomes the natural expression
of the role.

**Calibration (the anti-fitting discipline):** the Scholar obeys `study` not because of a
verb-forcing rule but because its identity is that of an observer/note-taker — an *identity-led*
lean. The travel variant matches that strength: it reframes identity, it does **not** add a "default
to travel" hard rule. The goal is "obeyed like study/craft," not "travel forced harder than any
other role." The cost table is **untouched** (Stranger already has `travel: 6.0` cheapest), so the
experiment isolates obedience, not economics.

**Why not weight or both:** ADR 0015 Decision 3(a) also named "higher 3rd weight," but weight drives
*scheduling frequency*, not per-turn obedience (the diagnosed failure), so it is a weaker lever for
this specific cause; combining levers would confound the read. The single persona lever is the
cleanest causal test of the diagnosed mechanism.

## What is new in the instrument (and why it does not break comparability)

Only a new **default-off env toggle**: `MICROVERSE_STRANGER_PERSONA` (validated at config import,
mirroring `_parse_bal_contribute`). Unset → `default` → the persona, the Watchdog rehab path, and the
frozen R2/dyad conditions are **byte-identical** to ADR 0015 (verified: the default-stranger render
and `persona_template` are unchanged when the flag is absent). The audit (`replay_economy.py`), the
gate (`spike_workshop_measure.py`), the roster hook, and the energy knobs are all unchanged. The gate
report already reports `mean_pairwise_jsd` additively (ADR 0016), so no measurement code changes for
this read.

---

## ===== DO NOT EDIT BELOW AFTER THE SWEEP LAUNCHES (pre-registered) =====

### Matrix — up to 3 runs (1 pilot, then 2 on continuation)

| roster | `MICROVERSE_ROSTER` | residents | persona |
|--------|---------------------|-----------|---------|
| R3-strong | `artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70` | Artisan(100) + Scholar(70) + Stranger(70) | `travel` |

R3 roster **unchanged** from ADR 0015 (only the Stranger persona differs). Seeds `{101, 202, 303}`
× 3000 ticks. `MICROVERSE_ECONOMY=bal`, `MICROVERSE_BAL_CONTRIBUTE=30` (**fixed**),
`MICROVERSE_STRANGER_PERSONA=travel`, energy knobs at the live defaults (max 100, regen 8). State
dirs `data/econ-r3strong-s<seed>`, harvest `harvest/econ-r3strong-s<seed>`. Seeds match ADR 0015 R3
so the only changed variable vs the failing baseline is the persona.

**Pilot-then-continue (scope, locked):** seed **101 first** as a pilot; read it; apply the
continuation rule below before launching 202/303. The continuation decision is mechanical (no
re-tuning).

### Run

```bash
# pilot
nohup ./scripts/run_r3_strong_sweep.sh 101 > econ-r3strong-s101.log 2>&1 &
# continuation (only if the locked rule below fires)
nohup ./scripts/run_r3_strong_sweep.sh 202 303 > econ-r3strong-rest.log 2>&1 &
```

(The script writes each run's Gate 1–9 report to `data/econ-r3strong-s<seed>/gate-report.json` via an
atomic `.tmp`+`mv`, refuses to append to a partial run dir, and skips a run whose report already
exists. A crashed run is re-run fresh, never restarted.)

### Instrument gate (checked before any behavioral verdict is read)

Per run, from `scripts/replay_economy.py --audit bal@30=PATH`:

- `fidelity` (predicted-vs-logged substitution) ≥ 0.90 on both the hint-on and hint-off subsets
  (0.90–0.91 satisfies but is flagged borderline);
- `fidelity.hint_logged` (reconstruction vs logged ground truth, full `(fired, verb)` agreement)
  ≥ 0.90;
- coverage: the hint-on fidelity subset and the `hint_logged` block each have ≥ 10 events per run.

If any run fails the instrument gate, the verdict is **INSTRUMENT-INVALID**: no behavioral verdict is
read, and the instrument fault is diagnosed before any re-run.

### Pass rule (locked)

Measured on the **chosen** (`parsed_verb`) stream via `scripts/spike_workshop_measure.py`
`gate9_verb_diversity(..., divergence_metric="mean_pairwise_jsd")`.

- **Layer 1 — society divergence (hard):** chosen-stream **`mean_pairwise_jsd ≥ 0.25`** (ADR 0016's
  size-invariant metric, the correct N>2 bar; floor inherited from the unchanged N=2 calibration).
  `jsd_norm` is **reported** alongside but not gated.
- **Layer 2 — per-agent role stability (hard, unchanged from ADR 0015):** for EVERY resident i with
  specialty s_i (artisan→`craft`, scholar→`study`, stranger→`travel`): i's top NON-contribute chosen
  verb is s_i, AND i's chosen-share of every OTHER resident's specialty ≤ 0.05 (no cross-role bleed).
  In particular **Vesna's top non-contribute chosen verb must be `travel`** (the positive control for
  the intervention).

**Per-seed verdict:** PASS iff Layer 1 AND Layer 2 hold (given the instrument gate passed).
**Sweep verdict:** ≥ 2 of 3 seeds PASS.

### Continuation rule (locked — operationalizes "clears or clearly trends up")

After the pilot (seed 101) passes the instrument gate:

- **Continue to seeds 202 and 303** iff pilot chosen-stream `mean_pairwise_jsd ≥ 0.235` — i.e. it
  clears the 0.25 floor, OR sits more than halfway up from the failing R3 baseline (0.207) toward the
  floor (midpoint 0.2285, rounded to 0.235). This is the "clears or clearly trends up" branch.
- **Otherwise STOP** (pilot `mean_pairwise_jsd < 0.235` AND Vesna's travel-obedience not materially
  above the ~0.31 baseline): report "the persona lever produced no material movement; full sweep not
  warranted." A single null pilot is sufficient to decline the remaining ~12 h.
- If the pilot fails the instrument gate → STOP, INSTRUMENT-INVALID, diagnose before any re-run.

The pilot read is reported to the user either way, before continuing.

### Verdict mapping (locked)

- **N=3 ACHIEVABLE WITH STRONG SPECIALIZER** — ≥ 2/3 seeds PASS Layer 1 (and Layer 2). The ADR 0015
  R3 FAIL was driven by the weak Stranger, not intrinsic roster-size difficulty; the ADR 0015 R3
  reading is updated and N=3 generality is demonstrated under a strong third specialist.
- **N=3 STILL FAILS EVEN WITH STRONG SPECIALIZER** — < 2/3 PASS Layer 1 despite Vesna's travel
  obedience rising. Roster-size difficulty is intrinsic at N=3 under bal@30 (contribute-dominance +
  thinner per-agent scheduling, not the stranger's persona); the PARTIAL verdict deepens.
- **LEVER INERT** — the pilot (or sweep) shows Vesna's travel obedience unchanged from ~0.31; the
  persona reframe did nothing, and no divergence conclusion is drawn (reported as a null lever, not a
  roster-size verdict).
- **INSTRUMENT-INVALID** — any read run fails the instrument gate.

No partial credit, no post-hoc amendment: a Layer that fails as drafted is reported as drafted.

### Secondary metrics (reported, NOT gating)

Per agent, per seed: chosen-`contribute` share; chosen-specialty share; **Vesna's travel-obedience
vs the ~0.31 ADR 0015 baseline** (the IV's direct effect); live hint firing rate; `jsd_norm` (for
continuity with ADR 0015); `specialization_ratio` and `identity_verb_nmi` (ADR 0016 diagnostics, for
cross-family corroboration of the divergence ordering). Old R3 (default persona) vs R3-strong is the
headline comparison.

### Quality counters (report, `MAX` not `SUM`)

`scene_completed` / `scene_aborted` (3-author collapse watch at N=3), `novelty_energy_hint_conflict`,
`json_fallback_rest`, `artisan_empty_craft_coerced`.

### Honesty note

These are **fresh runs** — no run on the R3-strong (travel-persona) condition has ever been measured,
so every behavioral number is genuinely unseen at pre-registration time (unlike the ADR 0016
frozen-data re-analysis, this carries real blindness). The persona edit is the disclosed independent
variable, calibrated to the Scholar's identity-led study lean (no verb-forcing rule), and the cost
table is untouched.

**Pre-committed prediction:** ACHIEVABLE is *more likely* than STILL FAILS, because ADR 0015/0016
isolated the weak Stranger as the residual cause and this lever targets exactly that — Vesna's travel
obedience should rise above ~0.31 toward the scholar-like ~0.5. But the required lift on Layer 1
(0.207 → ≥ 0.25, +0.043 on `mean_pairwise_jsd`) is non-trivial and contribute-dominance persists
under bal@30, so STILL FAILS remains a live, interpretable outcome. Both branches are committed
above; neither is assumed away. This rule is committed to git before the sweep's first tick.

---

After the read: `docs/economy-phase2-r3-strong-specializer-findings.md` (old R3 vs R3-strong table,
per-seed Layer 1/2, fidelity, obedience deltas) and `docs/adr/0017-action-economy-r3-strong-specializer.md`
recording the ACHIEVABLE / STILL FAILS / LEVER INERT / INSTRUMENT-INVALID verdict, appended to this PR.
