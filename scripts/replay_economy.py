"""Offline replay + synthetic simulator for the action-economy spike (ADR 0008).

ZERO new LLM compute. Two stages that gate the expensive A/B runs (Codex review):

  Stage 0 (replay): feed an EXISTING run's committed verb trace
    (data/<run>/episodic.sqlite, produced with the economy OFF) through the
    EnergyLedger executor offline, to estimate the mechanical contribute-share
    ceiling and substitution rate the cost numbers would produce — and to
    expose degenerate equilibria (e.g. ENERGY_MAX too high to bite) BEFORE
    spending ~6h/run on a live A/B.

  Stage 1 (synthetic): drive the same executor with no-LLM policies
    (always-contribute, uniform-random, role-biased) to prove the cost numbers
    mechanically diversify and to read the theoretical entropy ceiling.

Both reuse ``EnergyLedger.resolve_executed_verb`` so the offline estimate
matches the live lever exactly. Regen mirrors the live loop: the whole roster
regenerates once per tick, with a scene's three forced contributes collapsed
into a single regen tick (run.py:1127). These remain estimates (the trace
records only the acting agent's chosen verb), not the live measurement — run
``spike_workshop_measure.py`` on a real run for the gate read.

Usage:
    uv run python scripts/replay_economy.py --data data/econ-off-s42
    uv run python scripts/replay_economy.py --synthetic --ticks 2000 --seed 42
    # tuning sweep: override the knobs without editing config
    uv run python scripts/replay_economy.py --synthetic --regen 8 --energy-max 100
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from microverse.config import ENERGY_MAX, ENERGY_REGEN_PER_TICK
from microverse.world.economy import EnergyLedger, build_cost_table

_VERBS = ("speak", "craft", "study", "rest", "travel", "contribute")
# Default 2-resident roster mirrors run.py's _build_roster.
_DEFAULT_ROSTER: tuple[tuple[str, str], ...] = (("Aki", "artisan"), ("Cy", "scholar"))


def _entropy_norm(counts: Counter, *, k: int = 6) -> float:
    total = sum(counts.values())
    if total <= 0 or k <= 1:
        return 0.0
    h = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
    return h / math.log2(k)


def _classify_scarcity(ledger: EnergyLedger, actor: str, role: str) -> tuple[bool, bool, bool]:
    """Snapshot a pool's affordability state for the scarcity probe:
    ``(contribute_affordable, study_affordable, any_productive_affordable)``.
    Read BEFORE the turn resolves/deducts. The live energy hint fires exactly
    when contribute is NOT affordable, and names study when study still is — so
    these three booleans are the mechanical proxy for "would the hint push this
    actor off contribute toward its specialty" at the current cost target."""
    contribute_ok = ledger.can_afford(actor, role, "contribute")
    study_ok = ledger.can_afford(actor, role, "study")
    productive_ok = any(ledger.can_afford(actor, role, v) for v in _VERBS if v != "rest")
    return contribute_ok, study_ok, productive_ok


def replay_executor(
    events: Sequence[
        tuple[str, str, str] | tuple[str, str, str, bool] | tuple[str, str, str, bool, str | None]
    ],
    *,
    ledger: EnergyLedger,
) -> dict:
    """Run a chronological ``(actor, role, chosen_verb[, forced[, scene_id]])``
    trace through the executor. Per event: resolve (afford-or-substitute) at the
    current pool, deduct the executed verb, then regen the whole roster once per
    live tick. Returns chosen/executed counts, the substitution rate, and a
    per-actor scarcity probe.

    ``forced`` (4th element, default False) marks a scene turn (ADR 0006). Live
    scene contributes are NEVER substituted (the lever skips scene turns), so a
    forced event is deducted at its chosen verb without substitution — otherwise
    the offline estimate would predict substitutions that cannot happen live and
    inflate the substitution rate (review). Forced turns are also excluded from
    the scarcity denominator: they cannot trigger the hint, so they are not
    "free turns" the lever could act on (Stage 6).

    ``scene_id`` (5th element, default None) groups a scene's three forced
    contributes into ONE regen tick. The live loop deducts each scene turn but
    regenerates the whole roster only once after ``SceneRunner.run`` completes
    (run.py:1127); regenerating per forced turn would add two phantom regens per
    scene, inflating later affordability and under-reporting scarcity (Codex
    review P1). A free (non-scene) turn is always its own tick."""
    chosen: Counter = Counter()
    executed: Counter = Counter()
    subs = 0
    # Per-actor scarcity tallies over non-forced ("free") turns.
    free_turns: Counter = Counter()
    contribute_out_study_ok: Counter = Counter()
    rest_only: Counter = Counter()
    ev_list = list(events)
    for idx, event in enumerate(ev_list):
        actor, role, verb, *rest = event
        forced = bool(rest[0]) if rest else False
        scene_id = rest[1] if len(rest) > 1 else None
        if not forced:
            contribute_ok, study_ok, productive_ok = _classify_scarcity(ledger, actor, role)
            free_turns[actor] += 1
            if not contribute_ok and study_ok:
                contribute_out_study_ok[actor] += 1
            if not productive_ok:
                rest_only[actor] += 1
        ex = verb if forced else ledger.resolve_executed_verb(actor, role, verb)
        ledger.deduct(actor, role, ex)
        # Whole-roster regen once per live tick (a lightly-scheduled actor still
        # regenerates on others' turns, so it is not under-regenerated — Stage 6
        # R2 fidelity). A scene is one tick: collapse consecutive same-scene
        # forced turns so the regen fires only on the scene's last turn, matching
        # the live single post-scene regen (Codex review P1). Key on ``forced``,
        # not scene_id alone, so a non-forced turn is always its own tick even if
        # a malformed trace attaches a scene_id to it (Codex review, defensive).
        nxt = ev_list[idx + 1] if idx + 1 < len(ev_list) else None
        next_forced = bool(nxt[3]) if nxt is not None and len(nxt) > 3 else False
        next_scene = nxt[4] if nxt is not None and len(nxt) > 4 else None
        in_same_scene = forced and scene_id is not None and next_forced and next_scene == scene_id
        if not in_same_scene:
            ledger.regen_all()
        chosen[verb] += 1
        executed[ex] += 1
        if ex != verb:
            subs += 1
    total = sum(executed.values())
    scarcity = {
        actor: {
            "free_turns": n,
            "contribute_out_study_ok_rate": round(contribute_out_study_ok[actor] / n, 4),
            "rest_only_rate": round(rest_only[actor] / n, 4),
        }
        if n
        else {"free_turns": 0, "contribute_out_study_ok_rate": 0.0, "rest_only_rate": 0.0}
        for actor, n in free_turns.items()
    }
    # Actors that appeared only in forced turns still deserve a (zeroed) entry.
    for actor in {a for a, _, *_ in events} - set(scarcity):
        scarcity[actor] = {
            "free_turns": 0,
            "contribute_out_study_ok_rate": 0.0,
            "rest_only_rate": 0.0,
        }
    return {
        "total": total,
        "chosen_counts": dict(chosen),
        "executed_counts": dict(executed),
        "substitution_rate": round(subs / total, 4) if total else 0.0,
        "chosen_contribute_share": round(chosen.get("contribute", 0) / total, 4) if total else 0.0,
        "executed_contribute_share": round(executed.get("contribute", 0) / total, 4)
        if total
        else 0.0,
        "executed_entropy_norm": round(_entropy_norm(executed), 4),
        "scarcity": scarcity,
    }


def _trace_from_episodic(path: Path) -> list[tuple[str, str, str, bool, str | None]]:
    """Chronological ``(actor, role, chosen_verb, forced, scene_id)`` from a
    committed run. The chosen verb is ``payload.parsed_verb`` when present (an
    economy-on run), else the executed action (an economy-off run — the model's
    own choice). ``forced`` is True for scene turns (``payload.scene_id``), which
    the executor must deduct without substituting (review). ``scene_id`` lets the
    replay group a scene's three forced contributes into one regen tick, matching
    the live loop's single whole-roster regen per scene (run.py:1127)."""
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT actor, action, payload_json FROM events "
            "WHERE actor NOT IN ('world','harvester','scene') ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()
    out: list[tuple[str, str, str, bool, str | None]] = []
    for actor, action, payload_json in rows:
        if action not in _VERBS:
            continue
        payload = json.loads(payload_json) if payload_json else {}
        role = str(payload.get("role", ""))
        chosen = payload.get("parsed_verb") or action
        scene_id = payload.get("scene_id")
        forced = bool(scene_id)
        if role and chosen in _VERBS:
            out.append((actor, role, chosen, forced, scene_id))
    return out


# --- Mechanism audit (ADR 0012 Phase 2 item 2) -------------------------------
#
# The Stage 6 PASS claims the scarcity hint is the operative channel: the
# balanced contribute cost drains the scholar, the hint fires and names study,
# the model obeys. The hint text itself is never persisted, but its state is
# deterministically reconstructable: replay the energy ledger over the LOGGED
# executed-verb stream (exact live arithmetic — live deducts the committed
# verb, run.py:1222) and re-evaluate run.py's hint predicate at each free turn
# pre-deduct (the live hint is computed before think(), run.py:983). The
# logged ``economy_substituted`` flag provides a per-event fidelity check on
# the reconstruction (the audit's C5 gate).

# Mirrors run.py's _ENERGY_HINT_PRODUCTIVE; the parity test in
# tests/test_economy_audit.py pins the two against each other so they cannot
# drift silently.
_HINT_PRODUCTIVE = ("speak", "craft", "study", "travel", "contribute")
# Modes whose live prompts carry the scarcity hint (config._ECONOMY_SUBSTITUTE).
_SUBSTITUTE_MODES = frozenset({"1", "flat", "sub", "adv", "bal"})


@dataclass(frozen=True)
class AuditEvent:
    """One committed agent action with both verb streams + telemetry flags.

    ``hint_fired_logged`` / ``hint_verb_logged`` carry the ground-truth hint
    state stamped live by replication runs (Phase 2 item 3, ADR 0013 D3);
    ``None`` (fired) means the run predates hint logging and there is no
    ground truth to check against."""

    actor: str
    role: str
    chosen: str
    executed: str
    forced: bool = False
    scene_id: str | None = None
    parse_fallback: bool = False
    economy_substituted: bool = False
    hint_fired_logged: bool | None = None
    hint_verb_logged: str | None = None


@dataclass(frozen=True)
class AuditSpec:
    """One ``--audit MODE[@TARGET]=PATH`` run assignment. The run dir does not
    record its economy mode, so the operator must restate it (the dir naming
    convention ``econ-stage6-<arm>-s<seed>`` carries it)."""

    mode: str
    target: float | None
    path: Path
    arm: str


def parse_audit_spec(spec: str) -> AuditSpec:
    """Parse ``MODE[@TARGET]=PATH``. Raises ``ValueError`` (never clamps) on a
    malformed spec so a typo cannot silently audit the wrong cost table."""
    arm, sep, path = spec.partition("=")
    if not sep or not path or not arm:
        raise ValueError(f"invalid audit spec {spec!r}: expected MODE[@TARGET]=PATH")
    mode, tsep, traw = arm.partition("@")
    target: float | None = None
    if tsep:
        if mode != "bal":
            raise ValueError(f"invalid audit spec {spec!r}: a @TARGET is only valid for mode 'bal'")
        try:
            target = float(traw)
        except ValueError:
            raise ValueError(
                f"invalid audit spec {spec!r}: target {traw!r} is not a number"
            ) from None
        if not math.isfinite(target) or target <= 0:
            raise ValueError(
                f"invalid audit spec {spec!r}: target must be a positive finite number"
            )
    if mode not in _SUBSTITUTE_MODES:
        raise ValueError(
            f"invalid audit spec {spec!r}: mode {mode!r} has no live scarcity hint "
            f"(expected one of {sorted(_SUBSTITUTE_MODES)})"
        )
    return AuditSpec(mode=mode, target=target, path=Path(path), arm=arm)


def _hint_state(
    ledger: EnergyLedger, actor: str, role: str, *, mode: str
) -> tuple[bool, str | None]:
    """Offline reimplementation of run.py's live hint predicate + selector:
    ``(fired, easy_verb)``. Fires iff any productive verb is unaffordable
    (``_compute_energy_hint``); the named "comes easily" verb is the perceived
    selector for ``adv``/``bal`` and the legacy productive selector otherwise
    (``_select_easy_verb``). Takes ``mode`` explicitly — run.py reads the
    process-global ``config.ECONOMY_MODE``, which cannot audit three arms in
    one process. ``easy_verb`` is ``None`` when nothing productive is
    affordable (live shows the "rest before ..." message)."""
    if mode not in _SUBSTITUTE_MODES:
        return (False, None)
    unaffordable = any(not ledger.can_afford(actor, role, v) for v in _HINT_PRODUCTIVE)
    if not unaffordable:
        return (False, None)
    if mode in ("adv", "bal"):
        return (True, ledger.cheapest_affordable_perceived(actor, role))
    return (True, ledger.cheapest_affordable_productive(actor, role))


def _audit_trace_from_episodic(path: Path) -> list[AuditEvent]:
    """Chronological :class:`AuditEvent` stream from a committed run. Sibling
    of :func:`_trace_from_episodic` (kept separate so Stage-0 replay output
    stays byte-stable) that also carries the EXECUTED verb (the ``action``
    column — what live deducted) and the gate9 telemetry flags."""
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT actor, action, payload_json FROM events "
            "WHERE actor NOT IN ('world','harvester','scene') ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()
    out: list[AuditEvent] = []
    for actor, action, payload_json in rows:
        if action not in _VERBS:
            continue
        payload = json.loads(payload_json) if payload_json else {}
        role = str(payload.get("role", ""))
        if not role:
            continue
        chosen = payload.get("parsed_verb") or action
        if chosen not in _VERBS:
            chosen = action
        scene_id = payload.get("scene_id")
        hint_fired = payload.get("energy_hint_fired")
        hint_verb = payload.get("energy_hint_verb")
        out.append(
            AuditEvent(
                actor=actor,
                role=role,
                chosen=chosen,
                executed=action,
                forced=bool(scene_id),
                scene_id=scene_id,
                parse_fallback=bool(payload.get("parse_fallback")),
                economy_substituted=bool(payload.get("economy_substituted")),
                hint_fired_logged=None if hint_fired is None else bool(hint_fired),
                hint_verb_logged=str(hint_verb) if hint_verb is not None else None,
            )
        )
    return out


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def _shares(counts: Counter) -> dict[str, float]:
    total = sum(counts.values())
    return {v: round(n / total, 4) for v, n in sorted(counts.items())} if total else {}


def audit_run(events: Sequence[AuditEvent], *, ledger: EnergyLedger, mode: str) -> dict:
    """Single chronological pass over a run's committed actions.

    Per free (non-scene) turn, BEFORE deducting (matching live, where the hint
    is computed pre-think and the deduct lands post-commit): snapshot the
    actor's energy, evaluate the hint predicate, classify the turn's stratum
    (``hint`` / ``absent_low`` / ``absent_comfortable`` — low means contribute
    is affordable but within one regen of the threshold, the deconfound band),
    and compare the offline executor's predicted substitution against the
    logged ``economy_substituted`` flag (fidelity). Then deduct the LOGGED
    executed verb and regen the whole roster once per live tick, collapsing a
    scene's forced turns into a single regen (same tick model as
    :func:`replay_executor`, PR #55 fidelity fix).

    Conditional chosen-verb streams exclude parse-fallback RESTs (gate9
    parity: a malformed payload is not a free verb choice); the hint-rate
    denominator keeps them (live computed the hint regardless of how the
    think() output parsed)."""

    def _new_acc(role: str) -> dict:
        return {
            "role": role,
            "free": 0,
            "energies": [],
            "contribute_out": 0,
            "hint_fired": 0,
            "easy": Counter(),
            "chosen": {"hint": Counter(), "absent_low": Counter(), "absent_comfortable": Counter()},
            "obey": 0,
            "obey_total": 0,
        }

    acc: dict[str, dict] = {}
    fid = {"hint_on": [0, 0], "hint_off": [0, 0]}  # [events, agreements]
    hint_logged = [0, 0]  # [events with ground truth, full (fired, verb) agreements]
    ev_list = list(events)
    for idx, ev in enumerate(ev_list):
        st = acc.setdefault(ev.actor, _new_acc(ev.role))
        if not ev.forced:
            energy = ledger.current(ev.actor)
            fired, easy = _hint_state(ledger, ev.actor, ev.role, mode=mode)
            # Ground-truth check (Phase 2 item 3): live stamps the hint state on
            # every free commit in substitution modes, parse_fallback included
            # (the hint preceded think()), so this comparison sits OUTSIDE the
            # parse-fallback guard below — unlike the chosen-stream conditionals.
            if ev.hint_fired_logged is not None:
                hint_logged[0] += 1
                if fired == ev.hint_fired_logged and easy == ev.hint_verb_logged:
                    hint_logged[1] += 1
            contribute_cost = ledger.cost(ev.role, "contribute")
            st["free"] += 1
            st["energies"].append(energy)
            if energy < contribute_cost:
                st["contribute_out"] += 1
            if fired:
                st["hint_fired"] += 1
                st["easy"][easy or "none"] += 1
            if not ev.parse_fallback:
                if fired:
                    stratum = "hint"
                elif energy < contribute_cost + ledger.regen_per_tick:
                    stratum = "absent_low"
                else:
                    stratum = "absent_comfortable"
                st["chosen"][stratum][ev.chosen] += 1
                if fired and easy is not None:
                    st["obey_total"] += 1
                    if ev.chosen == easy:
                        st["obey"] += 1
                predicted = ledger.resolve_executed_verb(ev.actor, ev.role, ev.chosen) != ev.chosen
                bucket = fid["hint_on" if fired else "hint_off"]
                bucket[0] += 1
                if predicted == ev.economy_substituted:
                    bucket[1] += 1
        ledger.deduct(ev.actor, ev.role, ev.executed)
        nxt = ev_list[idx + 1] if idx + 1 < len(ev_list) else None
        in_same_scene = (
            ev.forced
            and ev.scene_id is not None
            and nxt is not None
            and nxt.forced
            and nxt.scene_id == ev.scene_id
        )
        if not in_same_scene:
            ledger.regen_all()

    agents: dict[str, dict] = {}
    for actor, st in acc.items():
        samples: list[float] = st["energies"]
        tail = samples[-max(1, len(samples) // 4) :] if samples else []
        absent = st["chosen"]["absent_low"] + st["chosen"]["absent_comfortable"]
        agents[actor] = {
            "role": st["role"],
            "free_turns": st["free"],
            "energy": {
                "min": round(min(samples), 4) if samples else None,
                "mean": round(sum(samples) / len(samples), 4) if samples else None,
                "equilibrium": round(sum(tail) / len(tail), 4) if tail else None,
                "contribute_unaffordable_rate": _rate(st["contribute_out"], st["free"]),
            },
            "hint": {
                "fired": st["hint_fired"],
                "rate": _rate(st["hint_fired"], st["free"]),
                "easy_verbs": dict(sorted(st["easy"].items())),
            },
            "chosen": {k: dict(sorted(c.items())) for k, c in st["chosen"].items()},
            "p_chosen": {
                "hint": _shares(st["chosen"]["hint"]),
                "absent": _shares(absent),
                "absent_low": _shares(st["chosen"]["absent_low"]),
                "absent_comfortable": _shares(st["chosen"]["absent_comfortable"]),
            },
            "obedience_rate": _rate(st["obey"], st["obey_total"]) if st["obey_total"] else None,
        }

    def _fid_block(events_n: int, agree_n: int) -> dict:
        return {
            "events": events_n,
            "agreements": agree_n,
            "rate": round(agree_n / events_n, 4) if events_n else None,
        }

    on_n, on_a = fid["hint_on"]
    off_n, off_a = fid["hint_off"]
    return {
        "events": len(ev_list),
        "mode": mode,
        "agents": agents,
        "fidelity": {
            **_fid_block(on_n + off_n, on_a + off_a),
            "hint_on": _fid_block(on_n, on_a),
            "hint_off": _fid_block(off_n, off_a),
            "hint_logged": _fid_block(hint_logged[0], hint_logged[1]),
        },
    }


def _combined_chosen(report_agent: dict) -> Counter:
    out: Counter = Counter()
    for counts in report_agent["chosen"].values():
        out.update(counts)
    return out


def aggregate_audit(runs: Sequence[dict]) -> dict:
    """Cross-run aggregate of ``audit_run`` reports (each wrapped as
    ``{"arm", "run", "report"}``): per-arm per-agent means, per-agent top-verb
    stability across every run, and — when two or more ``bal@T`` arms are
    present — the decomposition check (is the observed chosen-share delta of
    the agent's modal easy verb consistent with delta-firing-rate times the
    pooled conditional effect?). Conditional probabilities are POOLED (counts
    summed before dividing) across a (arm, agent) group: more stable than a
    mean of per-run ratios when hint turns are scarce."""
    by_arm: dict[str, list[dict]] = {}
    for item in runs:
        by_arm.setdefault(item["arm"], []).append(item)

    def _agent_names(items: Sequence[dict]) -> list[str]:
        names: list[str] = []
        for it in items:
            for name in it["report"]["agents"]:
                if name not in names:
                    names.append(name)
        return names

    arms: dict[str, dict] = {}
    pooled: dict[tuple[str, str], dict[str, Counter]] = {}
    for arm, items in by_arm.items():
        arm_out: dict[str, dict] = {}
        for name in _agent_names(items):
            reports = [
                it["report"]["agents"][name] for it in items if name in it["report"]["agents"]
            ]
            hint_counts: Counter = Counter()
            absent_counts: Counter = Counter()
            combined: Counter = Counter()
            easy: Counter = Counter()
            for rep in reports:
                hint_counts.update(rep["chosen"]["hint"])
                absent_counts.update(rep["chosen"]["absent_low"])
                absent_counts.update(rep["chosen"]["absent_comfortable"])
                combined.update(_combined_chosen(rep))
                easy.update(rep["hint"]["easy_verbs"])
            pooled[(arm, name)] = {
                "hint": hint_counts,
                "absent": absent_counts,
                "combined": combined,
                "easy": easy,
            }
            arm_out[name] = {
                "runs": len(reports),
                "hint_rate_mean": round(sum(r["hint"]["rate"] for r in reports) / len(reports), 4),
                "chosen_share": _shares(combined),
                "p_chosen_hint_pooled": _shares(hint_counts),
                "p_chosen_absent_pooled": _shares(absent_counts),
            }
        arms[arm] = arm_out

    stability: dict[str, dict] = {}
    for name in _agent_names(list(runs)):
        top_by_run: dict[str, str] = {}
        top_shares: list[float] = []
        for item in runs:
            rep = item["report"]["agents"].get(name)
            if rep is None:
                continue
            combined = _combined_chosen(rep)
            total = sum(combined.values())
            if not total:
                continue
            verb, count = combined.most_common(1)[0]
            top_by_run[f"{item['arm']}/{item['run']}"] = verb
            top_shares.append(count / total)
        stability[name] = {
            "top_verb_by_run": top_by_run,
            "stable": len(set(top_by_run.values())) == 1 if top_by_run else False,
            "top_share_spread": round(max(top_shares) - min(top_shares), 4) if top_shares else None,
        }

    decomposition: dict[str, dict] = {}
    bal_targets: list[tuple[float, str]] = []
    for arm in by_arm:
        mode, tsep, traw = arm.partition("@")
        if mode == "bal" and tsep:
            bal_targets.append((float(traw), arm))
    if len(bal_targets) >= 2:
        bal_targets.sort()
        (_, low_arm), (_, high_arm) = bal_targets[0], bal_targets[-1]
        bal_arms = [arm for _, arm in bal_targets]
        for name in _agent_names(list(runs)):
            if name not in arms.get(low_arm, {}) or name not in arms.get(high_arm, {}):
                continue
            hint_counts = Counter()
            absent_counts = Counter()
            easy = Counter()
            for arm in bal_arms:
                grp = pooled.get((arm, name))
                if grp:
                    hint_counts.update(grp["hint"])
                    absent_counts.update(grp["absent"])
                    easy.update(grp["easy"])
            easy.pop("none", None)
            if not easy:
                continue
            verb = easy.most_common(1)[0][0]
            p_hint = _shares(hint_counts).get(verb, 0.0)
            p_absent = _shares(absent_counts).get(verb, 0.0)
            cond_effect = round(p_hint - p_absent, 4)
            d_firing = round(
                arms[high_arm][name]["hint_rate_mean"] - arms[low_arm][name]["hint_rate_mean"], 4
            )
            observed = round(
                arms[high_arm][name]["chosen_share"].get(verb, 0.0)
                - arms[low_arm][name]["chosen_share"].get(verb, 0.0),
                4,
            )
            predicted = round(d_firing * cond_effect, 4)
            decomposition[name] = {
                "verb": verb,
                "arms": [low_arm, high_arm],
                "delta_firing": d_firing,
                "conditional_effect": cond_effect,
                "observed_delta": observed,
                "predicted_delta": predicted,
                "fit_ratio": round(observed / predicted, 4) if predicted else None,
            }

    return {"arms": arms, "stability": stability, "decomposition": decomposition}


def synthetic_run(
    policy: str,
    *,
    n_ticks: int,
    roster: tuple[tuple[str, str], ...],
    cost_table: dict[str, dict[str, float]],
    seed: int,
    max_energy: float = ENERGY_MAX,
    regen_per_tick: float = ENERGY_REGEN_PER_TICK,
) -> dict:
    """Drive the executor with a no-LLM policy to read the mechanical ceiling.

    Policies: ``always-contribute`` (every chosen verb is contribute),
    ``uniform-random`` (uniform over the six verbs), ``role-biased`` (each role
    mostly picks its cheapest specialty). Round-robins the roster one action
    per tick and regenerates the whole roster each tick (matching live).

    ``max_energy``/``regen_per_tick`` default to the config constants but can be
    overridden so the Stage-1 sweep can search for throttling numbers without
    editing ``config`` (the cost table being unthrottled at the default knobs is
    the whole point of the stage)."""
    rng = random.Random(seed)
    ledger = EnergyLedger.fresh(
        [n for n, _ in roster],
        max_energy=max_energy,
        regen_per_tick=regen_per_tick,
        cost_table=cost_table,
    )
    productive = [v for v in _VERBS if v != "rest"]
    chosen: Counter = Counter()
    executed: Counter = Counter()
    subs = 0
    for tick in range(n_ticks):
        actor, role = roster[tick % len(roster)]
        if policy == "always-contribute":
            verb = "contribute"
        elif policy == "uniform-random":
            verb = rng.choice(_VERBS)
        elif policy == "role-biased":
            specialty = min(
                (v for v in productive),
                key=lambda v: cost_table.get(role, {}).get(v, 0.0),
            )
            verb = specialty if rng.random() < 0.8 else rng.choice(productive)
        else:  # pragma: no cover - guarded by argparse choices
            raise ValueError(f"unknown policy {policy}")
        ex = ledger.resolve_executed_verb(actor, role, verb)
        ledger.deduct(actor, role, ex)
        chosen[verb] += 1
        executed[ex] += 1
        if ex != verb:
            subs += 1
        for name, _ in roster:
            ledger.regen(name)
    total = sum(executed.values())
    return {
        "policy": policy,
        "total": total,
        "executed_counts": dict(executed),
        "substitution_rate": round(subs / total, 4) if total else 0.0,
        "executed_contribute_share": round(executed.get("contribute", 0) / total, 4)
        if total
        else 0.0,
        "executed_entropy_norm": round(_entropy_norm(executed), 4),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", help="Stage 0: data dir with episodic.sqlite to replay")
    p.add_argument("--synthetic", action="store_true", help="Stage 1: run synthetic policies")
    p.add_argument(
        "--mode",
        default="1",
        choices=("1", "flat", "sub", "throttle", "adv", "bal"),
        help="economy cost-table mode (role-advantage for all but 'flat'/'bal'); 'adv' is "
        "identical to 'sub' offline (it differs only in the live energy_hint selector), while "
        "'bal' uses the balanced cost table (every role's contribute raised to the dearest)",
    )
    p.add_argument("--ticks", type=int, default=2000, help="Stage 1 tick budget")
    p.add_argument("--seed", type=int, default=42, help="Stage 1 RNG seed")
    p.add_argument(
        "--energy-max",
        type=float,
        default=ENERGY_MAX,
        help="override ENERGY_MAX (tuning sweep; defaults to config)",
    )
    p.add_argument(
        "--regen",
        type=float,
        default=ENERGY_REGEN_PER_TICK,
        help="override ENERGY_REGEN_PER_TICK (tuning sweep; defaults to config)",
    )
    p.add_argument(
        "--bal-contribute",
        type=float,
        default=None,
        help="mode 'bal' only (Stage 6 R2): raise the balanced contribute target "
        "above the natural dearest (22) so the lower-weight scholar drains harder. "
        "A target below 22 is rejected at table build (never silently clamped).",
    )
    p.add_argument(
        "--audit",
        action="append",
        metavar="MODE[@TARGET]=PATH",
        help="mechanism audit (ADR 0012 Phase 2): assign a run dir to its economy "
        "arm, e.g. bal@30=data/econ-stage6-bal30-s42. Repeatable; the report "
        "carries per-run reconstructions plus the cross-run aggregate.",
    )
    args = p.parse_args(argv)
    for spec in args.audit or []:
        try:
            parse_audit_spec(spec)
        except ValueError as exc:
            p.error(str(exc))
    # Fail fast on meaningless knobs: a non-positive pool or negative regen is
    # never a valid economy and would silently yield a degenerate all-rest sweep.
    if not math.isfinite(args.energy_max) or args.energy_max <= 0:
        p.error("--energy-max must be a positive finite number")
    if not math.isfinite(args.regen) or args.regen < 0:
        p.error("--regen must be a finite non-negative number")
    if args.bal_contribute is not None and (
        not math.isfinite(args.bal_contribute) or args.bal_contribute <= 0
    ):
        # nan/inf pass float() and the <= 0 guard yet make every affordability
        # comparison degenerate (contribute reads as permanently unavailable),
        # silently corrupting the sweep (Codex review).
        p.error("--bal-contribute must be a positive finite number")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cost_table = build_cost_table(args.mode, balanced_contribute=args.bal_contribute)
    report: dict = {
        "mode": args.mode,
        "energy_max": args.energy_max,
        "regen": args.regen,
        "bal_contribute": args.bal_contribute,
    }
    if args.mode == "bal":
        # Record the EFFECTIVE dear contribute actually applied — even when it
        # came from the live env (config.ECONOMY_BALANCED_CONTRIBUTE) rather than
        # --bal-contribute. Otherwise a stale exported 30 silently relabels an
        # intended bal@22 replay as bal@30 while metadata reads null (Codex review).
        report["effective_bal_contribute"] = max(
            costs.get("contribute", 0.0) for costs in cost_table.values()
        )

    if args.data:
        ep = Path(args.data) / "episodic.sqlite"
        if not ep.exists():
            print(f"FATAL: {ep} not found", file=sys.stderr)
            return 2
        ledger = EnergyLedger.fresh(
            [n for n, _ in _DEFAULT_ROSTER],
            max_energy=args.energy_max,
            regen_per_tick=args.regen,
            cost_table=cost_table,
        )
        report["stage0_replay"] = replay_executor(_trace_from_episodic(ep), ledger=ledger)

    if args.synthetic:
        report["stage1_synthetic"] = [
            synthetic_run(
                pol,
                n_ticks=args.ticks,
                roster=_DEFAULT_ROSTER,
                cost_table=cost_table,
                seed=args.seed,
                max_energy=args.energy_max,
                regen_per_tick=args.regen,
            )
            for pol in ("always-contribute", "uniform-random", "role-biased")
        ]

    if args.audit:
        audit_runs: list[dict] = []
        for spec_raw in args.audit:
            spec = parse_audit_spec(spec_raw)
            ep = spec.path / "episodic.sqlite"
            if not ep.exists():
                print(f"FATAL: {ep} not found", file=sys.stderr)
                return 2
            ledger = EnergyLedger.fresh(
                [n for n, _ in _DEFAULT_ROSTER],
                max_energy=args.energy_max,
                regen_per_tick=args.regen,
                cost_table=build_cost_table(spec.mode, balanced_contribute=spec.target),
            )
            audit_runs.append(
                {
                    "arm": spec.arm,
                    "run": spec.path.name,
                    "report": audit_run(
                        _audit_trace_from_episodic(ep), ledger=ledger, mode=spec.mode
                    ),
                }
            )
        report["mechanism_audit"] = {
            "runs": audit_runs,
            "aggregate": aggregate_audit(audit_runs),
        }

    if not args.data and not args.synthetic and not args.audit:
        print("nothing to do: pass --data <dir>, --synthetic, and/or --audit", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
