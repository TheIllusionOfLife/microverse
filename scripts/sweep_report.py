"""Aggregate an action-economy sweep into one Gate-9 verdict (Phase-2 item 5).

Across ADRs 0014-0018 a "sweep" (3 seeds of a lever) was read into a verdict by
hand: per seed dir, run ``spike_workshop_measure.py`` (Layer 1) and
``replay_economy.py --audit`` (instrument fidelity), eyeball Layer 2 role
stability, then aggregate to the >=2/3-seeds rule. This tool does that read in
one shot so verdicts are reproducible and traceable, not retyped per ADR.

It IMPLEMENTS the locked pre-registration rules (it does not change them): the
floors below are named constants citing their runbooks and are echoed into the
emitted ``sweep-verdict.json`` so every ADR artifact records the criteria it was
judged against. Changing a floor is a reviewed code change, never a CLI flag.

Usage:
    uv run python scripts/sweep_report.py \
        data/econ-r3bal42-s101 data/econ-r3bal42-s202 data/econ-r3bal42-s303 \
        --roster artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70 \
        --audit-mode bal@42 [--json-out sweep-verdict.json]

Each dir must hold a ``gate-report.json`` produced by the current
``spike_workshop_measure.py`` (it must carry ``per_agent_verb_share``; regenerate
old reports) and an ``episodic.sqlite`` for the fidelity audit.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# --- locked criteria (single source of truth; see the cited runbooks) -------
MEAN_PAIRWISE_FLOOR = 0.25  # Layer 1, ADR 0016 size-invariant divergence
CROSS_BLEED_FLOOR = 0.05  # Layer 2 role-stability cross-bleed ceiling
# The ADR 0018 findings adjudicated cross-bleed at 3-dp precision (s202 Aki study
# 0.050 judged at-floor PASS; the true 4-dp share is 0.0504). Round to that same
# precision before the <= compare so the tool reproduces the published verdict
# rather than silently forking it at the knife-edge. See findings doc Layer 2.
CROSS_BLEED_DECIMALS = 3
FIDELITY_FLOOR = 0.90  # instrument gate, ADR 0014
MIN_FIDELITY_EVENTS = 10  # >=10 events/subset (ADR 0014/0018 runbooks)
MIN_SEEDS_PASS = 2  # sweep verdict = >=2/3 seeds
# Canonical role->specialty across the whole arc (ADRs 0013-0018). Fixed by
# design: this is the differentiation the gate measures, not a tunable.
ROLE_SPECIALTY = {"artisan": "craft", "scholar": "study", "stranger": "travel"}
CONTRIBUTE = "contribute"


# --- roster parsing ---------------------------------------------------------
def parse_roster(spec: str) -> dict[str, str]:
    """``name -> role`` from a ``role:name:tokens`` MICROVERSE_ROSTER spec.

    Fail-fast (no silent fallback) on a malformed entry, an unknown role with no
    defined specialty, or a duplicate name — mirrors ``run.py::_parse_roster_spec``
    so the report cannot judge an unintended roster.
    """
    role_by_name: dict[str, str] = {}
    for pos, raw in enumerate([e.strip() for e in spec.split(",")], start=1):
        if not raw:
            raise ValueError(f"empty roster entry #{pos} in roster spec {spec!r}")
        parts = [p.strip() for p in raw.split(":")]
        if len(parts) != 3:
            raise ValueError(f"roster entry {raw!r} must be 'role:name:tokens'")
        role, name, _tokens = parts
        if role not in ROLE_SPECIALTY:
            raise ValueError(
                f"role {role!r} in entry {raw!r} has no defined specialty; "
                f"known: {sorted(ROLE_SPECIALTY)}"
            )
        if not name:
            raise ValueError(f"empty resident name in roster entry {raw!r}")
        if name in role_by_name:
            raise ValueError(f"duplicate resident name {name!r} in roster spec {spec!r}")
        role_by_name[name] = role
    if not role_by_name:
        raise ValueError(f"empty roster spec {spec!r}")
    return role_by_name


# --- Layer 2: role stability ------------------------------------------------
def _top_non_contribute(share: dict[str, float]) -> str | None:
    candidates = {v: s for v, s in share.items() if v != CONTRIBUTE}
    if not candidates or max(candidates.values()) <= 0:
        return None
    return max(candidates, key=lambda v: candidates[v])


def role_stability(
    per_agent_verb_share: dict[str, dict[str, float]],
    role_by_name: dict[str, str],
    *,
    cross_bleed_floor: float = CROSS_BLEED_FLOOR,
) -> dict[str, Any]:
    """Layer 2: every roster-named resident's top non-``contribute`` verb is its
    specialty AND its cross-bleed is <= ``cross_bleed_floor``. Agents present in
    the report but not the roster (e.g. Watchdog rehab Strangers) are reported as
    ``extra_agents``, never gated.

    Cross-bleed is the agent's share of the SINGLE binding other-resident
    specialty verb (the max over other specialties), matching the ADR 0018
    findings operationalization ("Aki ``study`` cross-bleed" = the largest single
    other-specialty share, not the sum across all of them). The binding verb is
    reported as ``cross_bleed_verb``.
    """
    per_resident: dict[str, Any] = {}
    for name, role in role_by_name.items():
        specialty = ROLE_SPECIALTY[role]
        share = per_agent_verb_share.get(name)
        if share is None:
            per_resident[name] = {
                "role": role,
                "specialty": specialty,
                "present": False,
                "top_non_contribute_verb": None,
                "is_specialty": False,
                "cross_bleed": None,
                "cross_bleed_verb": None,
                "pass": False,
            }
            continue
        # An agent's OWN specialty is never cross-bleed even if a same-role peer
        # shares it.
        other_specialties = {ROLE_SPECIALTY[r] for n, r in role_by_name.items() if n != name} - {
            specialty
        }
        top = _top_non_contribute(share)
        bleed_shares = {v: share.get(v, 0.0) for v in other_specialties}
        bleed_verb = max(bleed_shares, key=lambda v: bleed_shares[v]) if bleed_shares else None
        cross_bleed = round(bleed_shares[bleed_verb], 4) if bleed_verb is not None else 0.0
        is_specialty = top == specialty
        per_resident[name] = {
            "role": role,
            "specialty": specialty,
            "present": True,
            "top_non_contribute_verb": top,
            "is_specialty": is_specialty,
            "cross_bleed": cross_bleed,
            "cross_bleed_verb": bleed_verb,
            "pass": is_specialty and round(cross_bleed, CROSS_BLEED_DECIMALS) <= cross_bleed_floor,
        }
    extra = sorted(set(per_agent_verb_share) - set(role_by_name))
    return {
        "per_resident": per_resident,
        "extra_agents": extra,
        "cross_bleed_floor": cross_bleed_floor,
        "pass": bool(per_resident) and all(r["pass"] for r in per_resident.values()),
    }


# --- instrument gate: fidelity ----------------------------------------------
def fidelity_verdict(
    fidelity: dict[str, Any],
    *,
    floor: float = FIDELITY_FLOOR,
    min_events: int = MIN_FIDELITY_EVENTS,
) -> dict[str, Any]:
    """Instrument gate from ``replay_economy.audit_run``'s ``fidelity`` block:
    ``hint_on``/``hint_off``/``hint_logged`` rates all >= ``floor``, and the
    hint-on/off subsets each carry >= ``min_events`` (a thin subset can hit a
    spurious 1.0). ``hint_logged`` is gated on rate only (ground-truth agreement).
    """
    subsets: dict[str, Any] = {}
    ok = True
    for key in ("hint_on", "hint_off", "hint_logged"):
        block = fidelity.get(key, {}) or {}
        rate = block.get("rate")
        events = block.get("events", 0)
        enough = events >= min_events if key in ("hint_on", "hint_off") else True
        passed = rate is not None and rate >= floor and enough
        subsets[key] = {"rate": rate, "events": events, "pass": passed}
        ok = ok and passed
    return {"subsets": subsets, "floor": floor, "min_events": min_events, "pass": ok}


# --- per-seed + sweep verdict ----------------------------------------------
def seed_verdict(
    seed: str,
    gate_report: dict[str, Any],
    fidelity: dict[str, Any],
    role_by_name: dict[str, str],
) -> dict[str, Any]:
    """Combine Layer 1 (``mean_pairwise_jsd``), Layer 2 (role stability), and the
    instrument gate into one seed verdict. A failed instrument gate yields
    ``INSTRUMENT-INVALID`` (no behavioral verdict, per the locked runbooks)."""
    chosen = gate_report["gate_9_verb_diversity"]["chosen"]
    mpj = chosen["mean_pairwise_jsd"]
    layer1 = {
        "mean_pairwise_jsd": mpj,
        "floor": MEAN_PAIRWISE_FLOOR,
        "pass": mpj >= MEAN_PAIRWISE_FLOOR,
    }
    layer2 = role_stability(chosen["per_agent_verb_share"], role_by_name)
    fidelity_v = fidelity_verdict(fidelity)
    instrument_valid = fidelity_v["pass"]
    behavioral_pass = layer1["pass"] and layer2["pass"]
    if not instrument_valid:
        status = "INSTRUMENT-INVALID"
    elif behavioral_pass:
        status = "PASS"
    else:
        status = "FAIL"
    return {
        "seed": seed,
        "layer1": layer1,
        "layer2": layer2,
        "fidelity": fidelity_v,
        "instrument_valid": instrument_valid,
        "status": status,
        "pass": instrument_valid and behavioral_pass,
    }


def sweep_verdict(
    seed_verdicts: list[dict[str, Any]], *, min_pass: int = MIN_SEEDS_PASS
) -> dict[str, Any]:
    n_total = len(seed_verdicts)
    n_pass = sum(1 for s in seed_verdicts if s["pass"])
    n_invalid = sum(1 for s in seed_verdicts if not s["instrument_valid"])
    return {
        "n_pass": n_pass,
        "n_total": n_total,
        "min_pass": min_pass,
        "n_instrument_invalid": n_invalid,
        "pass": n_pass >= min_pass,
    }


# --- fidelity audit (reuse replay_economy) ----------------------------------
def _load_replay_module() -> Any:
    path = Path(__file__).resolve().parent / "replay_economy.py"
    spec = importlib.util.spec_from_file_location("replay_economy", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses in replay_economy need the module registered before exec.
    sys.modules["replay_economy"] = module
    spec.loader.exec_module(module)
    return module


def compute_fidelity(run_dir: Path, audit_mode: str) -> dict[str, Any]:
    """Run ``replay_economy``'s offline audit on ``run_dir/episodic.sqlite`` and
    return its ``fidelity`` block — the same instrument read the runbooks use,
    at the live default energy knobs."""
    rm = _load_replay_module()
    spec = rm.parse_audit_spec(f"{audit_mode}={run_dir}")
    ep = spec.path / "episodic.sqlite"
    if not ep.exists():
        raise FileNotFoundError(f"{ep} not found (needed for the fidelity audit)")
    trace = rm._audit_trace_from_episodic(ep)
    roster_names = list(dict.fromkeys(ev.actor for ev in trace)) or [
        n for n, _ in rm._DEFAULT_ROSTER
    ]
    ledger = rm.EnergyLedger.fresh(
        roster_names,
        max_energy=rm.ENERGY_MAX,
        regen_per_tick=rm.ENERGY_REGEN_PER_TICK,
        cost_table=rm.build_cost_table(spec.mode, balanced_contribute=spec.target),
    )
    report = rm.audit_run(trace, ledger=ledger, mode=spec.mode)
    return report["fidelity"]


def read_seed(run_dir: Path, audit_mode: str, role_by_name: dict[str, str]) -> dict[str, Any]:
    gate_report = json.loads((run_dir / "gate-report.json").read_text())
    chosen = gate_report.get("gate_9_verb_diversity", {}).get("chosen", {})
    if "per_agent_verb_share" not in chosen:
        raise ValueError(
            f"{run_dir}/gate-report.json lacks per_agent_verb_share; regenerate it with the "
            "current spike_workshop_measure.py (--divergence-metric mean_pairwise_jsd)"
        )
    fidelity = compute_fidelity(run_dir, audit_mode)
    return seed_verdict(run_dir.name, gate_report, fidelity, role_by_name)


def build_sweep(run_dirs: list[Path], audit_mode: str, roster_spec: str) -> dict[str, Any]:
    role_by_name = parse_roster(roster_spec)
    seeds = [read_seed(d, audit_mode, role_by_name) for d in run_dirs]
    return {
        "audit_mode": audit_mode,
        "roster": roster_spec,
        "thresholds": {
            "mean_pairwise_floor": MEAN_PAIRWISE_FLOOR,
            "cross_bleed_floor": CROSS_BLEED_FLOOR,
            "cross_bleed_decimals": CROSS_BLEED_DECIMALS,
            "fidelity_floor": FIDELITY_FLOOR,
            "min_fidelity_events": MIN_FIDELITY_EVENTS,
            "min_seeds_pass": MIN_SEEDS_PASS,
        },
        "seeds": seeds,
        "sweep": sweep_verdict(seeds),
    }


# --- rendering --------------------------------------------------------------
def render_table(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Sweep: {report['roster']}  (audit {report['audit_mode']})")
    lines.append(f"{'seed':<22} {'L1 mpjsd':>9} {'L2':>4} {'fidelity':>9} {'status':>18}")
    lines.append("-" * 66)
    for s in report["seeds"]:
        l1 = s["layer1"]
        l1cell = f"{l1['mean_pairwise_jsd']:.4f}{'*' if l1['pass'] else ' '}"
        l2cell = "ok" if s["layer2"]["pass"] else "X"
        fidcell = "ok" if s["fidelity"]["pass"] else "X"
        lines.append(f"{s['seed']:<22} {l1cell:>9} {l2cell:>4} {fidcell:>9} {s['status']:>18}")
    sw = report["sweep"]
    verdict = "PASS" if sw["pass"] else "FAIL"
    lines.append("-" * 66)
    lines.append(
        f"SWEEP {verdict}: {sw['n_pass']}/{sw['n_total']} seeds pass "
        f"(need >={sw['min_pass']}; {sw['n_instrument_invalid']} instrument-invalid)"
    )
    # Per-resident Layer-2 detail (cross-bleed is the usual culprit).
    for s in report["seeds"]:
        for name, r in s["layer2"]["per_resident"].items():
            if not r["pass"]:
                cb = r["cross_bleed"]
                cb_s = "n/a" if cb is None else f"{cb:.4f} ({r['cross_bleed_verb']})"
                lines.append(
                    f"  L2 {s['seed']} {name}: top={r['top_non_contribute_verb']} "
                    f"(want {r['specialty']}), cross_bleed={cb_s}, present={r['present']}"
                )
        if s["layer2"]["extra_agents"]:
            extras = s["layer2"]["extra_agents"]
            lines.append(f"  note {s['seed']}: extra agents (informational): {extras}")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "dirs",
        nargs="+",
        help="sweep seed dirs (each with gate-report.json + episodic.sqlite)",
    )
    p.add_argument(
        "--roster",
        required=True,
        help="MICROVERSE_ROSTER spec, e.g. artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70",
    )
    p.add_argument(
        "--audit-mode",
        required=True,
        help="replay_economy audit MODE[@TARGET], e.g. bal@42 (T=42 lever) or bal@30",
    )
    p.add_argument(
        "--json-out",
        default=None,
        help="write the machine-readable sweep-verdict JSON to this path",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    report = build_sweep([Path(d) for d in args.dirs], args.audit_mode, args.roster)
    print(render_table(report))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"\nwrote {args.json_out}")
    return 0 if report["sweep"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
