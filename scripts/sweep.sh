#!/usr/bin/env bash
# Run every working congestion control policy N times on one workload and
# topology, then report median and spread.
#
#   ./scripts/sweep.sh <traffic-matrix> [topo] [load-label] [reps]
#
# Repetition i runs with -seed i (SEED=i in run.sh). Every binary is
# deterministic for a given seed (HPCC's srand(time(NULL)) was removed), so
# repeating one seed reproduces the same answer bit for bit and tells you
# nothing. What the seed changes is the ECMP hash salts and path ids, i.e.
# which flows collide on a shared link. For a handful of large flows that
# placement is the dominant source of spread: on kv_llama70b_4k_tp8.cm the
# slowest shard sits at 1x/2x/3x/4x floor depending on the seed (see
# HTSIM/OUR_CHANGES.md, "Runs were not deterministic", and
# results/reference/reference_table.md). Never report one seed. The same seed
# gives the same placement in every ECMP policy, so per-seed comparisons
# across policies are paired. Default 5 reps.
#
# STRAT / PATHS / HTSIM_BUILD / END / EXTRA_ARGS / HPCC_T / ECN_* in the
# environment are passed through to run.sh. On the 8-flow matrices a rep takes
# 1-30 s per policy; DCQCN, which used to take ~70 s, now takes 2-5 s.
set -euo pipefail
TM=${1:?path to .cm traffic matrix}
TOPO=${2:-default}
LOAD=${3:-load100}
REPS=${4:-5}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STRAT="${STRAT:-ecmp_host}"
STAG=""
if [ "$STRAT" != "ecmp_host" ]; then S="${STRAT// /}"; STAG="_${S#ecmp_}"; fi
RAW="$HERE/results/sweep_${TOPO}_${LOAD}${STAG}.raw"
mkdir -p "$HERE/results"; : > "$RAW"

# which policies can run on this topology
case "$TOPO" in
  default)             POLICIES="eqds hpcc roce dcqcn" ;;
  *)                   POLICIES="eqds hpcc roce dcqcn" ;;   # eqds oversub abort fixed, see HTSIM/OUR_CHANGES.md
esac

echo "sweep: $(basename "$TM")  topo=$TOPO  strat=$STRAT  reps=$REPS (seeds 1..$REPS)"
echo "policies: $POLICIES   (every policy takes seconds on 8-flow matrices; -dcqcn_legacy takes minutes)"
echo

GBPS=400
for CC in $POLICIES; do
  for i in $(seq 1 "$REPS"); do
    OUT=$(SEED=$i "$HERE/scripts/run.sh" "$CC" "$TOPO" "${LOAD}_r$i" "$TM" 2>&1) || { echo "$CC rep $i FAILED"; continue; }
    echo "$OUT" | sed 's/^/  /'
    G=$(echo "$OUT" | sed -n 's/.*(link \([0-9.]*\) Gbps).*/\1/p' | head -1)
    [ -n "$G" ] && GBPS=$G
    V=$(echo "$OUT" | grep -o "last shard [0-9.]*" | awk '{print $3}')
    [ -n "$V" ] && echo "$CC $V" >> "$RAW"
  done
done

echo
python3 "$HERE/scripts/summarize.py" "$RAW" "$TM" "$GBPS"
