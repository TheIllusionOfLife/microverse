"""SQLite FTS5 lexical retrieval — semantic memory without embeddings.

Phase 3a contract:
  - ``SemanticMemory.index(doc_id, text, payload)`` adds a row.
  - ``SemanticMemory.top_k(query, k)`` returns ``(doc_id, score, payload)``
    tuples ordered by BM25 (best first).
  - Empty corpus / empty query → []. Never raises on garbage input.
  - Re-indexing the same ``doc_id`` replaces the prior row (upsert).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from microverse.memory.semantic import SemanticMemory


def _seed(mem: SemanticMemory) -> None:
    mem.index(doc_id="a", text="a wooden bowl carved from cedar", payload={"actor": "aki"})
    mem.index(doc_id="b", text="a stone hammer used for forging metal", payload={"actor": "aki"})
    mem.index(doc_id="c", text="a leather pouch sewn with linen thread", payload={"actor": "bo"})
    mem.index(
        doc_id="d",
        text="another wooden carving, this time of a bird",
        payload={"actor": "aki"},
    )


def test_empty_corpus_returns_empty(tmp_path: Path):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        assert mem.top_k("anything", k=5) == []


def test_top_k_orders_by_bm25(tmp_path: Path):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        _seed(mem)
        results = mem.top_k("wooden carving", k=4)
    doc_ids = [r.doc_id for r in results]
    # Both "a" and "d" mention wood/carving; "d" mentions both directly,
    # "a" mentions wooden+carved. Either way both must rank above b and c.
    assert "d" in doc_ids[:2]
    assert "a" in doc_ids[:2]


def test_top_k_caps_at_k(tmp_path: Path):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        _seed(mem)
        results = mem.top_k("wooden", k=2)
    assert len(results) <= 2


def test_payload_roundtrips(tmp_path: Path):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        _seed(mem)
        results = mem.top_k("hammer forging", k=1)
    assert results[0].doc_id == "b"
    assert results[0].payload == {"actor": "aki"}


def test_reindex_upserts_same_doc_id(tmp_path: Path):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        mem.index(doc_id="x", text="first version", payload={"v": 1})
        mem.index(doc_id="x", text="second much improved version", payload={"v": 2})
        results = mem.top_k("improved", k=5)
    # Only one row for doc_id 'x' — the rewrite, not the original.
    matching = [r for r in results if r.doc_id == "x"]
    assert len(matching) == 1
    assert matching[0].payload == {"v": 2}


def test_query_with_special_chars_does_not_raise(tmp_path: Path):
    """FTS5 has its own query syntax; the API must escape user input
    so quotes / parens / colons don't cause SQL errors."""
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        _seed(mem)
        # Don't care what we get — only that we don't crash.
        mem.top_k('"hammer (with) [bracket]" AND foo:bar', k=3)
        mem.top_k("''", k=3)
        mem.top_k("", k=3)


def test_count(tmp_path: Path):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        assert mem.count() == 0
        _seed(mem)
        assert mem.count() == 4


def test_durability_across_reopen(tmp_path: Path):
    db = tmp_path / "fts.sqlite"
    with SemanticMemory(db) as mem:
        _seed(mem)
    with SemanticMemory(db) as mem:
        assert mem.count() == 4
        assert mem.top_k("wooden", k=3) != []


@pytest.mark.parametrize("k", [-1, 0])
def test_non_positive_k_returns_empty(tmp_path: Path, k: int):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        _seed(mem)
        assert mem.top_k("wooden", k=k) == []


def test_delete_removes_row_from_index_and_search(tmp_path: Path):
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        _seed(mem)
        assert mem.delete("a") is True
        assert mem.delete("a") is False  # already gone
        assert mem.count() == 3
        results = mem.top_k("wooden", k=10)
        assert "a" not in {r.doc_id for r in results}


def test_list_ids_with_prefix(tmp_path: Path):
    """Phase 3b's Elder enumerates lore chunks by prefix to replace
    them transactionally."""
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        mem.index(doc_id="lore-001", text="forest", payload={})
        mem.index(doc_id="lore-002", text="river", payload={})
        mem.index(doc_id="event-001", text="harvest", payload={})

        assert sorted(mem.list_ids()) == ["event-001", "lore-001", "lore-002"]
        assert sorted(mem.list_ids(prefix="lore-")) == ["lore-001", "lore-002"]
        assert mem.list_ids(prefix="missing-") == []


def test_list_ids_prefix_treats_sql_wildcards_literally(tmp_path: Path):
    """SQL LIKE '%' / '_' must not act as wildcards in the prefix;
    they should match the literal characters only."""
    with SemanticMemory(tmp_path / "fts.sqlite") as mem:
        mem.index(doc_id="100%-cotton", text="x", payload={})
        mem.index(doc_id="100Xcotton", text="x", payload={})
        # '%' in prefix should match only the literal-percent doc.
        result = mem.list_ids(prefix="100%")
        assert result == ["100%-cotton"]
