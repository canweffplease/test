#!/usr/bin/env bash
# E1: uncontended per-tier cost table. Reproduces every number in docs/E1.md.
#
#   ./scripts/e1.sh            # everything below, in order
#   ./scripts/e1.sh table      # main table runs through scripts/run.sh
#   ./scripts/e1.sh sweep      # DCQCN ECN-threshold sensitivity (-dcqcn K)
#   ./scripts/e1.sh ctl        # 128-node control runs with -linkspeed 100000
#   ./scripts/e1.sh seeds      # TP=8 runs over -seed 1..10 (other ECMP placements)
#   ./scripts/e1.sh eqdsx      # EQDS with -paths 1..256 and -q 2000 (multipath confound)
#   ./scripts/e1.sh trace      # ECMP path identification (elephant isolation)
#   ./scripts/e1.sh sinks      # per-flow goodput time series (-log sink)
#   ./scripts/e1.sh queues     # switch queue occupancy (-log tor_upqueue)
#   ./scripts/e1.sh summary    # re-summarise existing results/e1 files
#
# Env: E1_JOBS (parallel simulations, default 16), E1_REPS (default 3),
#      E1_HPCC_REPS (default 5), HTSIM_BUILD (build dir name, default "build").
#      POLICIES  which policies to run (default "roce dcqcn eqds hpcc").
#      HTSIM_BUILD_<cc>  per-policy build dir, e.g. HTSIM_BUILD_hpcc=build_hpccfix
#                HTSIM_BUILD_eqds=build_eqds2 HTSIM_BUILD_roce=build_start
#                (falls back to HTSIM_BUILD). Used by the table (via run.sh)
#                and by every direct invocation (base_args).
#      E1_SEEDS  if set (e.g. "1 2 3 4 5"), "table" runs every cell for every
#                selected policy once per seed (SEED=<s> through run.sh, so
#                run.sh's matched -linkspeed, -q and -hpcc_T apply) instead of
#                E1_REPS repetitions at the default seed; tags end _s<seed>
#                instead of _r<k>. HPCC then also runs on the 128-node trees
#                and EQDS on 4os/8os. Unset = the original table.
#      E1_SWEEP_K  K values for "sweep" (default "5 10 25 50 100 150 200 300 400").
#      E1_SWEEP_MODES  if set (e.g. "step red"), "sweep" goes through run.sh
#                at E1_SEEDS (default "1 2 3 4 5") instead of the original
#                direct legacy command: step = ECN_STEP=1 with -dcqcn K (step
#                marking at K packets), red = run.sh's RED marking with
#                Kmax = K packets x 4 KB (Kmin 1 us, Pmax 1%). Tags
#                dcqcn_default_sens_<mode>_k<K>_s<seed> (run.sh naming).
#
# Everything lands in results/e1/:
#   <cc>_<topo>_e1_<pattern>_r<k>.{log,csv}  table runs (scripts/run.sh)
#   dcqcn_sens_k<K>_r<k>.{log,csv}            threshold sweep
#   ctl100g_<cc>_ft128_1os_<pattern>_r<k>.*   -linkspeed 100000 controls
#   seed_<cc>_<topo>_<pattern>_s<seed>.*      other ECMP placements
#   trace/<topo>_<pattern>_f<i>.{cm,links}    -log queue_usage, one elephant
#   sinks/<name>.csv                          goodput per flow per 10 us
#   queues/<name>.txt                         queue occupancy per switch queue
#   eqdsx_<topo>_tp8_crosspod_p<P>_q<Q>_ls<L>.*  EQDS multipath grid
#   summary.csv sens_summary.csv ctl_summary.csv seeds_summary.csv
#   eqdsx_summary.csv paths.txt sinks_summary.txt
#
# Every simulation is resumable: an existing CSV (or trace log) is skipped.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$HERE/results/e1"
WL="$HERE/workloads/e1"
BUILD_DIR="${HTSIM_BUILD:-build}"
SIM="$HERE/HTSIM/htsim/sim"
BIN="$SIM/$BUILD_DIR/datacenter"
TOPODIR="$SIM/datacenter/topologies"
JOBS=${E1_JOBS:-16}
REPS=${E1_REPS:-3}
HPCC_REPS=${E1_HPCC_REPS:-5}
WHAT=${1:-all}
POLICIES="${POLICIES:-roce dcqcn eqds hpcc}"
E1_SEEDS="${E1_SEEDS:-}"
mkdir -p "$OUT" "$OUT/trace" "$OUT/sinks"

pool() {   # never more than $JOBS simulations at once
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
  "$@" &
}

want()   { case " $POLICIES " in *" $1 "*) return 0 ;; esac; return 1; }   # cc selected?
build_of() {   # per-policy build dir; dcqcn shares htsim_roce but may have its own
  local v="HTSIM_BUILD_$1"
  echo "${!v:-$BUILD_DIR}"
}
bin_of() { echo "$SIM/$(build_of "$1")/datacenter"; }

matrix() { case "$1" in default) echo "$WL/ft16_$2.cm" ;; *) echo "$WL/ft128_$2.cm" ;; esac; }
gbps()   { case "$1" in default) echo 400 ;; *) echo 100 ;; esac; }

# Base command line, identical to what scripts/run.sh builds for <cc> <topo>.
base_args() {   # cc topo
  local q=500 nodes=16 topoarg=""
  case "$2" in
    ft128_1os|ft128_4os) nodes=128; topoarg="-topo $TOPODIR/fat_tree_${2#ft}.topo" ;;
    ft128_8os) nodes=128; q=2000; topoarg="-topo $TOPODIR/fat_tree_128_8os.topo" ;;
  esac
  local a="-nodes $nodes $topoarg -strat ecmp_host -paths 16 -linkspeed 400000 -end 2000000 -q $q"
  local bin; bin=$(bin_of "$1")
  case "$1" in
    roce)  echo "$bin/htsim_roce $a -pfc_thresholds 80 150" ;;
    dcqcn) echo "$bin/htsim_roce $a -dcqcn 100 -pfc_thresholds 80 150" ;;
    eqds)  echo "$bin/htsim_eqds $a" ;;
    hpcc)  echo "$bin/htsim_hpcc $a" ;;
  esac
}

# Run a binary in a private scratch cwd (binaries write logout.dat/idmap.txt to cwd).
in_scratch() { local w; w=$(mktemp -d "${TMPDIR:-/tmp}/e1.XXXXXX"); ( cd "$w" && "$@" ); local rc=$?; rm -rf "$w"; return $rc; }

parse() {   # log csv tag matrix gbps  -> prints parse.py summary line
  python3 "$HERE/scripts/parse.py" "$1" "$2" "$3" "$4" "$5" 2>&1 | grep -E 'last shard|REFUSING|WARNING' | tr -s ' '
}

# ---------------------------------------------------------------- table
run_one() {   # cc topo pattern rep [seed]
  local cc=$1 topo=$2 pat=$3 rep=$4 seed=${5:-}
  local label="e1_${pat}_r${rep}" tm; tm=$(matrix "$topo" "$pat")
  local tag="${cc}_${topo}_${label}"
  [ -n "$seed" ] && { label="e1_${pat}"; tag="${cc}_${topo}_${label}_s${seed}"; }
  [ -f "$OUT/$tag.csv" ] && return 0
  # (E1_SEEDS mode runs DCQCN through run.sh like the others: the final
  # transport finishes in seconds, and run.sh adds its ECN marking args.)
  if [ "$cc" = dcqcn ] && [ -z "$seed" ]; then
    # DCQCN: run.sh's exact command except -end. At -end 2000000 a 128-node
    # TP=8 DCQCN run takes hours (not 70 s) under load: RoceSrc's pacing timer
    # never stops (roce.cpp:329 compares _highest_sent, in packets, with
    # _flow_size, in bytes) and every CNP / 55 us rate timer
    # (dcqcn.cpp:75,147) adds another self-rescheduling event chain, so the
    # whole 2 s horizon is simulated at packet granularity. All flows finish
    # by ~70 ms (8:1) so -end 200000 (128-node) / 20000 (16-node) is
    # result-identical; parse.py warns if any flow is missing.
    local end; end=$([ "$topo" = default ] && echo 20000 || echo 200000)
    local cmd; cmd=$(base_args dcqcn "$topo" | sed "s/-end 2000000/-end $end/")
    [ -n "$seed" ] && cmd="$cmd -seed $seed"
    echo "direct: $cmd -tm $tm" > "$OUT/$tag.out"
    in_scratch $cmd -tm "$tm" > "$OUT/$tag.log" 2>&1
    parse "$OUT/$tag.log" "$OUT/$tag.csv" "$tag" "$tm" "$(gbps "$topo")" >> "$OUT/$tag.out"
    echo "table $tag: $(tail -1 "$OUT/$tag.out")"
    return 0
  fi
  if [ -n "$seed" ]; then
    SEED="$seed" HTSIM_BUILD="$(build_of "$cc")" OUTDIR="$OUT" \
      "$HERE/scripts/run.sh" "$cc" "$topo" "$label" "$tm" > "$OUT/$tag.out" 2>&1
  else
    HTSIM_BUILD="$(build_of "$cc")" OUTDIR="$OUT" \
      "$HERE/scripts/run.sh" "$cc" "$topo" "$label" "$tm" > "$OUT/$tag.out" 2>&1
  fi
  # older run.sh without OUTDIR support writes into results/
  for x in log csv; do [ -f "$HERE/results/$tag.$x" ] && mv -f "$HERE/results/$tag.$x" "$OUT/"; done
  # if run.sh itself failed after the simulation finished, parse here
  if [ ! -f "$OUT/$tag.csv" ] && grep -q '^Done' "$OUT/$tag.log" 2>/dev/null; then
    parse "$OUT/$tag.log" "$OUT/$tag.csv" "$tag" "$tm" "$(gbps "$topo")" >> "$OUT/$tag.out"
  fi
  echo "table $tag: $(grep -E 'last shard|REFUSING' "$OUT/$tag.out" | tail -1 | tr -s ' ')"
}

# E1_SEEDS mode: every cell, every selected policy, one run per seed.
table_seeds() {
  local s cc topo p
  for s in $E1_SEEDS; do
    for cc in dcqcn roce hpcc eqds; do
      want $cc || continue
      for p in single_sametor single_samepod single_crosspod tp8_samepod tp8_crosspod; do
        pool run_one $cc ft128_1os $p 0 "$s"
      done
      for topo in ft128_4os ft128_8os; do
        for p in tp8_samepod tp8_crosspod; do pool run_one $cc $topo $p 0 "$s"; done
      done
      for p in single_sametor single_samepod single_crosspod tp8_crosspod; do
        pool run_one $cc default $p 0 "$s"
      done
    done
  done
  wait
}

table() {
  [ -n "$E1_SEEDS" ] && { table_seeds; return; }
  local p r cc topo
  for r in $(seq 1 "$REPS"); do          # DCQCN first: it is the long pole
    want dcqcn || break
    for p in single_sametor single_samepod single_crosspod tp8_samepod tp8_crosspod; do
      pool run_one dcqcn ft128_1os $p $r
    done
    for topo in ft128_4os ft128_8os; do
      for p in tp8_samepod tp8_crosspod; do pool run_one dcqcn $topo $p $r; done
    done
    for p in single_sametor single_samepod single_crosspod tp8_crosspod; do
      pool run_one dcqcn default $p $r
    done
  done
  for r in $(seq 1 "$REPS"); do
    for cc in roce eqds; do
      want $cc || continue
      for p in single_sametor single_samepod single_crosspod tp8_samepod tp8_crosspod; do
        pool run_one $cc ft128_1os $p $r
      done
      for p in single_sametor single_samepod single_crosspod tp8_crosspod; do
        pool run_one $cc default $p $r
      done
    done
    want roce || continue
    for topo in ft128_4os ft128_8os; do
      for p in tp8_samepod tp8_crosspod; do pool run_one roce $topo $p $r; done
    done
  done
  # HPCC on the 16-node tree only, more reps (its ECMP path ids are wall-clock seeded)
  want hpcc && for r in $(seq 1 "$HPCC_REPS"); do
    for p in single_sametor single_samepod single_crosspod tp8_crosspod; do
      pool run_one hpcc default $p $r
    done
  done
  wait
}

# ---------------------------------------------------------------- sweep
# DCQCN ECN threshold sensitivity, 16-node tree, TP=8 cross-pod. Same command
# as run.sh's "dcqcn default" except -dcqcn K and -end 20000 (20 ms; all flows
# finish by ~12.1 ms). The 2 s horizon only matters for wall clock: the RoCE
# pacing timer never stops (roce.cpp:329 compares packets with bytes) and each
# CNP/rate timer adds another self-rescheduling chain, so DCQCN runs at -end
# 2000000 take many minutes. K=400 r1 was run at -end 2000000 and is identical.
sens_one() {
  local k=$1 r=$2 tag="dcqcn_sens_k${1}_r${2}" tm="$WL/ft16_tp8_crosspod.cm"
  [ -f "$OUT/$tag.csv" ] && return 0
  local end=20000; [ "$k" = 400 ] && [ "$r" = 1 ] && end=2000000
  local cmd; cmd=$(base_args dcqcn default | sed "s/-dcqcn 100/-dcqcn $k/; s/-end 2000000/-end $end/")
  in_scratch $cmd -tm "$tm" > "$OUT/$tag.log" 2>&1
  echo "sweep $tag: $(parse "$OUT/$tag.log" "$OUT/$tag.csv" "$tag" "$tm" 400)"
}
sens_run() {   # mode K seed: the final DCQCN through run.sh
  local mode=$1 k=$2 sd=$3 tag="dcqcn_default_sens_${1}_k${2}_s${3}" tm="$WL/ft16_tp8_crosspod.cm"
  [ -f "$OUT/$tag.csv" ] && return 0
  local env=(SEED="$sd" HTSIM_BUILD="$(build_of dcqcn)" OUTDIR="$OUT" END=50000)
  if [ "$mode" = step ]; then env+=(ECN_STEP=1 EXTRA_ARGS="-dcqcn $k")
  else env+=(ECN_KMAX=$((k * 4))); fi
  env "${env[@]}" "$HERE/scripts/run.sh" dcqcn default "sens_${mode}_k${k}" "$tm" > "$OUT/$tag.out" 2>&1
  echo "sweep $tag: $(grep -E 'last shard|REFUSING' "$OUT/$tag.out" | tail -1 | tr -s ' ')"
}
sweep() {
  local k r m s
  local ks="${E1_SWEEP_K:-5 10 25 50 100 150 200 300 400}"
  if [ -n "${E1_SWEEP_MODES:-}" ]; then
    for m in $E1_SWEEP_MODES; do for k in $ks; do for s in ${E1_SEEDS:-1 2 3 4 5}; do
      pool sens_run "$m" "$k" "$s"
    done; done; done
    wait; return
  fi
  for r in $(seq 1 "$REPS"); do
    for k in $ks; do pool sens_one $k $r; done
  done
  wait
}

# ---------------------------------------------------------------- ctl
# run.sh passes -linkspeed 400000 on every topology. A .topo file overrides the
# switch link speeds (100 Gbps), but NOT the rate the transports pace at:
# RoceSrc/DCQCNSrc take it as line rate, EQDS sizes its NIC and pull pacer by it.
# These controls repeat the 128-node runs with -linkspeed 100000.
ctl_one() {
  local cc=$1 pat=$2 r=$3 tag="ctl100g_${1}_ft128_1os_${2}_r${3}"
  local tm; tm=$(matrix ft128_1os "$pat")
  [ -f "$OUT/$tag.csv" ] && return 0
  # -end 200000 (200 ms) instead of 2 s: every flow is done by ~45 ms, and the
  # RoCE pacing timer never stops (roce.cpp:329 compares packets with bytes),
  # so a 2 s horizon only burns wall clock. parse.py warns if a flow is missing.
  local cmd; cmd=$(base_args "$cc" ft128_1os | sed 's/-linkspeed 400000/-linkspeed 100000/; s/-end 2000000/-end 200000/')
  in_scratch $cmd -tm "$tm" > "$OUT/$tag.log" 2>&1
  echo "ctl $tag: $(parse "$OUT/$tag.log" "$OUT/$tag.csv" "$tag" "$tm" 100)"
}
ctl() {
  local cc p r
  for r in $(seq 1 "$REPS"); do
    for cc in dcqcn roce eqds; do
      for p in single_crosspod tp8_samepod tp8_crosspod; do pool ctl_one $cc $p $r; done
    done
  done
  wait
}

# ---------------------------------------------------------------- seeds
# Table runs all use the binaries' default -seed 13, which fixes the per-switch
# ECMP salts and RoceSrc::_pathid draws, i.e. one ECMP placement. These runs
# repeat the TP=8 matrices over other seeds (paired: same seed = same
# placement for roce and dcqcn) to check that DCQCN > RoCE and EQDS ~1.2x are
# not artifacts of placement 13. -end trimmed as in the sweep.
seed_one() {
  local cc=$1 topo=$2 pat=$3 sd=$4 tag="seed_${1}_${2}_${3}_s${4}"
  local tm; tm=$(matrix "$topo" "$pat")
  [ -f "$OUT/$tag.csv" ] && return 0
  local end; end=$([ "$topo" = default ] && echo 20000 || echo 80000)
  local cmd; cmd=$(base_args "$cc" "$topo" | sed "s/-end 2000000/-end $end/")
  in_scratch $cmd -seed "$sd" -tm "$tm" > "$OUT/$tag.log" 2>&1
  echo "seed $tag: $(parse "$OUT/$tag.log" "$OUT/$tag.csv" "$tag" "$tm" "$(gbps "$topo")")"
}
seeds() {
  local cc s
  for s in 1 2 3 4 5 6 7 8 9 10; do
    for cc in roce dcqcn eqds; do pool seed_one $cc default tp8_crosspod $s; done
  done
  for s in 1 2 3 4 5; do
    for cc in roce dcqcn eqds; do pool seed_one $cc ft128_1os tp8_crosspod $s; done
  done
  wait
}

# ---------------------------------------------------------------- eqdsx
# EQDS confound check. EQDS sprays: every packet gets a fresh entropy value
# (EqdsSrc::nextEntropy, eqds.cpp:1238-1278) out of -paths values, and the
# switch ECMP hash includes it (fat_tree_switch.cpp:339). -paths 1 pins each
# flow to one ECMP path like RoCE/DCQCN/HPCC. -q 2000 tests whether the tail
# is driven by trimming at -q 500.
eqdsx_one() {   # topo paths q linkspeed_gbps
  local topo=$1 paths=$2 q=$3 ls=${4:-400}
  local tag="eqdsx_${topo}_tp8_crosspod_p${paths}_q${q}_ls${ls}"
  local tm; tm=$(matrix "$topo" tp8_crosspod)
  [ -f "$OUT/$tag.csv" ] || [ -f "$OUT/$tag.out" ] && return 0
  local end; end=$([ "$topo" = default ] && echo 20000 || echo 100000)
  local cmd; cmd=$(base_args eqds "$topo" | sed "s/-paths 16/-paths $paths/; s/-q [0-9]*/-q $q/; s/-end 2000000/-end $end/; s/-linkspeed 400000/-linkspeed ${ls}000/")
  in_scratch $cmd -tm "$tm" > "$OUT/$tag.log" 2>&1
  local rc=$?
  { echo "rc=$rc"; parse "$OUT/$tag.log" "$OUT/$tag.csv" "$tag" "$tm" "$(gbps "$topo")"; grep '^New' "$OUT/$tag.log"; } > "$OUT/$tag.out"
  echo "eqdsx $tag: $(tr '\n' ' ' < "$OUT/$tag.out")"
}
eqdsx() {
  local p
  for p in 1 2 4 16 64 256; do pool eqdsx_one default $p 500; done
  pool eqdsx_one default 16 2000
  for p in 1 2 4 16 64 256; do pool eqdsx_one ft128_1os $p 500 400; pool eqdsx_one ft128_1os $p 500 100; done
  pool eqdsx_one ft128_1os 16 2000 400; pool eqdsx_one ft128_1os 16 2000 100
  # run.sh refuses eqds on 4os/8os because htsim_eqds aborts there; test
  # whether the abort is caused by -linkspeed 400000 on 100 Gbps links
  pool eqdsx_one ft128_4os 16 500 400; pool eqdsx_one ft128_4os 16 500 100
  pool eqdsx_one ft128_8os 16 2000 400; pool eqdsx_one ft128_8os 16 2000 100
  wait
}

# ---------------------------------------------------------------- trace
# Path identification by elephant isolation. For flow i of a TP=8 matrix, keep
# all 8 connections (same ids, same construction order, so the same
# RoceSrc::_pathid draws from random() and the same per-switch ECMP salts) but
# shrink every other flow to one packet. -log queue_usage then prints, every
# 10 us, each queue's busy fraction (QueueLoggerEmpty::doNextEvent,
# loggers.cpp:143); the queues busy ~100% are flow i's data path.
# The logger re-arms forever, so -end stops the run at ~1.03x floor.
trace_one() {
  local topo=$1 pat=$2 i=$3
  local tag="${topo}_${pat}_f${i}" src; src=$(matrix "$topo" "$pat")
  local m="$OUT/trace/$tag.cm" log="$OUT/trace/$tag.log"
  [ -f "$OUT/trace/$tag.links" ] && return 0
  awk -v keep="$i" '/ id /{ if ($3!=keep) sub(/size [0-9]+/, "size 4000") } {print}' "$src" > "$m"
  local end; end=$([ "$topo" = default ] && echo 3460 || echo 13850)
  local cmd; cmd=$(base_args roce "$topo" | sed "s/-end 2000000/-end $end/")
  in_scratch $cmd -log queue_usage -tm "$m" > "$log" 2>&1
  # keep only each queue's mean busy fraction (raw logs are ~10 MB each)
  python3 "$HERE/scripts/e1_summary.py" --links "$log" > "$OUT/trace/$tag.links" && rm -f "$log"
  echo "trace $tag"
}
trace() {
  local t p i
  for t in default; do for i in 1 2 3 4 5 6 7 8; do pool trace_one $t tp8_crosspod $i; done; done
  for t in ft128_1os ft128_4os ft128_8os; do
    for p in tp8_samepod tp8_crosspod; do
      for i in 1 2 3 4 5 6 7 8; do pool trace_one $t $p $i; done
    done
  done
  wait
}

# ---------------------------------------------------------------- sinks
# Per-flow goodput every 10 us from RoceSinkLoggerSampling (-log sink, binary
# log). parse_output is not a default build target, so compile it privately
# against the already built libhtsim.a (does not touch the build tree).
PARSE_OUTPUT="${TMPDIR:-/tmp}/e1_parse_output"
sinks_one() {
  local name=$1 cc=$2 k=$3 end=$4 tm="$WL/ft16_tp8_crosspod.cm"
  [ -f "$OUT/sinks/$name.csv" ] && return 0
  local w; w=$(mktemp -d "${TMPDIR:-/tmp}/e1s.XXXXXX")
  local cmd; cmd=$(base_args "$cc" default | sed "s/-end 2000000/-end $end/")
  [ "$cc" = dcqcn ] && cmd=$(echo "$cmd" | sed "s/-dcqcn 100/-dcqcn $k/")
  ( cd "$w" && $cmd -log sink -logtime 0.01 -tm "$tm" -o "$w/s.dat" > "$OUT/sinks/$name.log" 2>&1 &&
    "$PARSE_OUTPUT" s.dat -ascii > s.txt 2>/dev/null &&
    python3 - "$w/idmap.txt" "$w/s.txt" "$OUT/sinks/$name.csv" <<'EOF'
import sys, csv
ids = {}
for line in open(sys.argv[1]):
    p = line.split()
    if len(p) >= 2: ids[p[0]] = p[1]
prev = {}
with open(sys.argv[3], "w", newline="") as f:
    w = csv.writer(f); w.writerow(["t_us", "flow", "cack_pkts", "goodput_gbps"])
    for line in open(sys.argv[2]):
        p = line.split()
        if "ROCE_SINK" not in p: continue
        t = float(p[0]) * 1e6; sid = p[p.index("ID") + 1]; ca = int(p[p.index("CAck") + 1])
        name = ids.get(sid, sid).replace("Roce_sink_", "")
        t0, c0 = prev.get(name, (0.0, 0))
        g = (ca - c0) * 4000 * 8 / ((t - t0) * 1e-6) / 1e9 if t > t0 else 0
        prev[name] = (t, ca)
        w.writerow([f"{t:.1f}", name, ca, f"{g:.1f}"])
EOF
  )
  rm -rf "$w"
  echo "sinks $name"
}
sinks() {
  if [ ! -x "$PARSE_OUTPUT" ]; then
    g++ -O2 -std=c++17 -I"$SIM" "$SIM/parse_output.cpp" "$SIM/$BUILD_DIR/libhtsim.a" -o "$PARSE_OUTPUT" || return 1
  fi
  pool sinks_one roce_default_tp8 roce 0 10400
  pool sinks_one dcqcn_k100_default_tp8 dcqcn 100 20000
  pool sinks_one dcqcn_k25_default_tp8 dcqcn 25 20000
  pool sinks_one dcqcn_k400_default_tp8 dcqcn 400 20000
  wait
}

# ---------------------------------------------------------------- queues
# Switch output-queue occupancy every 10 us (-log tor_upqueue: despite the
# name it attaches a QueueLoggerSampling to every queue, main_roce.cpp:386-389).
# Reduced to queues/<name>.txt: per queue mean and max occupancy in 4064 B
# packets and the fraction of samples with an empty queue, up to the time the
# last flow finished. Only the busiest queues are kept.
queues_one() {
  local name=$1 cc=$2 k=$3 end=$4 tm="$WL/ft16_tp8_crosspod.cm"
  [ -f "$OUT/queues/$name.txt" ] && return 0
  local w; w=$(mktemp -d "${TMPDIR:-/tmp}/e1q.XXXXXX")
  local cmd; cmd=$(base_args "$cc" default | sed "s/-end 2000000/-end $end/")
  [ "$cc" = dcqcn ] && cmd=$(echo "$cmd" | sed "s/-dcqcn 100/-dcqcn $k/")
  ( cd "$w" && $cmd -log tor_upqueue -tm "$tm" -o "$w/q.dat" > "$w/log" 2>&1 &&
    "$PARSE_OUTPUT" q.dat -ascii > q.txt 2>/dev/null &&
    python3 - "$w/idmap.txt" "$w/q.txt" "$w/log" "$OUT/queues/$name.txt" "$name" <<'EOF'
import sys, re, collections
ids = {}
for line in open(sys.argv[1]):
    p = line.split()
    if len(p) >= 2: ids[p[0]] = p[1]
tend = max(float(m.group(1)) for m in re.finditer(r"finished at ([0-9.]+)", open(sys.argv[3]).read()))
acc = collections.defaultdict(lambda: [0, 0.0, 0, 0])   # n, sum lastq, max, empty
for line in open(sys.argv[2]):
    if " Ev RANGE " not in line: continue
    p = line.split(); t = float(p[0]) * 1e6
    if t > tend: continue
    q = ids.get(p[p.index("ID") + 1], p[p.index("ID") + 1])
    last = int(p[p.index("LastQ") + 1]) / 4064; mx = int(p[p.index("MaxQ") + 1]) / 4064
    a = acc[q]; a[0] += 1; a[1] += last; a[2] = max(a[2], mx); a[3] += last == 0
rows = sorted(((a[1] / a[0], a[2], a[3] / a[0], q) for q, a in acc.items() if a[0]), reverse=True)
with open(sys.argv[4], "w") as f:
    f.write(f"# {sys.argv[5]}: switch queue occupancy, 10 us samples up to last flow finish {tend:.1f} us\n")
    f.write("# queue  mean_pkts  max_pkts  frac_samples_empty\n")
    for mean, mx, emp, q in rows[:16]:
        f.write(f"{q:<22} {mean:8.1f} {mx:8.1f} {emp:6.2f}\n")
EOF
  )
  rm -rf "$w"
  echo "queues $name"
}
# EQDS spraying balance: per-link mean busy fraction (-log queue_usage, text
# logger) for the full TP=8 matrix at -paths 16 and 256, reduced with
# e1_summary.py --links. Window is fixed at 4300 us for both.
eqds_links_one() {
  local p=$1 f="$OUT/queues/eqds_default_tp8_p$1.links"
  [ -f "$f" ] && return 0
  local cmd; cmd=$(base_args eqds default | sed "s/-end 2000000/-end 4300/; s/-paths 16/-paths $p/")
  in_scratch $cmd -log queue_usage -tm "$WL/ft16_tp8_crosspod.cm" > "$f.log" 2>&1
  python3 "$HERE/scripts/e1_summary.py" --links "$f.log" > "$f" && rm -f "$f.log"
  echo "queues eqds p$p"
}
queues() {
  mkdir -p "$OUT/queues"
  if [ ! -x "$PARSE_OUTPUT" ]; then
    g++ -O2 -std=c++17 -I"$SIM" "$SIM/parse_output.cpp" "$SIM/$BUILD_DIR/libhtsim.a" -o "$PARSE_OUTPUT" || return 1
  fi
  pool queues_one roce_default_tp8 roce 0 10400
  pool queues_one dcqcn_k100_default_tp8 dcqcn 100 12200
  pool queues_one dcqcn_k25_default_tp8 dcqcn 25 12000
  pool eqds_links_one 16
  pool eqds_links_one 256
  wait
}

# Parse any finished table/sweep log that has no CSV (e.g. run.sh was edited
# underneath a running job and died after the simulation completed).
reparse() {
  local log tag pat topo tm g
  for log in "$OUT"/*_e1_*_r*.log "$OUT"/dcqcn_sens_*.log; do
    [ -f "$log" ] || continue
    tag=$(basename "$log" .log)
    [ -f "$OUT/$tag.csv" ] && continue
    grep -q '^Done' "$log" || { echo "reparse: $tag did not finish"; continue; }
    case "$tag" in
      dcqcn_sens_*) tm="$WL/ft16_tp8_crosspod.cm"; g=400 ;;
      *) topo=$(echo "$tag" | sed -E 's/^[a-z]+_(default|ft128_[0-9]os)_e1_.*/\1/')
         pat=$(echo "$tag" | sed -E 's/.*_e1_(.*)_r[0-9]+$/\1/')
         tm=$(matrix "$topo" "$pat"); g=$(gbps "$topo") ;;
    esac
    echo "reparse $tag: $(parse "$log" "$OUT/$tag.csv" "$tag" "$tm" "$g")"
  done
}

summary() { reparse; python3 "$HERE/scripts/e1_summary.py" "$OUT"; }

case "$WHAT" in
  all)     table; sweep; ctl; seeds; eqdsx; trace; sinks; queues; summary ;;
  table|sweep|ctl|seeds|eqdsx|trace|sinks|queues|summary) "$WHAT" ;;
  *) echo "usage: $0 [all|table|sweep|ctl|seeds|eqdsx|trace|sinks|queues|summary]"; exit 1 ;;
esac
