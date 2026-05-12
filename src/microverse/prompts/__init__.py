"""Jinja2 persona templates loaded by agents.

Phase 0 spike (THROWAWAY branch ``spike/workshop-view-measurement``):
``MICROVERSE_SPIKE_WORKSHOP_VIEW`` is read once at import time and exposed
to every persona template as a Jinja global. When set, the persona prompt
renders a "village workshop currently holds" block above the task section
so the operator can measure how a hand-crafted external evolving object
shifts the verb-attractor before any schema work lands. Empty (default)
is identical to v0.1.1 behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_DIR = Path(__file__).resolve().parent
_env = Environment(
    loader=FileSystemLoader(str(_PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,  # prompts are not HTML
)

_env.globals["workshop_view_spike"] = os.environ.get("MICROVERSE_SPIKE_WORKSHOP_VIEW", "")


def render(template_name: str, **context: object) -> str:
    return _env.get_template(template_name).render(**context)
