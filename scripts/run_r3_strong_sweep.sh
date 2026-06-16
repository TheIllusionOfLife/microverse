#!/usr/bin/env bash
# ADR 0017 (ADR 0015 Decision 3(a)): R3 strong-specializer sweep. The R3 (N=3)
# FAIL was pinned on the Stranger's weak travel-obedience (~0.31). This sweep
# re-runs the SAME R3 roster with the stronger-travel Stranger persona
# (MICROVERSE_STRANGER_PERSONA=travel), holding everything else fixed (roster,
# bal@30, energy knobs) so the persona is the only changed variable.
#   R3 (all-roles): Aki(artisan,100) + Cy(scholar,70) + Vesna(stranger,70)
# Pre-registration: docs/economy-phase2-r3-strong-specializer-runbook.md
# (locked before launch).
#
# Pilot-then-continue: pass one seed first, read it, then pass the rest.
# Launch detached from the repo root:
#   nohup ./scripts/run_r3_strong_sweep.sh 101 > econ-r3strong-s101.log 2>&1 &
#   nohup ./scripts/run_r3_strong_sweep.sh 202 303 > econ-r3strong-rest.log 2>&1 &
# With no args it runs all three seeds {101, 202, 303}.
set -euo pipefail
cd "$(dirname "$0")/.."

SPEC="artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70"

SEEDS=("$@")
if [ "${#SEEDS[@]}" -eq 0 ]; then
  SEEDS=(101 202 303)
fi

for SEED in "${SEEDS[@]}"; do
  TAG="econ-r3strong-s${SEED}"
  if [ -f "data/${TAG}/gate-report.json" ]; then
    echo "=== skip ${TAG}: gate-report.json already present ==="
    continue
  fi
  if [ -d "data/${TAG}" ] || [ -d "harvest/${TAG}" ]; then
    # A data/ or harvest/ dir without a gate report is a partial (crashed/killed)
    # run. Reusing it would violate the no-restart fidelity caveat — and harvest/
    # is append-only, so a stale manifest would mix old records into the fresh
    # run's gate report. Move both aside and relaunch.
    echo "ERROR ${TAG}: partial run dir exists — move data/${TAG} and harvest/${TAG} aside first" >&2
    exit 1
  fi
  echo "=== $(date '+%F %T') start ${TAG} (roster=${SPEC}, persona=travel) ==="
  env \
    MICROVERSE_ECONOMY=bal \
    MICROVERSE_BAL_CONTRIBUTE=30 \
    MICROVERSE_STRANGER_PERSONA=travel \
    MICROVERSE_ROSTER="$SPEC" \
    MICROVERSE_DATA="data/${TAG}" \
    MICROVERSE_HARVEST="harvest/${TAG}" \
    uv run python -m microverse.run --ticks 3000 --tempo 0 --seed "$SEED"
  # Atomic gate-report write: a crash mid-measure must not leave a partial file
  # that the skip guard above would mistake for a completed run.
  uv run python scripts/spike_workshop_measure.py \
    --data "data/${TAG}" --harvest "harvest/${TAG}" \
    > "data/${TAG}/gate-report.json.tmp"
  mv "data/${TAG}/gate-report.json.tmp" "data/${TAG}/gate-report.json"
  echo "=== $(date '+%F %T') done ${TAG} ==="
done
echo "=== $(date '+%F %T') sweep batch complete ==="
