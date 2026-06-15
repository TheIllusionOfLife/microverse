#!/usr/bin/env bash
# Phase 2 item 4 (ADR 0012/0014): roster-generality sweep, dose T fixed at 30.
# Matrix: two rosters x fresh seeds {101, 202, 303}, roster-outer / seed-inner
# so each roster's three seeds run adjacently in time.
#   R2 (role-swap):  Aki(artisan,100) + Vesna(stranger,70)
#   R3 (all-roles):  Aki(artisan,100) + Cy(scholar,70) + Vesna(stranger,70)
# Pre-registration: docs/economy-phase2-roster-generality-runbook.md (locked before launch).
#
# Launch detached from the repo root:
#   nohup ./scripts/run_roster_gen_sweep.sh > econ-roster-sweep.log 2>&1 &
set -euo pipefail
cd "$(dirname "$0")/.."

R2_SPEC="artisan:Aki:100,stranger:Vesna:70"
R3_SPEC="artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70"

for ROSTER in r2 r3; do
  case "$ROSTER" in
    r2) SPEC="$R2_SPEC" ;;
    r3) SPEC="$R3_SPEC" ;;
  esac
  for SEED in 101 202 303; do
    TAG="econ-roster-${ROSTER}-s${SEED}"
    if [ -f "data/${TAG}/gate-report.json" ]; then
      echo "=== skip ${TAG}: gate-report.json already present ==="
      continue
    fi
    if [ -d "data/${TAG}" ] || [ -d "harvest/${TAG}" ]; then
      # A data/ or harvest/ dir without a gate report is a partial (crashed/
      # killed) run. Reusing it would violate the runbook's no-restart fidelity
      # caveat — and harvest/ is append-only, so a stale manifest would mix old
      # records into the fresh run's gate report. Move both aside and relaunch.
      echo "ERROR ${TAG}: partial run dir exists — move data/${TAG} and harvest/${TAG} aside first" >&2
      exit 1
    fi
    echo "=== $(date '+%F %T') start ${TAG} (roster=${SPEC}) ==="
    env \
      MICROVERSE_ECONOMY=bal \
      MICROVERSE_BAL_CONTRIBUTE=30 \
      MICROVERSE_ROSTER="$SPEC" \
      MICROVERSE_DATA="data/${TAG}" \
      MICROVERSE_HARVEST="harvest/${TAG}" \
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
