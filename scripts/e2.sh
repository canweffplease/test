#!/usr/bin/env bash
# E2: topology (fat tree 1:1, 4:1, 8:1) x offered load.
#
#   [POLICIES="roce"] [HTSIM_BUILD=build_start] ./scripts/e2.sh [reps] [-j N] [--dry-run]
#
# Topologies: fat_tree_128_{1,4,8}os (128 hosts, 100 G host links), passed to
# run.sh by file name so all three run at -q 2000 (run_topo in explib.sh
# explains why -q 500 is not lossless here; FT128_ALIAS=1 uses run.sh's
# ft128_* aliases instead). Matrices, MATRICES env to override
# (names under workloads/, without .cm):
#
#   e2_load30_ft128 e2_load50_ft128 e2_load70_ft128
#       interleaved placement, every flow cross-pod, each pod sends and
#       receives 1/8 of the bytes. Effective load 1:1 / 4:1 / 8:1:
#       0.30/0.60/1.21, 0.50/1.01/2.01, 0.70/1.41/2.82. This is the load axis.
#   e3_load30_ft128 e3_load50_ft128
#       E3's split placement (prefill pods 0-3 -> decode pods 4-7), so only
#       half the pods' uplinks carry traffic: 0.30/1.21/2.42 and
#       0.50/2.01/4.03. On ft128_1os these are exactly E3 runs, so they are
#       taken from results/e3/ft128_1os when present (REUSE_E3=0 to rerun).
#
#   victim (VICTIM=1, default; VICTIM=0 skips it), PFC head-of-line
#   diagnostic on ft128_1os and ft128_4os, workloads/e2/victim_ft128.cm:
#       six congestors 0,1,2,4,5,6 -> 64,65,66,68,69,70 (pod 0 -> pod 4,
#       600 G into pod 0's 400 G of agg->core uplinks on 4:1) from t = 0, and
#       one victim 3 -> 15 at t = 1 ms that stays inside pod 0 (ToR 0 -> agg
#       -> ToR 3) and so crosses no oversubscribed link. Its ideal is the
#       floor (13,422 us). workloads/e2/victim_alone_ft128.cm is the victim
#       alone. Tags <cc>_<topo>_victim_r<i>..., <cc>_<topo>_victimalone_r<i>...
#
#   POLICIES   subset of "dcqcn hpcc roce eqds" (default all four), each
#              preflighted on each topology (a policy that fails a topology is
#              dropped for that topology only).
#   HTSIM_BUILD, STRAT, PATHS, END   passed to run.sh. END default 800000 us
#              (50 ms window; an overloaded fabric drains after the window;
#              runs that do not finish are rerun with END doubled).
#   OUTDIR     default results/e2
#   reps       seeds 1..reps (default 3)
#
# Tags: <cc>_fat_tree_128_<x>os_<e2|e3>load<L>_r<i>[_<strat>][_p<paths>]_s<i>.
# Resumable. Output: OUTDIR/e2_table.{md,csv}, results/figs/e2_topology.{png,pdf}.
#
# Example (the other policies, from their own builds):
#   POLICIES=dcqcn HTSIM_BUILD=build_dcqcn ./scripts/e2.sh 3
#   POLICIES=hpcc  HTSIM_BUILD=build_hpcc  ./scripts/e2.sh 3
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
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) REPS=$1; shift ;;
  esac
done
POLICIES="${POLICIES:-dcqcn hpcc roce eqds}"
MATRICES="${MATRICES:-e2_load70_ft128 e2_load50_ft128 e2_load30_ft128 e3_load50_ft128 e3_load30_ft128}"
TOPOS="$(run_topo ft128_8os) $(run_topo ft128_4os) $(run_topo ft128_1os)"   # slowest first
T1=$(run_topo ft128_1os); T4=$(run_topo ft128_4os)
END="${END:-800000}"
REUSE_E3="${REUSE_E3:-1}"
E3DIR="$HERE/results/e3/ft128_1os"
[ -n "${HTSIM_BUILD:-}" ] && export HTSIM_BUILD
OUTDIR="${OUTDIR:-$HERE/results/e2}"
mkdir -p "$OUTDIR"; OUTDIR="$(cd "$OUTDIR" && pwd)"; export OUTDIR
exp_suffix

for M in $MATRICES; do
  [ -f "$HERE/workloads/$M.cm" ] || { echo "missing workloads/$M.cm"; exit 1; }
done

declare -A OKPOL
for T in $TOPOS; do
  if [ "$DRY" = 1 ]; then OKPOL[$T]="$POLICIES"; continue; fi
  # shellcheck disable=SC2086
  OKPOL[$T]=$(exp_probe "$T" 128 $POLICIES)
done

JOBS=()
for CC in dcqcn hpcc roce eqds; do
  for T in $TOPOS; do
    case " ${OKPOL[$T]} " in *" $CC "*) ;; *) continue ;; esac
    for M in $MATRICES; do
      FAM=${M%%_*}                                  # e2 | e3
      L=$(echo "$M" | sed -n 's/.*load\([0-9]*\).*/\1/p')
      for i in $(seq 1 "$REPS"); do
        TAG="${CC}_${T}_${FAM}load${L}_r${i}${SUFFIX}_s${i}"
        [ -f "$OUTDIR/${TAG}_summary.json" ] && continue
        if [ "$FAM" = e3 ] && [ "$T" = "$T1" ] && [ "$REUSE_E3" = 1 ] && \
           [ -f "$E3DIR/${CC}_${T1}_load${L}_r${i}${SUFFIX}_s${i}_summary.json" ]; then
          continue
        fi
        JOBS+=("$CC $T ${FAM}load${L}_r${i} $HERE/workloads/$M.cm $i $END $TAG")
      done
    done
  done
done

if [ "${VICTIM:-1}" = 1 ]; then
  for CC in dcqcn hpcc roce eqds; do
    for T in $T4 $T1; do
      case " ${OKPOL[$T]} " in *" $CC "*) ;; *) continue ;; esac
      for V in victim victimalone; do
        F=$HERE/workloads/e2/victim_ft128.cm; [ $V = victimalone ] && F=$HERE/workloads/e2/victim_alone_ft128.cm
        for i in $(seq 1 "$REPS"); do
          TAG="${CC}_${T}_${V}_r${i}${SUFFIX}_s${i}"
          [ -f "$OUTDIR/${TAG}_summary.json" ] && continue
          JOBS=("$CC $T ${V}_r${i} $F $i 200000 $TAG" "${JOBS[@]}")   # cheap: run first
        done
      done
    done
  done
fi

echo "e2: policies [$POLICIES]  topologies [$TOPOS]  matrices [$MATRICES]  reps $REPS  END $END"
exp_run_jobs
[ "$DRY" = 1 ] && exit 0

mapfile -t FILES < <(cd "$OUTDIR" && ls | grep -E "^[a-z]+_(ft128|fat_tree_128)_[148]os_(e[23]load[0-9]+|victim|victimalone)_r[0-9]+${SUFFIX}_s[0-9]+_summary\.json$" | sed "s|^|$OUTDIR/|")
if [ "$REUSE_E3" = 1 ] && [ -d "$E3DIR" ]; then
  mapfile -t -O "${#FILES[@]}" FILES < <(cd "$E3DIR" && ls | grep -E "^[a-z]+_${T1}_load(30|50)_r[0-9]+${SUFFIX}_s[0-9]+_summary\.json$" | sed "s|^|$E3DIR/|")
fi
[ ${#FILES[@]} -gt 0 ] || { echo "no summaries"; exit 1; }
python3 "$HERE/scripts/exp_report.py" e2 --out "$OUTDIR/e2_table" --fig "e2_topology${SUFFIX}" "${FILES[@]}"
