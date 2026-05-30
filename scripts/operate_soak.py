#!/usr/bin/env python3
"""Operator wrapper for long soaks — watches health, never mutates state.

Spawns ``microverse.run`` as a subprocess with the requested duration
(default 7 days). Every 5 minutes appends a JSONL health record to
``<data>/soak_health.jsonl`` covering disk usage, RSS, episodic /
metrics / harvest sizes, and approximate LLM latency derived from
``data/metrics.sqlite``. Pure observer — does not touch live state.

The 7-day rung of plan Phase D is run via:

    nohup uv run python scripts/operate_soak.py \\
        --data data/soak-v1 --harvest harvest/soak-v1 \\
        --duration 7d > soak.log 2>&1 &

The script exits when the underlying run process exits OR when the
duration elapses (whichever first). Operator is responsible for SIGINT
on early termination.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def _parse_duration(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("d"):
        return float(s[:-1]) * 86400.0
    if s.endswith("h"):
        return float(s[:-1]) * 3600.0
    if s.endswith("m"):
        return float(s[:-1]) * 60.0
    return float(s)


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
    return total


def _rss_bytes(pid: int) -> int:
    """Process RSS via ps (portable enough for macOS + Linux)."""
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True)
        return int(out.strip()) * 1024
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return 0


def _snapshot_count(snapshots_dir: Path) -> int:
    if not snapshots_dir.exists():
        return 0
    return sum(1 for _ in snapshots_dir.glob("*.tar.gz"))


def _latest_metric_value(db_path: Path, name: str) -> int | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT MAX(value) FROM metrics WHERE name=?", (name,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _sample_health(data_dir: Path, harvest_dir: Path, pid: int | None) -> dict:
    snapshots_dir = data_dir / "snapshots"
    return {
        "ts": time.time(),
        "data_bytes": _dir_size_bytes(data_dir),
        "harvest_bytes": _dir_size_bytes(harvest_dir),
        "snapshots_count": _snapshot_count(snapshots_dir),
        "episodic_sqlite_bytes": (
            (data_dir / "episodic.sqlite").stat().st_size
            if (data_dir / "episodic.sqlite").exists()
            else 0
        ),
        "metrics_sqlite_bytes": (
            (data_dir / "metrics.sqlite").stat().st_size
            if (data_dir / "metrics.sqlite").exists()
            else 0
        ),
        "child_rss_bytes": _rss_bytes(pid) if pid else 0,
        "free_disk_bytes": shutil.disk_usage(
            data_dir.parent if data_dir.exists() else Path.cwd()
        ).free,
        "thinking_leak_total": _latest_metric_value(data_dir / "metrics.sqlite", "thinking_leak")
        or 0,
        "harvest_flush_fail_total": _latest_metric_value(
            data_dir / "metrics.sqlite", "harvest_flush_fail"
        )
        or 0,
        "snapshot_fail_total": _latest_metric_value(data_dir / "metrics.sqlite", "snapshot_fail")
        or 0,
        "scene_completed_total": _latest_metric_value(
            data_dir / "metrics.sqlite", "scene_completed"
        )
        or 0,
        "scene_aborted_total": _latest_metric_value(data_dir / "metrics.sqlite", "scene_aborted")
        or 0,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--harvest", type=Path, default=Path("harvest"))
    p.add_argument("--duration", type=str, default="7d")
    p.add_argument(
        "--sample-interval-s",
        type=float,
        default=300.0,
        help="Health sample cadence in seconds (default 5 min).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=38,
        help="Forwarded to microverse.run for reproducibility.",
    )
    args = p.parse_args(argv)

    duration_s = _parse_duration(args.duration)
    deadline = time.time() + duration_s
    args.data.mkdir(parents=True, exist_ok=True)
    health_log = args.data / "soak_health.jsonl"

    env = os.environ.copy()
    env["MICROVERSE_DATA"] = str(args.data)
    env["MICROVERSE_HARVEST"] = str(args.harvest)

    # Spawn the runtime. No --ticks → defaults to MAX_TICKS_DEFAULT;
    # we control termination via SIGTERM at the deadline.
    cmd = [
        sys.executable,
        "-m",
        "microverse.run",
        "--seed",
        str(args.seed),
    ]
    sys.stderr.write(f"operate_soak: launching {' '.join(cmd)}\n")
    proc = subprocess.Popen(cmd, env=env)
    sys.stderr.write(
        f"operate_soak: pid={proc.pid} duration_s={duration_s:.0f} health_log={health_log}\n"
    )

    # Forward operator SIGTERM (e.g. systemd / nohup kill) to the child
    # so the runtime gets the same graceful-shutdown signal it would
    # have received directly. SIGINT is handled separately via the
    # KeyboardInterrupt branch (Python translates the signal into the
    # exception for the main thread).
    def _forward_sigterm(_signum: int, _frame: object) -> None:
        sys.stderr.write("operate_soak: SIGTERM received, forwarding to child\n")
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, _forward_sigterm)

    next_sample = time.time()
    exit_code = 0
    try:
        while True:
            now = time.time()
            if proc.poll() is not None:
                sys.stderr.write(f"operate_soak: child exited with code {proc.returncode}\n")
                exit_code = proc.returncode or 0
                break
            if now >= deadline:
                sys.stderr.write("operate_soak: deadline reached, signaling SIGTERM\n")
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    sys.stderr.write("operate_soak: child slow to exit, sending SIGKILL\n")
                    proc.kill()
                    # Reap the SIGKILL'd child so returncode is set;
                    # otherwise it stays None and an "or 0" fallback
                    # would silently report success on forced kill.
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=30)
                exit_code = proc.returncode if proc.returncode is not None else 137
                break
            if now >= next_sample:
                record = _sample_health(args.data, args.harvest, proc.pid)
                with open(health_log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, separators=(",", ":")) + "\n")
                    f.flush()
                next_sample = now + args.sample_interval_s
            time.sleep(1.0)
    except KeyboardInterrupt:
        sys.stderr.write("operate_soak: SIGINT received, forwarding to child\n")
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=30)
        exit_code = proc.returncode if proc.returncode is not None else 130
    finally:
        # Belt-and-suspenders: never leave an orphan child process if
        # the loop exits unexpectedly (uncaught exception, etc.).
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                try:
                    proc.terminate()
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
