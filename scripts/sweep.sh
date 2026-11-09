#!/usr/bin/env bash
# Run every working congestion control policy N times on one workload and
# topology, then report median and spread.
#
#   ./scripts/sweep.sh <traffic-matrix> [topo] [load-label] [reps]
#
# Repetitions matter: HPCC varies ~58% across identical runs, and -seed does
# not fix it. Default 5.
set -euo pipefail
TM=${1:?path to .cm traffic matrix}
TOPO=${2:-default}
LOAD=${3:-load100}
REPS=${4:-5}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$HERE/results/sweep_${TOPO}_${LOAD}.raw"
mkdir -p "$HERE/results"; : > "$RAW"

# which policies can run on this topology
case "$TOPO" in
  default) POLICIES="eqds hpcc roce dcqcn" ;;
  *)       POLICIES="roce dcqcn" ;;   # hpcc has no -topo; eqds aborts on oversub
esac

echo "sweep: $(basename "$TM")  topo=$TOPO  reps=$REPS"
echo "policies: $POLICIES   (dcqcn is ~70s per rep)"
echo

for CC in $POLICIES; do
  for i in $(seq 1 "$REPS"); do
    OUT=$("$HERE/scripts/run.sh" "$CC" "$TOPO" "${LOAD}_r$i" "$TM" 2>&1) || { echo "$CC rep $i FAILED"; continue; }
    echo "$OUT" | sed 's/^/  /'
    V=$(echo "$OUT" | grep -o "last shard [0-9.]*" | awk '{print $3}')
    [ -n "$V" ] && echo "$CC $V" >> "$RAW"
  done
done

echo
python3 "$HERE/scripts/summarize.py" "$RAW" "$TM"
