"""Shared pytest fixtures for the microverse test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from microverse.ops.metrics import Metrics


@pytest.fixture
def metrics() -> Iterator[Metrics]:
    """In-memory ``Metrics`` instance, closed on teardown.

    Use this for tests that just need a counter sink and don't care
    about persistence. Tests that exercise ``Metrics`` itself, or that
    want to pass ``auto_flush_every`` / a file-backed path, should
    construct their own.
    """
    m = Metrics(":memory:")
    try:
        yield m
    finally:
        m.close()
