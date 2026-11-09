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
# Writes results/<cc>_<topo>_<load>[_<strat>][_p<paths>][_s<seed>].{log,csv}
#
# Optional environment overrides (the tag records any non-default value):
#   STRAT  load balancing, passed to -strat. Default "ecmp_host" (switch ECMP
#          hashing). Others: "ecmp_ar" (switch adaptive routing, flowlet
#          sticky), "ecmp_host_ar <n>" (ECMP, 50% of packets re-steered off
#          the worst port), "ecmp_rr" (per-packet round robin).
#   PATHS  -paths value, default 16. NOTE: htsim_roce/htsim_hpcc ignore it in
#          ECMP_FIB mode (every -strat ecmp_* above); see HTSIM/OUR_CHANGES.md.
#   SEED   -seed value. Default: none passed (binary default 13). The seed
#          fixes the ECMP hash salts and therefore which flows collide.
#   END    simulated end time in microseconds, passed to -end. Default
#          2000000. htsim runs until -end even after the last flow finishes,
#          so short matrices run faster with a smaller value.
#   OUTDIR results directory, default results/
#   HTSIM_BUILD  build directory under HTSIM/htsim/sim, default "build"
#   EXTRA_ARGS   appended verbatim to the binary's arguments, e.g.
#          "-dcqcn_legacy" or "-hpcc_legacy" (checkpoint 1 transports),
#          "-hpcc_N 2", "-dcqcn_rai 400".
#   HPCC_T       HPCC base RTT in us: 13.1 on the 16-node tree (binary default),
#          16.4 on the 128-node trees (set automatically).
#   ECN_KMIN/ECN_KMAX/ECN_PMAX  DCQCN probabilistic marking (KB, KB, 0..1);
#          defaults 1 us of queueing at link rate / 400 / 0.01. ECN_STEP=1
#          disables it and restores the step marking at -dcqcn 100.
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
STRAT="${STRAT:-ecmp_host}"
PATHS="${PATHS:-16}"
SEED="${SEED:-}"
END="${END:-2000000}"
OUTDIR="${OUTDIR:-$HERE/results}"
mkdir -p "$OUTDIR"
TAG="${CC}_${TOPO}_${LOAD}"
if [ "$STRAT" != "ecmp_host" ]; then
  S="${STRAT// /}"; S="${S#ecmp_}"           # ecmp_ar -> ar, "ecmp_host_ar 50" -> host_ar50
  TAG="${TAG}_${S}"
fi
[ "$PATHS" != "16" ] && TAG="${TAG}_p${PATHS}"
[ -n "$SEED" ] && TAG="${TAG}_s${SEED}"
LOG="$OUTDIR/${TAG}.log"
CSV="$OUTDIR/${TAG}.csv"

# --- topology -----------------------------------------------------------
# Buffer size: every 128-node tree uses 2000 packets. 500 is enough for 8 flows
# but under the E2/E3 load matrices RoCE logs 10^4-10^5 lossless violations on
# ToR uplinks at 500 (a 4-host ToR has 8 ingress ports of ~155 packets each).
# Matched buffers across topologies are a stated convention. FT128_ALIAS is
# honored by the experiment scripts, see scripts/explib.sh.
case "$TOPO" in
  default)    TOPOARG="";                                    NODES=16;  Q=500;  GBPS=400 ;;
  ft128_1os)  TOPOARG="-topo $TOPODIR/fat_tree_128_1os.topo"; NODES=128; Q=2000;  GBPS=0   ;;
  ft128_4os)  TOPOARG="-topo $TOPODIR/fat_tree_128_4os.topo"; NODES=128; Q=2000;  GBPS=0   ;;
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

# A topology file sets its own link speeds. Read the host-facing (tier 0)
# downlink speed out of the file so that (a) the theoretical floor is computed
# against the real bottleneck and (b) -linkspeed, which the transports use for
# their initial rate, credit pacing and timeouts, matches the fabric. Passing
# 400000 on a 100 Gbps fabric gave EQDS false timeouts and 1.33x single flows.

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

COMMON="-nodes $NODES $TOPOARG -strat $STRAT -paths $PATHS -linkspeed $((GBPS*1000)) -end $END -q $Q"
[ -n "$SEED" ] && COMMON="$COMMON -seed $SEED"

# HPCC's base RTT T must match the fabric: 13.1 us measured on the 16-node
# tree (the binary default), 16.4 us on the 3-tier 128-node trees. Override
# with HPCC_T. EXTRA_ARGS is appended verbatim for one-off flags.
case "$TOPO" in
  ft128_*) : "${HPCC_T:=16.4}" ;;
  *)       : "${HPCC_T:=}" ;;
esac
HPCC_ARGS=""; [ -n "$HPCC_T" ] && HPCC_ARGS="-hpcc_T $HPCC_T"
EXTRA_ARGS="${EXTRA_ARGS:-}"

# DCQCN marking: the paper's probabilistic (RED) marking between Kmin and
# Kmax with Pmax, on top of the -dcqcn 100 (packets = 400 KB) step threshold.
# Kmin is one microsecond of queueing at the link rate (50 KB at 400 Gbps,
# 12 KB at 100 Gbps), Kmax is 400 KB so it equals the old step threshold and
# sits below the 600 KB PFC pause point, Pmax 1%. See OUR_CHANGES.md (ECN).
# Set ECN_STEP=1 to get the pre-RED step marking back.
: "${ECN_KMIN:=$((GBPS/8))}"; : "${ECN_KMAX:=400}"; : "${ECN_PMAX:=0.01}"
if [ -n "${ECN_STEP:-}" ]; then ECN_ARGS=""; else
  ECN_ARGS="-ecn_kmin $ECN_KMIN -ecn_kmax $ECN_KMAX -ecn_pmax $ECN_PMAX"; fi

case "$CC" in
  dcqcn) EXE="$BIN/htsim_roce"; ARGS="-dcqcn 100 $ECN_ARGS -pfc_thresholds 80 150 $EXTRA_ARGS" ;;
  roce)  EXE="$BIN/htsim_roce"; ARGS="-pfc_thresholds 80 150 $EXTRA_ARGS" ;;
  hpcc)  EXE="$BIN/htsim_hpcc"; ARGS="$HPCC_ARGS $EXTRA_ARGS" ;;
  eqds)  EXE="$BIN/htsim_eqds"; ARGS="$EXTRA_ARGS" ;;
  *) echo "unknown cc: $CC"; exit 1 ;;
esac

[ -x "$EXE" ] || { echo "missing binary $EXE - run ./setup.sh first"; exit 1; }

# Each run gets its own scratch working directory: the binaries write
# logout.dat into the cwd, and parallel runs sharing one cwd clobber it.
WORK=$(mktemp -d "${TMPDIR:-/tmp}/htsim_run.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
echo "running $TAG ... (link ${GBPS} Gbps)"
START=$(date +%s)
$EXE $COMMON $ARGS -tm "$TM_ABS" > "$LOG" 2>&1 || true
echo "  wall $(( $(date +%s)-START ))s"
python3 "$HERE/scripts/parse.py" "$LOG" "$CSV" "$TAG" "$TM_ABS" "$GBPS"
