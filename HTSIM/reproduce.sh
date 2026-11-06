#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DC_DIR="$ROOT_DIR/htsim/sim/datacenter"

usage() {
  cat <<'EOF'
Usage:
  bash reproduce.sh quick
  bash reproduce.sh full
  bash reproduce.sh plot <fig6|fig7|fig8|incast> [quick|full]

Environment variables:
  REPRO_PARALLEL   Parallel variant runs (default: nproc)
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

build_htsim() {
  echo "[T1] Building htsim + datacenter executables"
  (cd "$ROOT_DIR/htsim/sim" && cmake -S . -B build)
  (cd "$ROOT_DIR/htsim/sim" && cmake --build build --parallel "${REPRO_PARALLEL:-$(nproc)}")
}

run_df_no_fail() {
  local output_root="$1"; shift
  local exp_name="$1"; shift
  (cd "$DC_DIR" && python3 simulate_df_no_fail.py --output-root "$output_root" --only-experiment "$exp_name" --parallel "${REPRO_PARALLEL:-$(nproc)}")
}

run_sf_no_fail() {
  local output_root="$1"; shift
  local exp_name="$1"; shift
  (cd "$DC_DIR" && python3 simulate_sf_no_fail.py --output-root "$output_root" --only-experiment "$exp_name" --parallel "${REPRO_PARALLEL:-$(nproc)}")
}

run_df_fail() {
  local output_root="$1"; shift
  local exp_name="$1"; shift
  (cd "$DC_DIR" && python3 simulate_df_fail_2p.py --output-root "$output_root" --only-experiment "$exp_name" --parallel "${REPRO_PARALLEL:-$(nproc)}")
}

run_sf_fail() {
  local output_root="$1"; shift
  local exp_name="$1"; shift
  (cd "$DC_DIR" && python3 simulate_sf_fail_2p.py --output-root "$output_root" --only-experiment "$exp_name" --parallel "${REPRO_PARALLEL:-$(nproc)}")
}

plot_fig6() {
  local output_root="$1"; shift
  local out_dir="$1"; shift
  mkdir -p "$out_dir"

  echo "[T3] Plotting Fig. 6 (microbenchmarks)"
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment permutation_global_4MiB --df-topo p4a8h4 --scenario no_fail --layout stacked --no-show --outfile "$out_dir/fig6_df_permutation.pdf"

  local df_adv_exp="adv_i5_4MiB"
  if [[ -d "$output_root/df/p4a8h4/no_fail/adv_i5_1MiB" ]]; then
    df_adv_exp="adv_i5_1MiB"
  fi
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment "$df_adv_exp" --df-topo p4a8h4 --scenario no_fail --layout stacked --no-show --outfile "$out_dir/fig6_df_adversarial.pdf"

  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment permutation_global_4MiB --sf-topo p7q9 --scenario no_fail --layout stacked --no-show --outfile "$out_dir/fig6_sf_permutation.pdf"
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment adv_all_4MiB --sf-topo p7q9 --scenario no_fail --layout stacked --no-show --outfile "$out_dir/fig6_sf_adversarial.pdf"
}

plot_fig7() {
  local output_root="$1"; shift
  local out_dir="$1"; shift
  mkdir -p "$out_dir"

  echo "[T3] Plotting Fig. 7 (collectives + trace)"

  # Allreduce Ring CCT (DF + SF)
  python3 "$DC_DIR/plot_coll.py" --output-root "$output_root" --experiment allreduce_ring_bg_64MiB_4MiB --df-topo p4a8h4 --scenario no_fail --unit ms --no-show --outfile "$out_dir/fig7_df_allreduce_ring_cct.pdf"
  python3 "$DC_DIR/plot_coll.py" --output-root "$output_root" --experiment allreduce_ring_bg_64MiB_4MiB --sf-topo p7q9 --scenario no_fail --unit ms --no-show --outfile "$out_dir/fig7_sf_allreduce_ring_cct.pdf"

  # Allreduce Butterfly CCT (DF + SF)
  python3 "$DC_DIR/plot_coll.py" --output-root "$output_root" --experiment allreduce_bf_bg_64MiB_4MiB --df-topo p4a8h4 --scenario no_fail --unit ms --no-show --outfile "$out_dir/fig7_df_allreduce_bf_cct.pdf"
  python3 "$DC_DIR/plot_coll.py" --output-root "$output_root" --experiment allreduce_bf_bg_64MiB_4MiB --sf-topo p7q9 --scenario no_fail --unit ms --no-show --outfile "$out_dir/fig7_sf_allreduce_bf_cct.pdf"

  # Alltoall CCT (DF + SF)
  python3 "$DC_DIR/plot_coll.py" --output-root "$output_root" --experiment alltoall_n4_bg_64MiB_4MiB --df-topo p4a8h4 --scenario no_fail --unit ms --no-show --outfile "$out_dir/fig7_df_alltoall_cct.pdf"
  python3 "$DC_DIR/plot_coll.py" --output-root "$output_root" --experiment alltoall_n4_bg_64MiB_4MiB --sf-topo p7q9 --scenario no_fail --unit ms --no-show --outfile "$out_dir/fig7_sf_alltoall_cct.pdf"

  # WebSearch datacenter traces (DF + SF)
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment WebSearch_2ms_l10_balance_global --df-topo p4a8h4 --scenario no_fail --layout stacked --no-show --outfile "$out_dir/fig7_df_websearch.pdf"
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment WebSearch_2ms_l10_balance_global --sf-topo p7q9 --scenario no_fail --layout stacked --no-show --outfile "$out_dir/fig7_sf_websearch.pdf"
}

plot_fig8() {
  local output_root="$1"; shift
  local out_dir="$1"; shift
  mkdir -p "$out_dir"

  echo "[T3] Plotting Fig. 8 (2% failed links)"

  # Permutation
  python3 "$DC_DIR/plot.py" --output-root "$output_root" --experiment permutation_global_4MiB --df-topo p4a8h4 --sf-topo p7q9 --scenario fail_2p --no-show --outfile "$out_dir/fig8_permutation_fail_2p.pdf" || true

  # Adversarial (DF uses adv_i5_4MiB, SF uses adv_all_4MiB — generate each topology separately)
  python3 "$DC_DIR/plot.py" --output-root "$output_root" --experiment adv_i5_4MiB --df-topo p4a8h4 --scenario fail_2p --no-show --outfile "$out_dir/fig8_adversarial_fail_2p_df.pdf" || true
  python3 "$DC_DIR/plot.py" --output-root "$output_root" --experiment adv_all_4MiB --sf-topo p7q9 --scenario fail_2p --no-df --no-show --outfile "$out_dir/fig8_adversarial_fail_2p_sf.pdf" || true

  # Allreduce Ring
  python3 "$DC_DIR/plot.py" --output-root "$output_root" --experiment allreduce_ring_bg_64MiB_4MiB --df-topo p4a8h4 --sf-topo p7q9 --scenario fail_2p --no-show --outfile "$out_dir/fig8_allreduce_ring_fail_2p.pdf" || true

  # Allreduce Butterfly
  python3 "$DC_DIR/plot.py" --output-root "$output_root" --experiment allreduce_bf_bg_64MiB_4MiB --df-topo p4a8h4 --sf-topo p7q9 --scenario fail_2p --no-show --outfile "$out_dir/fig8_allreduce_bf_fail_2p.pdf" || true

  # Alltoall
  python3 "$DC_DIR/plot.py" --output-root "$output_root" --experiment alltoall_n4_bg_64MiB_4MiB --df-topo p4a8h4 --sf-topo p7q9 --scenario fail_2p --no-show --outfile "$out_dir/fig8_alltoall_fail_2p.pdf" || true

  # WebSearch
  python3 "$DC_DIR/plot.py" --output-root "$output_root" --experiment WebSearch_2ms_l10_balance_global --df-topo p4a8h4 --sf-topo p7q9 --scenario fail_2p --no-show --outfile "$out_dir/fig8_websearch_fail_2p.pdf" || true
}

plot_incast() {
  local output_root="$1"; shift
  local out_dir="$1"; shift
  mkdir -p "$out_dir"

  echo "[T3] Plotting Incast + Bystanders experiment"

  # DF: incast flows (dstNode=160) and bystanders
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment incast_bystanders_4MiB --df-topo p4a8h4 --layout stacked --unit us --flow-kind incast --incast-dst 160 --no-show --outfile "$out_dir/incast_df_incast.pdf" || true
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment incast_bystanders_4MiB --df-topo p4a8h4 --layout stacked --unit us --flow-kind bystanders --incast-dst 160 --no-show --outfile "$out_dir/incast_df_bystanders.pdf" || true

  # SF: incast flows (dstNode=161) and bystanders
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment incast_bystanders_4MiB --sf-topo p7q9 --layout stacked --unit us --flow-kind incast --incast-dst 161 --no-show --outfile "$out_dir/incast_sf_incast.pdf" || true
  python3 "$DC_DIR/plot_alternative.py" --output-root "$output_root" --experiment incast_bystanders_4MiB --sf-topo p7q9 --layout stacked --unit us --flow-kind bystanders --incast-dst 161 --no-show --outfile "$out_dir/incast_sf_bystanders.pdf" || true
}

main() {
  require_cmd python3
  require_cmd cmake
  require_cmd g++

  local cmd="${1:-}"
  case "$cmd" in
    quick)
      build_htsim

      local out_root="$DC_DIR/experiments_output_quick"
      local plots_root="$ROOT_DIR/paper_plots/quick"
      mkdir -p "$out_root" "$plots_root"

      echo "[T2] QUICK simulations (subset)"
      run_df_no_fail "$out_root" permutation_global_4MiB
      if [[ -f "$DC_DIR/experiments/df/p4a8h4/adv_i5_1MiB.cm" ]]; then
        run_df_no_fail "$out_root" adv_i5_1MiB
      else
        run_df_no_fail "$out_root" adv_i5_4MiB
      fi
      run_sf_no_fail "$out_root" permutation_global_4MiB
      run_sf_no_fail "$out_root" adv_all_4MiB

      run_df_no_fail "$out_root" allreduce_ring_bg_64MiB_4MiB
      run_sf_no_fail "$out_root" allreduce_ring_bg_64MiB_4MiB
      run_df_no_fail "$out_root" WebSearch_2ms_l10_balance_global
      run_sf_no_fail "$out_root" WebSearch_2ms_l10_balance_global

      run_df_fail "$out_root" permutation_global_4MiB
      run_sf_fail "$out_root" permutation_global_4MiB

      # Incast + bystanders (DF only in quick mode)
      run_df_no_fail "$out_root" incast_bystanders_4MiB

      plot_fig6 "$out_root" "$plots_root/fig6"
      plot_fig7 "$out_root" "$plots_root/fig7"
      plot_fig8 "$out_root" "$plots_root/fig8"
      plot_incast "$out_root" "$plots_root/fig_incast"

      echo "DONE. Raw outputs: $out_root"
      echo "DONE. Plots:       $plots_root"
      ;;

    full)
      build_htsim

      local out_root="$DC_DIR/experiments_output_full"
      local plots_root="$ROOT_DIR/paper_plots/full"
      mkdir -p "$out_root" "$plots_root"

      echo "[T2] FULL simulations (all inputs)"
      (cd "$DC_DIR" && python3 simulate_df_no_fail.py --output-root "$out_root" --parallel "${REPRO_PARALLEL:-$(nproc)}")
      (cd "$DC_DIR" && python3 simulate_sf_no_fail.py --output-root "$out_root" --parallel "${REPRO_PARALLEL:-$(nproc)}")
      (cd "$DC_DIR" && python3 simulate_df_fail_2p.py --output-root "$out_root" --parallel "${REPRO_PARALLEL:-$(nproc)}")
      (cd "$DC_DIR" && python3 simulate_sf_fail_2p.py --output-root "$out_root" --parallel "${REPRO_PARALLEL:-$(nproc)}")

      plot_fig6 "$out_root" "$plots_root/fig6"
      plot_fig7 "$out_root" "$plots_root/fig7"
      plot_fig8 "$out_root" "$plots_root/fig8"
      plot_incast "$out_root" "$plots_root/fig_incast"

      echo "DONE. Raw outputs: $out_root"
      echo "DONE. Plots:       $plots_root"
      ;;

    plot)
      local fig="${2:-}"
      local mode="${3:-quick}"
      local out_root="$DC_DIR/experiments_output_${mode}"
      local plots_root="$ROOT_DIR/paper_plots/${mode}"
      mkdir -p "$plots_root"
      case "$fig" in
        fig6) plot_fig6 "$out_root" "$plots_root/fig6" ;;
        fig7) plot_fig7 "$out_root" "$plots_root/fig7" ;;
        fig8) plot_fig8 "$out_root" "$plots_root/fig8" ;;
        incast) plot_incast "$out_root" "$plots_root/fig_incast" ;;
        *)
          echo "Unknown figure: $fig" >&2
          usage
          exit 2
          ;;
      esac
      ;;

    *)
      usage
      exit 2
      ;;
  esac
}

main "$@"
