#!/usr/bin/env bash
# Phase 2 item 3 (ADR 0012/0013): held-out replication sweep, T fixed at 30.
# Matrix: {bal@22 in-sweep control, bal@30} x fresh seeds {101, 202, 303},
# seed-outer / arm-inner so each seed's pair runs adjacently in time.
# Pre-registration: docs/economy-phase2-replication-runbook.md (locked before launch).
#
# Launch detached from the repo root:
#   nohup ./scripts/run_rep30_sweep.sh > econ-rep30-sweep.log 2>&1 &
set -euo pipefail
cd "$(dirname "$0")/.."

for SEED in 101 202 303; do
  for ARM in bal22 bal30; do
    case "$ARM" in
      bal22) BAL="" ;; # no target -> natural dearest contribute (22)
      bal30) BAL=30 ;;
    esac
    TAG="econ-rep30-${ARM}-s${SEED}"
    if [ -f "data/${TAG}/gate-report.json" ]; then
      echo "=== skip ${TAG}: gate-report.json already present ==="
      continue
    fi
    if [ -d "data/${TAG}" ]; then
      # A data dir without a gate report is a partial (crashed/killed) run.
      # Appending to it would violate the runbook's no-restart fidelity caveat;
      # move it aside and relaunch the sweep instead.
      echo "ERROR ${TAG}: partial run dir exists — move data/${TAG} aside first" >&2
      exit 1
    fi
    echo "=== $(date '+%F %T') start ${TAG} ==="
    # Truly omit MICROVERSE_BAL_CONTRIBUTE for the control arm rather than
    # exporting "" (the parser treats both as unset, but the env should state
    # the intent exactly — Gemini/CodeRabbit review).
    ENV_ARGS=(
      MICROVERSE_ECONOMY=bal
      MICROVERSE_DATA="data/${TAG}"
      MICROVERSE_HARVEST="harvest/${TAG}"
    )
    if [ -n "$BAL" ]; then
      ENV_ARGS+=(MICROVERSE_BAL_CONTRIBUTE="$BAL")
    fi
    env "${ENV_ARGS[@]}" \
      uv run python -m microverse.run --ticks 3000 --tempo 0 --seed "$SEED"
    # Atomic gate-report write: a crash mid-measure must not leave a partial
    # file that the skip guard above would mistake for a completed run.
    uv run python scripts/spike_workshop_measure.py \
      --data "data/${TAG}" --harvest "harvest/${TAG}" \
      > "data/${TAG}/gate-report.json.tmp"
    mv "data/${TAG}/gate-report.json.tmp" "data/${TAG}/gate-report.json"
    echo "=== $(date '+%F %T') done ${TAG} ==="
  done
done
echo "=== $(date '+%F %T') sweep complete ==="
