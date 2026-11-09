# Shared job runner for scripts/e2.sh, e3.sh and e6.sh. Source it, do not run it.
#
# The caller sets HERE (repo root), OUTDIR (absolute; exported), J (parallel
# runs), DRY (1 = print the job list only), then fills the JOBS array with one
# string per run:
#
#     "<cc> <topo> <load-label> <matrix> <seed> <end_us> <tag>"
#
# and calls exp_run_jobs. <tag> must be exactly the tag run.sh will produce,
# which is <cc>_<topo>_<load-label><SUFFIX>_s<seed>; exp_suffix computes
# SUFFIX from STRAT / PATHS with run.sh's rules.
#
# Environment honoured (all passed through to run.sh):
#   POLICIES     read by the callers, not here
#   HTSIM_BUILD  build directory under HTSIM/htsim/sim (run.sh default "build")
#   STRAT, PATHS as in run.sh (non-default values change the tag)
#   END_MULT_MAX a run that finishes fewer flows than the matrix has is rerun
#                with END doubled, up to this multiple of its first END
#                (default 4). The incomplete summary is kept as
#                <tag>_summary.incomplete.json until a complete one replaces it.
#   LOG_KEEP_MB  raw logs bigger than this (default 20) are deleted after
#                parsing; the first 40 and last 60 lines go to <tag>.logtail.
#
# Per run the summary json gets extra fields (parse.py does not write them):
#   wall_s, end_us, htsim_build, log_bytes, and log_counters, the end-of-run
#   counters each binary prints: htsim_eqds "New: Rtx: RTS: Bounced:" and
#   "Stale NACKs ignored:", htsim_roce "New: Rtx:" (htsim_roce never
#   increments _new_packets_sent, so "New:" is always 0; "Rtx:" counts go-back-N
#   resends after a NACK, roce.cpp:146).
#
# Resumable: a run whose <tag>_summary.json exists is skipped by the callers.

# run_topo <alias>: the topology name handed to run.sh. The 128-node trees are
# passed by FILE name (fat_tree_128_<x>os), which run.sh's generic branch runs
# with -q 2000, instead of its ft128_1os / ft128_4os aliases (-q 500). With
# 4 hosts per ToR, a ToR or agg switch has 8 ingress ports, each allowed to
# hold up to 150 packets (+ in-flight headroom) before it sends PAUSE, so one
# egress queue can be asked to absorb ~8 x 155 = 1,240 packets: a 500-packet
# egress buffer overflows (E3/E2 RoCE runs at -q 500 logged 10^4-10^5
# "LOSSLESS not working" lines, all on ToR uplink queues), 2,000 does not.
# The 16-node tree (2 hosts/ToR, 4 ingress ports) is fine at -q 500.
# FT128_ALIAS=1 restores run.sh's aliases.
run_topo() {
  case "$1" in
    ft128_1os|ft128_4os|ft128_8os)
      if [ "${FT128_ALIAS:-0}" = 1 ]; then echo "$1"; else echo "fat_tree_128_${1#ft128_}"; fi ;;
    *) echo "$1" ;;
  esac
}

exp_suffix() {
  SUFFIX=""
  local st="${STRAT:-ecmp_host}" s
  if [ "$st" != "ecmp_host" ]; then s="${st// /}"; SUFFIX="_${s#ecmp_}"; fi
  [ "${PATHS:-16}" != "16" ] && SUFFIX="${SUFFIX}_p${PATHS}"
  return 0
}

# exp_probe <topo> <nodes> <policies...>: echo the policies whose binary runs
# on <topo> and honours the matrix 'start' field (one 1 MB flow at t = 1 ms;
# parse.py refuses a run whose logged start differs from the matrix).
exp_probe() {
  local topo=$1 pn=$2; shift 2
  local P ok="" cc
  P=$(mktemp -d "${TMPDIR:-/tmp}/exp_probe.XXXXXX")
  printf 'Nodes %s\nConnections 1\n0->%s id 1 start 1000000000 size 1000000\n' "$pn" $((pn / 2)) > "$P/probe.cm"
  for cc in "$@"; do
    if SEED= END=20000 OUTDIR="$P" timeout 600 "$HERE/scripts/run.sh" "$cc" "$topo" probe "$P/probe.cm" \
         > "$P/$cc.out" 2>&1 && [ -f "$P/${cc}_${topo}_probe${SUFFIX}_summary.json" ]; then
      ok="$ok $cc"
    else
      echo "preflight: $cc on $topo FAILED, dropping it:" >&2
      grep -m2 "REFUSING\|node mismatch\|missing binary\|unknown" "$P/$cc.out" | sed 's/^/    /' >&2
      grep -m1 "^Usage\|Unknown parameter" "$P/${cc}_${topo}_probe${SUFFIX}.log" 2>/dev/null | cut -c1-100 | sed 's/^/    /' >&2
    fi
  done
  rm -rf "${P:?}"
  echo "${ok# }"
}

_exp_post() {   # <summary> <log> <wall> <end> -> prints "complete" or "incomplete"
  python3 - "$@" <<'EOF'
import json, os, re, sys
p, log, w, end = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
s = json.load(open(p))
s["wall_s"] = w
s["end_us"] = end
s["htsim_build"] = os.environ.get("HTSIM_BUILD", "build")
c = {}
if os.path.exists(log):
    s["log_bytes"] = os.path.getsize(log)
    with open(log, errors="replace") as fh:
        for line in fh:
            m = re.search(r"New: (\d+) Rtx: (\d+)(?: RTS: (\d+) Bounced: (\d+))?", line)
            if m:
                c["new_pkts"], c["rtx_pkts"] = int(m[1]), int(m[2])
                if m[3] is not None:
                    c["rts_pkts"], c["bounced"] = int(m[3]), int(m[4])
            m = re.search(r"Stale NACKs ignored: (\d+)", line)
            if m:
                c["stale_nacks"] = int(m[1])
s["log_counters"] = c
json.dump(s, open(p, "w"), indent=1)
print("complete" if s["n_flows_finished"] == s["n_flows"] else "incomplete")
EOF
}

# exp_run_one <cc> <topo> <label> <matrix> <seed> <end> <tag>
#
# HPCC base RTT: run.sh applies HPCC_T=16.4 only when its topology argument
# matches ft128_* (run.sh:105-108), but run_topo above passes the 128-node
# trees as fat_tree_128_<x>os, which fall through to the binary default
# T = 13.1 us (the 16-node tree's RTT). So the 16.4 us default is applied
# here for those names; an explicit HPCC_T in the environment still wins.
exp_run_one() {
  local cc=$1 topo=$2 label=$3 tm=$4 seed=$5 end=$6 tag=$7
  case "$topo" in fat_tree_128_*) export HPCC_T="${HPCC_T:-16.4}" ;; esac
  local out="$OUTDIR/${tag}.out" sum="$OUTDIR/${tag}_summary.json" log="$OUTDIR/${tag}.log"
  local maxend=$(( end * ${END_MULT_MAX:-4} )) t0 rc try state wall
  rm -f "$sum"
  while :; do
    t0=$(date +%s)
    for try in 1 2; do        # run.sh may be edited concurrently: retry once
      SEED=$seed END=$end "$HERE/scripts/run.sh" "$cc" "$topo" "$label" "$tm" > "$out" 2>&1
      rc=$?
      [ $rc -eq 0 ] && [ -f "$sum" ] && break
      grep -q "REFUSING" "$out" && break    # a real refusal: do not retry
    done
    wall=$(( $(date +%s) - t0 ))
    if [ ! -f "$sum" ]; then
      echo "  FAILED $tag  ${wall}s  (see $out)  $(grep -m1 'REFUSING\|WARNING\|node mismatch' "$out")"
      break
    fi
    state=$(_exp_post "$sum" "$log" "$wall" "$end")
    if [ "$state" = complete ]; then
      echo "  done   $tag  ${wall}s  END=${end}  $(grep -o 'requests .*max [0-9.]*' "$out")"
      break
    fi
    mv -f "$sum" "$OUTDIR/${tag}_summary.incomplete.json"
    if [ $(( end * 2 )) -gt "$maxend" ]; then
      echo "  INCOMPLETE $tag  ${wall}s  END=${end} (not all flows finished; gave up at END_MULT_MAX)"
      break
    fi
    echo "  incomplete $tag at END=${end} (${wall}s), rerunning with END=$(( end * 2 ))"
    end=$(( end * 2 ))
  done
  if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt $(( ${LOG_KEEP_MB:-20} * 1024 * 1024 )) ]; then
    { head -n 40 "$log"; echo "... [log of $(stat -c %s "$log") bytes deleted after parsing] ..."; tail -n 60 "$log"; } > "$OUTDIR/${tag}.logtail"
    rm -f "$log"
  fi
  return 0
}

exp_run_jobs() {
  echo "  ${#JOBS[@]} runs to do (existing summaries skipped), -j $J, HTSIM_BUILD=${HTSIM_BUILD:-build}, results in $OUTDIR"
  if [ "$DRY" = 1 ]; then printf '  %s\n' "${JOBS[@]}"; return 0; fi
  local T0 running=0 job
  T0=$(date +%s)
  for job in "${JOBS[@]}"; do
    # shellcheck disable=SC2086
    set -- $job
    while [ "$running" -ge "$J" ]; do wait -n; running=$((running - 1)); done
    echo "  start  $7"
    exp_run_one "$@" &
    running=$((running + 1))
  done
  wait
  echo "  finished in $(( $(date +%s) - T0 ))s"
}
