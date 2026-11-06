#!/usr/bin/env bash
# Run one simulation and save a named result.
#
#   ./scripts/run.sh <cc> <topo> <load-label> <traffic-matrix>
#
#   <cc>    dcqcn | hpcc | eqds | roce
#   <topo>  default            16-node auto-generated fat tree
#           ft128_1os          128-node 3-tier fat tree, 1:1
#           ft128_4os          128-node 3-tier fat tree, 4:1 oversubscribed
#           ft128_8os          128-node 3-tier fat tree, 8:1 oversubscribed
#           <name>             any file in HTSIM/.../topologies as <name>.topo
#
# Example:
#   ./scripts/run.sh roce ft128_4os load100 workloads/kv128_conv.cm
#
# Writes results/<cc>_<topo>_<load>.{log,csv}
set -euo pipefail

CC=${1:?congestion control: dcqcn | hpcc | eqds | roce}
TOPO=${2:-default}
LOAD=${3:-load100}
TM=${4:?path to .cm traffic matrix}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${HTSIM_BUILD:-build}"
BIN="$HERE/HTSIM/htsim/sim/$BUILD_DIR/datacenter"
TOPODIR="$HERE/HTSIM/htsim/sim/datacenter/topologies"
TM_ABS="$(cd "$(dirname "$TM")" && pwd)/$(basename "$TM")"
mkdir -p "$HERE/results"
TAG="${CC}_${TOPO}_${LOAD}"
LOG="$HERE/results/${TAG}.log"
CSV="$HERE/results/${TAG}.csv"

# --- topology -----------------------------------------------------------
# Buffer size is per-topology: 8:1 needs 2000 packets or PFC fails and the
# run produces hundreds of thousands of lossless violations.
case "$TOPO" in
  default)    TOPOARG="";                                    NODES=16;  Q=500;  GBPS=400 ;;
  ft128_1os)  TOPOARG="-topo $TOPODIR/fat_tree_128_1os.topo"; NODES=128; Q=500;  GBPS=0   ;;
  ft128_4os)  TOPOARG="-topo $TOPODIR/fat_tree_128_4os.topo"; NODES=128; Q=500;  GBPS=0   ;;
  ft128_8os)  TOPOARG="-topo $TOPODIR/fat_tree_128_8os.topo"; NODES=128; Q=2000; GBPS=0   ;;
  *)
    F="$TOPODIR/${TOPO}.topo"
    [ -f "$F" ] || { echo "no such topology: $F"; exit 1; }
    TOPOARG="-topo $F"
    NODES=$(awk 'tolower($1)=="nodes"{print $2; exit}' "$F")
    Q=2000
    GBPS=0
    ;;
esac

# A topology file sets its own link speeds and overrides -linkspeed. Read the
# host-facing (tier 0) downlink speed out of the file so the theoretical floor
# is computed against the real bottleneck, not the flag we passed.
if [ "$GBPS" = "0" ]; then
  TOPOF=$(echo "$TOPOARG" | awk '{print $2}')
  GBPS=$(awk 'tolower($1)=="downlink_speed_gbps"{print $2; exit}' "$TOPOF")
  : "${GBPS:=400}"
fi

# node count must match the traffic matrix or htsim refuses
TM_NODES=$(awk 'tolower($1)=="nodes"{print $2; exit}' "$TM_ABS")
if [ -n "$TM_NODES" ] && [ "$TM_NODES" != "$NODES" ]; then
  echo "node mismatch: topology $TOPO has $NODES, matrix has $TM_NODES"; exit 1
fi

COMMON="-nodes $NODES $TOPOARG -strat ecmp_host -paths 16 -linkspeed 400000 -end 2000000 -q $Q"

case "$CC" in
  dcqcn) EXE="$BIN/htsim_roce"; ARGS="-dcqcn 100 -pfc_thresholds 80 150" ;;
  roce)  EXE="$BIN/htsim_roce"; ARGS="-pfc_thresholds 80 150" ;;
  hpcc)  EXE="$BIN/htsim_hpcc"; ARGS=""
         [ "$TOPO" != "default" ] && { echo "htsim_hpcc does not accept -topo"; exit 1; } ;;
  eqds)  EXE="$BIN/htsim_eqds"; ARGS=""
         case "$TOPO" in ft128_4os|ft128_8os)
           echo "eqds aborts on oversubscribed fabrics, see HTSIM/OUR_CHANGES.md"; exit 1 ;;
         esac ;;
  *) echo "unknown cc: $CC"; exit 1 ;;
esac

[ -x "$EXE" ] || { echo "missing binary $EXE - run ./setup.sh first"; exit 1; }

cd "$HERE/HTSIM/htsim/sim/datacenter"
echo "running $TAG ... (link ${GBPS} Gbps)"
START=$(date +%s)
$EXE $COMMON $ARGS -tm "$TM_ABS" > "$LOG" 2>&1 || true
echo "  wall $(( $(date +%s)-START ))s"
python3 "$HERE/scripts/parse.py" "$LOG" "$CSV" "$TAG" "$TM_ABS" "$GBPS"
