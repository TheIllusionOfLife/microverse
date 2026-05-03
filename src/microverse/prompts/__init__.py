"""Jinja2 persona templates loaded by agents."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_DIR = Path(__file__).resolve().parent
_env = Environment(
    loader=FileSystemLoader(str(_PROMPTS_DIR)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,  # prompts are not HTML
)


def render(template_name: str, **context: object) -> str:
    return _env.get_template(template_name).render(**context)
