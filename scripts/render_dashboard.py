#!/usr/bin/env python3
"""Render a static HTML dashboard from a Microverse data dir.

Reads ``data/episodic.sqlite`` + ``data/metrics.sqlite`` + the
``harvest/`` tree, emits a single self-contained ``harvest/dashboard.html``
(vanilla HTML + inline CSS, no JS framework, no external assets).

Usage::

    uv run python scripts/render_dashboard.py \
        --data data --harvest harvest [--out harvest/dashboard.html]
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


def _read_metrics_snapshot(db: Path) -> list[tuple[str, str | None, int, float]]:
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            """
            SELECT name, agent, value, ts
            FROM metrics m
            WHERE id = (
                SELECT MAX(id) FROM metrics
                WHERE name = m.name AND IFNULL(agent, '') = IFNULL(m.agent, '')
            )
            ORDER BY name, agent
            """
        ).fetchall()
    finally:
        conn.close()
    return list(rows)


def _read_recent_artifacts(harvest_root: Path, limit: int = 25) -> list[dict[str, object]]:
    manifest = harvest_root / "manifest.jsonl"
    if not manifest.exists():
        return []
    lines = manifest.read_text().splitlines()
    recs: list[dict[str, object]] = []
    for line in reversed(lines[-limit * 4 :]):  # over-pull, then filter
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("accepted"):
            recs.append(r)
        if len(recs) >= limit:
            break
    return recs


def _read_residents(db: Path) -> list[tuple[str, int]]:
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT actor, COUNT(*) AS n FROM events "
            "WHERE actor != 'world' GROUP BY actor ORDER BY n DESC"
        ).fetchall()
    finally:
        conn.close()
    return list(rows)


def _read_world_events(db: Path, limit: int = 20) -> list[tuple[str, float, str]]:
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT action, ts, payload_json FROM events "
            "WHERE actor='world' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return list(rows)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")


_CSS = """
body { font-family: -apple-system, sans-serif; max-width: 980px; margin: 2em auto;
       padding: 0 1em; color: #222; }
h1 { border-bottom: 2px solid #444; padding-bottom: 0.3em; }
h2 { margin-top: 2em; color: #444; }
table { border-collapse: collapse; width: 100%; margin: 0.5em 0 1.5em; }
th, td { text-align: left; padding: 0.4em 0.8em; border-bottom: 1px solid #ddd; }
th { background: #f4f4f4; }
.metric { font-family: ui-monospace, monospace; }
.artifact { background: #fafafa; padding: 0.8em 1em; border-left: 3px solid #888;
            margin: 0.6em 0; }
.muted { color: #888; font-size: 0.9em; }
"""


def render(data_dir: Path, harvest_dir: Path, out_path: Path) -> int:
    metrics = _read_metrics_snapshot(data_dir / "metrics.sqlite")
    artifacts = _read_recent_artifacts(harvest_dir)
    residents = _read_residents(data_dir / "episodic.sqlite")
    weather = _read_world_events(data_dir / "episodic.sqlite")

    parts: list[str] = []
    parts.append("<!doctype html><html><head><meta charset='utf-8'>")
    parts.append("<title>Microverse Dashboard</title>")
    parts.append(f"<style>{_CSS}</style></head><body>")
    parts.append("<h1>Microverse Dashboard</h1>")
    parts.append(
        f"<p class='muted'>generated {_fmt_ts(datetime.now(UTC).timestamp())} "
        f"&middot; data={html.escape(str(data_dir))} "
        f"&middot; harvest={html.escape(str(harvest_dir))}</p>"
    )

    parts.append("<h2>Residents</h2>")
    if residents:
        parts.append("<table><tr><th>actor</th><th>events</th></tr>")
        for actor, n in residents:
            parts.append(f"<tr><td>{html.escape(actor)}</td><td>{n}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>(no agent events yet)</p>")

    parts.append("<h2>Metrics (latest snapshot)</h2>")
    if metrics:
        parts.append(
            "<table class='metric'><tr><th>name</th><th>agent</th><th>value</th><th>ts</th></tr>"
        )
        for name, agent, value, ts in metrics:
            parts.append(
                f"<tr><td>{html.escape(name)}</td>"
                f"<td>{html.escape(agent or '')}</td>"
                f"<td>{html.escape(str(value))}</td>"
                f"<td>{_fmt_ts(ts)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>(no metrics yet)</p>")

    parts.append("<h2>Recent weather</h2>")
    if weather:
        parts.append("<ul>")
        for action, ts, _payload in weather:
            parts.append(
                f"<li><span class='metric'>{_fmt_ts(ts)}</span> &mdash; {html.escape(action)}</li>"
            )
        parts.append("</ul>")
    else:
        parts.append("<p class='muted'>(no weather events yet)</p>")

    parts.append("<h2>Recent harvested artifacts</h2>")
    if artifacts:
        for r in artifacts:
            path = r.get("path") or "?"
            actor = r.get("actor") or "?"
            ts_raw = r.get("ts") or 0.0
            ts = float(ts_raw) if isinstance(ts_raw, (int, float)) else 0.0
            score = r.get("score")
            score_s = f" score={score:.2f}" if isinstance(score, (int, float)) else ""
            body = ""
            try:
                # Path-traversal guard: the manifest is operator-controlled
                # but agent-derived rows could contain ``..`` segments. Resolve
                # the candidate and refuse to read anything outside harvest_dir.
                harvest_root = harvest_dir.resolve()
                candidate = (harvest_dir / str(path)).resolve()
                candidate.relative_to(harvest_root)
                body = candidate.read_text(errors="replace")
                body = body.split("---", 2)[-1].strip()[:600]
            except (OSError, ValueError):
                body = "(unreadable)"
            parts.append(
                f"<div class='artifact'>"
                f"<div class='muted'>{html.escape(str(actor))} &middot; "
                f"{_fmt_ts(float(ts))}{html.escape(score_s)} &middot; "
                f"{html.escape(str(path))}</div>"
                f"<pre>{html.escape(body)}</pre></div>"
            )
    else:
        parts.append("<p class='muted'>(no harvested artifacts yet)</p>")

    parts.append("</body></html>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out_path}")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data", type=Path)
    p.add_argument("--harvest", default="harvest", type=Path)
    p.add_argument("--out", default=None, type=Path)
    args = p.parse_args(argv)
    out = args.out or (args.harvest / "dashboard.html")
    return render(args.data, args.harvest, out)


if __name__ == "__main__":
    sys.exit(main())
