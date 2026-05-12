#!/usr/bin/env bash
# Phase 0 — workshop-view measurement spike.
#
# Run one arm at a time. Each arm is a 4-6h soak with seed 38 and a
# fresh data dir. The persona prompt receives a hand-crafted
# ``workshop_view`` block via ``MICROVERSE_SPIKE_WORKSHOP_VIEW`` (read
# once at import time in ``src/microverse/prompts/__init__.py``).
#
# Halt criterion (decide BEFORE Phase 1 / ADR 0003 lands): across arms
# B, C, D, if Aki craft-share does NOT drop below 75% in at least one
# arm AND artifact median length does NOT rise above 25 words in at
# least one arm, the workshop mechanism claim is falsified — STOP and
# replan (model-swap or persona-only routes from ADR 0002).
#
# Usage:
#     ./scripts/spike_workshop_arms.sh A         # baseline (no workshop block)
#     ./scripts/spike_workshop_arms.sh B
#     ./scripts/spike_workshop_arms.sh C
#     ./scripts/spike_workshop_arms.sh D
#     ./scripts/spike_workshop_arms.sh E
#
# Each arm writes to data/soak-spike-<ARM>/ and harvest/soak-spike-<ARM>/.
set -euo pipefail

ARM="${1:-}"
if [[ -z "${ARM}" ]]; then
  echo "usage: $0 {A|B|C|D|E}" >&2
  exit 64
fi

DURATION_HOURS="${SPIKE_HOURS:-5}"
TICKS="${SPIKE_TICKS:-0}"  # 0 = unbounded; halt by signal at $DURATION_HOURS

case "${ARM}" in
  A)
    export MICROVERSE_SPIKE_WORKSHOP_VIEW=""
    NOTE="A. Baseline — no workshop block; reproduces v0.1.1."
    ;;
  B)
    export MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: three-quarters of a tapestry, rough warp, blue stitching, a half-finished bird.'
    NOTE="B. Neutral WIP — static external object, no attributed contributor."
    ;;
  C)
    export MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: three-quarters of a tapestry. Bo has been adding the bird on the upper third.'
    NOTE="C. Peer-only WIP — same object, attributed to a peer."
    ;;
  D)
    export MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: three-quarters of a tapestry. [an earlier contributor] wove the rough warp; Bo has been adding the bird.'
    NOTE="D. Self-redacted WIP — your own past contributions appear as anonymous markers (simulates Phase 4 redaction)."
    ;;
  E)
    export MICROVERSE_SPIKE_WORKSHOP_VIEW=$'- The village loom: a richly crafted weaving with carved wooden beads, painted threads, sketched bird motifs and hand-shaped clay finials. Bo has been adding the bird, carved the beads, painted the threads, sketched the motifs.'
    NOTE="E. Craft-heavy WIP (inversion control) — craft vocabulary saturated, peer-attributed."
    ;;
  *)
    echo "unknown arm: ${ARM}" >&2
    exit 64
    ;;
esac

DATA_DIR="data/soak-spike-${ARM}"
HARVEST_DIR="harvest/soak-spike-${ARM}"
LOG="soak-spike-${ARM}.log"
PIDFILE="soak-spike-${ARM}.pid"

mkdir -p "${DATA_DIR}" "${HARVEST_DIR}"

echo "=== Arm ${ARM} ==="
echo "${NOTE}"
echo "MICROVERSE_SPIKE_WORKSHOP_VIEW: ${MICROVERSE_SPIKE_WORKSHOP_VIEW:-(empty)}"
echo "data dir: ${DATA_DIR}  harvest dir: ${HARVEST_DIR}  log: ${LOG}"
echo

TICKS_ARG=()
if [[ "${TICKS}" != "0" ]]; then
  TICKS_ARG=(--ticks "${TICKS}")
fi

# nohup so the soak survives shell disconnect. Track PID so the operator
# can kill it manually after $DURATION_HOURS, or set a cron.
MICROVERSE_DATA="${DATA_DIR}" \
MICROVERSE_HARVEST="${HARVEST_DIR}" \
nohup uv run python -m microverse.run --seed 38 --tempo 0 "${TICKS_ARG[@]}" \
  >"${LOG}" 2>&1 &
echo $! >"${PIDFILE}"

echo "started: pid $(cat "${PIDFILE}")"
echo "stop with: kill \$(cat ${PIDFILE})"
echo "or schedule auto-stop: (sleep $((DURATION_HOURS * 3600)) && kill \$(cat ${PIDFILE})) &"
