"""Stranger: a fresh-perspective immigrant agent.

Spawned by the Watchdog when diversity drops below floor — the
village's existing inhabitants are stuck in an echo chamber and
need an outsider's voice to perturb the equilibrium. The Stranger
uses a different RNG seed from the rest of the world (system clock
when no seed is given) so its persona ideas don't track village
conventions.

Phase 4a Stranger inherits Artisan's behavior with a separate persona
template that emphasizes new perspectives.
"""

from __future__ import annotations

import time
import uuid

from microverse.agents.artisan import Artisan
from microverse.config import SAMPLING_CREATIVE
from microverse.ops.metrics import Metrics


class Stranger(Artisan):
    role = "stranger"
    persona_template = "persona_stranger.j2"
    sampling = SAMPLING_CREATIVE

    def __init__(
        self,
        name: str | None = None,
        *,
        soul_tokens: int = 60,
        metrics: Metrics | None = None,
    ) -> None:
        # Auto-name with system-clock millis + uuid hex so two Strangers
        # spawned in the same millisecond still get distinct names.
        if name is None:
            ms = int(time.time() * 1000) % 1_000_000
            name = f"stranger-{ms}-{uuid.uuid4().hex[:6]}"
        super().__init__(name, soul_tokens=soul_tokens, metrics=metrics)
