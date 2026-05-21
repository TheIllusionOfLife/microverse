"""Harvester: the only out-of-world entity.

Inhabitants do not perceive the Harvester. It observes today's artifact
buffer and writes accepted artifacts to ``harvest/inbox/<UTC date>/``
for the host user.

Two acceptance modes (selectable at construction):
  - **Phase 1 heuristic** (no trader): each ``consider()`` writes
    immediately if the artifact text is at least ``MIN_ARTIFACT_CHARS``
    long. Maintained for backwards compat and zero-LLM smoke tests.
  - **Phase 2 percentile** (trader given): each ``consider()`` buffers
    the candidate and returns ``None``. ``flush()`` rates the entire
    buffer through the Trader, applies a percentile threshold (default
    p70), writes accepted artifacts, and records every candidate's
    score in ``manifest.jsonl`` so rejection decisions are auditable.

Atomic write contract:
  - artifact files: write to ``*.tmp``, fsync, then ``os.replace`` to
    the final name. The final name is reserved via ``O_CREAT|O_EXCL``
    *before* the temp write so two concurrent harvesters cannot race
    to the same path. (Single-process today; Phase 4 watchdog may run
    concurrently.)
  - ``manifest.jsonl``: open in append mode, write one JSON line, fsync.
    POSIX guarantees atomicity for ``write()`` calls smaller than
    ``PIPE_BUF`` (4096 on Linux/macOS); our records are ~200 bytes, so
    a single ``f.write(line)`` is one atomic ``write(2)``.

Filename slug: lowercase alnum/hyphen/underscore only, derived from the
first ~60 chars of the artifact text. If the slug collapses to empty
(e.g., emoji-only artifact), it is replaced by an 8-char content hash
so distinct artifacts don't pile up under collision suffixes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import yaml

from microverse.config import (
    HARVEST_CRAFT_CAP_PER_FLUSH,
    HARVEST_PENDING_TIMEOUT_S,
    MANIFEST_ROTATE_BYTES,
    MAX_HARVEST_ATTEMPTS,
    WIP_ACCEPTANCE_FLOOR,
    WIP_CONTRIBUTOR_FLOOR,
)

if TYPE_CHECKING:
    from microverse.agents.trader import Score
    from microverse.memory.episodic import EpisodicMemory
    from microverse.ops.metrics import Metrics
    from microverse.world.workshop import WorkshopProjection

MIN_ARTIFACT_CHARS = 20
SLUG_MAX_LEN = 60
DEFAULT_PERCENTILE = 70

# Per-primary-verb caps applied AFTER Trader scoring. Bounds the rate
# at which any single verb can dominate the harvest manifest —
# defence-in-depth against Trader v2 itself becoming a new attractor.
_PER_VERB_FLUSH_CAP: dict[str, int] = {
    "craft": HARVEST_CRAFT_CAP_PER_FLUSH,
}


class _RankerProtocol(Protocol):
    def rank(
        self,
        candidates: list[ArtifactCandidate | WIPCandidate],
    ) -> list[Score]: ...


@dataclass(frozen=True, slots=True)
class ArtifactCandidate:
    actor: str
    action: str
    artifact: str | None
    ts: float


@dataclass(frozen=True, slots=True)
class WIPCandidate:
    """A completed workshop WIP ready for harvest.

    Built by ``Harvester.flush()`` from the ``WorkshopProjection`` at
    completion time. The fragments are a snapshot — the WIP is in
    phase=complete so the projection will not add more.

    ``contributors`` is the distinct contributor set in first-
    appearance order; ``fragments`` is the (contributor, text)
    sequence in commit order so the harvested file preserves
    chronology.
    """

    name: str
    contributors: tuple[str, ...]
    fragments: tuple[tuple[str, str], ...]
    ts: float


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("-", text.lower())
    s = s.strip("-_")[:SLUG_MAX_LEN].rstrip("-_")
    if s:
        return s
    # Fallback: hash-based stable slug for non-ASCII / emoji-only inputs.
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"artifact-{digest}"


def _atomic_write_text(target: Path, content: str) -> None:
    """Atomically reserve ``target`` (O_EXCL) then fill it via tmp + replace.

    ``target`` is expected to already be the result of a collision check
    upstream; this function only handles the actual byte transfer.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def _reserve_unique_path(parent: Path, stem: str, suffix: str) -> Path:
    """Atomically create an empty file at ``parent/<stem><n><suffix>``.

    Uses ``O_CREAT|O_EXCL``; on EEXIST, increments ``n`` and retries.
    Returns the reserved path. The caller fills it with the real content
    via ``_atomic_write_text`` (which writes to a tmp and replaces).
    """
    parent.mkdir(parents=True, exist_ok=True)
    n = 0
    while True:
        candidate = parent / (f"{stem}{suffix}" if n == 0 else f"{stem}-{n + 1}{suffix}")
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return candidate


class Harvester:
    """Out-of-world artifact collector.

    Phase 1 contract: ``consider()`` returns the written Path on accept,
    None on reject; in either case appends one line to manifest.jsonl.
    """

    def __init__(
        self,
        harvest_root: str | Path,
        *,
        trader: _RankerProtocol | None = None,
        percentile: int = DEFAULT_PERCENTILE,
        workshop: WorkshopProjection | None = None,
        episodic: EpisodicMemory | None = None,
        now_fn: Callable[[], float] | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._root = Path(harvest_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest = self._root / "manifest.jsonl"
        self._trader = trader
        self._percentile = percentile
        self._buffer: list[ArtifactCandidate] = []
        # v0.2 (ADR 0003): workshop projection reference. flush()
        # scans for newly-completed WIPs and adds them to the
        # candidates list. _harvested_wips dedupes within a single
        # flush — entries are discarded on workshop.recycle so a
        # future completion of the same WIP can be harvested.
        self._workshop = workshop
        self._harvested_wips: set[str] = set()
        # v0.3 (ADR 0004 Decision 1): episodic reference lets the
        # Harvester emit ``workshop.recycle`` and
        # ``workshop.harvest_attempt`` events as the WIP terminal
        # lifecycle progresses. When None the Harvester degrades to
        # v0.2 behavior (no events emitted, ``_harvested_wips`` is the
        # only dedupe). ``now_fn`` lets tests inject a clock so the
        # timeout path can be exercised without sleeping.
        self._episodic = episodic
        self._now_fn: Callable[[], float] = now_fn or time.time
        # v0.3 (ADR 0004 Decision 4): optional metrics reference so the
        # subfloor rejection path can bump ``wip_contributor_subfloor``.
        # Distinct from per-agent metrics — this is a Harvester-side
        # counter.
        self._metrics = metrics

    def consider(self, candidate: ArtifactCandidate) -> Path | None:
        # Phase 2 path: trader present → buffer the candidate, write at flush().
        # Only pre-reject genuinely empty artifacts; let the Trader judge
        # short ones (it has the right context).
        if self._trader is not None:
            if candidate.artifact and candidate.artifact.strip():
                self._buffer.append(candidate)
            else:
                self._append_manifest(candidate, accepted=False, path=None, score=None)
            return None

        # Phase 1 path: heuristic only — accept on length, no LLM call.
        accepted = False
        path: Path | None = None
        if candidate.artifact and len(candidate.artifact.strip()) >= MIN_ARTIFACT_CHARS:
            accepted = True
            path = self._write_artifact(candidate)
        self._append_manifest(candidate, accepted=accepted, path=path, score=None)
        return path

    def flush(self) -> list[Path]:
        """Apply Trader scoring + percentile threshold; write accepted.

        No-op when no trader is configured. Builds a heterogeneous
        candidates list of ArtifactCandidate (from the per-tick
        buffer) AND WIPCandidate (snapshotted from any newly-completed
        WIPs on the workshop projection, if one is attached). Both
        flow through ``Trader.rank()``; the per-verb cap then bounds
        the accepted set so no single primary verb can dominate the
        manifest (ADR 0003 — Trader v2 must not become a new
        attractor).

        WIPs are only marked as harvested *after* a successful write —
        a WIP that scored below the percentile cutoff, was rejected
        by the per-verb cap, or hit a write error remains eligible on
        the next flush. If ``trader.rank()`` raises, the buffer is
        preserved (a transient ranker failure must not silently
        discard pending artifacts) and the exception is re-raised so
        the caller can decide whether to retry; no WIP is marked
        harvested in that case either.
        """
        if self._trader is None:
            # No-trader mode: workshop projection is never drained,
            # buffer is the only state and is already empty (consider()
            # writes immediately in this mode).
            self._buffer.clear()
            return []

        # v0.3 (ADR 0004 Decision 1): time out any WIPs that have been
        # in ``complete`` longer than ``HARVEST_PENDING_TIMEOUT_S``
        # BEFORE the snapshot, so timed-out WIPs never reach the
        # ranker. Force-recycle them and drop their fragments.
        self._timeout_pending_wips()

        wip_candidates = self._snapshot_pending_wips()
        if not self._buffer and not wip_candidates:
            return []

        artifact_candidates: list[ArtifactCandidate] = list(self._buffer)
        candidates: list[ArtifactCandidate | WIPCandidate] = [
            *artifact_candidates,
            *wip_candidates,
        ]
        scores = self._trader.rank(candidates)
        if len(scores) != len(candidates):
            raise RuntimeError(
                f"trader.rank() returned {len(scores)} scores for "
                f"{len(candidates)} candidates — refusing to drop the batch"
            )

        # v0.3 PR #33 review (Gemini): defer the buffer clear until
        # after the write loop completes. If any ``_write_candidate``
        # raises mid-loop, the buffer is preserved so the next flush
        # re-ranks the same artifacts (WIPs already recover via the
        # ``_harvested_wips`` set; artifacts had no equivalent guard).
        # v0.3 (ADR 0004 Decision 4): two cutoffs by candidate kind.
        # Artifacts keep the p70 percentile (v0.2 behavior). WIPs use
        # the absolute WIP_ACCEPTANCE_FLOOR plus the contributor
        # subfloor — the latter is the structurally load-bearing
        # guard against single-contributor padded WIPs.
        artifact_scores = [
            s.score for i, s in enumerate(scores) if isinstance(candidates[i], ArtifactCandidate)
        ]
        artifact_cutoff = self._percentile_cutoff(artifact_scores)

        # Decide acceptance in score-desc order so per-verb caps pick
        # the TOP items (not the first-buffered). Then write the
        # manifest in candidate-input order so audit-trail consumers
        # get a stable insertion-order log.
        order = sorted(range(len(candidates)), key=lambda i: -scores[i].score)
        accepted_set: set[int] = set()
        accepted_paths: dict[int, Path] = {}
        per_verb_count: dict[str, int] = {}
        for idx in order:
            cand = candidates[idx]
            score = scores[idx]
            verb = self._verb_of(cand)
            cap = _PER_VERB_FLUSH_CAP.get(verb)
            if isinstance(cand, WIPCandidate):
                # WIPs: absolute floor + contributor subfloor.
                if len(cand.contributors) < WIP_CONTRIBUTOR_FLOOR:
                    if self._metrics is not None:
                        self._metrics.bump("wip_contributor_subfloor")
                    above_cutoff = False
                else:
                    above_cutoff = score.score >= WIP_ACCEPTANCE_FLOOR
            else:
                # Artifacts: existing p70 percentile.
                # cutoff is None ⇒ no signal across a multi-item all-tied
                # population. Accept nothing rather than spam the inbox.
                above_cutoff = artifact_cutoff is not None and score.score >= artifact_cutoff
            under_cap = cap is None or per_verb_count.get(verb, 0) < cap
            accepted = above_cutoff and under_cap
            if accepted:
                path = self._write_candidate(cand)
                accepted_set.add(idx)
                accepted_paths[idx] = path
                per_verb_count[verb] = per_verb_count.get(verb, 0) + 1
                # Mark the WIP harvested only after a successful write.
                # If _write_candidate raised above, the WIP stays
                # eligible for the next flush.
                if isinstance(cand, WIPCandidate):
                    self._harvested_wips.add(cand.name)
                    self._emit_recycle(cand.name, reason="accepted", dropped_fragments=0)
            elif isinstance(cand, WIPCandidate):
                # v0.3 (ADR 0004 Decision 1): emit one harvest_attempt
                # event per WIPCandidate rejection so the projection's
                # ``harvest_attempts`` counter advances. After
                # MAX_HARVEST_ATTEMPTS the WIP is force-recycled —
                # rejected fragments are dropped (the operator does
                # not want a perpetual rejected backlog).
                self._emit_harvest_attempt(cand.name)
                if self._workshop is not None:
                    wip = self._workshop.get(cand.name)
                    if wip is not None and wip.harvest_attempts >= MAX_HARVEST_ATTEMPTS:
                        self._emit_recycle(
                            cand.name,
                            reason="attempts_exceeded",
                            dropped_fragments=len(wip.fragments),
                        )

        written: list[Path] = []
        for idx, cand in enumerate(candidates):
            score = scores[idx]
            if idx in accepted_set:
                path = accepted_paths[idx]
                written.append(path)
                self._append_manifest(cand, accepted=True, path=path, score=score.score)
            else:
                self._append_manifest(cand, accepted=False, path=None, score=score.score)
        # Successful end-of-flush: every artifact in the buffer has
        # been ranked and resolved (accepted+written or rejected+
        # manifest-logged). Safe to discard. A raise above this line
        # preserves the buffer for the next flush.
        self._buffer.clear()
        return written

    def _timeout_pending_wips(self) -> None:
        """v0.3 (ADR 0004 Decision 1): force-recycle any WIP held in
        ``complete`` longer than ``HARVEST_PENDING_TIMEOUT_S``.

        Skips entirely when there is no workshop projection or no
        episodic reference — both are required to (a) observe the
        ``completed_ts`` and (b) emit the recycle event the projection
        replays from. Timed-out WIPs are NEVER added to the candidates
        list for the current flush; their fragments are dropped.
        """
        if self._workshop is None or self._episodic is None:
            return
        now = self._now_fn()
        for wip in self._workshop.wips():
            if wip.phase != "complete":
                continue
            if wip.completed_ts <= 0.0:
                continue
            if (now - wip.completed_ts) <= HARVEST_PENDING_TIMEOUT_S:
                continue
            self._emit_recycle(
                wip.name,
                reason="timeout",
                dropped_fragments=len(wip.fragments),
            )

    def _emit_recycle(
        self,
        wip_name: str,
        *,
        reason: str,
        dropped_fragments: int,
    ) -> None:
        """Append one ``workshop.recycle`` event to episodic and apply
        it to the live projection so the rest of this flush sees the
        reset state. Discards the ``_harvested_wips`` entry so a
        future re-completion of the same WIP is eligible for harvest.
        """
        if self._episodic is None:
            return
        from microverse.memory.episodic import Event

        ts = self._now_fn()
        payload = {"reason": reason, "dropped_fragments": dropped_fragments}
        event_id = self._episodic.append(
            actor="harvester",
            action="workshop.recycle",
            target=wip_name,
            payload=payload,
            ts=ts,
        )
        self._harvested_wips.discard(wip_name)
        if self._workshop is not None:
            self._workshop.on_event(
                Event(
                    id=event_id,
                    ts=ts,
                    actor="harvester",
                    action="workshop.recycle",
                    target=wip_name,
                    payload=payload,
                )
            )

    def _emit_harvest_attempt(self, wip_name: str) -> None:
        """Append one ``workshop.harvest_attempt`` event and apply it
        to the projection so the counter advances immediately. Used by
        the rejection path inside ``flush()``.
        """
        if self._episodic is None:
            return
        from microverse.memory.episodic import Event

        ts = self._now_fn()
        event_id = self._episodic.append(
            actor="harvester",
            action="workshop.harvest_attempt",
            target=wip_name,
            payload={},
            ts=ts,
        )
        if self._workshop is not None:
            self._workshop.on_event(
                Event(
                    id=event_id,
                    ts=ts,
                    actor="harvester",
                    action="workshop.harvest_attempt",
                    target=wip_name,
                    payload={},
                )
            )

    def _snapshot_pending_wips(self) -> list[WIPCandidate]:
        """Snapshot newly-completed WIPs from the workshop projection
        as immutable WIPCandidate objects. Does NOT mark them
        harvested — that happens only after a successful write in
        ``flush()`` so a WIP rejected by the percentile cutoff or
        per-verb cap stays eligible on the next flush.
        """
        if self._workshop is None:
            return []
        out: list[WIPCandidate] = []
        for w in self._workshop.wips():
            if w.phase != "complete":
                continue
            if w.name in self._harvested_wips:
                continue
            out.append(
                WIPCandidate(
                    name=w.name,
                    contributors=w.contributors(),
                    fragments=tuple((f.contributor, f.text) for f in w.fragments),
                    ts=w.last_activity_ts,
                )
            )
        return out

    def _verb_of(self, cand: ArtifactCandidate | WIPCandidate) -> str:
        if isinstance(cand, WIPCandidate):
            return "wip"
        return cand.action

    def _write_candidate(self, cand: ArtifactCandidate | WIPCandidate) -> Path:
        if isinstance(cand, WIPCandidate):
            return self._write_wip(cand)
        return self._write_artifact(cand)

    def _percentile_cutoff(self, scores: list[float]) -> float | None:
        """Compute a score floor such that values >= floor land in the
        top (100 - percentile)% of the population.

        Edge cases:
          - empty input → None (nothing to compare).
          - single element → that element's score (one item is its own
            top quantile; refusing to harvest a sparse early run would
            be perverse).
          - 2+ elements all tied → None (no ranking signal across
            multiple items; accept nothing rather than spam the inbox).
          - p=0 → minimum score (accept everything).
          - p=100 → maximum score (accept only the top).
        """
        if not scores:
            return None
        if len(scores) == 1:
            return scores[0]
        ordered = sorted(scores)
        if ordered[0] == ordered[-1]:
            return None
        # Linear-rank percentile (no interpolation): index = N*p/100
        # clamped to [0, len-1]. p=70 on 10 items → index 7 → 8th lowest.
        idx = max(0, min(len(ordered) - 1, (len(ordered) * self._percentile) // 100))
        return ordered[idx]

    def _write_wip(self, cand: WIPCandidate) -> Path:
        """Write a completed WIP to ``harvest/inbox/<date>/<name>.md``.

        Each fragment is rendered as one line prefixed by its
        contributor; the frontmatter captures the WIP name, the
        contributor set, the action discriminator ``"wip"``, and the
        completion timestamp. Atomic-replace + O_EXCL collision guard
        matches ``_write_artifact``.
        """
        date = datetime.fromtimestamp(cand.ts, tz=UTC).strftime("%Y-%m-%d")
        day_dir = self._root / "inbox" / date
        # Strip the "workshop." prefix from the slug for readability;
        # the YAML frontmatter still carries the full name.
        slug = _slugify(cand.name.removeprefix("workshop."))
        target = _reserve_unique_path(day_dir, slug, ".md")
        frontmatter = yaml.safe_dump(
            {
                "wip": cand.name,
                "contributors": list(cand.contributors),
                "action": "wip",
                "ts": cand.ts,
            },
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
        body_lines = [f"- {actor}: {text}" for actor, text in cand.fragments]
        body = f"---\n{frontmatter}---\n\n" + "\n".join(body_lines) + "\n"
        _atomic_write_text(target, body)
        return target

    def _write_artifact(self, candidate: ArtifactCandidate) -> Path:
        date = datetime.fromtimestamp(candidate.ts, tz=UTC).strftime("%Y-%m-%d")
        day_dir = self._root / "inbox" / date
        slug = _slugify((candidate.artifact or "")[: SLUG_MAX_LEN * 2])
        target = _reserve_unique_path(day_dir, slug, ".md")
        # YAML-safe frontmatter: defend against actor/action containing
        # colons, quotes, or newlines, even though Phase 1 hard-codes
        # them to safe values.
        frontmatter = yaml.safe_dump(
            {"actor": candidate.actor, "action": candidate.action, "ts": candidate.ts},
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
        body = f"---\n{frontmatter}---\n\n{candidate.artifact}\n"
        _atomic_write_text(target, body)
        return target

    def _active_manifest_path(self) -> Path:
        """Return the live manifest path, rotating if the current file
        exceeds ``MANIFEST_ROTATE_BYTES``.

        On rotation the active ``manifest.jsonl`` is renamed to
        ``manifest-<UTC>.jsonl`` (a frozen audit copy) and a fresh
        ``manifest.jsonl`` becomes the next write target. Readers must
        glob ``manifest*.jsonl`` to see the full history. A 7-day soak
        at Soak B throughput accumulates ~500 KB/h of manifest, so
        rotation is rare (about once a day under the 256 MiB default)
        but bounded.
        """
        live = self._root / "manifest.jsonl"
        if not live.exists():
            return live
        try:
            size = live.stat().st_size
        except OSError:
            return live
        if size <= MANIFEST_ROTATE_BYTES:
            return live
        # Rotate: rename the live file aside, return the fresh path.
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        rotated = self._root / f"manifest-{ts}.jsonl"
        if rotated.exists():
            # Counter for sub-second rotations within the same UTC second.
            for seq in range(1, 1000):
                alt = self._root / f"manifest-{ts}-{seq:03d}.jsonl"
                if not alt.exists():
                    rotated = alt
                    break
        live.replace(rotated)
        return self._root / "manifest.jsonl"

    def _append_manifest(
        self,
        candidate: ArtifactCandidate | WIPCandidate,
        *,
        accepted: bool,
        path: Path | None,
        score: float | None = None,
    ) -> None:
        if isinstance(candidate, WIPCandidate):
            record = {
                "ts": candidate.ts,
                "actor": "workshop",
                "action": "wip",
                "wip": candidate.name,
                "contributors": list(candidate.contributors),
                "accepted": accepted,
                "path": str(path.relative_to(self._root)) if path else None,
                "score": score,
            }
        else:
            record = {
                "ts": candidate.ts,
                "actor": candidate.actor,
                "action": candidate.action,
                "accepted": accepted,
                "path": str(path.relative_to(self._root)) if path else None,
                "score": score,
            }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        # Records are well below PIPE_BUF (4 KB on macOS/Linux), so a
        # single os.write under O_APPEND is atomic across concurrent
        # appenders. fsync ensures the data is on disk before we return.
        # Rotate via _active_manifest_path() each append so a long-soak
        # writer can't outgrow MANIFEST_ROTATE_BYTES.
        target = self._active_manifest_path()
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
