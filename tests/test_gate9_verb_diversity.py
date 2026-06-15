"""gate9_verb_diversity: society verb entropy + cross-agent JSD (ADR 0008 spike).

Old Gate 3 penalizes any single agent concentrating on one verb. But the
re-diagnosis target (ADR 0007 measurement #1) is the inverse: agents should
concentrate on *different* verbs (divergence), and the society as a whole
should stop being a contribute-monoculture (rising entropy). Gate 9 measures
both, on the executed-verb stream and the model's chosen (parsed) stream.
"""

from __future__ import annotations

import importlib.util
import math
import sqlite3
import sys
from pathlib import Path

import pytest

_MEASURE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "spike_workshop_measure.py"
_spec = importlib.util.spec_from_file_location("spike_workshop_measure", _MEASURE_PATH)
assert _spec is not None
assert _spec.loader is not None
swm = importlib.util.module_from_spec(_spec)
sys.modules["spike_workshop_measure"] = swm
_spec.loader.exec_module(swm)


def _events_db(rows: list[tuple[str, str, dict]]) -> sqlite3.Connection:
    """In-memory events table. rows = (actor, action, payload)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, "
        "actor TEXT, action TEXT, target TEXT, payload_json TEXT)"
    )
    import json

    ts = 1000.0
    for actor, action, payload in rows:
        conn.execute(
            "INSERT INTO events (ts, actor, action, target, payload_json) VALUES (?,?,?,?,?)",
            (ts, actor, action, None, json.dumps(payload)),
        )
        ts += 1.0
    conn.commit()
    return conn


# --- pure-math helpers ------------------------------------------------------


def test_entropy_monoculture_is_zero():
    assert swm._shannon_entropy_bits({"craft": 100}) == pytest.approx(0.0)


def test_entropy_uniform_six_verbs_norm_is_one():
    counts = dict.fromkeys(("speak", "craft", "study", "rest", "travel", "contribute"), 10)
    assert swm._entropy_norm(counts, k=6) == pytest.approx(1.0)
    # raw bits = log2(6)
    assert swm._shannon_entropy_bits(counts) == pytest.approx(math.log2(6))


def test_jsd_identical_distributions_zero():
    p = {"craft": 1.0}
    assert swm._jsd_bits(p, dict(p)) == pytest.approx(0.0, abs=1e-9)


def test_jsd_disjoint_specialists_is_one():
    aki = {"craft": 1.0}
    cy = {"study": 1.0}
    assert swm._jsd_bits(aki, cy) == pytest.approx(1.0, abs=1e-9)


# --- gate9 over an events DB ------------------------------------------------


def test_gate9_monoculture_fails():
    rows = [("Aki", "contribute", {}) for _ in range(50)]
    rows += [("Cy", "contribute", {}) for _ in range(50)]
    g = swm.gate9_verb_diversity(_events_db(rows))
    assert g["executed"]["society_entropy_norm"] == pytest.approx(0.0)
    assert g["executed"]["jsd_norm"] == pytest.approx(0.0)
    assert g["pass"] is False


def test_gate9_disjoint_specialists_pass():
    rows = [("Aki", "craft", {}) for _ in range(50)]
    rows += [("Cy", "study", {}) for _ in range(50)]
    g = swm.gate9_verb_diversity(_events_db(rows))
    # Society uses two verbs equally => entropy_norm = log2(2)/log2(6) ≈ 0.387.
    expected_norm = math.log2(2) / math.log2(6)
    assert g["executed"]["society_entropy_norm"] == pytest.approx(expected_norm, abs=1e-4)
    # Disjoint specialists => maximal divergence.
    assert g["executed"]["jsd_norm"] == pytest.approx(1.0, abs=1e-9)
    # This is the TARGET outcome (comparative-advantage specialization), so the
    # gate must PASS it: the 0.35 entropy floor sits just under the 2-agent
    # two-verb ceiling and JSD is well over its floor.
    assert g["pass"] is True


def test_gate9_excludes_scene_forced_contributes():
    # Free choices: Aki craft, Cy study. The 40 forced scene contributes
    # (tagged with scene_id) must NOT enter either diversity stream, else scene
    # volume would swamp the signal back toward a contribute-monoculture.
    rows = [("Aki", "craft", {}) for _ in range(20)]
    rows += [("Cy", "study", {}) for _ in range(20)]
    rows += [("Aki", "contribute", {"scene_id": "s1", "turn_index": 0}) for _ in range(40)]
    g = swm.gate9_verb_diversity(_events_db(rows))
    assert g["scene_excluded"] == 40
    assert set(g["executed"]["society_counts"]) == {"craft", "study"}
    assert "contribute" not in g["chosen"]["society_counts"]


def test_gate9_parse_fallback_excluded_from_chosen_only():
    # 20 real studies + 10 parse-fallback RESTs. The fallbacks are realized
    # (executed=rest) but not free choices, so they count in the executed
    # stream yet are dropped from the chosen stream.
    rows = [("Aki", "study", {"parsed_verb": "study"}) for _ in range(20)]
    rows += [("Aki", "rest", {"parsed_verb": "rest", "parse_fallback": True}) for _ in range(10)]
    g = swm.gate9_verb_diversity(_events_db(rows))
    assert g["executed"]["society_counts"] == {"study": 20, "rest": 10}
    assert g["chosen"]["society_counts"] == {"study": 20}


def test_gate9_excludes_world_and_namespaced_events():
    rows = [
        ("world", "weather.storm", {}),
        ("scene", "scene.open", {}),
        ("harvester", "harvest.write", {}),
        ("Aki", "craft", {}),
        ("Cy", "study", {}),
    ]
    g = swm.gate9_verb_diversity(_events_db(rows))
    assert set(g["executed"]["society_counts"]) == {"craft", "study"}


def test_gate9_chosen_stream_uses_parsed_verb_payload():
    # Executed is all-craft (forced by the economy lever), but the model
    # actually CHOSE contribute every time -> chosen stream stays monocultural.
    rows = [("Aki", "craft", {"parsed_verb": "contribute"}) for _ in range(40)]
    rows += [("Cy", "study", {"parsed_verb": "contribute"}) for _ in range(40)]
    g = swm.gate9_verb_diversity(_events_db(rows))
    assert g["chosen"]["society_counts"] == {"contribute": 80}
    assert g["chosen"]["society_entropy_norm"] == pytest.approx(0.0)
    # substitution_rate: every row had chosen != executed.
    assert g["substitution_rate"] == pytest.approx(1.0)
    # The headline pass reads the CHOSEN stream, so forced-by-construction fails.
    assert g["pass"] is False


def test_gate9_chosen_falls_back_to_action_when_no_payload():
    rows = [("Aki", "craft", {}) for _ in range(20)]
    rows += [("Cy", "study", {}) for _ in range(20)]
    g = swm.gate9_verb_diversity(_events_db(rows))
    assert g["substitution_rate"] == pytest.approx(0.0)
    assert g["chosen"]["society_counts"] == {"craft": 20, "study": 20}


def test_gate9_economy_substitution_rate_is_economy_only():
    # 10 economy substitutions, 10 NON-economy overrides (e.g. diversity), 10 clean.
    rows = [("Aki", "craft", {"parsed_verb": "contribute", "economy_substituted": True})] * 10
    rows += [("Aki", "craft", {"parsed_verb": "speak"})] * 10  # parsed!=exec, not economy
    rows += [("Aki", "craft", {"parsed_verb": "craft"})] * 10
    g = swm.gate9_verb_diversity(_events_db(rows))
    # Total override counts both the economy subs and the diversity-style ones.
    assert g["substitution_rate"] == pytest.approx(20 / 30, abs=1e-4)
    # Economy-only rate counts just the flagged ones.
    assert g["economy_substitution_rate"] == pytest.approx(10 / 30, abs=1e-4)


# --- metric-validity calibration (ADR 0009 follow-up) -----------------------
#
# Stage 3 halted on a co-drift read (no mode cleared the JSD floor). Before
# spending more compute, these tests pin WHAT the 2-agent gate actually demands,
# so the halt is read as a real behavioral finding and not a measurement floor
# that 2 agents cannot reach. The disjoint-specialists PASS above already shows
# the ceiling is 1.0; these characterize the partial-credit middle.


def _dist_rows(by_agent: dict[str, dict[str, int]]) -> list[tuple[str, str, dict]]:
    """Expand {agent: {verb: count}} into free-choice rows (empty payload =>
    chosen stream == executed stream, which is what `pass` reads)."""
    return [
        (agent, verb, {})
        for agent, dist in by_agent.items()
        for verb, count in dist.items()
        for _ in range(count)
    ]


def test_gate9_real_stage3_codrift_flat_s42_fails():
    # The actual seed-42 `flat` chosen distribution (docs/economy-stage3-findings.md).
    # Cy is ~93% contribute; Aki diversified to ~51% contribute + a speak/rest/study
    # tail. Society entropy clears its floor, but because both agents keep contribute
    # as their plurality AND their tails overlap (not disjoint — cf. the
    # disjoint-secondary test below, which PASSES on a shared plurality) the
    # cross-agent JSD is capped well under 0.25 -> Gate 9 fails.
    # This is the metric reproducing the live read, not a synthetic toy.
    rows = _dist_rows(
        {
            "Aki": {"speak": 415, "craft": 33, "study": 149, "rest": 230, "contribute": 858},
            "Cy": {"speak": 19, "craft": 20, "study": 21, "rest": 17, "contribute": 968},
        }
    )
    g = swm.gate9_verb_diversity(_events_db(rows))
    ch = g["chosen"]
    assert ch["society_entropy_norm"] == pytest.approx(0.5738, abs=1e-3)  # clears 0.35
    assert ch["jsd_norm"] == pytest.approx(0.1846, abs=1e-3)  # misses 0.25
    assert g["pass"] is False


def test_gate9_shared_modal_verb_caps_divergence_even_with_a_long_tail():
    # Diagnostic half 1: both agents keep `contribute` as their MODE; Aki grows a
    # large, diverse tail (speak/rest/study). Society entropy rises but the shared
    # dominant verb pins cross-agent JSD below the floor. Growing a tail is not
    # enough.
    rows = _dist_rows(
        {
            "Aki": {"contribute": 520, "speak": 260, "rest": 140, "study": 80},
            "Cy": {"contribute": 950, "speak": 50},
        }
    )
    g = swm.gate9_verb_diversity(_events_db(rows))
    ch = g["chosen"]
    assert ch["society_entropy_norm"] == pytest.approx(0.4633, abs=1e-3)  # clears 0.35...
    assert ch["jsd_norm"] == pytest.approx(0.2122, abs=1e-3)  # ...but JSD is not
    assert g["pass"] is False


def test_gate9_relocating_one_agents_modal_verb_passes():
    # Diagnostic half 2: identical heavy `contribute` overlap, but now Aki's MODE
    # is `craft` (it still contributes, just not as its plurality) while Cy stays
    # contribute-dominant. Moving a single agent's modal verb off the shared
    # attractor lifts JSD over the floor -> Gate 9 PASSES. Modal relocation is ONE
    # sufficient route to divergence (see the disjoint-secondary test below for
    # another); the Stage 3 lever achieved neither, only tail diversification.
    rows = _dist_rows(
        {
            "Aki": {"craft": 500, "contribute": 300, "speak": 200},
            "Cy": {"contribute": 800, "speak": 200},
        }
    )
    g = swm.gate9_verb_diversity(_events_db(rows))
    ch = g["chosen"]
    assert ch["society_entropy_norm"] == pytest.approx(0.5566, abs=1e-3)
    assert ch["jsd_norm"] == pytest.approx(0.3351, abs=1e-3)  # clears 0.25
    assert g["pass"] is True


def test_gate9_shared_modal_verb_but_disjoint_secondary_mass_passes():
    # Counter to any "the gate requires moving the modal verb" reading: Gate 9
    # inspects the FULL distributions, not the mode. Both agents keep `contribute`
    # as their plurality (60% each), but their remaining 40% is on DISJOINT verbs
    # (craft vs study). The distributions diverge enough to clear the floor ->
    # PASS, without either agent relocating its modal verb. Divergence, not modal
    # relocation specifically, is what the metric demands.
    rows = _dist_rows(
        {
            "Aki": {"contribute": 600, "craft": 400},
            "Cy": {"contribute": 600, "study": 400},
        }
    )
    g = swm.gate9_verb_diversity(_events_db(rows))
    ch = g["chosen"]
    assert ch["society_entropy_norm"] == pytest.approx(0.5304, abs=1e-3)
    assert ch["jsd_norm"] == pytest.approx(0.4, abs=1e-3)  # clears 0.25
    assert g["pass"] is True


def test_gate9_partial_specialization_with_shared_contribute_passes():
    # The floor is not punishingly strict: even with 40% of EACH agent's mass on a
    # shared `contribute`, distinct specialties (craft vs study) clear both floors.
    rows = _dist_rows(
        {
            "Aki": {"craft": 600, "contribute": 400},
            "Cy": {"study": 600, "contribute": 400},
        }
    )
    g = swm.gate9_verb_diversity(_events_db(rows))
    assert g["chosen"]["society_entropy_norm"] == pytest.approx(0.6077, abs=1e-3)
    assert g["chosen"]["jsd_norm"] == pytest.approx(0.6, abs=1e-3)
    assert g["pass"] is True


# --- N>2 divergence criterion (ADR 0016) ------------------------------------
#
# ADR 0015 read R3 (Artisan+Scholar+Stranger, N=3) as FAIL on the chosen-stream
# `jsd_norm` < 0.25 even though society entropy sat near the N=3 ceiling. The
# headline metric `_multi_jsd` divides the centroid-radius by log2(n), folding a
# roster-size penalty into a quantity sold as size-invariant. `_mean_pairwise_jsd`
# removes that divisor: it is the mean of pairwise JSDs, each already in [0, 1],
# so the [0, 1] scale (and the inherited 0.25 floor) holds at every N. The
# crucial property is that it is IDENTICAL to `_multi_jsd` at N=2, so the entire
# locked 2-agent calibration is preserved byte-for-byte.

# Real chosen-stream distribution of the frozen R3-s101 run (data/econ-roster-
# r3-s101). Reproduced here so the "R3 still fails under the new metric" claim is
# testable offline without the run dir. jsd_norm read 0.1964 live (gate-report).
_R3_S101 = {
    "Aki": {"contribute": 595, "craft": 386, "speak": 30, "study": 63, "rest": 3},
    "Cy": {"contribute": 443, "study": 193, "speak": 31, "craft": 21, "rest": 9},
    "Vesna": {"contribute": 552, "speak": 48, "travel": 120, "study": 23, "rest": 21, "craft": 3},
}


def _dists(by_agent: dict[str, dict[str, int]]) -> list[dict[str, float]]:
    """Normalize {agent: {verb: count}} into the list of per-agent distributions
    that the divergence helpers consume."""
    return [swm._normalize(d, swm._VERBS) for d in by_agent.values()]


@pytest.mark.parametrize(
    "by_agent",
    [
        {"Aki": {"craft": 1}, "Cy": {"study": 1}},  # disjoint specialists
        {"Aki": {"contribute": 600, "craft": 400}, "Cy": {"contribute": 600, "study": 400}},
        {
            "Aki": {"craft": 500, "contribute": 300, "speak": 200},
            "Cy": {"contribute": 800, "speak": 200},
        },
        {"Aki": {"contribute": 50}, "Cy": {"contribute": 50}},  # monoculture
    ],
)
def test_mean_pairwise_equals_multi_jsd_at_n2(by_agent):
    # The load-bearing equivalence: at N=2 the new metric reproduces the current
    # one exactly, so the 0.25 floor is inherited (not re-derived) on every locked
    # 2-agent anchor. `_multi_jsd` already returns `_jsd_bits` at n=2.
    dists = _dists(by_agent)
    assert swm._mean_pairwise_jsd(dists) == pytest.approx(swm._multi_jsd(dists), abs=1e-12)


def test_mean_pairwise_disjoint_three_specialists_is_one():
    dists = _dists({"Aki": {"craft": 1}, "Cy": {"study": 1}, "Vesna": {"travel": 1}})
    assert swm._mean_pairwise_jsd(dists) == pytest.approx(1.0, abs=1e-9)


def test_mean_pairwise_three_agent_monoculture_is_zero():
    dists = _dists({"a": {"contribute": 9}, "b": {"contribute": 9}, "c": {"contribute": 9}})
    assert swm._mean_pairwise_jsd(dists) == pytest.approx(0.0, abs=1e-9)


def test_mean_pairwise_real_r3_still_below_floor():
    # The integrity row: the reframe does NOT rescue R3. Mean-pairwise reads
    # ~0.207 (vs the log2(n)-normalized 0.196) — still under 0.25. The criterion
    # change cannot flip the verdict, which is what makes it a refinement and not
    # a post-hoc rescue.
    mpjsd = swm._mean_pairwise_jsd(_dists(_R3_S101))
    assert mpjsd == pytest.approx(0.2073, abs=1e-3)
    assert mpjsd < 0.25


def test_specialization_ratio_near_one_for_ceiling_diverse_society():
    # R3-s101 society entropy 0.6507 vs the N=3 perfect-specialist ceiling
    # log2(3)/log2(6) = 0.6131 -> ratio ~1.06. High society diversity; this is
    # exactly why it is REPORTED, not gated (it is ~1.0 while agents stay
    # undifferentiated). Gating on it would manufacture an R3 pass.
    assert swm._specialization_ratio(0.6507, n=3) == pytest.approx(1.061, abs=1e-2)
    assert swm._specialization_ratio(0.0, n=3) == pytest.approx(0.0)


def test_identity_verb_nmi_bounds():
    # 0 when verb is independent of identity (monoculture), 1 when verb perfectly
    # predicts identity (disjoint specialists, equal mass).
    mono = {"a": {"contribute": 10}, "b": {"contribute": 10}, "c": {"contribute": 10}}
    assert swm._identity_verb_nmi(mono) == pytest.approx(0.0, abs=1e-9)
    disjoint = {"a": {"craft": 10}, "b": {"study": 10}, "c": {"travel": 10}}
    assert swm._identity_verb_nmi(disjoint) == pytest.approx(1.0, abs=1e-9)


def test_diversity_block_reports_new_diagnostics_without_dropping_jsd_norm():
    rows = _dist_rows(_R3_S101)
    g = swm.gate9_verb_diversity(_events_db(rows))
    ch = g["chosen"]
    # The legacy metric is untouched.
    assert ch["jsd_norm"] == pytest.approx(0.1964, abs=1e-3)
    # The new diagnostics are additive.
    assert ch["mean_pairwise_jsd"] == pytest.approx(0.2073, abs=1e-3)
    assert ch["entropy_ceiling_norm"] == pytest.approx(math.log2(3) / math.log2(6), abs=1e-4)
    expected_ratio = ch["society_entropy_norm"] / ch["entropy_ceiling_norm"]
    assert ch["specialization_ratio"] == pytest.approx(expected_ratio, abs=1e-4)
    assert 0.0 <= ch["identity_verb_nmi"] <= 1.0


def test_gate9_divergence_metric_defaults_to_jsd_norm():
    # The default call must be byte-identical to the pre-ADR-0016 behavior: the
    # gate reads jsd_norm. The new metric is opt-in via the param.
    rows = _dist_rows(_R3_S101)
    default = swm.gate9_verb_diversity(_events_db(rows))
    explicit = swm.gate9_verb_diversity(_events_db(rows), divergence_metric="jsd_norm")
    assert default["divergence_metric"] == "jsd_norm"
    assert default["pass_divergence"] == explicit["pass_divergence"]
    # Both per-metric verdicts are always reported, regardless of which gates.
    assert default["pass_divergence_jsd"] is False
    assert default["pass_divergence_mpjsd"] is False


def test_gate9_mean_pairwise_metric_r3_fails():
    # Selecting the new metric re-reads R3 and it STILL fails (0.207 < 0.25).
    rows = _dist_rows(_R3_S101)
    g = swm.gate9_verb_diversity(_events_db(rows), divergence_metric="mean_pairwise_jsd")
    assert g["divergence_metric"] == "mean_pairwise_jsd"
    assert g["chosen"]["mean_pairwise_jsd"] == pytest.approx(0.2073, abs=1e-3)
    assert g["pass_divergence"] is False
    assert g["pass"] is False


def test_gate9_mean_pairwise_metric_passes_disjoint_three_specialists():
    rows = _dist_rows({"Aki": {"craft": 50}, "Cy": {"study": 50}, "Vesna": {"travel": 50}})
    g = swm.gate9_verb_diversity(_events_db(rows), divergence_metric="mean_pairwise_jsd")
    assert g["chosen"]["mean_pairwise_jsd"] == pytest.approx(1.0, abs=1e-9)
    assert g["pass_divergence"] is True
