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
import math
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

# Six free-choice verbs (ADR 0008 spike measurement). Gate 9 measures the
# shape of the verb economy; namespaced events (scene.open, weather.*) and
# the world/harvester/scene pseudo-actors are excluded.
_VERBS: tuple[str, ...] = ("speak", "craft", "study", "rest", "travel", "contribute")

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


def _shannon_entropy_bits(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def _entropy_norm(counts: dict[str, int], *, k: int) -> float:
    """Shannon entropy normalized to [0, 1] against ``k`` possible symbols."""
    if k <= 1:
        return 0.0
    return _shannon_entropy_bits(counts) / math.log2(k)


def _normalize(counter: Counter, verbs: Iterable[str]) -> dict[str, float]:
    total = sum(counter.values())
    if total <= 0:
        return dict.fromkeys(verbs, 0.0)
    return {v: counter.get(v, 0) / total for v in verbs}


def _kl_bits(p: dict[str, float], q: dict[str, float]) -> float:
    s = 0.0
    for key, pk in p.items():
        if pk <= 0:
            continue
        qk = q.get(key, 0.0)
        if qk <= 0:  # never happens against the JSD mixture (M >= P/2)
            continue
        s += pk * math.log2(pk / qk)
    return s


def _jsd_bits(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen-Shannon divergence (base 2) between two distributions. For two
    distributions this lands in [0, 1]: 0 identical, 1 disjoint supports."""
    keys = set(p) | set(q)
    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}
    return 0.5 * _kl_bits(p, m) + 0.5 * _kl_bits(q, m)


def _multi_jsd(dists: list[dict[str, float]]) -> float:
    """Cross-agent divergence normalized to [0, 1]. Pairwise for the 2-agent
    roster (the headline case); generalized + normalized by log2(n) otherwise."""
    n = len(dists)
    if n < 2:
        return 0.0
    if n == 2:
        return _jsd_bits(dists[0], dists[1])
    keys = set().union(*(set(d) for d in dists))
    m = {k: sum(d.get(k, 0.0) for d in dists) / n for k in keys}

    def _entropy(d: dict[str, float]) -> float:
        return -sum(v * math.log2(v) for v in d.values() if v > 0)

    jsd = _entropy(m) - sum(_entropy(d) for d in dists) / n
    return jsd / math.log2(n)


def _mean_pairwise_jsd(dists: list[dict[str, float]]) -> float:
    """Mean pairwise Jensen-Shannon divergence across agents (ADR 0016).

    Unlike ``_multi_jsd`` this does NOT divide by ``log2(n)``: each pairwise JSD
    is already in ``[0, 1]`` and the mean of values in ``[0, 1]`` stays in
    ``[0, 1]`` at every N, so the scale (and the inherited 0.25 floor) is
    roster-size invariant by construction. At ``n == 2`` it equals ``_multi_jsd``
    exactly (both return the single ``_jsd_bits``), preserving the entire locked
    2-agent calibration.
    """
    n = len(dists)
    if n < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += _jsd_bits(dists[i], dists[j])
            pairs += 1
    return total / pairs


def _entropy_ceiling_norm(n: int, *, k: int) -> float:
    """Normalized society entropy a perfectly-specialized n-agent roster could
    reach: ``log2(min(n, k)) / log2(k)`` (each agent on a distinct verb)."""
    if k <= 1:
        return 0.0
    span = min(n, k)
    if span <= 1:
        return 0.0
    return math.log2(span) / math.log2(k)


def _specialization_ratio(society_entropy_norm: float, *, n: int, k: int = 6) -> float:
    """Society entropy as a fraction of the perfect-specialist ceiling for N
    agents (ADR 0016, REPORTED not gated). ~1.0 means the society reached the
    diversity a fully-specialized roster of this size would — which can be true
    even when agents are not differentiated from each other, so it must never
    gate."""
    ceiling = _entropy_ceiling_norm(n, k=k)
    if ceiling <= 0:
        return 0.0
    return society_entropy_norm / ceiling


def _identity_verb_nmi(by_agent: dict[str, Counter]) -> float:
    """Normalized mutual information ``I(agent; verb) / sqrt(H(A) * H(V))``
    (ADR 0016, REPORTED not gated). 0 iff verb is independent of identity
    (no specialization); 1 iff agent and verb are perfectly mutually
    predictive (a bijection between residents and verbs)."""
    total = sum(sum(c.values()) for c in by_agent.values())
    if total <= 0:
        return 0.0
    agent_marg: dict[str, float] = {}
    verb_marg: dict[str, float] = defaultdict(float)
    mutual = 0.0
    for agent, counts in by_agent.items():
        a_total = sum(counts.values())
        if a_total <= 0:
            continue
        agent_marg[agent] = a_total / total
        for verb, c in counts.items():
            if c > 0:
                verb_marg[verb] += c / total
    for agent, counts in by_agent.items():
        pa = agent_marg.get(agent, 0.0)
        if pa <= 0:
            continue
        for verb, c in counts.items():
            if c <= 0:
                continue
            pav = c / total
            pv = verb_marg[verb]
            if pv > 0:
                mutual += pav * math.log2(pav / (pa * pv))
    h_a = -sum(p * math.log2(p) for p in agent_marg.values() if p > 0)
    h_v = -sum(p * math.log2(p) for p in verb_marg.values() if p > 0)
    if h_a <= 0 or h_v <= 0:
        return 0.0
    # Clamp: the ratio is in [0, 1] analytically but floating-point can overshoot
    # 1.0 by an ULP at the bijection ceiling.
    return min(1.0, max(0.0, mutual / math.sqrt(h_a * h_v)))


def _diversity_block(by_agent: dict[str, Counter]) -> dict:
    society: Counter = Counter()
    for c in by_agent.values():
        society.update(c)
    society_counts = {v: int(n) for v, n in society.items()}
    agents = sorted(by_agent)
    dists = [_normalize(by_agent[a], _VERBS) for a in agents]
    per_agent_top = {
        a: (
            round(max(by_agent[a].values()) / sum(by_agent[a].values()), 4)
            if sum(by_agent[a].values())
            else 0.0
        )
        for a in agents
    }
    n = len(agents)
    society_entropy_norm = _entropy_norm(society_counts, k=len(_VERBS))
    entropy_ceiling_norm = _entropy_ceiling_norm(n, k=len(_VERBS))
    return {
        "society_counts": society_counts,
        "society_entropy_bits": round(_shannon_entropy_bits(society_counts), 4),
        "society_entropy_norm": round(society_entropy_norm, 4),
        "jsd_norm": round(_multi_jsd(dists), 4),
        # ADR 0016 N>2 diagnostics: mean_pairwise_jsd is the size-invariant
        # divergence; the rest are REPORTED only (never gated).
        "mean_pairwise_jsd": round(_mean_pairwise_jsd(dists), 4),
        "entropy_ceiling_norm": round(entropy_ceiling_norm, 4),
        "specialization_ratio": round(
            _specialization_ratio(society_entropy_norm, n=n, k=len(_VERBS)), 4
        ),
        "identity_verb_nmi": round(_identity_verb_nmi(by_agent), 4),
        "n_agents": n,
        "per_agent_top_share": per_agent_top,
    }


def gate9_verb_diversity(
    ep: sqlite3.Connection,
    *,
    entropy_norm_floor: float = 0.35,
    jsd_norm_floor: float = 0.25,
    divergence_metric: str = "jsd_norm",
    mean_pairwise_floor: float = 0.25,
) -> dict:
    """ADR 0008 re-diagnosis metric. Reports society verb entropy and
    cross-agent JSD on BOTH the executed-verb stream and the model's CHOSEN
    stream (``payload.parsed_verb``, falling back to the executed verb).

    The CHOSEN stream is the headline: a positive read requires the model to
    choose differently under economic pressure, not merely the executor to
    override verbs (high ``substitution_rate`` with a flat chosen stream is a
    forced-by-construction result, not emergent behavior).

    Scope (review): only FREE verb choices count. Scene turns are forced
    contributes (ADR 0006) and ``parse_action`` fallback RESTs are parse
    failures, not choices — both are excluded (scene turns from both streams;
    parse-fallback from the chosen stream) so neither inflates the diversity
    signal. Scene volume is reported separately as ``scene_excluded``.

    ``entropy_norm_floor`` defaults to 0.35: with the 2-agent roster two perfect
    specialists over two verbs cap normalized society entropy at
    ``log2(2)/log2(6) ≈ 0.387`` — the divergence we WANT — so a 0.55 floor would
    reject the target outcome. JSD (cross-agent divergence) is the primary
    signal; the entropy floor only certifies the society broke its single-verb
    monoculture (baseline ≈ 0.2).
    """
    rows = list(
        ep.execute(
            "SELECT actor, action, payload_json FROM events "
            "WHERE actor NOT IN ('world','harvester','scene')"
        )
    )
    verbset = set(_VERBS)
    exec_by_agent: dict[str, Counter] = defaultdict(Counter)
    chosen_by_agent: dict[str, Counter] = defaultdict(Counter)
    n_total = 0
    n_sub = 0
    n_econ_sub = 0
    n_scene_excluded = 0
    for r in rows:
        executed = r["action"]
        if executed not in verbset:
            continue
        payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        # Forced scene contributes (ADR 0006) are not free choices: drop them
        # from BOTH streams so scene volume can't swamp the free-choice signal.
        if payload.get("scene_id"):
            n_scene_excluded += 1
            continue
        actor = r["actor"]
        exec_by_agent[actor][executed] += 1
        n_total += 1
        # Economy-ONLY substitution flag set by the agent's economy lever,
        # distinct from the chosen!=executed total which also folds in
        # diversity / engagement / validator overrides (Codex review).
        if payload.get("economy_substituted"):
            n_econ_sub += 1
        # The CHOSEN (free-choice) stream excludes parse-fallback RESTs: a
        # malformed payload folded to REST is a parse failure, not a verb the
        # model freely chose, so it must not pollute chosen-verb diversity.
        if payload.get("parse_fallback"):
            continue
        chosen = payload.get("parsed_verb") or executed
        if chosen not in verbset:
            chosen = executed
        chosen_by_agent[actor][chosen] += 1
        if chosen != executed:
            n_sub += 1

    executed_block = _diversity_block(exec_by_agent)
    chosen_block = _diversity_block(chosen_by_agent)

    exec_soc: Counter = Counter()
    chosen_soc: Counter = Counter()
    for c in exec_by_agent.values():
        exec_soc.update(c)
    for c in chosen_by_agent.values():
        chosen_soc.update(c)
    cxe = _jsd_bits(_normalize(exec_soc, _VERBS), _normalize(chosen_soc, _VERBS))

    pass_entropy = chosen_block["society_entropy_norm"] >= entropy_norm_floor
    # Both per-metric verdicts are always reported; ``divergence_metric`` selects
    # which one gates ``pass``. The default (``jsd_norm``) preserves the
    # pre-ADR-0016 behavior exactly. ``mean_pairwise_jsd`` removes the log2(n)
    # divisor that penalized N>2 rosters (ADR 0016).
    pass_divergence_jsd = chosen_block["jsd_norm"] >= jsd_norm_floor
    pass_divergence_mpjsd = chosen_block["mean_pairwise_jsd"] >= mean_pairwise_floor
    if divergence_metric == "mean_pairwise_jsd":
        pass_divergence = pass_divergence_mpjsd
    elif divergence_metric == "jsd_norm":
        pass_divergence = pass_divergence_jsd
    else:
        raise ValueError(f"unknown divergence_metric {divergence_metric!r}")
    return {
        "executed": executed_block,
        "chosen": chosen_block,
        "substitution_rate": round((n_sub / n_total) if n_total else 0.0, 4),
        "economy_substitution_rate": round((n_econ_sub / n_total) if n_total else 0.0, 4),
        "chosen_vs_executed_divergence": round(cxe, 4),
        "scene_excluded": n_scene_excluded,
        "entropy_norm_floor": entropy_norm_floor,
        "jsd_norm_floor": jsd_norm_floor,
        "mean_pairwise_floor": mean_pairwise_floor,
        "divergence_metric": divergence_metric,
        "pass_entropy": pass_entropy,
        "pass_divergence_jsd": pass_divergence_jsd,
        "pass_divergence_mpjsd": pass_divergence_mpjsd,
        "pass_divergence": pass_divergence,
        "pass": pass_entropy and pass_divergence,
    }


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


def _iter_manifest_records(harvest_dir: Path):
    """Phase A: rotation may produce multiple manifest*.jsonl files.
    Yield records from all of them (live + archives) in time order."""
    manifests = sorted(harvest_dir.glob("manifest*.jsonl"))
    for m in manifests:
        try:
            with m.open() as f:
                for line in f:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def gate2_wip_throughput(manifest_path: Path, ep: sqlite3.Connection) -> dict:
    """Accepted WIPs per hour >= 5.

    ``manifest_path`` may be either the live `manifest.jsonl` or the
    harvest directory itself; we accept both and iterate every
    rotation-produced ``manifest*.jsonl`` under the parent dir."""
    harvest_dir = manifest_path if manifest_path.is_dir() else manifest_path.parent
    if not harvest_dir.exists():
        return {"accepted_wips": 0, "hours": 0.0, "rate_per_hour": 0.0, "pass": False}
    accepted = 0
    for rec in _iter_manifest_records(harvest_dir):
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
    harvest_dir = manifest_path if manifest_path.is_dir() else manifest_path.parent
    if harvest_dir.exists():
        for rec in _iter_manifest_records(harvest_dir):
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


def gate8_scene_semantic_dependence(
    ep: sqlite3.Connection,
    *,
    band_low: float = 0.30,
    band_high: float = 0.85,
) -> dict:
    """ADR 0005 Gate 7 (scene semantic dependence) — median cosine of
    turn-N's embedding against the union of prior-turn embeddings
    should fall in ``[band_low, band_high]``. Below band: non-sequitur
    turns. Above band: echoing. The band is the structural guard that
    scenes pass gate 1 (peer reference) for real rather than by lexical
    fluke.

    The embedding model (``config.EMBEDDING_MODEL`` =
    ``nomic-embed-text``) must be pulled separately: if unavailable
    the gate degrades to ``available=False`` with no pass/fail verdict
    — the operator decides whether to halt or proceed.

    Numbered ``gate8_*`` instead of ``gate7_*`` to coexist with the
    existing ``gate7_capacity_invariant``. The ADR 0005 v0.4 acceptance
    gates 1-7 in this script map to 1=g1, 2=g2, 3=g3, 4=g4, 5=g5, 6=g6,
    7=g7 (capacity), and the scene-only sub-gate lives here as ``g8``.
    """
    # Lazy import: the spike script can run on a soak dir that has no
    # scene events at all (pre-Phase-C data), and we should not fail
    # on the embedding import in that case.
    try:
        from microverse.llm.embeddings import cosine, embed
    except Exception as exc:
        return {"available": False, "reason": f"import_failed: {exc}"}

    # Pull every scene_id → ordered turn fragments.
    scene_open_rows = list(
        ep.execute("SELECT payload_json FROM events WHERE action='scene.open'").fetchall()
    )
    if not scene_open_rows:
        return {"available": True, "scenes": 0, "pass": True, "reason": "no_scenes"}

    scene_ids: list[str] = []
    for r in scene_open_rows:
        try:
            payload = json.loads(r[0] or "{}")
        except json.JSONDecodeError:
            continue
        sid = payload.get("scene_id")
        if sid:
            scene_ids.append(sid)

    # Fetch ALL scene-tagged contributes once, group by scene_id in
    # Python. Avoids O(N_scenes x N_events) pathological scans on long
    # soaks (per Gemini PR review on #38).
    turns_by_scene: dict[str, dict[int, str]] = {}
    for (payload_json,) in ep.execute(
        "SELECT payload_json FROM events WHERE action='contribute' ORDER BY id ASC"
    ).fetchall():
        try:
            p = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            continue
        sid = p.get("scene_id")
        ti = p.get("turn_index")
        if not sid or ti not in (1, 2, 3):
            continue
        txt = (p.get("fragment") or p.get("artifact") or "").strip()
        if not txt:
            continue
        turns_by_scene.setdefault(sid, {})[int(ti)] = txt

    cos_t2 = []  # cosine(turn2, turn1)
    cos_t3 = []  # cosine(turn3, mean-ish: cosine vs concatenation of t1+t2)
    completed = 0
    aborted = 0
    for sid in scene_ids:
        turns = turns_by_scene.get(sid, {})
        if len(turns) < 3:
            aborted += 1
            continue
        completed += 1
        e1 = embed(turns[1])
        e2 = embed(turns[2])
        e3 = embed(turns[3])
        if not e1 or not e2 or not e3:
            # embedding unavailable
            continue
        cos_t2.append(cosine(e2, e1))
        # turn3 vs t1+t2: treat the concatenation as one input.
        e12 = embed(turns[1] + " " + turns[2])
        if e12:
            cos_t3.append(cosine(e3, e12))

    def _median(xs: list[float]) -> float:
        if not xs:
            return 0.0
        ys = sorted(xs)
        n = len(ys)
        return ys[n // 2] if n % 2 else 0.5 * (ys[n // 2 - 1] + ys[n // 2])

    # If we had complete scenes but either cosine stream is empty, the
    # gate cannot be evaluated symmetrically — degrade to unknown
    # rather than report a false fail (CodeRabbit + Codex on #38). The
    # turn3-vs-(turn1+turn2) stream can be empty independently of the
    # turn2-vs-turn1 stream (the concatenation may exceed the embedding
    # model's input cap even when individual turns embed fine), so we
    # require both streams present before computing the gate.
    if completed > 0 and (not cos_t2 or not cos_t3):
        return {
            "available": False,
            "reason": "embedding_unavailable",
            "scenes_opened": len(scene_ids),
            "scenes_completed": completed,
            "scenes_aborted": aborted,
            "samples_turn2": len(cos_t2),
            "samples_turn3": len(cos_t3),
            "band": [band_low, band_high],
        }

    median_t2 = _median(cos_t2)
    median_t3 = _median(cos_t3)
    pass_t2 = bool(cos_t2) and band_low <= median_t2 <= band_high
    pass_t3 = bool(cos_t3) and band_low <= median_t3 <= band_high
    return {
        "available": True,
        "scenes_opened": len(scene_ids),
        "scenes_completed": completed,
        "scenes_aborted": aborted,
        "cosine_turn2_vs_turn1_median": round(median_t2, 4),
        "cosine_turn3_vs_turn1_2_median": round(median_t3, 4),
        "samples_turn2": len(cos_t2),
        "samples_turn3": len(cos_t3),
        "band": [band_low, band_high],
        "subgate_a_turn2_in_band": pass_t2,
        "subgate_b_turn3_in_band": pass_t3,
        "pass": pass_t2 and pass_t3,
    }


def summarize_contribute_total(ep: sqlite3.Connection) -> int:
    row = ep.execute("SELECT COUNT(*) FROM events WHERE action='contribute'").fetchone()
    return int(row[0])


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, help="data dir with episodic.sqlite + metrics.sqlite")
    p.add_argument("--harvest", required=True, help="harvest dir with manifest.jsonl")
    p.add_argument(
        "--divergence-metric",
        choices=["jsd_norm", "mean_pairwise_jsd"],
        default="jsd_norm",
        help="metric gating Gate 9's top-line pass (default jsd_norm; "
        "mean_pairwise_jsd is the size-invariant N>2 bar, ADR 0016)",
    )
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
            "SELECT DISTINCT actor FROM events WHERE actor NOT IN ('world','harvester','scene')"
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
    g8 = gate8_scene_semantic_dependence(ep)
    g9 = gate9_verb_diversity(ep, divergence_metric=args.divergence_metric)

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
        "gate_8_scene_semantic_dependence": g8,
        "gate_9_verb_diversity": g9,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
