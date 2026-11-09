#!/usr/bin/env bash
# Rail-optimized placement / topology comparison (docs/TOPOLOGY_RAIL.md).
#
#   ./scripts/rail.sh            run every missing job, then summarise
#   ./scripts/rail.sh summary    summarise only
#   DRY=1 ./scripts/rail.sh      print the job list
#
# RoCE without CC (htsim_roce from HTSIM_BUILD, default build_start), ECMP
# per-flow hashing (-strat ecmp_host), seeds 1..5, one 4K Llama-3-70B TP=8
# KV transfer (8 x 167,772,160 B shards, all at t=0) or a 4-to-1 incast of
# four of them. Every run goes through scripts/run.sh, so every topology is
# passed by FILE name and gets run.sh's generic-branch buffer -q 2000: the
# fat trees and the rail fabrics are compared with matched buffers.
#
# Topologies (HTSIM/htsim/sim/datacenter/topologies/):
#   fat_tree_128_1os   3-tier, 4 hosts/ToR, 16 hosts/pod, 1:1
#   fat_tree_128_4os   same, 4:1 at the agg->core tier
#   rail_128_1os       2-tier, 8 leaves of 16 hosts + 16 spines, 1:1  (NEW)
#   rail_128_16os      same leaves, 1 spine, 16:1 (rail-only stand-in) (NEW)
#
# Matrices (host ids; on the rail fabrics host = 16*gpu + node is "rail
# labelling", host = 8*node + gpu is "conventional labelling"):
#   conv      workloads/kv128_conv.cm            i -> 64+i
#   samepod   workloads/e1/ft128_tp8_samepod.cm  i -> 8+i
#   railpod   workloads/kv128_rail.cm            16i -> 16i+8
#   railtor   workloads/rail/ft128_tp8_rail_tor.cm  4i -> 4i+2
#   rail      = railpod (on the rail fabric this is rail-local)
#   incast4rail / incast4conv  workloads/rail/rail128_incast4_{rail,split}.cm
#
# Results: results/rail/<cc>_<topo>_<label>_s<seed>.{log,csv,...} and
# results/rail/summary.csv. Logs are kept (they are ~100 KB each).
#
# Policies (default: roce only, as originally):
#   POLICIES="roce hpcc eqds" ./scripts/rail.sh
# Build dir per policy: HTSIM_BUILD_<cc> if set, else HTSIM_BUILD if set,
# else roce=build_start, hpcc=build_hpccfix, eqds=build_eqds2, dcqcn=build (the
# rebuilt main build: paper-faithful DCQCN; run.sh adds RED ECN marking).
# HPCC's base RTT T (-hpcc_T via run.sh's HPCC_T) is set per topology unless
# HPCC_T is given: 16.4 us on the 3-tier fat_tree_128_* (12 us propagation +
# 6 x 0.725 us serialisation of 9,064 B at 100 Gb/s), 10.9 us on the 2-tier
# rail_128_* (8 + 4 x 0.725). run.sh only sets it for the ft128_* names, and
# rail.sh passes topologies by file name.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export OUTDIR="$HERE/results/rail"
POLICIES="${POLICIES:-roce}"
declare -A DEFBUILD=([roce]=build_start [dcqcn]=build [hpcc]=build_hpccfix [eqds]=build_eqds2)
build_of() { local v="HTSIM_BUILD_$1"; echo "${!v:-${HTSIM_BUILD:-${DEFBUILD[$1]}}}"; }
J="${J:-4}"
SEEDS="${SEEDS:-1 2 3 4 5}"
mkdir -p "$OUTDIR"

W="$HERE/workloads"
declare -A M=(
  [conv]="$W/kv128_conv.cm"
  [samepod]="$W/e1/ft128_tp8_samepod.cm"
  [railpod]="$W/kv128_rail.cm"
  [railtor]="$W/rail/ft128_tp8_rail_tor.cm"
  [rail]="$W/kv128_rail.cm"
  [incast4rail]="$W/rail/rail128_incast4_rail.cm"
  [incast4conv]="$W/rail/rail128_incast4_split.cm"
)
PLAN=(
  "fat_tree_128_1os conv samepod railpod railtor"
  "fat_tree_128_4os conv samepod railpod railtor"
  "rail_128_1os conv rail incast4conv incast4rail"
  "rail_128_16os conv rail incast4rail"
)

# Regenerate the generated matrices if missing (deterministic).
gen() {
  [ -f "$W/rail/ft128_tp8_rail_tor.cm" ] || python3 "$HERE/scripts/gen_workload.py" \
    --topo ft128_1os --model llama3-70b --tp 8 --pattern same_tor --placement rail \
    --rail-domain tor --arrival fixed --requests 1 --prompt fixed:4096 \
    --out "$W/rail/ft128_tp8_rail_tor.cm" >/dev/null
  for pl in rail split; do
    [ -f "$W/rail/rail128_incast4_$pl.cm" ] || python3 "$HERE/scripts/gen_workload.py" \
      --topo rail_128_1os --model llama3-70b --tp 8 --pattern incast --incast 4 \
      --placement $pl --arrival fixed --requests 4 --interval-us 0 --prompt fixed:4096 \
      --out "$W/rail/rail128_incast4_$pl.cm" >/dev/null
  done
}

jobs_list() {
  for cc in $POLICIES; do
  for row in "${PLAN[@]}"; do
    set -- $row; topo=$1; shift
    for lab in "$@"; do
      for s in $SEEDS; do
        tag="${cc}_${topo}_${lab}_s${s}"
        [ -f "$OUTDIR/${tag}_summary.json" ] && continue
        echo "$cc $(build_of $cc) $topo $lab ${M[$lab]} $s"
      done
    done
  done
  done
}

run_one() {  # cc build topo label matrix seed
  local t="${HPCC_T:-}"
  if [ -z "$t" ]; then case "$3" in fat_tree_128_*) t=16.4 ;; rail_128_*) t=10.9 ;; esac; fi
  HTSIM_BUILD=$2 HPCC_T=$t SEED=$6 END=200000 "$HERE/scripts/run.sh" "$1" "$3" "$4" "$5" >/dev/null 2>&1 || true
  echo "done $1 $3 $4 s$6"
}
export -f run_one; export HERE

if [ "${1:-}" != "summary" ]; then
  gen
  if [ "${DRY:-0}" = 1 ]; then jobs_list; exit 0; fi
  jobs_list | xargs -P "$J" -L 1 bash -c 'run_one "$@"' _
fi

python3 - "$OUTDIR" <<'EOF'
import csv, glob, json, os, re, sys, collections
out = sys.argv[1]
# one uncontended 2-6 hop shard at 100 Gb/s, x floor (docs/E1.md, final binaries)
T1 = {"roce": 1.0163, "dcqcn": 1.0163, "hpcc": 1.057, "eqds": 1.031}
rows = []
for sj in sorted(glob.glob(f"{out}/*_s[0-9]*_summary.json")):
    tag = os.path.basename(sj)[:-len("_summary.json")]
    m = re.match(r"(roce|dcqcn|hpcc|eqds)_(.+?)_(conv|samepod|railpod|railtor|rail|incast4rail|incast4conv)_s(\d+)$", tag)
    if not m:
        continue
    cc, topo, lab, seed = m.group(1), m.group(2), m.group(3), int(m.group(4))
    log = f"{out}/{tag}.log"
    viol = sum(1 for l in open(log, errors="replace") if "LOSSLESS not working" in l)
    xs = [float(r["x_floor"]) for r in csv.DictReader(open(f"{out}/{tag}.csv"))]
    s = json.load(open(sj))
    cls = collections.Counter(round(x / T1[cc]) for x in xs)
    rtx = ""
    for l in open(log, errors="replace"):
        mm = re.match(r"^New: (\d+) Rtx: (\d+)", l)
        if mm:
            rtx = int(mm.group(2))
    rows.append(dict(cc=cc, topo=topo, variant=lab, seed=seed, flows=len(xs),
                     finished=s["n_flows_finished"], violations=viol,
                     slowest_x_floor=round(max(xs), 3), L_max=round(max(xs) / T1[cc]), rtx=rtx,
                     classes=" ".join(f"{k}x:{v}" for k, v in sorted(cls.items()))))
rows.sort(key=lambda r: (r["cc"], r["topo"], r["variant"], r["seed"]))
with open(f"{out}/summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
bad = [r for r in rows if r["violations"] or r["finished"] != r["flows"]]
print(f"{len(rows)} runs, {len(bad)} invalid (violations or unfinished flows)")
for r in bad: print("  INVALID", r)
g = collections.defaultdict(dict)
for r in rows: g[(r["cc"], r["topo"], r["variant"])][r["seed"]] = r
seeds = sorted({r["seed"] for r in rows})
print("| cc | topology | variant | " + " | ".join(f"s{s}" for s in seeds) + " | median | per-flow classes (seed order) |")
print("|---|---|---|" + "---|" * len(seeds) + "---|---|")
for (c, t, v), d in sorted(g.items()):
    xs = sorted(d[s]["slowest_x_floor"] for s in d)
    print(f"| {c} | {t} | {v} | " + " | ".join(f"{d[s]['slowest_x_floor']:.3f}" if s in d else "-" for s in seeds)
          + f" | {xs[len(xs)//2]:.3f} | " + "; ".join(d[s]["classes"] for s in seeds if s in d) + " |")
EOF
