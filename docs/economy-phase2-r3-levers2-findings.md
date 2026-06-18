# Findings — R3 generality levers II (ADR 0018)

Fresh live reads of the two remaining ADR 0015 Decision 3(a) levers (after the persona lever was
rejected in ADR 0017), both on the **default** Stranger persona and existing knobs (no new code).
Pre-registration: `docs/economy-phase2-r3-levers2-runbook.md` (locked before launch).

**Verdicts:**
- **Lever (a) economic `bal@42`: N=3 ACHIEVABLE.** Layer 1 (divergence) rescued on all three seeds;
  ≥2/3 pass both layers. The R3 FAIL was a **fixed-dose artifact**, not intrinsic roster-size
  difficulty — the mechanism generalizes to N=3 once the contribute cost is rescaled for the diluted
  per-agent scheduling.
- **Lever (b) weights `wt130`: N=3 STILL FAILS.** A redistribution wash; stopped at the pilot per the
  locked rule, confirming the pre-registered prediction.

## Lever (a) — economic `bal@42` (R3 100/70/70, default persona)

### Layer 1 — society divergence: PASS on all three seeds

| seed | `mean_pairwise_jsd` | baseline (bal@30) | `jsd_norm` | `identity_verb_nmi` | fidelity | fallback | Layer 1 |
|---|---:|---:|---:|---:|---:|---:|:---:|
| s101 | **0.3053** | 0.2073 | 0.2929 | 0.260 | 0.960 | 0.0% | PASS |
| s202 | **0.2990** | 0.2063 | 0.2871 | 0.256 | 0.966 | 3.0% | PASS |
| s303 | **0.3261** | 0.2231 | 0.3131 | 0.281 | 0.961 | 0.1% | PASS |

Every seed clears 0.25 decisively (baseline 0.206–0.223 all failed). `identity_verb_nmi` rose ~0.19 →
~0.26–0.28 — an independent-family confirmation that agents became genuinely more
distinguishable-by-verb, not just that the gate metric moved. Instrument gate passes on every seed.

### Layer 2 — per-agent role stability: PASS on 2/3 (s202, s303); s101 fails on Aki cross-bleed

Chosen-stream shares (specialty in **bold**; cross-bleed = share of another resident's specialty):

| seed | Aki (craft) | Cy (study) | Vesna (travel) | Aki study cross-bleed |
|---|---|---|---|---:|
| s101 | **craft 0.435**, contrib 0.451, study 0.070 | **study 0.486**, contrib 0.424 | contrib 0.674, **travel 0.175** | 0.070 ❌ (>0.05) |
| s202 | **craft 0.428**, contrib 0.475, study 0.050 | **study 0.470**, contrib 0.448 | contrib 0.622, **travel 0.189** | 0.050 ✅ |
| s303 | **craft 0.493**, contrib 0.433, study 0.049 | **study 0.459**, contrib 0.459 | contrib 0.623, **travel 0.182** | 0.049 ✅ |

Every resident's **top non-contribute verb is its own specialty on every seed** (Cy's `study` even
ties/leads `contribute` outright). The only Layer-2 blemish is **Aki's `study` cross-bleed**, which
hovers right at the 0.05 floor (0.049 / 0.050 / 0.070). This is **structural, not echo-chamber bleed**:
`study` (cost 14) is the artisan's second-cheapest productive verb, so when dear contribute is
unaffordable and `craft` has just drained the pool, Aki occasionally diverts to `study`. It is present
in the baseline too (s101 baseline Aki study 0.058). Under the strict locked rule s101 is a per-seed
FAIL; s202 and s303 pass.

**Per-lever verdict: ≥2/3 seeds pass both layers (s202, s303) → N=3 ACHIEVABLE.** Layer 1 is the
robust, clean win (3/3); the verdict rests on 2/3 only because of the marginal Aki cross-bleed at one
seed.

### Mechanism — exactly the pre-registered theory

Making `contribute` dear (42) suppresses the **society-wide** contribute-dominance via affordability,
so the strong-specialty agents break out into their specialty. Versus the baseline:

- **Cy (scholar)** specializes hardest: study 0.277 → 0.459–0.486, now tying or leading `contribute`.
- **Aki (artisan)**: craft 0.358 → 0.428–0.493.
- **Vesna (stranger)**: travel 0.156 → 0.175–0.189 (improves modestly; still the weakest specialist,
  consistent with ADR 0017's finding that the Stranger is obedience-limited — but bal@42 clears the
  bar through the two strong specialists without needing to fix the Stranger).

This is the lever ADR 0015 pre-scoped as the "drain-equated ~T = 42" re-tune; the offline round-robin
T-sweep set 42 (always-contribute ceiling 0.71 → 0.45), and the live read confirms it.

## Lever (b) — weights `wt130` (Vesna 70→130, bal@30, default persona): STILL FAILS

Pilot seed 101 (instrument gate valid: fidelity 0.954): `mean_pairwise_jsd` **0.2087** ≈ baseline
0.207, below the 0.235 continuation bar → **STOPPED at the pilot** per the locked rule; 202/303 not
run.

It is a **redistribution wash**. Bumping Vesna to 130 scheduled her far more (free turns 768 → 1090)
and did lift her travel (0.156 → 0.233, her best across all conditions) — but it **starved Aki and Cy**
(free turns fell), whose contribute-dominance *rose* (Aki contribute 0.552 → 0.617; Cy study fell
0.277 → 0.233). Net cross-agent divergence barely moved. Weights can only **redistribute** scheduling,
not increase total per-agent draining — at N=3 no weighting gives each agent as many turns as the dyad
got. This is precisely the N=3 scheduling-dilution tension the economic lever solves (drain harder per
turn) and the weights lever cannot. The pre-registered prediction (weights unlikely to clear) is
confirmed.

## The arc of ADR 0015 Decision 3(a) — three levers

| lever | intervention | result | why |
|---|---|---|---|
| persona (ADR 0017) | stronger-travel Stranger | COUNTERPRODUCTIVE | road-narrative crowded out the travel action |
| weights (this doc) | Vesna 70→130 | STILL FAILS (wash) | redistribution can't add total draining at N=3 |
| **economic (this doc)** | **bal@42** | **N=3 ACHIEVABLE** | **affordability breaks society-wide contribute-dominance** |

**Combined reading:** R3's N=3 Gate-9 failure (ADR 0015 PARTIAL) was **not intrinsic** — it was a
**fixed-dose artifact**. The dyad's T=30 dose under-drains the diluted weight-70 agents at N=3
(ADR 0015 held T fixed deliberately, answering the "transfer without re-tuning" question, which
failed). Rescaling the contribute cost to T=42 — the pre-scoped drain-equate — makes N=3 specialize:
the mechanism generalizes, the *dose* must scale with roster size. Neither persona prose nor weight
redistribution achieves this; affordability does.

## Operational note — Ollama instability (reproduction caveat)

During this sweep the local Ollama `gemma4:26b` runtime degraded twice under sustained multi-hour
load: once a full wedge (the API returned empty `{"model":"","done":false}` while `ollama ps` showed
the model loaded — silently producing all-`rest` parse-fallback runs), and once a partial degradation
(a 43%-fallback s303 that finished early). **Both were detected by a fallback-rate check** (healthy
runs ≤ 3%; degraded runs 43–100%) and fixed by `ollama stop gemma4:26b` + a fresh reload. Degraded
runs were quarantined (`.dead-runs/`) and re-run clean. **Any re-run of this sweep must verify
per-run fallback rate ≤ ~5% before trusting a result**; the instrument fidelity gate does not catch
LLM-runtime degradation.

## Reproduction

```bash
./scripts/run_r3_levers2_sweep.sh bal42 101 202 303     # lever (a)
./scripts/run_r3_levers2_sweep.sh wt130 101             # lever (b) pilot
uv run python scripts/spike_workshop_measure.py --data data/econ-r3bal42-s101 --harvest harvest/econ-r3bal42-s101
uv run python scripts/replay_economy.py --audit bal@42=data/econ-r3bal42-s101
```
Run dirs `data/econ-r3bal42-s{101,202,303}`, `data/econ-r3wt130-s101` (untracked, kept locally).
Baseline `data/econ-roster-r3-s*`. Pre-registration: `docs/economy-phase2-r3-levers2-runbook.md`.
