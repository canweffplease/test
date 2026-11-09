#!/usr/bin/env bash
# Congestion-control convergence validation (docs/VALIDATION.md).
#
#   ./scripts/validation.sh            # all four transports, seeds 1-5, then analyze
#   SEEDS="1" ./scripts/validation.sh  # one seed only (quick look)
#   ANALYZE_ONLY=1 ./scripts/validation.sh
#
#   # final transports: per-policy build dirs, canonical picosecond matrix
#   POLICIES="hpcc hpccpfc eqds roce" HTSIM_BUILD_hpcc=build_hpccfix \
#     HTSIM_BUILD_eqds=build_eqds2 HTSIM_BUILD_roce=build_start ./scripts/validation.sh
#
# Selection (all optional):
#   POLICIES (or CCS)  policies to run, default "dcqcn hpcc eqds roce hpccpfc"
#   HTSIM_BUILD_<cc>   build dir for one policy (hpccpfc uses HTSIM_BUILD_hpcc,
#                      dcqcn falls back to HTSIM_BUILD_roce); else HTSIM_BUILD;
#                      else "build" (the legacy binaries).
#   START_UNITS        ps | us | trigger | probe. Default: "ps" (the canonical
#                      matrix, no workaround) for any policy whose build dir was
#                      given explicitly, "probe" (the legacy workarounds below)
#                      for the default "build". The final binaries all honour
#                      picosecond starts.
#   VAL_OUT            output dir, default results/validation
#   FIGDIR             figure dir, default results/figs
#
# Workload: workloads/validation/converge_stagger.cm. Four flows into host 8
# (from hosts 0, 4, 12, 2) joining at 0, 2, 4, 6 ms, sized so that under
# ideal max-min sharing they leave last-in-first-out at 16, 18, 20, 22 ms.
# The only shared bottleneck is the receiver's 400 Gbps ToR downlink.
#
# Arguments mirror scripts/run.sh for the default topology exactly
# (COMMON + per-policy ARGS), plus logging:
#   -log sink            per-flow cumulative ACK, sampled every -logtime
#   -log tor_downqueue   attaches a 10 us sampling queue logger to every queue
#   -logtime 0.01        10 us sink samples
#   -o <file>            HTSIM binary logfile, decoded by validation_analyze.py
#
# FLOW START TIMES. The matrix parser stores "start" as picoseconds
# (connection_matrix.cpp:755), but in the built binaries
#   RoceSrc::connect  (roce.cpp:134)  does timeFromUs(starttime)  -> treats it as us
#   EqdsSrc::connect  (eqds.cpp:432)  does timeFromUs(starttime)  -> treats it as us
#   HPCCSrc::connect  (hpcc.cpp:145-147) calls startflow() at once -> ignores it
# So each binary is probed first and fed the matrix in the units it actually
# honours: "ps" (the canonical file), "us" (starts divided by 1e6), or
# "trigger" (HPCC: flows 2-4 are started by HTSIM triggers fired when a ~2 ms
# "timer" flow on an otherwise unused host pair, 14->15, finishes; its size
# is calibrated by a solo run). Actual start times are always read back from
# the run log and used by the analysis, never the nominal ones.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIMDIR="$HERE/HTSIM/htsim/sim"
OUT="${VAL_OUT:-$HERE/results/validation}"
FIGDIR="${FIGDIR:-$HERE/results/figs}"
# build dir for a policy, and whether it was chosen explicitly
build_var() {  # cc -> name of the env var that selects its build, or empty
  local c=$1 v
  case "$c" in hpccpfc) c=hpcc ;; esac
  for v in "HTSIM_BUILD_$c" $([ "$c" = dcqcn ] && echo HTSIM_BUILD_roce) HTSIM_BUILD; do
    [ -n "${!v:-}" ] && { echo "$v"; return; }
  done
}
bin_of() { local v b=build; v=$(build_var "$1"); [ -n "$v" ] && b="${!v}"; echo "$SIMDIR/$b/datacenter"; }
TM="$HERE/workloads/validation/converge_stagger.cm"
SEEDS="${SEEDS:-1 2 3 4 5}"
END_US="${END_US:-50000}"
JOBS="${JOBS:-20}"
mkdir -p "$OUT/matrices" "$OUT/raw"

COMMON="-nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -end $END_US -q 500"
LOGARGS="-log sink -log tor_downqueue -logtime 0.01"

declare -A EXE ARGS
# DCQCN marking as run.sh passes it for the final build (probabilistic RED
# between Kmin = 1 us of queueing at 400 Gb/s = 50 KB and Kmax = 400 KB, Pmax
# 1%, HTSIM/OUR_CHANGES.md (section "DCQCN switch marking: probabilistic (RED) ECN")), only when the dcqcn build is chosen
# explicitly (the legacy binary has no -ecn_* flags) and ECN_STEP is unset.
DCQCN_ECN=""
[ -n "$(build_var dcqcn)" ] && [ -z "${ECN_STEP:-}" ] && DCQCN_ECN="-ecn_kmin 50 -ecn_kmax 400 -ecn_pmax 0.01"
EXE[dcqcn]="$(bin_of dcqcn)/htsim_roce"; ARGS[dcqcn]="-dcqcn 100 $DCQCN_ECN -pfc_thresholds 80 150"
EXE[roce]="$(bin_of roce)/htsim_roce";  ARGS[roce]="-pfc_thresholds 80 150"
EXE[hpcc]="$(bin_of hpcc)/htsim_hpcc";  ARGS[hpcc]=""
EXE[eqds]="$(bin_of eqds)/htsim_eqds";  ARGS[eqds]=""
# Sensitivity variant: HPCC with RoCE's PFC thresholds instead of the binary
# default (12/15 packets). Not part of run.sh.
EXE[hpccpfc]="$(bin_of hpccpfc)/htsim_hpcc"; ARGS[hpccpfc]="-pfc_thresholds 80 150"
CCS="${POLICIES:-${CCS:-dcqcn hpcc eqds roce hpccpfc}}"

# Logged start time (us) of the flow src->dst in a log, or empty.
logged_start() {  # log src dst
  grep -E "(startflow [A-Za-z]+_$2_$3 at |Flow [A-Za-z]+_$2_$3 flowId [0-9]+ .*starting at )" "$1" \
    | head -1 | sed -E 's/.* at ([-0-9.e+]+).*/\1/'
}

# Which start-time units does this binary honour?  echo ps | us | trigger
probe_units() {  # cc
  local cc=$1 d; d=$(mktemp -d "${TMPDIR:-/tmp}/val_probe.XXXXXX")
  printf 'Nodes 16\nConnections 2\n0->8 id 1 start 0 size 400000\n4->8 id 2 start 50000000 size 400000\n' > "$d/ps.cm"
  printf 'Nodes 16\nConnections 2\n0->8 id 1 start 0 size 400000\n4->8 id 2 start 50 size 400000\n' > "$d/us.cm"
  (cd "$d" && ${EXE[$cc]} $COMMON ${ARGS[$cc]} -end 1000 -tm ps.cm > ps.log 2>&1 || true)
  (cd "$d" && ${EXE[$cc]} $COMMON ${ARGS[$cc]} -end 1000 -tm us.cm > us.log 2>&1 || true)
  local sps sus; sps=$(logged_start "$d/ps.log" 4 8); sus=$(logged_start "$d/us.log" 4 8)
  rm -rf "$d"
  if [ -n "$sps" ] && awk -v s="$sps" 'BEGIN{exit !(s>49 && s<51)}'; then echo ps
  elif [ -n "$sus" ] && awk -v s="$sus" 'BEGIN{exit !(s>49 && s<51)}'; then echo us
  else echo trigger; fi
}

# Solo FCT (us) of a 50 MB flow 14->15, to size the HPCC timer flows.
timer_size() {  # cc -> bytes for a ~2000 us timer flow
  local cc=$1 d fct; d=$(mktemp -d "${TMPDIR:-/tmp}/val_cal.XXXXXX")
  printf 'Nodes 16\nConnections 1\n14->15 id 1 start 0 size 50000000\n' > "$d/cal.cm"
  (cd "$d" && ${EXE[$cc]} $COMMON ${ARGS[$cc]} -end 20000 -tm cal.cm > cal.log 2>&1 || true)
  fct=$(grep -E "Flow [A-Za-z]+_14_15 .*finished at" "$d/cal.log" | sed -E 's/.*finished at ([0-9.e+]+).*/\1/')
  rm -rf "$d"
  awk -v f="$fct" 'BEGIN{printf "%d\n", 50000000*2000.0/f}'
}

make_matrix() {  # cc units -> path
  local cc=$1 units=$2 m="$OUT/matrices/converge_stagger_${1}.cm"
  case "$units" in
    ps) cp "$TM" "$m" ;;
    us) awk '{for(i=1;i<=NF;i++) if($i=="start"){$(i+1)=$(i+1)/1e6} print}' "$TM" > "$m" ;;
    trigger)
      local ts; ts=$(timer_size "$cc")
      awk -v ts="$ts" '
        $1=="Connections" {next}
        /->/ { n++; line[n]=$0; next }
        { hdr = hdr $0 "\n" }
        END {
          printf "%sConnections %d\nTriggers %d\n", hdr, 2*n-1, n-1
          for (k=1; k<=n; k++) {
            l=line[k]
            if (k>1) sub(/start [0-9.e+]+/, "trigger " (k-1), l)
            print l
            if (k<n) {
              t = "14->15 id " (100+k) " " (k==1 ? "start 0" : "trigger " (k-1)) " size " ts " send_done_trigger " k
              print t
            }
          }
          for (k=1; k<n; k++) print "trigger id " k " oneshot"
        }' "$TM" > "$m" ;;
  esac
  echo "$m"
}

run_one() {  # cc seed matrix
  local cc=$1 seed=$2 m=$3 tag="${1}_s${2}" w
  w=$(mktemp -d "${TMPDIR:-/tmp}/val_run.XXXXXX")
  local t0; t0=$(date +%s)
  (cd "$w" && ${EXE[$cc]} $COMMON -seed "$seed" ${ARGS[$cc]} $LOGARGS -o run.dat \
      -tm "$m" > run.log 2>&1 || true)
  # HPCC prints its window on every ACK ("CWND1/CWND2"); keep the full log
  # compressed under raw/ and a filtered copy (no per-ACK lines) beside it.
  grep -v -E '^CWND[12] ' "$w/run.log" > "$OUT/${tag}.log" || true
  gzip -c "$w/run.log" > "$OUT/raw/${tag}.log.gz"
  gzip -c "$w/run.dat" > "$OUT/raw/${tag}.dat.gz"
  gzip -c "$w/idmap.txt" > "$OUT/raw/${tag}.idmap.gz"
  rm -rf "$w"
  local v; v=$(grep -c "LOSSLESS not working" "$OUT/${tag}.log" || true)
  echo "  $tag done in $(( $(date +%s)-t0 ))s, lossless violations: $v"
}

if [ -z "${ANALYZE_ONLY:-}" ]; then
  for cc in $CCS; do
    [ -x "${EXE[$cc]}" ] || { echo "missing ${EXE[$cc]} - run ./setup.sh"; exit 1; }
  done
  declare -A MAT
  : > "$OUT/start_units.txt"
  for cc in $CCS; do
    u="${START_UNITS:-}"
    if [ -z "$u" ]; then [ -n "$(build_var "$cc")" ] && u=ps || u=probe; fi
    [ "$u" = probe ] && u=$(probe_units "$cc")
    MAT[$cc]=$(make_matrix "$cc" "$u")
    echo "$cc $u ${MAT[$cc]#$HERE/} ${EXE[$cc]#$HERE/}" | tee -a "$OUT/start_units.txt"
  done
  echo "running: $CCS x seeds $SEEDS (up to $JOBS in parallel; DCQCN takes minutes)"
  for cc in $CCS; do
    for s in $SEEDS; do
      while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
      run_one "$cc" "$s" "${MAT[$cc]}" &
    done
  done
  wait
fi

python3 "$HERE/scripts/validation_analyze.py" "$OUT" "$TM" "$FIGDIR"
