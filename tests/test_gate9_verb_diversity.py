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
    # Society uses two verbs equally => entropy_norm = log2(2)/log2(6).
    assert g["executed"]["society_entropy_norm"] > 0.0
    # Disjoint specialists => maximal divergence.
    assert g["executed"]["jsd_norm"] == pytest.approx(1.0, abs=1e-9)


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
