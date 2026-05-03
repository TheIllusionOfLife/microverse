"""Project-wide constants.

Single source of truth for the model name, sampling defaults, and runtime
caps. Importable from anywhere — keep this module dependency-free.
"""

from __future__ import annotations

# Single-model invariant. Every LLM call goes here. No router, no fallback.
MODEL: str = "gemma4:e4b"

# Hard caps for a single LLM call.
LLM_TIMEOUT_S: float = 90.0
LLM_MAX_TOKENS: int = 1024

# Retry / failure-mode caps used by agents and the watchdog.
MAX_RETRIES: int = 2
MAX_CONSECUTIVE_FAIL: int = 3
