"""End-to-end scene-path smoke through run.py.

Drives ``microverse.run.run`` with a mocked LLM that always emits a
valid contribute targeting one workshop WIP. SCENE_GATE_P is monkey-
patched to 1.0 so every eligible tick takes the scene path. We then
verify:

  - ``scene.open`` events appear in the episodic log;
  - their payload carries turn1/turn2/turn3 author names plus
    ``scene_id`` + ``wip_name``;
  - matching ``contribute`` events carry ``scene_id`` + ``turn_index``;
  - ``scene_completed`` counter is bumped.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from microverse.run import run
from microverse.world.workshop import CONFIGURED_WIPS


def _contrib_chat(wip: str):
    """Stub LLM: always emit a contribute action targeting `wip`."""
    call_count = [0]

    def _chat(**_kwargs: object) -> dict[str, object]:
        call_count[0] += 1
        fragment = (
            f"A careful study of the loom workshop in turn {call_count[0]:03d}; "
            f"considering the warp tension and the next thread to weave with care. "
            f"This fragment is well past the MIN_FRAGMENT_CHARS floor of 120."
        )
        content = (
            '{"thought": "I weave a continuation.", "action": "contribute", '
            f'"contribute_to": "{wip}", '
            f'"artifact": "{fragment}"}}'
        )
        return {"content": content, "thinking": "", "raw": {}}

    return _chat


def _trader_chat():
    def _chat(**_kwargs: object) -> dict[str, object]:
        return {
            "content": '[{"artifact_id": 0, "score": 0.5, "rationale": "x"}]',
            "thinking": "",
            "raw": {},
        }

    return _chat


def test_scene_path_produces_logged_events(tmp_path: Path, monkeypatch) -> None:
    """With SCENE_GATE_P=1.0 every eligible tick routes through
    SceneRunner; the episodic log should grow scene.open + contribute
    events with consistent scene_id linkage."""
    import microverse.config as cfg

    monkeypatch.setattr(cfg, "SCENE_GATE_P", 1.0)
    # Default roster is Aki + Cy = 2 agents → 1 peer; lower the
    # min-peers threshold so the gate fires under the default roster.
    # (A real soak with a Stranger has >= 2 peers and uses the
    # production threshold.)
    monkeypatch.setattr(cfg, "SCENE_MIN_PEERS", 1)
    # Disable engagement gate so the LLM's contribute action is the
    # one that lands (engagement coercion would override turn 1).
    monkeypatch.setattr(cfg, "PEER_ENGAGEMENT_INTERVAL", 100_000)

    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"
    wip = CONFIGURED_WIPS[0]

    with (
        patch("microverse.agents.artisan.chat", side_effect=_contrib_chat(wip)),
        patch("microverse.agents.scholar.chat", side_effect=_contrib_chat(wip)),
        patch("microverse.agents.trader.chat", side_effect=_trader_chat()),
    ):
        run(
            ticks=12,
            seed=42,
            tempo=0,
            data_dir=data_dir,
            harvest_dir=harvest_dir,
            solo=False,
        )

    # Inspect the episodic log directly.
    conn = sqlite3.connect(str(data_dir / "episodic.sqlite"))
    try:
        scene_opens = list(
            conn.execute("SELECT payload_json FROM events WHERE action='scene.open'").fetchall()
        )
        scene_contribs = list(
            conn.execute(
                "SELECT payload_json FROM events WHERE action='contribute' "
                "AND payload_json LIKE '%scene_id%'"
            ).fetchall()
        )
    finally:
        conn.close()

    assert len(scene_opens) >= 1, "expected at least one scene.open with SCENE_GATE_P=1.0"

    # Every scene.open must carry the contract fields.
    for (payload_json,) in scene_opens:
        p = json.loads(payload_json)
        assert "scene_id" in p
        assert "turn1_author" in p
        assert "turn2_author" in p
        assert "turn3_author" in p
        # wip_name is whichever non-complete WIP run.py picked at
        # gate-fire time; not pinned to the LLM stub's target since
        # WIPs cycle as they fill + recycle.
        assert p["wip_name"] in CONFIGURED_WIPS

    # And every scene-linked contribute must carry scene_id + turn_index 1/2/3.
    scene_id_to_turns: dict[str, set[int]] = {}
    for (payload_json,) in scene_contribs:
        p = json.loads(payload_json)
        sid = p.get("scene_id")
        ti = p.get("turn_index")
        if sid and ti in (1, 2, 3):
            scene_id_to_turns.setdefault(sid, set()).add(ti)

    # At least one scene should have completed all 3 turns.
    completed_scenes = {sid for sid, ts in scene_id_to_turns.items() if ts == {1, 2, 3}}
    assert completed_scenes, (
        f"expected at least one fully-completed scene; got per-scene turns: {scene_id_to_turns}"
    )

    # And the scene_completed metric counter should be bumped.
    conn = sqlite3.connect(str(data_dir / "metrics.sqlite"))
    try:
        row = conn.execute("SELECT MAX(value) FROM metrics WHERE name='scene_completed'").fetchone()
    finally:
        conn.close()
    assert row[0] is not None
    assert row[0] >= 1


def test_scene_gate_off_skips_scene_path(tmp_path: Path, monkeypatch) -> None:
    """With SCENE_GATE_P=0 the scene branch is never taken; only
    single-tick contributes land. Confirms the gate variable is the
    sole entry point for scene mode (no leakage)."""
    import microverse.config as cfg

    monkeypatch.setattr(cfg, "SCENE_GATE_P", 0.0)

    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"
    wip = CONFIGURED_WIPS[0]

    with (
        patch("microverse.agents.artisan.chat", side_effect=_contrib_chat(wip)),
        patch("microverse.agents.scholar.chat", side_effect=_contrib_chat(wip)),
        patch("microverse.agents.trader.chat", side_effect=_trader_chat()),
    ):
        run(
            ticks=10,
            seed=42,
            tempo=0,
            data_dir=data_dir,
            harvest_dir=harvest_dir,
            solo=False,
        )

    conn = sqlite3.connect(str(data_dir / "episodic.sqlite"))
    try:
        scene_opens = conn.execute(
            "SELECT COUNT(*) FROM events WHERE action='scene.open'"
        ).fetchone()
    finally:
        conn.close()
    assert scene_opens[0] == 0, "scene gate at 0 must not produce scene.open events"
