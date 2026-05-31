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
matches the live lever exactly. The per-tick whole-roster regen is
approximated as per-actor regen (the trace only records the acting agent),
so these are estimates, not the live measurement — run
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


def replay_executor(
    events: list[tuple[str, str, str]] | list[tuple[str, str, str, bool]],
    *,
    ledger: EnergyLedger,
) -> dict:
    """Run a chronological ``(actor, role, chosen_verb[, forced])`` trace
    through the executor. Per event: resolve (afford-or-substitute) at the
    current pool, deduct the executed verb, then regen the actor (per-actor
    approximation of the live whole-roster per-tick regen). Returns
    chosen/executed counts and the substitution rate.

    ``forced`` (4th element, default False) marks a scene turn (ADR 0006). Live
    scene contributes are NEVER substituted (the lever skips scene turns), so a
    forced event is deducted at its chosen verb without substitution — otherwise
    the offline estimate would predict substitutions that cannot happen live and
    inflate the substitution rate (review)."""
    chosen: Counter = Counter()
    executed: Counter = Counter()
    subs = 0
    for event in events:
        actor, role, verb, *rest = event
        forced = bool(rest[0]) if rest else False
        ex = verb if forced else ledger.resolve_executed_verb(actor, role, verb)
        ledger.deduct(actor, role, ex)
        ledger.regen(actor)
        chosen[verb] += 1
        executed[ex] += 1
        if ex != verb:
            subs += 1
    total = sum(executed.values())
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
    }


def _trace_from_episodic(path: Path) -> list[tuple[str, str, str, bool]]:
    """Chronological ``(actor, role, chosen_verb, forced)`` from a committed
    run. The chosen verb is ``payload.parsed_verb`` when present (an economy-on
    run), else the executed action (an economy-off run — the model's own
    choice). ``forced`` is True for scene turns (``payload.scene_id``), which the
    executor must deduct without substituting (review)."""
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT actor, action, payload_json FROM events "
            "WHERE actor NOT IN ('world','harvester','scene') ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()
    out: list[tuple[str, str, str, bool]] = []
    for actor, action, payload_json in rows:
        if action not in _VERBS:
            continue
        payload = json.loads(payload_json) if payload_json else {}
        role = str(payload.get("role", ""))
        chosen = payload.get("parsed_verb") or action
        forced = bool(payload.get("scene_id"))
        if role and chosen in _VERBS:
            out.append((actor, role, chosen, forced))
    return out


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
        choices=("1", "flat", "sub", "throttle"),
        help="economy cost-table mode (role-advantage for all but 'flat')",
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
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cost_table = build_cost_table(args.mode)
    report: dict = {"mode": args.mode, "energy_max": args.energy_max, "regen": args.regen}

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

    if not args.data and not args.synthetic:
        print("nothing to do: pass --data <dir> and/or --synthetic", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
