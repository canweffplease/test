#!/usr/bin/env bash
# Run one simulation and save a named result.
#
#   ./scripts/run.sh <cc> <topo-label> <load-label> <traffic-matrix>
#
# Example:
#   ./scripts/run.sh hpcc default load100 workloads/kv_llama70b_4k_tp8.cm
#
# Writes results/<cc>_<topo>_<load>.{log,csv}
set -euo pipefail

CC=${1:?congestion control: dcqcn | hpcc | eqds | roce}
TOPO=${2:-default}
LOAD=${3:-load100}
TM=${4:?path to .cm traffic matrix}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HERE/HTSIM/htsim/sim/build/datacenter"
TM_ABS="$(cd "$(dirname "$TM")" && pwd)/$(basename "$TM")"
mkdir -p "$HERE/results"
TAG="${CC}_${TOPO}_${LOAD}"
LOG="$HERE/results/${TAG}.log"
CSV="$HERE/results/${TAG}.csv"

# held identical across policies so comparisons are fair
COMMON="-nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -end 200000 -q 500"

case "$CC" in
  dcqcn) EXE="$BIN/htsim_roce"; ARGS="-dcqcn 100 -pfc_thresholds 80 150" ;;
  roce)  EXE="$BIN/htsim_roce"; ARGS="-pfc_thresholds 80 150" ;;
  hpcc)  EXE="$BIN/htsim_hpcc"; ARGS="" ;;
  eqds)  EXE="$BIN/htsim_eqds"; ARGS="" ;;
  *) echo "unknown cc: $CC (use dcqcn|hpcc|eqds|roce)"; exit 1 ;;
esac

[ -x "$EXE" ] || { echo "missing binary $EXE - run ./setup.sh first"; exit 1; }

cd "$HERE/HTSIM/htsim/sim/datacenter"
echo "running $TAG ..."
START=$(date +%s)
$EXE $COMMON $ARGS -tm "$TM_ABS" > "$LOG" 2>&1 || true
echo "  wall $(( $(date +%s)-START ))s"
python3 "$HERE/scripts/parse.py" "$LOG" "$CSV" "$TAG"
