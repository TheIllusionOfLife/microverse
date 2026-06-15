# Findings — N>2 divergence criterion (ADR 0016)

Frozen-data re-analysis of the six `data/econ-roster-*` runs (ADR 0015) and the nine dyad runs
(`econ-rep30-bal30-*`, `econ-stage6-bal30-*`) under the pre-registered `mean_pairwise_jsd` metric.
Pre-registration: `docs/economy-phase2-divergence-criterion-runbook.md` (locked before adoption).
No new GPU compute. **Verdict: CRITERION REFINED, VERDICT UNCHANGED.**

## Setup

```bash
# Per run, the chosen-stream Gate 9 block now reports mean_pairwise_jsd + diagnostics:
uv run python scripts/spike_workshop_measure.py --data data/econ-roster-r3-s101 \
    --harvest harvest/econ-roster-r3-s101 | python3 -c \
    "import json,sys; print(json.load(sys.stdin)['gate_9_verb_diversity']['chosen'])"
```

The metric and diagnostics are in `scripts/spike_workshop_measure.py`
(`_mean_pairwise_jsd`, `_specialization_ratio`, `_identity_verb_nmi`); the gate reads them via
`gate9_verb_diversity(..., divergence_metric="mean_pairwise_jsd")`. The default
(`divergence_metric="jsd_norm"`) is unchanged, so `main()` and prior reads are byte-stable.

## Old vs new metric — all 15 runs (chosen stream)

| run | n | `jsd_norm` (÷log2 n) | `mean_pairwise_jsd` | equal? | L1 old | L1 new |
|---|---|---:|---:|:---:|:---:|:---:|
| econ-roster-r2-s101 | 2 | 0.3322 | 0.3322 | YES | PASS | PASS |
| econ-roster-r2-s202 | 2 | 0.3745 | 0.3745 | YES | PASS | PASS |
| econ-roster-r2-s303 | 2 | 0.3539 | 0.3539 | YES | PASS | PASS |
| **econ-roster-r3-s101** | 3 | 0.1964 | **0.2073** | no | **FAIL** | **FAIL** |
| **econ-roster-r3-s202** | 3 | 0.1940 | **0.2063** | no | **FAIL** | **FAIL** |
| **econ-roster-r3-s303** | 3 | 0.2115 | **0.2231** | no | **FAIL** | **FAIL** |
| econ-rep30-bal30-s101 | 2 | 0.3369 | 0.3369 | YES | PASS | PASS |
| econ-rep30-bal30-s202 | 2 | 0.3715 | 0.3715 | YES | PASS | PASS |
| econ-rep30-bal30-s303 | 2 | 0.3873 | 0.3873 | YES | PASS | PASS |
| econ-stage6-bal30-s42 | 2 | 0.3073 | 0.3073 | YES | PASS | PASS |
| econ-stage6-bal30-s38 | 2 | 0.3436 | 0.3436 | YES | PASS | PASS |
| econ-stage6-bal30-s7 | 2 | 0.3755 | 0.3755 | YES | PASS | PASS |

**Equivalence claim CONFIRMED:** every one of the 9 dyad + 3 R2 (all N=2) runs has
`mean_pairwise_jsd == jsd_norm` to 4 decimals. The metrics differ only at N=3 (R3), where the new
metric reads slightly *higher* (the `log2(n)=1.585` divisor is removed) but **still below the 0.25
floor on all three seeds**. The inherited floor preserves every locked N=2 decision.

## Reported diagnostics (NOT gating) — the R3 signature

| run | n | `society_entropy_norm` | `entropy_ceiling_norm` | `specialization_ratio` | `identity_verb_nmi` |
|---|---|---:|---:|---:|---:|
| econ-roster-r2-s101 | 2 | 0.6749 | 0.3869 | 1.745 | 0.247 |
| econ-roster-r3-s101 | 3 | 0.6507 | 0.6131 | 1.061 | 0.190 |
| econ-roster-r3-s202 | 3 | 0.6133 | 0.6131 | 1.000 | 0.193 |
| econ-roster-r3-s303 | 3 | 0.6675 | 0.6131 | 1.089 | 0.204 |

- **`specialization_ratio` ≈ 1.0 for R3** — the society reached (slightly exceeded) the entropy a
  perfectly-specialized 3-agent roster would produce. This is precisely why it is **barred from
  gating**: it is ~1.0 while the agents stay undifferentiated. Gating on it would manufacture an R3
  pass — the named forbidden move. It is reported only as the explanation for R3's signature
  (high society diversity, low cross-agent divergence).
- **`identity_verb_nmi`** orders R2 (~0.25) above R3 (~0.19–0.20), an independent-family
  confirmation that R3's agents are less distinguishable-by-verb than R2's — the same ordering the
  divergence metric gives. The weak-differentiation conclusion is robust across measure families.

## Layer 2 (per-agent role stability) — unchanged from ADR 0015

R3 agents do hold their specialty as the top non-contribute verb (e.g. R3-s101: Aki→`craft` 0.36,
Cy→`study` 0.28, Vesna→`travel` 0.16, each contribute-dominant ~0.55–0.72). The R3 failure is purely
Layer 1 (cross-agent divergence), under **both** metrics — not a role-stability failure.

## Sanity matrix (unit anchors, `tests/test_gate9_verb_diversity.py`)

| case | n | `mean_pairwise_jsd` | gate @0.25 | role |
|---|---|---:|:---:|---|
| disjoint specialists | 3 | 1.000 | PASS | new N=3 ceiling anchor |
| 3-agent monoculture | 3 | 0.000 | FAIL | new N=3 negative anchor |
| all N=2 anchors | 2 | ≡ `jsd_norm` | unchanged | inherited calibration |

## Conclusion

The `log2(n)` normalization in `_multi_jsd` is a real measurement defect at N>2, and
`mean_pairwise_jsd` removes it while preserving the entire locked N=2 calibration. But the reframe
does **not** rescue R3: under the size-invariant metric R3 reads 0.207/0.206/0.223, still under 0.25
on all three seeds. The ADR 0015 R3 FAIL was a genuine weak-differentiation finding, not a
normalization artifact — corroborated by NMI and explained (not excused) by `specialization_ratio`.
The PARTIAL roster-generality verdict stands; the arc now has a roster-size-invariant divergence bar
for future N>2 reads.

## Reproduction

Metric: `scripts/spike_workshop_measure.py` (`_mean_pairwise_jsd` + diagnostics). Pre-registration:
`docs/economy-phase2-divergence-criterion-runbook.md`. Tests:
`tests/test_gate9_verb_diversity.py` (the ADR 0016 block). Run dirs `data/econ-roster-*`,
`data/econ-rep30-*`, `data/econ-stage6-*` (untracked, kept locally).
