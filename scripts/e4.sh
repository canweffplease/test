#!/usr/bin/env bash
# E4: transport scheduling (fair share / SRPT / EDF) under incast, EQDS only.
#
#   ./scripts/e4.sh [gen|run|plot|all] [-j N] [--dry-run]
#
#   gen    write workloads/e4/*.cm (+ .json sidecars) and check that the
#          policy variants of one (load, seed) differ ONLY in the prio token
#   run    run every matrix through htsim_eqds (run.sh), results/e4/
#   plot   results/figs/e4_*.{png,pdf} and results/e4/e4_table.{csv,md}
#   diag   micro-tests behind docs/E4.md's mechanism section (workloads/e4/diag,
#          results/e4/diag): the "zombie" idle-list credit after a flow
#          finishes, strict priority with one vs several urgent flows, and
#          the same priority tests at larger -cwnd (direct binary call,
#          diagnostic only; run.sh has no -cwnd). ~2 min.
#   all    gen, run, plot (default; diag is separate)
#   -j N   parallel simulations, default 6 (each is single threaded)
#
# Workload (see docs/E4.md): 16-node default fat tree, 400 Gbps. One rail of
# a TP=8 deployment (--shards 1): 8 prefill GPUs (hosts 0-7, one per prefill
# instance) each send their shard of Llama-3-70B KV cache to decode GPU 0
# (host 8). Prompt lengths are sampled from the Azure 2023 conversation trace
# (clamped to 8192, Llama-3's context), Poisson arrivals over a 200 ms window
# with the count fixed, at 50/70/90% of the receiver's 400 Gbps downlink.
# Three workload seeds W = 1,2,3; the simulator runs with SEED = W too, so
# every policy at a given (load, W) sees the same matrix and the same ECMP
# salts (paired comparison).
#
# Policies (prio column only; arrivals and sizes identical):
#   fifo    --prio none   every flow prio 2000000: receiver round robin
#                         (fair share / processor sharing, NOT arrival order)
#   srpt    --prio srpt   prio = flow bytes (shortest flow first)
#   edf     --prio edf    prio = deadline (us), deadline = start + 2.0 x floor
#   edf1p5  --prio edf --deadline-slack 1.5   tighter deadlines
#   edf4    --prio edf --deadline-slack 4     looser deadlines
#
# Run tag: eqds_default_load<L>_<policy>_w<W>_s<W>. Resumable: a run whose
# summary json exists is skipped. HTSIM_BUILD defaults to build (the main
# binary with the start-time fix); END defaults to 500000 us (the last flow
# finishes ~205-215 ms; parse.py warns if any flow did not finish).
# OUTDIR (default results/e4) is where runs, tables and diag/ go; FIGPREFIX
# (default e4) names the figures results/figs/<FIGPREFIX>_*.{png,pdf}, e.g.
#   HTSIM_BUILD=build_eqds2 OUTDIR=results/e4_fixed FIGPREFIX=e4_fixed ./scripts/e4.sh run
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WL="$HERE/workloads/e4"
OUTDIR="${OUTDIR:-$HERE/results/e4}"
mkdir -p "$WL" "$OUTDIR"; OUTDIR="$(cd "$OUTDIR" && pwd)"; export OUTDIR
export HTSIM_BUILD="${HTSIM_BUILD:-build}"
export END="${END:-500000}"

MODE=all; J=6; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    gen|run|plot|diag|all) MODE=$1; shift ;;
    -j) J=$2; shift 2 ;;
    -j*) J=${1#-j}; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,41p' "$0"; exit 0 ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
done

LOADS="90 70 50"
SEEDS="1 2 3"
POLICIES="fifo srpt edf edf1p5 edf4"

gen_args() {   # policy -> generator flags
  case "$1" in
    fifo)   echo "--prio none" ;;
    srpt)   echo "--prio srpt" ;;
    edf)    echo "--prio edf --deadline-slack 2.0" ;;
    edf1p5) echo "--prio edf --deadline-slack 1.5" ;;
    edf4)   echo "--prio edf --deadline-slack 4" ;;
  esac
}

do_gen() {
  local CMDS="$WL/COMMANDS.txt"
  : > "$CMDS"
  for W in $SEEDS; do
    for L in $LOADS; do
      for P in $POLICIES; do
        local OUT="$WL/e4_load${L}_w${W}_${P}.cm"
        # shellcheck disable=SC2046
        local CMD="scripts/gen_workload.py --topo default --model llama3-70b --tp 8 --shards 1 \
--pattern incast --incast 8 --prompt trace --trace data/azure/AzureLLMInferenceTrace_conv.csv \
--max-prompt 8192 --arrival poisson --load 0.$L --duration-ms 200 --fix-count --seed $W \
$(gen_args "$P") --out workloads/e4/e4_load${L}_w${W}_${P}.cm"
        echo "$CMD" >> "$CMDS"
        (cd "$HERE" && eval "python3 $CMD") > "$WL/e4_load${L}_w${W}_${P}.gen.txt" 2>&1 \
          || { echo "generator failed for $OUT"; cat "$WL/e4_load${L}_w${W}_${P}.gen.txt"; exit 1; }
      done
      # the variants must differ only in the prio token
      local BASE="$WL/e4_load${L}_w${W}_fifo.cm"
      for P in $POLICIES; do
        [ "$P" = fifo ] && continue
        if ! diff -q <(sed 's/ prio [0-9]*//' "$WL/e4_load${L}_w${W}_${P}.cm") "$BASE" > /dev/null; then
          echo "MISMATCH: e4_load${L}_w${W}_${P}.cm differs from fifo beyond the prio token"; exit 1
        fi
      done
      local N; N=$(awk '/^Connections/{print $2}' "$BASE")
      local R; R=$(grep -o 'realized [0-9.]*' "$WL/e4_load${L}_w${W}_fifo.gen.txt" | awk '{print $2}')
      echo "  load $L seed $W: $N flows, realized load $R; prio-only diff OK"
    done
  done
  echo "generator commands recorded in workloads/e4/COMMANDS.txt"
}

do_run() {
  local JOBS=()
  for L in $LOADS; do               # high load first: longest runs start first
    for W in $SEEDS; do
      for P in $POLICIES; do
        local TAG="eqds_default_load${L}_${P}_w${W}_s${W}"
        [ -f "$OUTDIR/${TAG}_summary.json" ] && continue
        [ -f "$WL/e4_load${L}_w${W}_${P}.cm" ] || { echo "missing matrix for $TAG; run gen first"; exit 1; }
        JOBS+=("$L $W $P $TAG")
      done
    done
  done
  echo "e4 run: ${#JOBS[@]} runs to do (existing summaries skipped), -j $J, HTSIM_BUILD=$HTSIM_BUILD END=$END"
  if [ "$DRY" = 1 ]; then printf '  %s\n' "${JOBS[@]}"; return; fi

  run_one() {
    local L=$1 W=$2 P=$3 TAG=$4 OUT="$OUTDIR/$4.out" t0 rc
    t0=$(date +%s)
    local try
    for try in 1 2; do      # run.sh may be edited concurrently; retry once
      SEED=$W "$HERE/scripts/run.sh" eqds default "load${L}_${P}_w${W}" \
        "$WL/e4_load${L}_w${W}_${P}.cm" > "$OUT" 2>&1
      rc=$?
      [ $rc -eq 0 ] && [ -f "$OUTDIR/${TAG}_summary.json" ] && break
      grep -q "REFUSING" "$OUT" && break    # a real refusal: do not retry
    done
    local Wall=$(( $(date +%s) - t0 ))
    if [ -f "$OUTDIR/${TAG}_summary.json" ]; then
      python3 - "$OUTDIR/${TAG}_summary.json" "$Wall" <<'EOF'
import json, sys
p, w = sys.argv[1], float(sys.argv[2])
s = json.load(open(p)); s["wall_s"] = w
json.dump(s, open(p, "w"), indent=1)
EOF
      echo "  done   $TAG  ${Wall}s  $(grep -o 'requests .*max [0-9.]*' "$OUT")"
    else
      echo "  FAILED $TAG  ${Wall}s  (see $OUT)  $(grep -m1 'REFUSING\|WARNING' "$OUT")"
    fi
  }

  local T0; T0=$(date +%s)
  local running=0 job
  for job in "${JOBS[@]}"; do
    # shellcheck disable=SC2086
    set -- $job
    while [ "$running" -ge "$J" ]; do wait -n; running=$((running - 1)); done
    echo "  start  $4"
    run_one "$1" "$2" "$3" "$4" &
    running=$((running + 1))
  done
  wait
  echo "e4 run: finished in $(( $(date +%s) - T0 ))s"
}

do_diag() {
  local D="$WL/diag" R="$OUTDIR/diag" M=41943040 L=167772160
  mkdir -p "$D" "$R"
  # 1. zombie: 16.2 MB 2->8 at t=0 (finishes 355.5 us), then 7.4 MB 3->8 after a gap
  printf 'Nodes 16\nConnections 1\n3->8 id 1 start 0 size 7416090\n' > "$D/alone.cm"
  for g in 70 200 300 1145; do
    printf 'Nodes 16\nConnections 2\n2->8 id 1 start 0 size 16220160\n3->8 id 2 start %s size 7416090\n' \
      $(( (355510 + g * 1000) * 1000 )) > "$D/gap$g.cm"
  done
  printf 'Nodes 16\nConnections 2\n2->8 id 1 start 0 size 16220160\n3->9 id 2 start 425510000 size 7416090\n' > "$D/gap70_otherdst.cm"
  printf 'Nodes 16\nConnections 2\n2->8 id 1 start 0 size 167772160\n3->8 id 2 start 5000000000 size 7416090\n' > "$D/big_gap1521.cm"
  # 2. priority with one / two urgent flows (all start at 0, into host 8)
  printf 'Nodes 16\nConnections 4\n0->8 id 1 start 0 size %s prio 1\n1->8 id 2 start 0 size %s prio 9\n2->8 id 3 start 0 size %s prio 9\n3->8 id 4 start 0 size %s prio 9\n' $M $L $L $L > "$D/u1b3.cm"
  printf 'Nodes 16\nConnections 4\n0->8 id 1 start 0 size %s\n1->8 id 2 start 0 size %s\n2->8 id 3 start 0 size %s\n3->8 id 4 start 0 size %s\n' $M $L $L $L > "$D/u1b3_noprio.cm"
  printf 'Nodes 16\nConnections 4\n0->8 id 1 start 0 size %s prio 1\n1->8 id 2 start 0 size %s prio 1\n2->8 id 3 start 0 size %s prio 9\n3->8 id 4 start 0 size %s prio 9\n' $M $M $L $L > "$D/u2b2.cm"
  printf 'Nodes 16\nConnections 4\n0->8 id 1 start 0 size %s prio 1\n1->8 id 2 start 0 size %s prio 2\n2->8 id 3 start 0 size %s prio 3\n3->8 id 4 start 0 size %s prio 4\n' $M $M $M $M > "$D/ladder4.cm"
  local f n
  for f in "$D"/*.cm; do
    n=$(basename "$f" .cm)
    while [ "$(jobs -rp | wc -l)" -ge "$J" ]; do wait -n; done
    (SEED=1 END=20000 OUTDIR="$R" "$HERE/scripts/run.sh" eqds default "diag_$n" "$f" > "$R/$n.out" 2>&1) &
  done
  wait
  # 3. same priority tests with a larger EQDS window (-cwnd, packets; default 64)
  local BIN="$HERE/HTSIM/htsim/sim/$HTSIM_BUILD/datacenter/htsim_eqds" c x T
  for c in 64 128 200 400; do
    for x in u1b3 ladder4; do
      while [ "$(jobs -rp | wc -l)" -ge "$J" ]; do wait -n; done
      T=$(mktemp -d "${TMPDIR:-/tmp}/e4diag.XXXXXX")
      (cd "$T" && "$BIN" -nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -end 20000 -q 500 \
         -seed 1 -cwnd $c -tm "$D/$x.cm" > "$R/cwnd${c}_$x.log" 2>&1; rm -rf "$T") &
    done
  done
  wait
  # 4. pull-pacer trace of gap70: count the stale idle-list entries (log not kept, ~14 MB)
  T=$(mktemp -d "${TMPDIR:-/tmp}/e4diag.XXXXXX")
  (cd "$T" && "$BIN" -nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -end 900 -q 500 -seed 1 \
     -debug -tm "$D/gap70.cm" > dbg.log 2>&1
   {
     echo "gap70 with -debug: flow 1 (2->8, eqdsSrc 0) finishes 355.51 us, flow 2 (3->8, eqdsSrc 1) starts 425.51 us"
     echo "active pulls to flow 1: $(grep -c 'PullPacer: Active: eqdsSrc 0 ' dbg.log)"
     echo "  of which left its backlog at 0 (=> pushed onto _idle_senders): $(grep -c 'PullPacer: Active: eqdsSrc 0 backlog 0 ' dbg.log)"
     echo "slow (idle) pulls to flow 1 after it finished: $(grep 'PullPacer: Idle: eqdsSrc 0 at' dbg.log | awk '$6+0>355.51' | wc -l)"
     echo "  of which while flow 2 was running (425.51-782.03 us): $(grep 'PullPacer: Idle: eqdsSrc 0 at' dbg.log | awk '$6+0>425.51 && $6+0<782.03' | wc -l)"
     echo "pulls to flow 2: active $(grep -c 'PullPacer: Active: eqdsSrc 1 ' dbg.log), idle $(grep -c 'PullPacer: Idle: eqdsSrc 1 ' dbg.log)"
     echo "last slow pull to flow 1: $(grep 'PullPacer: Idle: eqdsSrc 0 at' dbg.log | tail -1)"
   } > "$R/gap70_debug_counts.txt"; rm -rf "$T")
  # summary
  {
    for f in "$D"/*.cm; do
      n=$(basename "$f" .cm)
      echo "$n: $(grep -o 'flowId [0-9]* .*\(starting\|finished\) at [0-9.]*' "$R/eqds_default_diag_${n}_s1.log" \
        | awk '{print "f"$2" "$(NF-2)" "$NF}' | tr '\n' ' ')"
    done
    for c in 64 128 200 400; do for x in u1b3 ladder4; do
      echo "cwnd $c $x: $(grep -o 'flowId [0-9]* .*finished at [0-9.]*' "$R/cwnd${c}_$x.log" | awk '{print "f"$2"="$NF}' | tr '\n' ' ')"
    done; done
    cat "$R/gap70_debug_counts.txt"
  } | tee "$R/diag_summary.txt"
}

do_plot() {
  local P="${FIGPREFIX:-e4}" T f
  if [ "$P" = e4 ]; then
    python3 "$HERE/scripts/e4_plot.py" --results "$OUTDIR"
  else
    T=$(mktemp -d "${TMPDIR:-/tmp}/e4plot.XXXXXX")
    python3 "$HERE/scripts/e4_plot.py" --results "$OUTDIR" --outdir "$T" || { rm -rf "$T"; return 1; }
    for f in "$T"/e4_*; do mv "$f" "$HERE/results/figs/${P}_${f##*/e4_}"; done
    rm -rf "$T"
  fi
}

case "$MODE" in
  gen)  do_gen ;;
  run)  do_run ;;
  plot) do_plot ;;
  diag) do_diag ;;
  all)  do_gen && do_run && [ "$DRY" = 0 ] && do_plot ;;
esac
