"""Small text helpers shared across modules.

Hosts:
  - ``tokenize`` / ``jaccard`` — used by Elder lore-drift guard and
    Watchdog echo-chamber detector. Two callers with slightly different
    filters (Elder drops stop-words; Watchdog keeps them) so the helper
    takes flags rather than mandating one rule.
  - ``safe_json_loads`` — strict json → ``json_repair`` → strict json
    ladder used by ``parse_action`` (agents.base) and
    ``_safe_parse_scores`` (agents.trader). Returns ``None`` if neither
    pass yields a value. Callers handle their own type-validation,
    bytes-cap guards, and metric side effects.

Lives at the package root so the agents and ops layers can both import
without creating a memory→agents or agents→ops cycle.
"""

from __future__ import annotations

import json
import re
from typing import Any

from json_repair import repair_json

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Common English function words; their overlap inflates raw Jaccard
# without signaling preservation of canon. Elder filters these from
# both sides of a similarity comparison.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "they",
        "them",
        "their",
        "we",
        "us",
        "our",
        "you",
        "your",
        "he",
        "she",
        "his",
        "her",
        "him",
        "i",
        "my",
        "me",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "not",
        "no",
        "so",
        "if",
        "then",
        "than",
        "when",
        "while",
        "where",
        "what",
        "who",
        "whom",
        "which",
        "such",
        "into",
        "out",
        "over",
        "under",
        "up",
        "down",
        "about",
        "again",
        "ever",
        "always",
        "never",
        "all",
        "any",
        "some",
        "each",
        "every",
        "more",
        "most",
        "less",
        "least",
        "very",
        "much",
        "also",
        "just",
    }
)


def tokenize(text: str, *, min_len: int = 1, drop_stopwords: bool = False) -> set[str]:
    """Lowercase token-set extraction with optional length and stop-word filters."""
    if drop_stopwords:
        return {
            t.lower()
            for t in _TOKEN_RE.findall(text)
            if len(t) >= min_len and t.lower() not in _STOPWORDS
        }
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= min_len}


def jaccard(a: set[str], b: set[str]) -> float:
    """Token-set Jaccard similarity.

    Vacuous case (both empty) returns 1.0 — there is no signal, so
    callers shouldn't trip drift/diversity detectors on empty inputs.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def safe_json_loads(raw: str) -> Any | None:
    """Best-effort JSON parse: strict → ``json_repair`` → ``None``.

    Returns the parsed object only if it is a *non-empty* dict or list.
    Anything else (parse failure, scalar, ``""``, ``{}``, ``[]``) yields
    ``None`` so callers don't have to special-case them.

    Does NOT enforce a byte-size cap; callers that care about runaway
    inputs (e.g. ``parse_action``) check ``MAX_PARSE_BYTES`` themselves
    before calling this so they can attach their own metric side effects.
    """

    def _container_or_none(value: Any) -> Any | None:
        if isinstance(value, (dict, list)) and value:
            return value
        return None

    try:
        return _container_or_none(json.loads(raw))
    except json.JSONDecodeError:
        pass

    try:
        repaired = repair_json(raw, return_objects=False)
        if not repaired or repaired in ("{}", "[]", '""'):
            return None
        return _container_or_none(json.loads(repaired))
    except json.JSONDecodeError:
        return None
