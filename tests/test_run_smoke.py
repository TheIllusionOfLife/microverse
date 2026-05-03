"""Smoke test for microverse.run.

End-to-end with mocked Ollama: 30 ticks at tempo=0 produce at least
one harvested artifact and the corresponding manifest.jsonl line.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from microverse.run import run


def _canned_chat(call_count: list[int]):
    """Every third call returns a craft action with an artifact."""

    def _chat(**_kwargs: object) -> dict[str, object]:
        call_count[0] += 1
        if call_count[0] % 3 == 0:
            content = (
                '{"thought": "I will craft a wooden bowl for the village.", '
                '"action": "craft", "target": null, '
                '"artifact": "A simple wooden bowl carved with three swirling lines."}'
            )
        else:
            content = (
                '{"thought": "I rest a moment.", "action": "rest", '
                '"target": null, "artifact": null}'
            )
        return {"content": content, "thinking": "", "raw": {}}

    return _chat


def test_30_ticks_produces_at_least_one_artifact(tmp_path: Path):
    data_dir = tmp_path / "data"
    harvest_dir = tmp_path / "harvest"
    call_count = [0]

    with patch("microverse.agents.artisan.chat", side_effect=_canned_chat(call_count)):
        executed = run(
            ticks=30,
            seed=42,
            tempo=0,
            data_dir=data_dir,
            harvest_dir=harvest_dir,
        )

    assert executed == 30
    assert call_count[0] == 30  # one chat per tick

    inbox_files = list((harvest_dir / "inbox").rglob("*.md"))
    assert len(inbox_files) >= 1, "expected at least one harvested artifact"

    # Manifest line count == number of consider() calls. The Artisan
    # always emits an artifact every 3rd tick (10 of 30) plus None
    # otherwise. Harvester is only called when artifact is non-null —
    # so manifest should have 10 lines (all accepted).
    manifest = harvest_dir / "manifest.jsonl"
    assert manifest.exists()
    lines = manifest.read_text().splitlines()
    assert len(lines) == 10
    accepted = [json.loads(line)["accepted"] for line in lines]
    assert all(accepted)


def test_run_creates_data_and_harvest_dirs(tmp_path: Path):
    """run() must create both directories even if no artifact is harvested."""
    data_dir = tmp_path / "fresh-data"
    harvest_dir = tmp_path / "fresh-harvest"
    call_count = [0]

    rest_only = {
        "content": '{"thought": "x", "action": "rest", "target": null, "artifact": null}',
        "thinking": "",
        "raw": {},
    }
    with patch("microverse.agents.artisan.chat", return_value=rest_only):
        run(ticks=3, seed=0, tempo=0, data_dir=data_dir, harvest_dir=harvest_dir)
        # Touch call_count so unused-import lints don't mind us.
        call_count[0] += 0

    assert data_dir.exists()
    assert (data_dir / "episodic.sqlite").exists()
    assert harvest_dir.exists()
