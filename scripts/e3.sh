#!/usr/bin/env bash
# E3: congestion-control sweep at 30/50/70/90% offered load, then plot.
#
#   [POLICIES="roce eqds"] [HTSIM_BUILD=build_start] ./scripts/e3.sh <ft16|ft128_1os> [reps] [-j N] [--dry-run]
#
#   ft16       16-node default fat tree (400 G), workloads/e3_load{30,50,70,90}_ft16.cm
#              (100 ms arrival window). END default 600000 us.
#   ft128_1os  128-node 1:1 fat tree (100 G hosts), workloads/e3_load{30,50,70,90}_ft128.cm
#              (50 ms window). END default 800000 us. Run as topology
#              fat_tree_128_1os, i.e. -q 2000 (see run_topo in explib.sh:
#              -q 500 loses PFC losslessness on 4-host ToRs); tags are
#              <cc>_fat_tree_128_1os_load<L>_r<i>..._s<i>.
#
#   POLICIES   space-separated subset of "dcqcn hpcc roce eqds" (default all
#              four). Every policy is preflighted with a 1 MB flow starting
#              at 1 ms; one that fails (rejects -topo, ignores 'start') is
#              dropped with a message.
#   HTSIM_BUILD, STRAT, PATHS, END   passed through to run.sh. Each policy can
#              come from a different build: run the script once per build with
#              the matching POLICIES; results accumulate in one directory.
#   OUTDIR     default results/e3/<ft16|ft128_1os>
#   reps       repetitions per (policy, load), default 3. Rep i runs with
#              SEED=i, so tags are <cc>_<topo>_load<L>_r<i>[_<strat>][_p<paths>]_s<i>.
#   -j N       parallel runs, default 6 (each run is single threaded).
#   --dry-run  print the job list and exit.
#
# Resumable: a run whose <tag>_summary.json exists is skipped. A refused run
# leaves no summary and is retried next time. A run that does not finish
# every flow before END is rerun with END doubled (scripts/explib.sh).
#
# Output: per-run CSVs and summaries in OUTDIR; OUTDIR/e3_table.{md,csv};
# results/figs/e3_<which>[<suffix>]_load_vs_floor.{png,pdf} and
# results/figs/e3_<which>[<suffix>]_cdf_load<L>.{png,pdf} (plot.py), and
# results/figs/e3_<which>[<suffix>]_vs_ideal.{png,pdf}: p50/p99 against the
# fluid ideal-fabric reference of scripts/exp_report.py.
#
# Example (the dcqcn / hpcc half, from their own builds):
#   POLICIES=dcqcn HTSIM_BUILD=build_dcqcn ./scripts/e3.sh ft16 3
#   POLICIES=hpcc  HTSIM_BUILD=build_hpcc  ./scripts/e3.sh ft16 3
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=explib.sh
source "$HERE/scripts/explib.sh"
J=6; REPS=3; DRY=0; WHICH=""
while [ $# -gt 0 ]; do
  case "$1" in
    -j) J=$2; shift 2 ;;
    -j*) J=${1#-j}; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) if [ -z "$WHICH" ]; then WHICH=$1; else REPS=$1; fi; shift ;;
  esac
done

case "$WHICH" in
  ft16)      TOPO=default;   MSUF=ft16;  PN=16;  DEF_END=600000 ;;
  ft128_1os) TOPO=$(run_topo ft128_1os); MSUF=ft128; PN=128; DEF_END=800000 ;;
  *) echo "usage: $0 <ft16|ft128_1os> [reps] [-j N] [--dry-run]"; exit 2 ;;
esac
POLICIES="${POLICIES:-dcqcn hpcc roce eqds}"
END="${END:-$DEF_END}"
LOADS="90 70 50 30"
[ -n "${HTSIM_BUILD:-}" ] && export HTSIM_BUILD

OUTDIR="${OUTDIR:-$HERE/results/e3/$WHICH}"
mkdir -p "$OUTDIR"; OUTDIR="$(cd "$OUTDIR" && pwd)"; export OUTDIR
exp_suffix

for L in $LOADS; do
  [ -f "$HERE/workloads/e3_load${L}_${MSUF}.cm" ] || { echo "missing workloads/e3_load${L}_${MSUF}.cm"; exit 1; }
done

if [ "$DRY" != 1 ]; then
  # shellcheck disable=SC2086
  POLICIES=$(exp_probe "$TOPO" "$PN" $POLICIES)
  [ -n "$POLICIES" ] || { echo "no policy passed preflight"; exit 1; }
fi

# --- job list: dcqcn first, high load first (longest runs start first) ----
JOBS=()
for CC in dcqcn hpcc roce eqds; do
  case " $POLICIES " in *" $CC "*) ;; *) continue ;; esac
  for L in $LOADS; do
    for i in $(seq 1 "$REPS"); do
      TAG="${CC}_${TOPO}_load${L}_r${i}${SUFFIX}_s${i}"
      [ -f "$OUTDIR/${TAG}_summary.json" ] && continue
      JOBS+=("$CC $TOPO load${L}_r${i} $HERE/workloads/e3_load${L}_${MSUF}.cm $i $END $TAG")
    done
  done
done

echo "e3 $WHICH: policies [$POLICIES]  loads [$LOADS]  reps $REPS  END $END"
exp_run_jobs
[ "$DRY" = 1 ] && exit 0

# --- tables and figures -------------------------------------------------
FIGTAG="${WHICH}${SUFFIX}"
# exact match on the tag so other variants (STRAT etc.) and test runs stay out
mapfile -t FILES < <(cd "$OUTDIR" && ls | grep -E "^[a-z]+_${TOPO}_load[0-9]+_r[0-9]+${SUFFIX}_s[0-9]+_summary\.json$" | sed "s|^|$OUTDIR/|")
if [ ${#FILES[@]} -eq 0 ]; then echo "no summaries to plot"; exit 1; fi
python3 "$HERE/scripts/exp_report.py" e3 --out "$OUTDIR/e3_table" --fig "e3_${FIGTAG}_vs_ideal" "${FILES[@]}"
python3 "$HERE/scripts/plot.py" load --topo "$TOPO" --out "e3_${FIGTAG}_load_vs_floor" "${FILES[@]}"
for L in $LOADS; do
  python3 "$HERE/scripts/plot.py" cdf --topo "$TOPO" --load "$L" --out "e3_${FIGTAG}_cdf_load${L}" "${FILES[@]}" 2>/dev/null || true
done
