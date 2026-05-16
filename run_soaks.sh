#!/usr/bin/env bash
# Sequenced launcher for the v0.2 operator soaks. Runs all 5 Phase 0
# arms on the spike branch (5h each, sequentially) plus the Phase 0
# measurement, then switches to main and runs the 24h acceptance soak
# against the merged v0.2 code. Total wall time ~49h.
#
# Designed to be nohup'd:
#     nohup ./run_soaks.sh >run_soaks.log 2>&1 &
#     echo $! >run_soaks.pid
#
# Safe to re-run if interrupted: each step skips its work if the
# completion marker for that step is already present under
# soak-status/.
set -euo pipefail
cd "$(dirname "$0")"

ARM_HOURS="${ARM_HOURS:-5}"
ACCEPTANCE_HOURS="${ACCEPTANCE_HOURS:-24}"
STATUS_DIR="soak-status"
mkdir -p "${STATUS_DIR}"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*"; }

run_arm() {
  local ARM="$1"
  local MARKER="${STATUS_DIR}/spike-${ARM}.done"
  if [[ -f "${MARKER}" ]]; then
    log "Phase 0 arm ${ARM} already complete (${MARKER}); skipping."
    return 0
  fi

  case "${ARM}" in
    A) MICROVERSE_SPIKE_WORKSHOP_VIEW="" ;;
    B) MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: three-quarters of a tapestry, rough warp, blue stitching, a half-finished bird.' ;;
    C) MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: three-quarters of a tapestry. Bo has been adding the bird on the upper third.' ;;
    D) MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: three-quarters of a tapestry. [an earlier contributor] wove the rough warp; Bo has been adding the bird.' ;;
    E) MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: a richly crafted weaving with carved wooden beads, painted threads, sketched bird motifs and hand-shaped clay finials. Bo has been adding the bird, carved the beads, painted the threads, sketched the motifs.' ;;
    *) log "FATAL: unknown arm ${ARM}"; exit 64 ;;
  esac
  export MICROVERSE_SPIKE_WORKSHOP_VIEW

  local DATA_DIR="data/soak-spike-${ARM}"
  local HARVEST_DIR="harvest/soak-spike-${ARM}"
  local LOG="soak-spike-${ARM}.log"
  mkdir -p "${DATA_DIR}" "${HARVEST_DIR}"

  log "Phase 0 arm ${ARM} starting (duration ${ARM_HOURS}h, view=${MICROVERSE_SPIKE_WORKSHOP_VIEW:0:60}...)"
  MICROVERSE_DATA="${DATA_DIR}" \
  MICROVERSE_HARVEST="${HARVEST_DIR}" \
  /usr/bin/env -- timeout $((ARM_HOURS * 3600)) \
    uv run python -m microverse.run --seed 38 --tempo 0 \
    >"${LOG}" 2>&1 || true
  log "Phase 0 arm ${ARM} finished."
  touch "${MARKER}"
}

# ----- Phase 0 -----------------------------------------------------
if [[ ! -f "${STATUS_DIR}/phase0.done" ]]; then
  log "Switching to spike/workshop-view-measurement for Phase 0."
  git fetch origin spike/workshop-view-measurement
  git checkout spike/workshop-view-measurement
  uv sync >/dev/null 2>&1

  for ARM in A B C D E; do
    run_arm "${ARM}"
  done

  log "Phase 0 arms complete; running measurement."
  uv run python scripts/spike_workshop_measure.py \
    2>&1 | tee soak-spike-measurement.log || true
  touch "${STATUS_DIR}/phase0.done"
else
  log "Phase 0 already complete; skipping."
fi

# ----- 24h acceptance soak -----------------------------------------
if [[ ! -f "${STATUS_DIR}/acceptance.done" ]]; then
  log "Switching to main for v0.2 acceptance soak."
  git checkout main
  git pull --ff-only origin main >/dev/null 2>&1 || true
  uv sync >/dev/null 2>&1

  DATA_DIR="data/soak-24h-pr-v02"
  HARVEST_DIR="harvest/soak-24h-pr-v02"
  LOG="soak-24h-v02.log"
  mkdir -p "${DATA_DIR}" "${HARVEST_DIR}"

  log "Acceptance soak starting (duration ${ACCEPTANCE_HOURS}h)."
  MICROVERSE_DATA="${DATA_DIR}" \
  MICROVERSE_HARVEST="${HARVEST_DIR}" \
  /usr/bin/env -- timeout $((ACCEPTANCE_HOURS * 3600)) \
    uv run python -m microverse.run --seed 38 --tempo 0 \
    >"${LOG}" 2>&1 || true
  log "Acceptance soak finished."
  touch "${STATUS_DIR}/acceptance.done"
fi

log "All soaks complete."
