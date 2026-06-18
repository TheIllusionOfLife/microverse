# Phase 2 item 4(a) runbook II — R3 generality, economic + weights levers (ADR 0018)

Operator handoff + **pre-registration** for the two remaining ADR 0015 Decision 3(a) levers, after
the persona lever was rejected (ADR 0017: the stronger-travel Stranger persona BACKFIRED — Vesna's
`travel` share fell 0.156→0.054, `contribute` rose 0.720→0.787, Layer 2 broke). The diagnosis stands:
R3 fails because all three residents are **contribute-dominant** and the Stranger's specialty is
thinnest. ADR 0017 re-scoped the frontier toward levers that act on the **contribute pull** (not
travel-narrative prose) and the weights axis. Both use **existing, validated knobs — no new code** —
and the **default Stranger persona** (the travel variant is not used).

This work lands as **follow-up commits on PR #60** (one-PR discipline); the findings + ADR 0018 land
in the same PR after the reads.

## The two levers (why this matrix)

### Lever (a) — economic re-tune: `bal@42` (the strong bet)

The contribute-dominance is **society-wide** (baseline R3-s101 chosen-contribute: Aki 0.55, Cy 0.64,
Vesna 0.72). Raising the balanced contribute cost makes contribute **unaffordable more often**, so
agents are *forced* toward their cheap specialty (cost 6) regardless of how well they obey the hint —
attacking the dominance via affordability, not persuasion. This is the lever ADR 0015 pre-scoped as
the "drain-equated ~T = 42" re-tune for N = 3 (the weight-70 residents are scheduled less at N = 3, so
a higher T compensates for fewer draining opportunities). It directly addresses the intrinsic N = 3
scheduling dilution that the weights axis (b) cannot — at N = 3 no weighting can give each agent as
many turns as the dyad got, but a dearer contribute drains harder per turn.

**Offline pre-flight (zero LLM, round-robin always-contribute throttle ceiling, R3 roster):**

| T (bal contribute) | always-contribute sub_rate | contribute share | exec entropy_norm |
|---:|---:|---:|---:|
| 30 (baseline) | 0.288 | 0.712 | 0.432 |
| **42** | **0.546** | **0.454** | **0.572** |
| 48 | 0.615 | 0.385 | 0.584 |
| 54 | 0.666 | 0.334 | 0.586 |

T = 42 roughly halves the always-contribute ceiling (0.71→0.45) while keeping healthy entropy.
**Caveat (disclosed):** the synthetic sim is round-robin and over-models throttling for the
under-scheduled weight-70 agents (it has no weighted scheduling), so the *live* contribute share will
be **higher** than 0.45 — these numbers set the direction and the T choice, not a live prediction.
Going above 42 risks pushing agents to the free `rest` fallback; 42 is the moderate, pre-scoped step.

### Lever (b) — weights axis: Vesna 70 → 130 (the weak, pre-scoped bet)

ADR 0015 Decision 3(a) also named "higher 3rd weight." Bumping the Stranger to 130 restores
dyad-like scheduling share for her (130/300 ≈ 0.43, vs 70/240 ≈ 0.29 at the standard R3 weights),
testing whether more scheduling/draining yields more `travel`.

**Honest motivation caveat (pre-registered):** the original ADR 0015 firing pre-flight *predicted* the
weight-70 agents would under-fire (~0.20) at N = 3, but the **live R3 read refuted that** — Vesna
actually fired the hint 0.50 and Cy 0.40 (adequate). Vesna's bottleneck is **obedience** (0.31), not
firing, so adding scheduling weight is unlikely to fix it, and it *starves* Cy (70/300 ≈ 0.23). I
therefore **predict lever (b) likely does NOT clear the bar.** It is run because (i) the user scoped
both levers, (ii) it is the literal pre-scoped weights probe, and (iii) redistribution could still
lift Aki/Cy specialization. The pilot gate caps its cost at one ~6 h run if null.

---

## ===== DO NOT EDIT BELOW AFTER THE SWEEP LAUNCHES (pre-registered) =====

### Matrix

| lever | tag base | `MICROVERSE_ECONOMY` | `MICROVERSE_BAL_CONTRIBUTE` | `MICROVERSE_ROSTER` | persona |
|---|---|---|---|---|---|
| (a) economic | `econ-r3bal42` | `bal` | `42` | `artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70` | default |
| (b) weights | `econ-r3wt130` | `bal` | `30` | `artisan:Aki:100,scholar:Cy:70,stranger:Vesna:130` | default |

Each lever: seeds `{101, 202, 303}` × 3000 ticks, `--tempo 0`, energy knobs at live defaults
(max 100, regen 8). `MICROVERSE_STRANGER_PERSONA` **unset** (default persona). State dirs
`data/<tag-base>-s<seed>`, harvest `harvest/<tag-base>-s<seed>`. Seeds match the R3 baseline so the
only changed variable per lever vs the failing baseline is the lever itself.

**Pilot-then-continue (locked, per lever):** seed **101 first**; read it; apply the continuation rule
before launching 202/303 for that lever.

### Run

```bash
# lever (a) pilot, then continuation
nohup ./scripts/run_r3_levers2_sweep.sh bal42 101 > econ-r3bal42-s101.log 2>&1 &
nohup ./scripts/run_r3_levers2_sweep.sh bal42 202 303 > econ-r3bal42-rest.log 2>&1 &
# lever (b) pilot, then continuation
nohup ./scripts/run_r3_levers2_sweep.sh wt130 101 > econ-r3wt130-s101.log 2>&1 &
nohup ./scripts/run_r3_levers2_sweep.sh wt130 202 303 > econ-r3wt130-rest.log 2>&1 &
```

(The script writes each run's Gate 1–9 report to `data/<tag>/gate-report.json` via atomic `.tmp`+`mv`,
refuses to append to a partial run dir, and skips a run whose report already exists. Runs serialize on
the single GPU; a crashed run is re-run fresh, never restarted.)

### Instrument gate (checked before any behavioral verdict, per run)

From `scripts/replay_economy.py --audit bal@<T>=PATH` (T = 42 for lever a, 30 for lever b):
`fidelity ≥ 0.90` on both hint-on and hint-off subsets; `fidelity.hint_logged ≥ 0.90`; ≥ 10 events
per subset. Any failure → **INSTRUMENT-INVALID** (no behavioral verdict; diagnose first).

### Pass rule (locked — identical to ADR 0017)

Chosen (`parsed_verb`) stream via `gate9_verb_diversity(..., divergence_metric="mean_pairwise_jsd")`:

- **Layer 1 (hard):** `mean_pairwise_jsd ≥ 0.25` (ADR 0016 size-invariant bar; inherited floor).
- **Layer 2 (hard):** every resident's top NON-contribute chosen verb is its specialty
  (artisan→`craft`, scholar→`study`, stranger→`travel`), cross-bleed ≤ 0.05.

Per-seed PASS = Layer 1 AND Layer 2 (given instrument gate). **Per-lever verdict: ≥ 2/3 seeds PASS.**
`jsd_norm`, obedience, firing, `specialization_ratio`, `identity_verb_nmi` reported, not gated.

### Continuation rule (locked, per lever — operationalizes "clears or clearly trends up")

After a lever's pilot (seed 101) passes the instrument gate:

- **Continue 202/303** iff pilot `mean_pairwise_jsd ≥ 0.235` (clears 0.25, or > halfway up from the
  0.207 R3 baseline toward the floor).
- **Otherwise STOP** that lever and report the null/sub-threshold pilot (one pilot is sufficient to
  decline the remaining ~12 h for that lever).
- Pilot fails instrument gate → STOP, INSTRUMENT-INVALID.

The two levers are independent: each is gated on its own pilot.

### Verdict mapping (locked, per lever)

- **N=3 ACHIEVABLE (lever X)** — ≥ 2/3 seeds PASS Layer 1 (and Layer 2). That lever resolves the R3
  FAIL; the ADR 0015 reading is updated for that intervention.
- **N=3 STILL FAILS (lever X)** — < 2/3 PASS. The lever does not rescue R3; report whether it moved
  the mechanism at all (contribute share / obedience deltas).
- **LEVER COUNTERPRODUCTIVE (lever X)** — the lever worsens the mechanism (e.g. drives contribute
  *up* or forces a `rest` collapse), as the persona lever did in ADR 0017.
- **INSTRUMENT-INVALID** — any read run fails the instrument gate.

If BOTH levers fail, the combined reading is that R3's N = 3 contribute-dominance is robust to the
affordability and scheduling levers tested, strengthening the "intrinsic N = 3 difficulty" case (which
the persona lever could not establish). No post-hoc amendment.

### Quality counters (report, `MAX` not `SUM`)

`scene_completed` / `scene_aborted` (N = 3 collapse watch), `json_fallback_rest` (the `bal@42`
rest-collapse watch — a spike means T is too dear), `novelty_energy_hint_conflict`,
`artisan_empty_craft_coerced`.

### Honesty note

Fresh runs — no R3 read at `bal@42` or at the bumped weight has ever been measured, so the behavioral
numbers are genuinely unseen. The offline T-sweep above is a round-robin throttle-ceiling direction
finder, disclosed as over-modelling throttling, not a live prediction. **Pre-committed predictions:**
lever (a) `bal@42` is the *stronger* bet (affordability directly suppresses the society-wide
contribute-dominance and compensates for N = 3 scheduling dilution), but contribute-dominance proved
sticky in ADR 0017, so STILL FAILS is live. Lever (b) weights is the *weaker* bet — the live firing
already refuted its under-firing premise — and I predict it likely does NOT clear. Both branches are
committed per lever. This rule is committed to git before the first tick.

---

After the reads: `docs/economy-phase2-r3-levers2-findings.md` (old R3 vs bal@42 vs wt130 tables,
per-seed Layer 1/2, fidelity, contribute/obedience deltas) and
`docs/adr/0018-action-economy-r3-levers2.md` recording the per-lever verdicts, appended to PR #60.
