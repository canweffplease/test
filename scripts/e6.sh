#!/usr/bin/env bash
# E6: communication pattern. K-to-1 incast of KV shards vs the TP=8
# permutation reference, 16-node default fat tree, 400 Gbps.
#
#   [POLICIES="roce"] [HTSIM_BUILD=build_start] ./scripts/e6.sh [reps] [-j N] [--dry-run]
#
# Matrices (all flows 167,772,160 B, all start at t = 0):
#   workloads/e6_incast{2,4,8}_ft16.cm   K senders (hosts 0..K-1) -> host 8
#   workloads/kv_llama70b_4k_tp8.cm      8 flows i -> i+8, no shared endpoint
#                                        (the non-incast reference)
# Tags: <cc>_default_incast<K>_r<i>[_<strat>][_p<paths>]_s<i> and
#       <cc>_default_tp8_r<i>..._s<i>.
#
#   POLICIES   subset of "dcqcn hpcc roce eqds" (default all four), each
#              preflighted on the default topology.
#   HTSIM_BUILD, STRAT, PATHS, END   passed to run.sh. END default 300000 us.
#   OUTDIR     default results/e6
#   reps       seeds 1..reps (default 3)
#
# Resumable (existing summaries are skipped). Output: OUTDIR/e6_table.{md,csv},
# OUTDIR/e6_order.csv (per-flow finish order inside each incast),
# results/figs/e6_incast.{png,pdf}.
#
# Example (the other policies, from their own builds):
#   POLICIES=dcqcn HTSIM_BUILD=build_dcqcn ./scripts/e6.sh 3
#   POLICIES=hpcc  HTSIM_BUILD=build_hpcc  ./scripts/e6.sh 3
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=explib.sh
source "$HERE/scripts/explib.sh"
J=6; REPS=3; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    -j) J=$2; shift 2 ;;
    -j*) J=${1#-j}; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) REPS=$1; shift ;;
  esac
done
POLICIES="${POLICIES:-dcqcn hpcc roce eqds}"
END="${END:-300000}"
[ -n "${HTSIM_BUILD:-}" ] && export HTSIM_BUILD
OUTDIR="${OUTDIR:-$HERE/results/e6}"
mkdir -p "$OUTDIR"; OUTDIR="$(cd "$OUTDIR" && pwd)"; export OUTDIR
exp_suffix

declare -A MAT=(
  [incast8]="$HERE/workloads/e6_incast8_ft16.cm"
  [incast4]="$HERE/workloads/e6_incast4_ft16.cm"
  [incast2]="$HERE/workloads/e6_incast2_ft16.cm"
  [tp8]="$HERE/workloads/kv_llama70b_4k_tp8.cm"
)
for m in "${MAT[@]}"; do [ -f "$m" ] || { echo "missing $m"; exit 1; }; done

if [ "$DRY" != 1 ]; then
  # shellcheck disable=SC2086
  POLICIES=$(exp_probe default 16 $POLICIES)
  [ -n "$POLICIES" ] || { echo "no policy passed preflight"; exit 1; }
fi

JOBS=()
for CC in dcqcn hpcc roce eqds; do
  case " $POLICIES " in *" $CC "*) ;; *) continue ;; esac
  for M in incast8 incast4 incast2 tp8; do
    for i in $(seq 1 "$REPS"); do
      TAG="${CC}_default_${M}_r${i}${SUFFIX}_s${i}"
      [ -f "$OUTDIR/${TAG}_summary.json" ] && continue
      JOBS+=("$CC default ${M}_r${i} ${MAT[$M]} $i $END $TAG")
    done
  done
done

echo "e6: policies [$POLICIES]  reps $REPS  END $END"
exp_run_jobs
[ "$DRY" = 1 ] && exit 0

mapfile -t FILES < <(cd "$OUTDIR" && ls | grep -E "^[a-z]+_default_(incast[0-9]+|tp8)_r[0-9]+${SUFFIX}_s[0-9]+_summary\.json$" | sed "s|^|$OUTDIR/|")
[ ${#FILES[@]} -gt 0 ] || { echo "no summaries"; exit 1; }
python3 "$HERE/scripts/exp_report.py" e6 --out "$OUTDIR/e6_table" --fig "e6_incast${SUFFIX}" "${FILES[@]}"
