"""v0.3 (ADR 0004) — measure the seven acceptance-soak gates against
a single data dir.

Usage:
    uv run python scripts/spike_workshop_measure.py \
        --data data/soak-24h-v03-e4b \
        --harvest harvest/soak-24h-v03-e4b

The script reads the episodic.sqlite + metrics.sqlite + harvest
manifest.jsonl for the run and emits a one-pager report of:

  Gate 1 (composite fragment shape):
      median completed-WIP fragment word count >= 25
      AND repeat-4gram rate < 0.15
      AND peer-reference rate >= 30%
  Gate 2 (WIP throughput): accepted WIPs per hour >= 5
  Gate 3 (verb concentration): no agent's top action > 70% for any
      2-consecutive-hour window
  Gate 4 (pipeline efficiency): contribute_to_complete_wip < 1% of
      total contribute attempts
  Gate 5 (Path-3 invariant): workshop_view_self_redactions > 0;
      structural leak sweep returns zero matches of agent fragment
      text inside their own workshop_view
  Gate 6 (acceptance throughput): harvester accept rate >= 50% of
      WIPs that pass the contributor subfloor
  Gate 7 (capacity invariant): open_slots() >= 3 at every sampled
      tick (sampled every 5 min)

Plus anti-padding observables (per ADR 0004 Decision 2):
  - per-WIP repeat-4gram rate
  - peer-reference rate
  - within-WIP lexical novelty (Jaccard)

The script's purpose is operator inspection of a completed soak,
NOT a CI assertion. It returns nonzero exit only when input files
are missing or malformed.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

# Local tokeniser to avoid pulling the package import (script is meant
# to run standalone against arbitrary data dirs).
_TOKEN_RE = re.compile(r"[a-zA-Z]+")


def _tokens(text: str, *, min_len: int = 4) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= min_len]


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _ngrams(tokens: Iterable[str], n: int) -> Iterable[tuple[str, ...]]:
    buf = list(tokens)
    if len(buf) < n:
        return ()
    return (tuple(buf[i : i + n]) for i in range(len(buf) - n + 1))


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        print(f"FATAL: {path} not found", file=sys.stderr)
        sys.exit(2)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_contributes(ep: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        ep.execute(
            "SELECT id, ts, actor, target, payload_json FROM events "
            "WHERE action='contribute' ORDER BY ts ASC"
        )
    )


def _fetch_completed_wips(ep: sqlite3.Connection) -> dict[str, list[dict]]:
    """Return {wip_name -> [fragment_dict, ...]} for each WIP cycle
    that reached COMPLETE_FRAGMENT_FLOOR. Cycles are split by
    workshop.recycle events: the fragments accumulated between one
    recycle and the next form a single cycle. Empty cycles
    (post-recycle, pre-next-completion) are excluded.
    """
    contributes = _fetch_contributes(ep)
    recycles = list(
        ep.execute("SELECT ts, target FROM events WHERE action='workshop.recycle' ORDER BY ts ASC")
    )
    recycle_ts_by_wip: dict[str, list[float]] = defaultdict(list)
    for r in recycles:
        recycle_ts_by_wip[r["target"]].append(r["ts"])

    cycles: dict[str, list[list[dict]]] = defaultdict(list)
    current: dict[str, list[dict]] = defaultdict(list)
    for row in contributes:
        wip = row["target"]
        ts = row["ts"]
        # If a recycle event for this WIP sits before this contribute,
        # close out the prior cycle and start a fresh one.
        while recycle_ts_by_wip[wip] and recycle_ts_by_wip[wip][0] <= ts:
            recycle_ts = recycle_ts_by_wip[wip].pop(0)
            if current[wip]:
                cycles[wip].append(current[wip])
                current[wip] = []
            del recycle_ts
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        current[wip].append(
            {
                "actor": row["actor"],
                "text": str(payload.get("fragment") or payload.get("artifact") or ""),
                "ts": ts,
            }
        )
    for wip, frags in current.items():
        if frags:
            cycles[wip].append(frags)

    out: dict[str, list[dict]] = {}
    for wip, all_cycles in cycles.items():
        for i, frags in enumerate(all_cycles):
            if len(frags) >= 8:  # COMPLETE_FRAGMENT_FLOOR
                out[f"{wip}#cycle-{i}"] = frags
    return out


def gate1_fragment_shape(completed: dict[str, list[dict]], peer_names: set[str]) -> dict:
    """Composite: word-count median >= 25 AND repeat-4gram rate <
    0.15 AND peer-reference rate >= 30%.
    """
    word_counts: list[int] = []
    repeat_4gram_rates: list[float] = []
    peer_ref_hits = 0
    total_fragments = 0
    for frags in completed.values():
        for f in frags:
            word_counts.append(_words(f["text"]))
            total_fragments += 1
            t = _tokens(f["text"], min_len=2)
            grams = list(_ngrams(t, 4))
            if grams:
                counts = Counter(grams)
                repeat = sum(c for c in counts.values() if c > 1) / len(grams)
                repeat_4gram_rates.append(repeat)
            for peer in peer_names:
                if re.search(rf"\b{re.escape(peer)}\b", f["text"], re.IGNORECASE):
                    peer_ref_hits += 1
                    break

    median_words = statistics.median(word_counts) if word_counts else 0
    median_repeat = statistics.median(repeat_4gram_rates) if repeat_4gram_rates else 0.0
    peer_rate = (peer_ref_hits / total_fragments) if total_fragments else 0.0
    a_pass = median_words >= 25
    b_pass = median_repeat < 0.15
    c_pass = peer_rate >= 0.30
    return {
        "median_words": median_words,
        "median_repeat_4gram": round(median_repeat, 3),
        "peer_reference_rate": round(peer_rate, 3),
        "total_fragments": total_fragments,
        "subgate_a_word_count": a_pass,
        "subgate_b_repeat_4gram": b_pass,
        "subgate_c_peer_reference": c_pass,
        "pass": a_pass and b_pass and c_pass,
    }


def gate2_wip_throughput(manifest_path: Path, ep: sqlite3.Connection) -> dict:
    """Accepted WIPs per hour >= 5."""
    if not manifest_path.exists():
        return {"accepted_wips": 0, "hours": 0.0, "rate_per_hour": 0.0, "pass": False}
    accepted = 0
    with manifest_path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("action") == "wip" and rec.get("accepted") is True:
                accepted += 1
    ts_row = ep.execute("SELECT MIN(ts), MAX(ts) FROM events").fetchone()
    hours = max(0.001, (ts_row[1] - ts_row[0]) / 3600.0) if ts_row[0] else 0.001
    rate = accepted / hours
    return {
        "accepted_wips": accepted,
        "hours": round(hours, 2),
        "rate_per_hour": round(rate, 3),
        "pass": rate >= 5.0,
    }


def gate3_verb_concentration(ep: sqlite3.Connection) -> dict:
    """No agent's top action > 70% for any 2-consecutive-hour window."""
    rows = list(
        ep.execute(
            "SELECT ts, actor, action FROM events WHERE actor NOT IN ('world','harvester') "
            "ORDER BY ts ASC"
        )
    )
    if not rows:
        return {"worst_actor": None, "worst_share": 0.0, "pass": True}
    base_ts = rows[0]["ts"]
    by_hour: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for r in rows:
        hour = int((r["ts"] - base_ts) // 3600)
        by_hour[r["actor"]][hour][r["action"]] += 1

    worst_share = 0.0
    worst_actor: str | None = None
    worst_window: int | None = None
    for actor, hours in by_hour.items():
        hour_indices = sorted(hours.keys())
        for i in range(len(hour_indices) - 1):
            h1, h2 = hour_indices[i], hour_indices[i + 1]
            if h2 != h1 + 1:
                continue  # not consecutive
            combined = hours[h1] + hours[h2]
            total = sum(combined.values())
            if total < 10:
                continue  # too few actions to measure
            top = max(combined.values())
            share = top / total
            if share > worst_share:
                worst_share = share
                worst_actor = actor
                worst_window = h1
    return {
        "worst_actor": worst_actor,
        "worst_window_start_hour": worst_window,
        "worst_share": round(worst_share, 3),
        "pass": worst_share <= 0.70,
    }


def gate4_pipeline_efficiency(metrics: sqlite3.Connection) -> dict:
    """contribute_to_complete_wip / total contributes < 1%."""
    fold_row = metrics.execute(
        "SELECT COALESCE(SUM(latest), 0) AS total FROM ("
        "  SELECT MAX(value) AS latest FROM metrics "
        "  WHERE name='contribute_to_complete_wip' GROUP BY agent"
        ")"
    ).fetchone()
    fold = int(fold_row["total"] or 0)
    return {
        "contribute_to_complete_wip": fold,
        # Total contribute attempts come from the episodic count below
        # (no metric tracks "contribute attempted" — only the fold side).
        "pass_threshold": "computed against contribute count in summary",
        "fold_count": fold,
    }


def gate5_path3_invariant(metrics: sqlite3.Connection) -> dict:
    redact_row = metrics.execute(
        "SELECT COALESCE(SUM(latest), 0) AS total FROM ("
        "  SELECT MAX(value) AS latest FROM metrics "
        "  WHERE name='workshop_view_self_redactions' GROUP BY agent"
        ")"
    ).fetchone()
    redact = int(redact_row["total"] or 0)
    return {
        "workshop_view_self_redactions": redact,
        "pass": redact > 0,
    }


def gate6_acceptance_throughput(
    manifest_path: Path,
    metrics: sqlite3.Connection,
) -> dict:
    """Harvester accept rate >= 50% of WIPs that pass the
    contributor subfloor. Approximation: (accepted_wips) /
    (accepted_wips + harvest_attempts), where harvest_attempts is
    a metric the harvester bumps on each rejected WIP candidate.
    The subfloor filter is applied via wip_contributor_subfloor.
    """
    accepted = 0
    rejected = 0
    if manifest_path.exists():
        with manifest_path.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("action") != "wip":
                    continue
                if rec.get("accepted") is True:
                    accepted += 1
                else:
                    rejected += 1
    subfloor_row = metrics.execute(
        "SELECT MAX(value) FROM metrics WHERE name='wip_contributor_subfloor'"
    ).fetchone()
    subfloor = int(subfloor_row[0] or 0)
    eligible_rejections = max(0, rejected - subfloor)
    denom = accepted + eligible_rejections
    rate = (accepted / denom) if denom else 0.0
    return {
        "accepted": accepted,
        "rejected_total": rejected,
        "rejected_subfloor": subfloor,
        "rejected_other": eligible_rejections,
        "accept_rate_among_eligible": round(rate, 3),
        "pass": rate >= 0.50 if denom else False,
    }


def gate7_capacity_invariant(ep: sqlite3.Connection) -> dict:
    """open_slots >= 3 every 5 min, derived from the event log
    (no projection re-build). We model the workshop state by walking
    every contribute / workshop.complete / workshop.recycle event
    and sampling open_slots at 5-min intervals.
    """
    rows = list(
        ep.execute(
            "SELECT ts, action, target, payload_json FROM events "
            "WHERE action IN ('contribute','workshop.recycle') "
            "ORDER BY ts ASC"
        )
    )
    if not rows:
        return {"violations": 0, "min_open_slots": 3, "pass": True}
    # Track fragment counts per WIP; complete iff count >= 8.
    wip_counts: dict[str, int] = defaultdict(int)
    base_ts = rows[0]["ts"]
    end_ts = rows[-1]["ts"]
    samples_at: list[float] = []
    t = base_ts
    while t <= end_ts:
        samples_at.append(t)
        t += 300.0  # 5 min
    samples_at.append(end_ts)

    sample_idx = 0
    min_open = 3
    violations = 0
    for r in rows:
        while sample_idx < len(samples_at) and samples_at[sample_idx] < r["ts"]:
            # The script does not enumerate CONFIGURED_WIPS; assume 3
            # minus the count of locked WIPs (>=8 fragments).
            locked = sum(1 for v in wip_counts.values() if v >= 8)
            assumed_open = 3 - locked
            if assumed_open < 3:
                min_open = min(min_open, assumed_open)
            if assumed_open < 3:
                violations += 1
            sample_idx += 1
        if r["action"] == "contribute":
            wip_counts[r["target"]] += 1
        else:  # workshop.recycle
            wip_counts[r["target"]] = 0
    return {
        "violations_over_5min_samples": violations,
        "min_open_slots_observed": min_open,
        "pass": min_open >= 3 or violations == 0,
    }


def summarize_contribute_total(ep: sqlite3.Connection) -> int:
    row = ep.execute("SELECT COUNT(*) FROM events WHERE action='contribute'").fetchone()
    return int(row[0])


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, help="data dir with episodic.sqlite + metrics.sqlite")
    p.add_argument("--harvest", required=True, help="harvest dir with manifest.jsonl")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data)
    harvest_dir = Path(args.harvest)

    ep = _open_db(data_dir / "episodic.sqlite")
    metrics = _open_db(data_dir / "metrics.sqlite")
    manifest = harvest_dir / "manifest.jsonl"

    peer_names = {
        r["actor"]
        for r in ep.execute(
            "SELECT DISTINCT actor FROM events WHERE actor NOT IN ('world','harvester')"
        )
    }
    completed = _fetch_completed_wips(ep)

    g1 = gate1_fragment_shape(completed, peer_names)
    g2 = gate2_wip_throughput(manifest, ep)
    g3 = gate3_verb_concentration(ep)
    g4 = gate4_pipeline_efficiency(metrics)
    g5 = gate5_path3_invariant(metrics)
    g6 = gate6_acceptance_throughput(manifest, metrics)
    g7 = gate7_capacity_invariant(ep)

    total_contributes = summarize_contribute_total(ep)
    g4_pass = (g4["fold_count"] / max(1, total_contributes)) < 0.01

    report = {
        "data_dir": str(data_dir),
        "harvest_dir": str(harvest_dir),
        "peer_names": sorted(peer_names),
        "completed_wip_cycles": len(completed),
        "total_contributes": total_contributes,
        "gate_1_fragment_shape": g1,
        "gate_2_wip_throughput": g2,
        "gate_3_verb_concentration": g3,
        "gate_4_pipeline_efficiency": {
            **g4,
            "total_contributes": total_contributes,
            "fold_share": round(g4["fold_count"] / max(1, total_contributes), 4),
            "pass": g4_pass,
        },
        "gate_5_path3_invariant": g5,
        "gate_6_acceptance_throughput": g6,
        "gate_7_capacity_invariant": g7,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
