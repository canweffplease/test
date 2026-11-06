#!/usr/bin/env bash
# E3: run every congestion control policy on one workload.
#
#   ./scripts/sweep.sh <traffic-matrix> [topo-label] [load-label]
#
# Example:
#   ./scripts/sweep.sh workloads/kv_llama70b_4k_tp8.cm default load100
set -euo pipefail
TM=${1:?path to .cm traffic matrix}
TOPO=${2:-default}
LOAD=${3:-load100}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "E3 sweep on $(basename "$TM")  topo=$TOPO  load=$LOAD"
echo "note: dcqcn takes ~90s, the rest are ~1s"
echo
for CC in hpcc eqds roce dcqcn; do
  "$HERE/scripts/run.sh" "$CC" "$TOPO" "$LOAD" "$TM" || echo "  $CC FAILED, continuing"
  echo
done

echo "=== summary ==="
python3 - "$HERE/results" "$TOPO" "$LOAD" <<'PYEOF'
import csv, glob, os, sys
d, topo, load = sys.argv[1], sys.argv[2], sys.argv[3]
print(f"{'policy':<8}{'last shard us':>15}{'floor us':>12}{'x floor':>10}")
for cc in ("hpcc","eqds","roce","dcqcn"):
    p = os.path.join(d, f"{cc}_{topo}_{load}.csv")
    if not os.path.exists(p):
        print(f"{cc:<8}{'(no result)':>15}"); continue
    rows = list(csv.DictReader(open(p)))
    last = max(float(r["completion_us"]) for r in rows)
    floor = float(rows[0]["floor_us"])
    print(f"{cc:<8}{last:>15.1f}{floor:>12.1f}{last/floor:>10.2f}")
PYEOF
