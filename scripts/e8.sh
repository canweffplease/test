#!/usr/bin/env bash
# E8: do congestion-control rankings hold when the transfer-size distribution
# changes? Four size regimes x loads 50/70 x four policies x 3 seeds on the
# 16-node default fat tree (400 G), then tables and figures.
#
#   [POLICIES="roce eqds"] [HTSIM_BUILD=build] ./scripts/e8.sh [reps] [-j N] [--dry-run] [--report-only]
#   [POLICIES=...] ./scripts/e8.sh iso [reps] [-j N] [--dry-run]
#
# `iso` runs the isolated size ladder instead: ONE TP=8 request alone on the
# fabric at t = 0 (workloads/e8/iso/e8iso_<m70b|m13b>_t<tokens>_ft16.cm,
# 16 .. 7,436 tokens on Llama-3-70B plus 4,096 on Llama-2-13B), default 5
# seeds, END 100000, results in results/e8/iso/. It separates a policy's
# fixed per-request cost from its size-proportional cost with no load and
# no other request; e8_report.py fits excess = a + b x floor per policy.
#
# Regimes (matrices in workloads/e8/, exact generator commands in
# workloads/e8/COMMANDS.txt; all --pattern tp --tp 8, one prefill instance
# on hosts 0-7, one decode instance on hosts 8-15, Poisson --fix-count,
# --seed 1):
#   fix4k  Llama-3-70B, fixed 4,096-token prompts, 100 ms window
#          (byte-identical to workloads/e3_load{50,70}_ft16.cm)
#   conv   Llama-3-70B, prompts sampled from the Azure 2023 conversation trace
#   code   Llama-3-70B, prompts sampled from the Azure 2023 code trace
#   l13b   Llama-2-13B (800 KiB/token, 2.5x bytes), fixed 4,096 tokens,
#          250 ms window = the fix4k arrivals stretched 2.5x, so the request
#          count, the arrival pattern in units of the floor, and the offered
#          load are all the same as fix4k; only the bytes per request change
#
#   POLICIES   subset of "dcqcn hpcc roce eqds" (default all four), each
#              preflighted as in e3.sh
#   HTSIM_BUILD, STRAT, PATHS   passed through to run.sh
#   END        first -end in us, default 600000 (fix4k/conv/code) and
#              1000000 (l13b, 250 ms window); doubled up to END_MULT_MAX (4)
#              times when a run leaves flows unfinished (scripts/explib.sh)
#   OUTDIR     default results/e8
#   reps       default 3; rep i runs with SEED=i (ECMP hash salts), tags
#              <cc>_default_<regime>_load<L>_r<i>[<suffix>]_s<i>
#   -j N       parallel runs, default 4
#   --report-only  skip simulation, only rebuild tables and figures
#
# Resumable: a run whose <tag>_summary.json exists is skipped.
#
# Output: per-run CSVs / summaries in OUTDIR, OUTDIR/e8_table.{md,csv},
# OUTDIR/e8_ranking.{md,csv}, OUTDIR/e8_runs.csv, and
# results/figs/e8_{xfloor_by_regime,scatter_code_load70,ranking}.{png,pdf}
# (scripts/e8_report.py).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=explib.sh
source "$HERE/scripts/explib.sh"
J=4; REPS=""; DRY=0; REPORT_ONLY=0; MODE=load
while [ $# -gt 0 ]; do
  case "$1" in
    iso) MODE=iso; shift ;;
    -j) J=$2; shift 2 ;;
    -j*) J=${1#-j}; shift ;;
    --dry-run) DRY=1; shift ;;
    --report-only) REPORT_ONLY=1; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) REPS=$1; shift ;;
  esac
done

TOPO=default; PN=16
POLICIES="${POLICIES:-dcqcn hpcc roce eqds}"
[ -n "${HTSIM_BUILD:-}" ] && export HTSIM_BUILD

if [ "$MODE" = iso ]; then
  REPS=${REPS:-5}
  OUTDIR="${OUTDIR:-$HERE/results/e8/iso}"
  mkdir -p "$OUTDIR"; OUTDIR="$(cd "$OUTDIR" && pwd)"; export OUTDIR
  exp_suffix
  ISO=$(cd "$HERE/workloads/e8/iso" && ls e8iso_*_ft16.cm | sed 's/^e8iso_//; s/_ft16\.cm$//' | sort -t t -k2 -n)
  if [ "$DRY" != 1 ]; then
    # shellcheck disable=SC2086
    POLICIES=$(exp_probe "$TOPO" "$PN" $POLICIES)
    [ -n "$POLICIES" ] || { echo "no policy passed preflight"; exit 1; }
  fi
  JOBS=()
  for CC in roce eqds dcqcn hpcc; do
    case " $POLICIES " in *" $CC "*) ;; *) continue ;; esac
    for W in $ISO; do
      for i in $(seq 1 "$REPS"); do
        TAG="${CC}_${TOPO}_iso_${W}_r${i}${SUFFIX}_s${i}"
        [ -f "$OUTDIR/${TAG}_summary.json" ] && continue
        JOBS+=("$CC $TOPO iso_${W}_r${i} $HERE/workloads/e8/iso/e8iso_${W}_ft16.cm $i ${END:-100000} $TAG")
      done
    done
  done
  echo "e8 iso: policies [$POLICIES]  sizes [$(echo $ISO)]  reps $REPS"
  exp_run_jobs
  exit 0
fi
REPS=${REPS:-3}
REGIMES="l13b code fix4k conv"
LOADS="70 50"

OUTDIR="${OUTDIR:-$HERE/results/e8}"
mkdir -p "$OUTDIR"; OUTDIR="$(cd "$OUTDIR" && pwd)"; export OUTDIR
exp_suffix

for R in $REGIMES; do for L in $LOADS; do
  [ -f "$HERE/workloads/e8/e8_${R}_load${L}_ft16.cm" ] || {
    echo "missing workloads/e8/e8_${R}_load${L}_ft16.cm (see workloads/e8/COMMANDS.txt)"; exit 1; }
done; done

if [ "$REPORT_ONLY" != 1 ]; then
  if [ "$DRY" != 1 ]; then
    # shellcheck disable=SC2086
    POLICIES=$(exp_probe "$TOPO" "$PN" $POLICIES)
    [ -n "$POLICIES" ] || { echo "no policy passed preflight"; exit 1; }
  fi
  # --- job list: slowest first (RoCE, large matrices, high load) ----------
  JOBS=()
  for CC in roce eqds dcqcn hpcc; do
    case " $POLICIES " in *" $CC "*) ;; *) continue ;; esac
    for R in $REGIMES; do
      case "$R" in l13b) DEF_END=1000000 ;; *) DEF_END=600000 ;; esac
      E="${END:-$DEF_END}"
      for L in $LOADS; do
        for i in $(seq 1 "$REPS"); do
          TAG="${CC}_${TOPO}_${R}_load${L}_r${i}${SUFFIX}_s${i}"
          [ -f "$OUTDIR/${TAG}_summary.json" ] && continue
          JOBS+=("$CC $TOPO ${R}_load${L}_r${i} $HERE/workloads/e8/e8_${R}_load${L}_ft16.cm $i $E $TAG")
        done
      done
    done
  done
  echo "e8: policies [$POLICIES]  regimes [$REGIMES]  loads [$LOADS]  reps $REPS"
  exp_run_jobs
  [ "$DRY" = 1 ] && exit 0
fi

# --- tables and figures -------------------------------------------------
mapfile -t FILES < <(cd "$OUTDIR" && ls | grep -E "^[a-z]+_${TOPO}_(fix4k|conv|code|l13b)_load[0-9]+_r[0-9]+${SUFFIX}_s[0-9]+_summary\.json$" | sed "s|^|$OUTDIR/|")
if [ ${#FILES[@]} -eq 0 ]; then echo "no summaries to report"; exit 1; fi
mapfile -t ISOF < <(ls "$OUTDIR"/iso/*_default_iso_*${SUFFIX}_s[0-9]*_summary.json 2>/dev/null)
python3 "$HERE/scripts/e8_report.py" --out "$OUTDIR/e8" --figtag "e8${SUFFIX}" \
  ${ISOF[@]:+--iso "${ISOF[@]}" --} "${FILES[@]}"
