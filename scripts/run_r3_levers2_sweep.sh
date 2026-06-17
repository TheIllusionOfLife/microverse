#!/usr/bin/env bash
# ADR 0018 (ADR 0015 Decision 3(a), after the persona lever was rejected in ADR 0017):
# the two remaining R3-generality levers, both on the DEFAULT Stranger persona and
# existing knobs (no new code).
#   bal42 (economic): R3 100/70/70, MICROVERSE_BAL_CONTRIBUTE=42 — make contribute
#                     unaffordable more often so agents are forced to their specialty.
#   wt130 (weights):  R3 with Vesna 70->130, bal@30 — restore dyad-like scheduling
#                     share for the Stranger.
# Pre-registration: docs/economy-phase2-r3-levers2-runbook.md (locked before launch).
#
# Pilot-then-continue: pass one seed first, read it, then the rest.
# Launch detached from the repo root:
#   nohup ./scripts/run_r3_levers2_sweep.sh bal42 101 > econ-r3bal42-s101.log 2>&1 &
#   nohup ./scripts/run_r3_levers2_sweep.sh wt130 101 > econ-r3wt130-s101.log 2>&1 &
# With no seeds it runs all three {101, 202, 303}.
set -euo pipefail
cd "$(dirname "$0")/.."

LEVER="${1:-}"
shift || true
case "$LEVER" in
  bal42)
    TAGBASE="econ-r3bal42"
    BAL=42
    SPEC="artisan:Aki:100,scholar:Cy:70,stranger:Vesna:70"
    ;;
  wt130)
    TAGBASE="econ-r3wt130"
    BAL=30
    SPEC="artisan:Aki:100,scholar:Cy:70,stranger:Vesna:130"
    ;;
  *)
    echo "usage: $0 {bal42|wt130} [seed ...]" >&2
    exit 2
    ;;
esac

SEEDS=("$@")
if [ "${#SEEDS[@]}" -eq 0 ]; then
  SEEDS=(101 202 303)
fi

for SEED in "${SEEDS[@]}"; do
  TAG="${TAGBASE}-s${SEED}"
  if [ -f "data/${TAG}/gate-report.json" ]; then
    echo "=== skip ${TAG}: gate-report.json already present ==="
    continue
  fi
  if [ -d "data/${TAG}" ] || [ -d "harvest/${TAG}" ]; then
    echo "ERROR ${TAG}: partial run dir exists — move data/${TAG} and harvest/${TAG} aside first" >&2
    exit 1
  fi
  echo "=== $(date '+%F %T') start ${TAG} (lever=${LEVER}, roster=${SPEC}, bal=${BAL}, persona=default) ==="
  env \
    MICROVERSE_ECONOMY=bal \
    MICROVERSE_BAL_CONTRIBUTE="$BAL" \
    MICROVERSE_ROSTER="$SPEC" \
    MICROVERSE_DATA="data/${TAG}" \
    MICROVERSE_HARVEST="harvest/${TAG}" \
    uv run python -m microverse.run --ticks 3000 --tempo 0 --seed "$SEED"
  uv run python scripts/spike_workshop_measure.py \
    --data "data/${TAG}" --harvest "harvest/${TAG}" \
    > "data/${TAG}/gate-report.json.tmp"
  mv "data/${TAG}/gate-report.json.tmp" "data/${TAG}/gate-report.json"
  echo "=== $(date '+%F %T') done ${TAG} ==="
done
echo "=== $(date '+%F %T') sweep batch complete ==="
