#!/usr/bin/env bash
# Run every congestion control policy N times on one workload and report
# the median slowest-shard time, plus the spread across repetitions.
#
#   ./scripts/sweep.sh <traffic-matrix> [topo-label] [load-label] [reps]
#
# Repetitions matter: HPCC is not deterministic across identical runs, so a
# single run can read anywhere from 2.0x to 3.2x floor. Default 5 reps.
set -euo pipefail
TM=${1:?path to .cm traffic matrix}
TOPO=${2:-default}
LOAD=${3:-load100}
REPS=${4:-5}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${HTSIM_BUILD:-build}"
BIN="$HERE/HTSIM/htsim/sim/$BUILD_DIR/datacenter"
TM_ABS="$(cd "$(dirname "$TM")" && pwd)/$(basename "$TM")"
mkdir -p "$HERE/results"
RAW="$HERE/results/sweep_${TOPO}_${LOAD}.raw"
: > "$RAW"

COMMON="-nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -end 200000 -q 500"

echo "sweep on $(basename "$TM")  topo=$TOPO  load=$LOAD  reps=$REPS"
echo "note: dcqcn is ~70s per rep, the others are ~1-25s"
echo

cd "$HERE/HTSIM/htsim/sim/datacenter"
for CC in eqds hpcc roce dcqcn; do
  case "$CC" in
    dcqcn) EXE="$BIN/htsim_roce"; ARGS="-dcqcn 100 -pfc_thresholds 80 150" ;;
    roce)  EXE="$BIN/htsim_roce"; ARGS="-pfc_thresholds 80 150" ;;
    hpcc)  EXE="$BIN/htsim_hpcc"; ARGS="" ;;
    eqds)  EXE="$BIN/htsim_eqds"; ARGS="" ;;
  esac
  [ -x "$EXE" ] || { echo "missing $EXE, run ./setup.sh"; exit 1; }
  printf "%-6s" "$CC"
  for i in $(seq 1 "$REPS"); do
    OUT=$($EXE $COMMON $ARGS -tm "$TM_ABS" 2>/dev/null || true)
    if echo "$OUT" | grep -q "LOSSLESS not working"; then
      echo " REP $i INVALID (lossless violations)"; continue
    fi
    V=$(echo "$OUT" | grep -o "finished at [0-9.]*" | awk '{print $3}' | sort -g | tail -1)
    [ -n "$V" ] && { printf " %s" "$V"; echo "$CC $V" >> "$RAW"; } || printf " FAIL"
  done
  echo
done

echo
python3 "$HERE/scripts/summarize.py" "$RAW" "$TM_ABS"
