"""IdentityStore — ADR 0007 Phase 1 (Stage C) belief persistence.

The store is a small materialized cache over the WAL log: one row per
agent holding the periodically summarized beliefs line. It is regenerable
from the episodic log, so a ``kill -9`` cannot desync it — but persisting
it means beliefs survive a clean restart rather than resetting to empty.
"""

from __future__ import annotations

from pathlib import Path

from microverse.memory.identity import IdentityStore


def test_missing_agent_returns_empty_string(tmp_path: Path) -> None:
    with IdentityStore(tmp_path / "identity.sqlite") as store:
        assert store.get("Aki") == ""


def test_put_then_get_roundtrips(tmp_path: Path) -> None:
    with IdentityStore(tmp_path / "identity.sqlite") as store:
        store.put("Aki", "the loom rewards a slow, even hand")
        assert store.get("Aki") == "the loom rewards a slow, even hand"


def test_put_is_upsert(tmp_path: Path) -> None:
    with IdentityStore(tmp_path / "identity.sqlite") as store:
        store.put("Aki", "first belief")
        store.put("Aki", "revised belief")
        assert store.get("Aki") == "revised belief"


def test_beliefs_persist_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "identity.sqlite"
    with IdentityStore(path) as store:
        store.put("Cy", "patience over haste")
    with IdentityStore(path) as reopened:
        assert reopened.get("Cy") == "patience over haste"
