#!/usr/bin/env bash
# HPCC fidelity study (HTSIM/OUR_CHANGES.md, HPCC fidelity section): legacy vs paper Algorithm 1.
#
#   HTSIM_BUILD=build_hpccfix ./scripts/hpcc_fidelity.sh [converge] [smoke] [incast] [e3]
#
# converge  workloads/validation/converge_stagger.cm, picosecond starts fed
#           directly (the binary honours them), 4 variants x seeds 1-5, with
#           validation.sh's logging flags; analyzed by hpcc_fidelity_analyze.py
# smoke     kv_llama70b_4k_tp8.cm, seeds 1-10, via run.sh (paper version);
#           legacy is bit-identical to results/determinism/hpcc_tp8_seeds1-100.csv
# incast    kv_incast_8to1.cm, seeds 1-3, legacy and paper (run.sh's args)
# e3        e3_load30_ft16.cm via run.sh, seed 1, END=500000
# Arguments are run.sh's for the default topology:
#   -nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -q 500
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export HTSIM_BUILD="${HTSIM_BUILD:-build_hpccfix}"
BIN="$HERE/HTSIM/htsim/sim/$HTSIM_BUILD/datacenter/htsim_hpcc"
OUT="$HERE/results/hpcc_fidelity"
JOBS="${JOBS:-8}"
STEPS="${*:-converge smoke incast e3}"
COMMON="-nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -q 500"
# hpcc = paper Algorithm 1 with the defaults (T = 13.1 us, W_AI = W_init*(1-eta)/10)
declare -A VARG=([hpcclegacy]="-hpcc_legacy" [hpcc]="" [hpccT12]="-hpcc_T 12" [hpccN2]="-hpcc_N 2")
mkdir -p "$OUT"

conv_one() {  # variant seed
  local v=$1 s=$2 tag="${1}_s${2}" w d="$OUT/converge"
  w=$(mktemp -d "${TMPDIR:-/tmp}/hf_conv.XXXXXX")
  (cd "$w" && $BIN $COMMON -end 50000 -seed "$s" ${VARG[$v]} -hpcc_trace \
      -log sink -log tor_downqueue -logtime 0.01 -o run.dat \
      -tm "$HERE/workloads/validation/converge_stagger.cm" > run.log 2>&1 || true)
  grep -v '^HPCCW ' "$w/run.log" > "$d/$tag.log" || true
  grep '^HPCCW ' "$w/run.log" | gzip -c > "$d/raw/$tag.wtrace.gz" || true
  gzip -c "$w/run.dat" > "$d/raw/$tag.dat.gz"
  gzip -c "$w/idmap.txt" > "$d/raw/$tag.idmap.gz"
  rm -rf "$w"
  echo "  $tag violations: $(grep -c 'LOSSLESS not working' "$d/$tag.log" || true)"
}

direct_one() {  # variant seed matrix label -> parse.py csv under $OUT/<label>
  local v=$1 s=$2 tm=$3 lab=$4 tag w
  tag="${1}_default_${lab}_s${2}"
  w=$(mktemp -d "${TMPDIR:-/tmp}/hf_run.XXXXXX")
  (cd "$w" && $BIN $COMMON -end 200000 -seed "$s" ${VARG[$v]} -tm "$tm" > "$OUT/$lab/$tag.log" 2>&1 || true)
  rm -rf "$w"
  python3 "$HERE/scripts/parse.py" "$OUT/$lab/$tag.log" "$OUT/$lab/$tag.csv" "$tag" "$tm" 400 | grep -E "flows|REFUS" | sed "s/^/  $tag /"
}

for step in $STEPS; do case $step in
converge)
  mkdir -p "$OUT/converge/raw"
  for v in hpcclegacy hpcc hpccT12 hpccN2; do for s in 1 2 3 4 5; do
    while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
    conv_one "$v" "$s" &
  done; done; wait
  python3 "$HERE/scripts/hpcc_fidelity_analyze.py" "$OUT/converge" \
      "$HERE/workloads/validation/converge_stagger.cm" "$OUT/figs" > "$OUT/converge/analyze.out"
  ;;
smoke)
  mkdir -p "$OUT/smoke"
  for s in $(seq 1 10); do
    while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
    (SEED=$s OUTDIR="$OUT/smoke" "$HERE/scripts/run.sh" hpcc default fidelity "$HERE/workloads/kv_llama70b_4k_tp8.cm" | grep -E "flows|REFUS" | sed "s/^/  s$s /") &
  done; wait
  ;;
incast)
  mkdir -p "$OUT/incast"
  for v in hpcclegacy hpcc; do for s in 1 2 3; do
    while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
    direct_one "$v" "$s" "$HERE/workloads/kv_incast_8to1.cm" incast &
  done; done; wait
  ;;
e3)
  mkdir -p "$OUT/e3"
  SEED=1 END=500000 OUTDIR="$OUT/e3" "$HERE/scripts/run.sh" hpcc default e3test "$HERE/workloads/e3_load30_ft16.cm"
  ;;
esac; done
