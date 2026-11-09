#!/usr/bin/env bash
# PFC / queue diagnostic rerun of one configuration with HTSIM's queue and
# sink samplers on, decoded by scripts/pfc_analyze.py. For htsim_roce
# (roce, dcqcn); the samplers exist in the other binaries too, but only the
# lossless (PFC) queues of htsim_roce/htsim_hpcc can be paused.
#
#   [HTSIM_BUILD=build_start] ./scripts/pfc_diag.sh <cc> <topo> <matrix.cm> <seed> <outdir> <name> [end_us]
#
# Same flags as scripts/run.sh (-strat ecmp_host -paths 16, per-topology -q
# and -linkspeed, -pfc_thresholds 80 150, -dcqcn 100 for dcqcn), plus
#   -log tor_downqueue   a 10 us QueueLoggerSampling on EVERY queue
#                        (fat_tree_topology.cpp creates one per queue when a
#                        logger factory exists; main_roce.cpp:402)
#   -log sink -logtime 0.05   per-sink delivered bytes every 50 us
#   -o run.dat           binary log (deleted after decoding; it is ~0.1-1 GB)
# Same seed => same ECMP placement as the run.sh run (the loggers draw no
# random numbers), so the per-flow finish times must match the run.sh run;
# pfc_analyze.py reports the largest difference.
#
# end_us defaults to 120000. The samplers write records until -end, so pass
# the regular run's last finish + a margin, not the sweep's END.
#
# Writes <outdir>/<name>_queues.csv, <name>_flows.csv, <name>_pfc.md and
# <name>.log (stdout of the binary).
set -euo pipefail
CC=$1; TOPO=$2; TM=$3; SEED=$4; OUT=$5; NAME=$6; END=${7:-120000}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HERE/HTSIM/htsim/sim/${HTSIM_BUILD:-build}/datacenter"
TOPODIR="$HERE/HTSIM/htsim/sim/datacenter/topologies"
TM_ABS="$(cd "$(dirname "$TM")" && pwd)/$(basename "$TM")"
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
case "$TOPO" in      # keep in step with scripts/run.sh
  default)    TOPOARG="";                                    NODES=16;  Q=500;  GBPS=400 ;;
  ft128_1os)  TOPOARG="-topo $TOPODIR/fat_tree_128_1os.topo"; NODES=128; Q=500;  GBPS=100 ;;
  ft128_4os)  TOPOARG="-topo $TOPODIR/fat_tree_128_4os.topo"; NODES=128; Q=500;  GBPS=100 ;;
  ft128_8os)  TOPOARG="-topo $TOPODIR/fat_tree_128_8os.topo"; NODES=128; Q=2000; GBPS=100 ;;
  fat_tree_128_[148]os) TOPOARG="-topo $TOPODIR/$TOPO.topo"; NODES=128; Q=2000; GBPS=100 ;;
  *) echo "unknown topo $TOPO"; exit 1 ;;
esac
case "$CC" in
  roce)  EXE="$BIN/htsim_roce"; ARGS="-pfc_thresholds 80 150" ;;
  dcqcn) EXE="$BIN/htsim_roce"; ARGS="-dcqcn 100 -pfc_thresholds 80 150" ;;
  *) echo "pfc_diag: only roce / dcqcn have PFC queues"; exit 1 ;;
esac
W=$(mktemp -d "${TMPDIR:-/tmp}/pfc_diag.XXXXXX")
trap 'rm -rf "$W"' EXIT
cd "$W"
t0=$(date +%s)
$EXE -nodes $NODES $TOPOARG -strat ecmp_host -paths 16 -linkspeed $((GBPS*1000)) -end "$END" -q $Q \
     -seed "$SEED" $ARGS -log tor_downqueue -log sink -logtime 0.05 -o run.dat -tm "$TM_ABS" \
     > "$OUT/$NAME.log" 2>&1 || true
echo "pfc_diag $NAME: sim $(( $(date +%s) - t0 ))s, run.dat $(du -h run.dat | cut -f1)"
python3 "$HERE/scripts/pfc_analyze.py" --dat run.dat --idmap idmap.txt --log "$OUT/$NAME.log" \
  --tm "$TM_ABS" --gbps "$GBPS" --pfc-high 150 --out "$OUT/$NAME"
