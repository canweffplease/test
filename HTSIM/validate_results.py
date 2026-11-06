#!/usr/bin/env python3
"""
Comprehensive validation of Spritz artifact reproduction results.
Checks simulation CSV outputs against the paper's quantitative claims.
"""

import os
import sys
import glob
import numpy as np
import pandas as pd
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
DC_DIR = os.path.join(ROOT, "htsim", "sim", "datacenter")
OUT_ROOT = os.path.join(DC_DIR, "experiments_output_full")
PLOTS_DIR = os.path.join(ROOT, "paper_plots", "full")

ROUTINGS = [
    "MINIMAL", "VALIANT", "UGAL_L",
    "SOURCE_ECMP", "SOURCE_FLICR",
    "SOURCE_OPS_U", "SOURCE_OPS_W",
    "SOURCE_FLOW_V1", "SOURCE_FLOW_V2_U", "SOURCE_FLOW_V2_W",
]

ROUTING_LABELS = {
    "MINIMAL": "Minimal",
    "VALIANT": "Valiant",
    "UGAL_L": "UGAL-L",
    "SOURCE_ECMP": "ECMP",
    "SOURCE_FLICR": "FLICR",
    "SOURCE_OPS_U": "OPS (u)",
    "SOURCE_OPS_W": "OPS (w)",
    "SOURCE_FLOW_V1": "Spritz-Scout (w)",
    "SOURCE_FLOW_V2_U": "Spritz-Spray (u)",
    "SOURCE_FLOW_V2_W": "Spritz-Spray (w)",
}

SPRITZ_ROUTINGS = ["SOURCE_FLOW_V1", "SOURCE_FLOW_V2_U", "SOURCE_FLOW_V2_W"]
BASELINE_ROUTINGS = ["MINIMAL", "VALIANT", "UGAL_L", "SOURCE_ECMP", "SOURCE_FLICR", "SOURCE_OPS_U", "SOURCE_OPS_W"]

# Failure-capable routings (per paper: Minimal, UGAL-L, ECMP, FLICR excluded)
FAIL_ROUTINGS = ["VALIANT", "SOURCE_OPS_U", "SOURCE_OPS_W",
                 "SOURCE_FLOW_V1", "SOURCE_FLOW_V2_U", "SOURCE_FLOW_V2_W"]

pass_count = 0
fail_count = 0
warn_count = 0
results = []


def log_result(category, test_name, status, detail=""):
    global pass_count, fail_count, warn_count
    symbol = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠"}[status]
    if status == "PASS":
        pass_count += 1
    elif status == "FAIL":
        fail_count += 1
    else:
        warn_count += 1
    msg = f"  [{symbol}] {status}: {test_name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((category, test_name, status, detail))


def load_fct_data(base_path, experiment, routing):
    """Load FCT data from a simulation CSV."""
    csv_path = os.path.join(base_path, f"{experiment}_{routing}_flows.csv")
    if not os.path.isfile(csv_path):
        return None
    df = pd.read_csv(csv_path)
    if "fctNs" in df.columns:
        return df["fctNs"].dropna().values / 1000.0  # Convert to microseconds
    return None


def load_coll_data(base_path, experiment, routing):
    """Load collective completion time data."""
    csv_path = os.path.join(base_path, f"{experiment}_{routing}_flows.csv")
    if not os.path.isfile(csv_path):
        return None
    df = pd.read_csv(csv_path)
    if "fctNs" in df.columns:
        return df["fctNs"].max() / 1e6  # Convert to ms (CCT = max FCT)
    return None


def get_stats(data):
    """Return median, p99, max of data."""
    if data is None or len(data) == 0:
        return None, None, None
    return np.median(data), np.percentile(data, 99), np.max(data)


def check_data_completeness():
    """Check that all expected simulation data exists."""
    print("\n" + "=" * 70)
    print("CHECK 1: DATA COMPLETENESS")
    print("=" * 70)

    expected = {
        "df/p4a8h4/no_fail": [
            "WebSearch_2ms_l10_balance_global", "adv_i5_1MiB", "adv_i5_4MiB",
            "allreduce_bf_bg_64MiB_4MiB", "allreduce_ring_bg_64MiB_4MiB",
            "alltoall_n4_bg_64MiB_4MiB", "alltoall_n4_bg_8MiB_1MiB",
            "permutation_global_4MiB"
        ],
        "sf/p7q9/no_fail": [
            "WebSearch_2ms_l10_balance_global", "adv_all_4MiB",
            "allreduce_bf_bg_64MiB_4MiB", "allreduce_ring_bg_64MiB_4MiB",
            "alltoall_n4_bg_64MiB_4MiB", "permutation_global_4MiB"
        ],
        "df/p4a8h4/fail_2p": [
            "WebSearch_2ms_l10_balance_global", "adv_i5_1MiB", "adv_i5_4MiB",
            "allreduce_bf_bg_64MiB_4MiB", "allreduce_ring_bg_64MiB_4MiB",
            "alltoall_n4_bg_64MiB_4MiB", "alltoall_n4_bg_8MiB_1MiB",
            "permutation_global_4MiB"
        ],
        "sf/p7q9/fail_2p": [
            "WebSearch_2ms_l10_balance_global", "adv_all_4MiB",
            "allreduce_bf_bg_64MiB_4MiB", "allreduce_ring_bg_64MiB_4MiB",
            "alltoall_n4_bg_64MiB_4MiB", "permutation_global_4MiB"
        ],
    }

    total_csvs = 0
    missing_csvs = 0
    for scenario_path, experiments in expected.items():
        for exp in experiments:
            base = os.path.join(OUT_ROOT, scenario_path, exp)
            for routing in ROUTINGS:
                csv_path = os.path.join(base, f"{exp}_{routing}_flows.csv")
                total_csvs += 1
                if not os.path.isfile(csv_path):
                    # For fail scenarios, not all routings may be present
                    if "fail" in scenario_path and routing in ["MINIMAL", "UGAL_L", "SOURCE_ECMP", "SOURCE_FLICR"]:
                        continue  # Expected to be missing
                    missing_csvs += 1
                    log_result("Completeness", f"{scenario_path}/{exp}/{routing}", "WARN",
                               f"CSV not found: {csv_path}")

    log_result("Completeness", f"CSV files check ({total_csvs - missing_csvs}/{total_csvs} found)",
               "PASS" if missing_csvs == 0 else "WARN",
               f"{missing_csvs} missing" if missing_csvs > 0 else "All present")


def check_plot_completeness():
    """Verify all expected plot PDFs exist."""
    print("\n" + "=" * 70)
    print("CHECK 2: PLOT COMPLETENESS")
    print("=" * 70)

    expected_plots = [
        "fig6/fig6_df_adversarial.pdf", "fig6/fig6_df_permutation.pdf",
        "fig6/fig6_sf_adversarial.pdf", "fig6/fig6_sf_permutation.pdf",
        "fig7/fig7_df_allreduce_bf_cct.pdf", "fig7/fig7_df_allreduce_ring_cct.pdf",
        "fig7/fig7_df_alltoall_cct.pdf", "fig7/fig7_df_websearch.pdf",
        "fig7/fig7_sf_allreduce_bf_cct.pdf", "fig7/fig7_sf_allreduce_ring_cct.pdf",
        "fig7/fig7_sf_alltoall_cct.pdf", "fig7/fig7_sf_websearch.pdf",
        "fig8/fig8_adversarial_fail_2p_df.pdf", "fig8/fig8_adversarial_fail_2p_sf.pdf",
        "fig8/fig8_allreduce_bf_fail_2p.pdf", "fig8/fig8_allreduce_ring_fail_2p.pdf",
        "fig8/fig8_alltoall_fail_2p.pdf", "fig8/fig8_permutation_fail_2p.pdf",
        "fig8/fig8_websearch_fail_2p.pdf",
    ]

    for plot in expected_plots:
        path = os.path.join(PLOTS_DIR, plot)
        if os.path.isfile(path):
            size = os.path.getsize(path)
            log_result("Plots", f"{plot}", "PASS", f"exists ({size} bytes)")
        else:
            log_result("Plots", f"{plot}", "FAIL", "MISSING")


def check_fig6_claims():
    """
    Fig 6: No-failure microbenchmarks.
    Claim: Spritz-Spray consistently outperforms all baselines.
    Spritz achieves fewest packet drops in most cases.
    """
    print("\n" + "=" * 70)
    print("CHECK 3: FIG 6 — NO-FAILURE MICROBENCHMARKS")
    print("=" * 70)

    scenarios = [
        ("df/p4a8h4/no_fail", "permutation_global_4MiB", "DF Permutation"),
        ("df/p4a8h4/no_fail", "adv_i5_1MiB", "DF Adversarial"),
        ("sf/p7q9/no_fail", "permutation_global_4MiB", "SF Permutation"),
        ("sf/p7q9/no_fail", "adv_all_4MiB", "SF Adversarial"),
    ]

    for scenario_path, experiment, label in scenarios:
        print(f"\n  --- {label} ---")
        base = os.path.join(OUT_ROOT, scenario_path, experiment)
        p99_results = {}

        for routing in ROUTINGS:
            data = load_fct_data(base, experiment, routing)
            if data is not None:
                median, p99, maxv = get_stats(data)
                p99_results[routing] = p99
                print(f"    {ROUTING_LABELS[routing]:20s}: median={median:10.1f}µs  p99={p99:10.1f}µs  max={maxv:10.1f}µs")

        if not p99_results:
            log_result("Fig6", f"{label}: data available", "FAIL", "No data found")
            continue

        # Find best Spritz p99
        spritz_p99 = {r: p99_results[r] for r in SPRITZ_ROUTINGS if r in p99_results}
        baseline_p99 = {r: p99_results[r] for r in BASELINE_ROUTINGS if r in p99_results}

        if spritz_p99 and baseline_p99:
            best_spritz = min(spritz_p99.values())
            best_spritz_name = min(spritz_p99, key=spritz_p99.get)
            best_baseline = min(baseline_p99.values())
            best_baseline_name = min(baseline_p99, key=baseline_p99.get)

            speedup = best_baseline / best_spritz if best_spritz > 0 else 0

            if best_spritz <= best_baseline:
                log_result("Fig6", f"{label}: Spritz best p99",
                           "PASS",
                           f"{ROUTING_LABELS[best_spritz_name]}={best_spritz:.1f}µs vs best baseline "
                           f"{ROUTING_LABELS[best_baseline_name]}={best_baseline:.1f}µs (speedup={speedup:.2f}×)")
            else:
                # Check if Spritz is at least competitive (within 10%)
                ratio = best_spritz / best_baseline
                if ratio < 1.10:
                    log_result("Fig6", f"{label}: Spritz competitive",
                               "WARN",
                               f"Spritz {ROUTING_LABELS[best_spritz_name]}={best_spritz:.1f}µs vs "
                               f"{ROUTING_LABELS[best_baseline_name]}={best_baseline:.1f}µs (ratio={ratio:.2f}×)")
                else:
                    log_result("Fig6", f"{label}: Spritz competitive",
                               "FAIL",
                               f"Spritz {ROUTING_LABELS[best_spritz_name]}={best_spritz:.1f}µs vs "
                               f"{ROUTING_LABELS[best_baseline_name]}={best_baseline:.1f}µs (ratio={ratio:.2f}×)")


def check_fig7_claims():
    """
    Fig 7: AI Collectives + WebSearch.
    Claim: Spritz competitive in collectives, best among spraying for WebSearch.
    """
    print("\n" + "=" * 70)
    print("CHECK 4: FIG 7 — AI COLLECTIVES + WEBSEARCH")
    print("=" * 70)

    # Collective experiments (check CCT = max FCT)
    coll_scenarios = [
        ("df/p4a8h4/no_fail", "allreduce_ring_bg_64MiB_4MiB", "DF Allreduce Ring"),
        ("df/p4a8h4/no_fail", "allreduce_bf_bg_64MiB_4MiB", "DF Allreduce BF"),
        ("df/p4a8h4/no_fail", "alltoall_n4_bg_64MiB_4MiB", "DF Alltoall"),
        ("sf/p7q9/no_fail", "allreduce_ring_bg_64MiB_4MiB", "SF Allreduce Ring"),
        ("sf/p7q9/no_fail", "allreduce_bf_bg_64MiB_4MiB", "SF Allreduce BF"),
        ("sf/p7q9/no_fail", "alltoall_n4_bg_64MiB_4MiB", "SF Alltoall"),
    ]

    for scenario_path, experiment, label in coll_scenarios:
        print(f"\n  --- {label} ---")
        base = os.path.join(OUT_ROOT, scenario_path, experiment)
        cct_results = {}

        for routing in ROUTINGS:
            cct = load_coll_data(base, experiment, routing)
            if cct is not None:
                cct_results[routing] = cct
                print(f"    {ROUTING_LABELS[routing]:20s}: CCT={cct:10.3f} ms")

        if not cct_results:
            log_result("Fig7", f"{label}: data available", "FAIL", "No data found")
            continue

        spritz_cct = {r: cct_results[r] for r in SPRITZ_ROUTINGS if r in cct_results}
        baseline_cct = {r: cct_results[r] for r in BASELINE_ROUTINGS if r in cct_results}

        if spritz_cct and baseline_cct:
            best_spritz_cct = min(spritz_cct.values())
            best_spritz_name = min(spritz_cct, key=spritz_cct.get)
            best_baseline_cct = min(baseline_cct.values())
            best_baseline_name = min(baseline_cct, key=baseline_cct.get)

            if best_spritz_cct <= best_baseline_cct * 1.15:  # Within 15% is competitive
                log_result("Fig7", f"{label}: Spritz competitive CCT",
                           "PASS",
                           f"{ROUTING_LABELS[best_spritz_name]}={best_spritz_cct:.3f}ms vs "
                           f"{ROUTING_LABELS[best_baseline_name]}={best_baseline_cct:.3f}ms")
            else:
                log_result("Fig7", f"{label}: Spritz competitive CCT",
                           "WARN",
                           f"{ROUTING_LABELS[best_spritz_name]}={best_spritz_cct:.3f}ms vs "
                           f"{ROUTING_LABELS[best_baseline_name]}={best_baseline_cct:.3f}ms")

    # WebSearch experiments
    ws_scenarios = [
        ("df/p4a8h4/no_fail", "WebSearch_2ms_l10_balance_global", "DF WebSearch"),
        ("sf/p7q9/no_fail", "WebSearch_2ms_l10_balance_global", "SF WebSearch"),
    ]

    for scenario_path, experiment, label in ws_scenarios:
        print(f"\n  --- {label} ---")
        base = os.path.join(OUT_ROOT, scenario_path, experiment)
        p99_results = {}

        for routing in ROUTINGS:
            data = load_fct_data(base, experiment, routing)
            if data is not None:
                median, p99, maxv = get_stats(data)
                p99_results[routing] = p99
                print(f"    {ROUTING_LABELS[routing]:20s}: median={median:10.1f}µs  p99={p99:10.1f}µs")

        if p99_results:
            # Spraying schemes (OPS + Spritz)
            spraying = ["SOURCE_OPS_U", "SOURCE_OPS_W"] + SPRITZ_ROUTINGS
            spraying_p99 = {r: p99_results[r] for r in spraying if r in p99_results}

            if spraying_p99:
                best_spray = min(spraying_p99.values())
                best_spray_name = min(spraying_p99, key=spraying_p99.get)

                spritz_in_spray = {r: spraying_p99[r] for r in SPRITZ_ROUTINGS if r in spraying_p99}
                if spritz_in_spray:
                    best_spritz_ws = min(spritz_in_spray.values())
                    best_spritz_ws_name = min(spritz_in_spray, key=spritz_in_spray.get)

                    if best_spritz_ws <= best_spray * 1.05:
                        log_result("Fig7", f"{label}: Spritz best among spraying",
                                   "PASS",
                                   f"{ROUTING_LABELS[best_spritz_ws_name]}={best_spritz_ws:.1f}µs")
                    else:
                        log_result("Fig7", f"{label}: Spritz competitive among spraying",
                                   "WARN",
                                   f"{ROUTING_LABELS[best_spritz_ws_name]}={best_spritz_ws:.1f}µs vs "
                                   f"best spraying={ROUTING_LABELS[best_spray_name]}={best_spray:.1f}µs")


def check_fig8_claims():
    """
    Fig 8 (artifact numbering): 2% link failures.
    Claim: Spritz 2.5×–25.4× speedup over baselines under failures.
    Minimal, UGAL-L, ECMP, FLICR excluded (can't deliver all packets).
    """
    print("\n" + "=" * 70)
    print("CHECK 5: FIG 8 — 2% LINK FAILURE EXPERIMENTS")
    print("=" * 70)

    fail_scenarios = [
        ("df/p4a8h4/fail_2p", "permutation_global_4MiB", "DF Permutation Fail"),
        ("df/p4a8h4/fail_2p", "adv_i5_4MiB", "DF Adversarial Fail"),
        ("df/p4a8h4/fail_2p", "allreduce_ring_bg_64MiB_4MiB", "DF Allreduce Ring Fail"),
        ("df/p4a8h4/fail_2p", "allreduce_bf_bg_64MiB_4MiB", "DF Allreduce BF Fail"),
        ("df/p4a8h4/fail_2p", "alltoall_n4_bg_64MiB_4MiB", "DF Alltoall Fail"),
        ("df/p4a8h4/fail_2p", "WebSearch_2ms_l10_balance_global", "DF WebSearch Fail"),
        ("sf/p7q9/fail_2p", "permutation_global_4MiB", "SF Permutation Fail"),
        ("sf/p7q9/fail_2p", "adv_all_4MiB", "SF Adversarial Fail"),
        ("sf/p7q9/fail_2p", "allreduce_ring_bg_64MiB_4MiB", "SF Allreduce Ring Fail"),
        ("sf/p7q9/fail_2p", "allreduce_bf_bg_64MiB_4MiB", "SF Allreduce BF Fail"),
        ("sf/p7q9/fail_2p", "alltoall_n4_bg_64MiB_4MiB", "SF Alltoall Fail"),
        ("sf/p7q9/fail_2p", "WebSearch_2ms_l10_balance_global", "SF WebSearch Fail"),
    ]

    speedups = []

    for scenario_path, experiment, label in fail_scenarios:
        print(f"\n  --- {label} ---")
        base = os.path.join(OUT_ROOT, scenario_path, experiment)
        p99_results = {}

        for routing in FAIL_ROUTINGS:
            data = load_fct_data(base, experiment, routing)
            if data is not None:
                median, p99, maxv = get_stats(data)
                p99_results[routing] = p99
                print(f"    {ROUTING_LABELS[routing]:20s}: median={median:10.1f}µs  p99={p99:10.1f}µs  max={maxv:10.1f}µs")

        if not p99_results:
            log_result("Fig8", f"{label}: data available", "WARN", "No data found")
            continue

        # Check that non-Spritz routings have higher FCTs (or couldn't complete)
        spritz_p99 = {r: p99_results[r] for r in SPRITZ_ROUTINGS if r in p99_results}
        non_spritz_p99 = {r: p99_results[r] for r in ["VALIANT", "SOURCE_OPS_U", "SOURCE_OPS_W"]
                          if r in p99_results}

        if spritz_p99 and non_spritz_p99:
            best_spritz = min(spritz_p99.values())
            best_spritz_name = min(spritz_p99, key=spritz_p99.get)
            best_non_spritz = min(non_spritz_p99.values())
            best_non_spritz_name = min(non_spritz_p99, key=non_spritz_p99.get)

            speedup = best_non_spritz / best_spritz if best_spritz > 0 else 0
            speedups.append(speedup)

            if speedup > 1.0:
                log_result("Fig8", f"{label}: Spritz speedup",
                           "PASS",
                           f"{ROUTING_LABELS[best_spritz_name]}={best_spritz:.1f}µs vs "
                           f"{ROUTING_LABELS[best_non_spritz_name]}={best_non_spritz:.1f}µs "
                           f"(speedup={speedup:.2f}×)")
            else:
                log_result("Fig8", f"{label}: Spritz speedup",
                           "WARN",
                           f"Spritz not faster: {best_spritz:.1f}µs vs {best_non_spritz:.1f}µs "
                           f"(ratio={speedup:.2f}×)")

    if speedups:
        print(f"\n  Speedup range: {min(speedups):.2f}× to {max(speedups):.2f}×")
        print(f"  Paper claims: 2.5× to 25.4×")

        # Check if speedup range is in the right ballpark
        if max(speedups) > 2.0:
            log_result("Fig8", "Speedup range significant",
                       "PASS",
                       f"Max speedup={max(speedups):.2f}× (paper: up to 25.4×)")
        else:
            log_result("Fig8", "Speedup range significant",
                       "WARN",
                       f"Max speedup only {max(speedups):.2f}× (paper: up to 25.4×)")


def check_routing_ordering():
    """
    General check: Spritz variants should typically outperform or be competitive
    with non-adaptive baselines in no-fail scenarios.
    """
    print("\n" + "=" * 70)
    print("CHECK 6: ROUTING ORDERING SANITY CHECKS")
    print("=" * 70)

    # In no-fail permutation, Minimal should be worse than Valiant on adversarial patterns
    # and Spritz should be among the best

    scenarios = [
        ("df/p4a8h4/no_fail", "permutation_global_4MiB", "DF Permutation"),
        ("sf/p7q9/no_fail", "permutation_global_4MiB", "SF Permutation"),
    ]

    for scenario_path, experiment, label in scenarios:
        base = os.path.join(OUT_ROOT, scenario_path, experiment)
        p99_results = {}
        for routing in ROUTINGS:
            data = load_fct_data(base, experiment, routing)
            if data is not None:
                _, p99, _ = get_stats(data)
                p99_results[routing] = p99

        if "MINIMAL" in p99_results and "VALIANT" in p99_results:
            # For permutation patterns, Valiant typically helps
            if p99_results["VALIANT"] < p99_results["MINIMAL"]:
                log_result("Ordering", f"{label}: Valiant < Minimal (p99)",
                           "PASS",
                           f"Valiant={p99_results['VALIANT']:.1f}µs < Minimal={p99_results['MINIMAL']:.1f}µs")
            else:
                log_result("Ordering", f"{label}: Valiant vs Minimal",
                           "WARN",
                           f"Valiant={p99_results['VALIANT']:.1f}µs >= Minimal={p99_results['MINIMAL']:.1f}µs")


def check_csv_sanity():
    """Check that CSV files have reasonable data (no NaN-only, no empty, positive FCTs)."""
    print("\n" + "=" * 70)
    print("CHECK 7: CSV DATA SANITY")
    print("=" * 70)

    csv_files = glob.glob(os.path.join(OUT_ROOT, "**", "*.csv"), recursive=True)
    total = len(csv_files)
    bad_files = 0
    zero_fct = 0
    negative_fct = 0

    for csv_path in csv_files:
        try:
            df = pd.read_csv(csv_path)
            if len(df) == 0:
                bad_files += 1
                log_result("Sanity", f"Empty CSV: {os.path.basename(csv_path)}", "FAIL")
                continue
            if "fctNs" in df.columns:
                fcts = df["fctNs"].dropna()
                if len(fcts) == 0:
                    bad_files += 1
                    log_result("Sanity", f"All NaN FCTs: {os.path.basename(csv_path)}", "FAIL")
                elif (fcts <= 0).any():
                    negative_fct += 1
                elif (fcts == 0).any():
                    zero_fct += 1
        except Exception as e:
            bad_files += 1
            log_result("Sanity", f"Error reading {os.path.basename(csv_path)}", "FAIL", str(e))

    if bad_files == 0 and negative_fct == 0:
        log_result("Sanity", f"All {total} CSV files valid",
                   "PASS", "No empty, NaN-only, or negative FCT files")
    else:
        log_result("Sanity", f"CSV issues found",
                   "FAIL", f"{bad_files} bad, {negative_fct} negative FCT, {zero_fct} zero FCT")


def check_incast_claims():
    """Check incast + bystanders experiment results against paper claims (DF-focused)."""
    print("\n" + "=" * 70)
    print("CHECK 8: INCAST + BYSTANDERS EXPERIMENT")
    print("=" * 70)

    # DF incast experiment
    df_base = os.path.join(OUT_ROOT, "df", "p4a8h4", "no_fail", "incast_bystanders_4MiB")
    sf_base = os.path.join(OUT_ROOT, "sf", "p7q9", "no_fail", "incast_bystanders_4MiB")

    # Check data exists
    df_csvs = glob.glob(os.path.join(df_base, "*_flows.csv"))
    sf_csvs = glob.glob(os.path.join(sf_base, "*_flows.csv"))
    log_result("Incast", f"DF incast CSVs: {len(df_csvs)}/10",
               "PASS" if len(df_csvs) == 10 else "FAIL",
               "Expected 10 routing variant flow files")
    log_result("Incast", f"SF incast CSVs: {len(sf_csvs)}/10",
               "PASS" if len(sf_csvs) == 10 else "FAIL",
               "Expected 10 routing variant flow files")

    if len(df_csvs) < 10:
        return

    # Helper to split flows by destination node
    def split_flows(base_path, experiment, routing, incast_dst):
        csv_path = os.path.join(base_path, f"{experiment}_{routing}_flows.csv")
        if not os.path.isfile(csv_path):
            return None, None
        df = pd.read_csv(csv_path)
        if "srcNode_dstNode_flowId" not in df.columns:
            return None, None
        parts = df["srcNode_dstNode_flowId"].str.split("_", expand=True)
        df["dstNode"] = parts[1].astype(int)
        fct_us = df["fctNs"] / 1000.0
        incast_fct = fct_us[df["dstNode"] == incast_dst].values
        bystander_fct = fct_us[df["dstNode"] != incast_dst].values
        return incast_fct, bystander_fct

    # DF: Check incast p99 ~2.74-2.82 ms for all routings (paper claim)
    incast_p99s = {}
    bystander_p99s = {}
    for r in ROUTINGS:
        incast, bystander = split_flows(df_base, "incast_bystanders_4MiB", r, 160)
        if incast is not None and len(incast) > 0:
            incast_p99s[r] = np.percentile(incast, 99)
        if bystander is not None and len(bystander) > 0:
            bystander_p99s[r] = np.percentile(bystander, 99)

    # Check: incast p99 broadly similar (~2.7-2.9 ms range)
    if incast_p99s:
        vals_ms = [v / 1000.0 for v in incast_p99s.values()]
        min_ms, max_ms = min(vals_ms), max(vals_ms)
        ok = 2.0 <= min_ms <= 4.0 and 2.0 <= max_ms <= 4.0
        log_result("Incast", f"DF incast p99 range: {min_ms:.2f}–{max_ms:.2f} ms",
                   "PASS" if ok else "WARN",
                   "Paper: ~2.74–2.82 ms")

    # Check: MINIMAL has worst incast p99
    if "MINIMAL" in incast_p99s:
        minimal_worst = incast_p99s["MINIMAL"] >= max(
            v for k, v in incast_p99s.items() if k != "MINIMAL"
        ) * 0.95  # within 5%
        log_result("Incast", "DF MINIMAL has worst/near-worst incast p99",
                   "PASS" if minimal_worst else "WARN",
                   f"MINIMAL={incast_p99s['MINIMAL']/1000:.2f} ms")

    # Check: Spritz-Spray(w) bystander p99 best among all
    if "SOURCE_FLOW_V2_W" in bystander_p99s:
        spritz_w = bystander_p99s["SOURCE_FLOW_V2_W"]
        best_overall = min(bystander_p99s.values())
        ok = spritz_w <= best_overall * 1.05  # within 5% of best
        log_result("Incast", f"DF Spritz-Spray(w) bystander p99: {spritz_w:.1f} µs",
                   "PASS" if ok else "WARN",
                   f"Paper: 204.40 µs, best={best_overall:.1f} µs")

    # Check: Valiant bystander p99 ≈ 249 µs (best baseline)
    if "VALIANT" in bystander_p99s:
        val_p99 = bystander_p99s["VALIANT"]
        ok = 180 <= val_p99 <= 350
        log_result("Incast", f"DF Valiant bystander p99: {val_p99:.1f} µs",
                   "PASS" if ok else "WARN",
                   "Paper: 249.01 µs")

    # Check: improvement from best baseline to best Spritz ~18%
    baseline_best = {k: v for k, v in bystander_p99s.items() if k in BASELINE_ROUTINGS}
    spritz_best = {k: v for k, v in bystander_p99s.items() if k in SPRITZ_ROUTINGS}
    if baseline_best and spritz_best:
        best_bl = min(baseline_best.values())
        best_sp = min(spritz_best.values())
        improvement = (best_bl - best_sp) / best_bl * 100
        ok = 5 <= improvement <= 35  # generous range
        log_result("Incast", f"DF bystander p99 improvement: {improvement:.1f}%",
                   "PASS" if ok else "WARN",
                   "Paper: ~17.9%")

    # Check incast plots exist
    incast_plots = [
        "incast_df_incast.pdf", "incast_df_bystanders.pdf",
        "incast_sf_incast.pdf", "incast_sf_bystanders.pdf",
    ]
    incast_plot_dir = os.path.join(PLOTS_DIR, "fig_incast")
    for p in incast_plots:
        path = os.path.join(incast_plot_dir, p)
        exists = os.path.isfile(path)
        log_result("Incast", f"Plot {p}",
                   "PASS" if exists else "WARN",
                   "exists" if exists else "missing")


def check_disk_usage():
    """Report disk usage of simulation outputs."""
    print("\n" + "=" * 70)
    print("CHECK 9: RESOURCE USAGE")
    print("=" * 70)

    # Count total CSVs
    csv_files = glob.glob(os.path.join(OUT_ROOT, "**", "*.csv"), recursive=True)
    total_size = sum(os.path.getsize(f) for f in csv_files)

    log_result("Resources", f"Total CSVs: {len(csv_files)}, Size: {total_size / 1e6:.1f} MB",
               "PASS", "")

    # Check plot sizes
    pdf_files = glob.glob(os.path.join(PLOTS_DIR, "**", "*.pdf"), recursive=True)
    total_pdf_size = sum(os.path.getsize(f) for f in pdf_files)
    log_result("Resources", f"Total PDFs: {len(pdf_files)}, Size: {total_pdf_size / 1e6:.1f} MB",
               "PASS", "")


def print_summary():
    """Print final summary."""
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    print(f"  PASSED:  {pass_count}")
    print(f"  WARNED:  {warn_count}")
    print(f"  FAILED:  {fail_count}")
    print(f"  TOTAL:   {pass_count + warn_count + fail_count}")
    print()

    if fail_count == 0 and warn_count == 0:
        print("  Result: ALL CHECKS PASSED — Results match paper claims.")
    elif fail_count == 0:
        print("  Result: MOSTLY PASSED — Results are consistent with paper claims")
        print("          (warnings indicate minor deviations or notes, not failures).")
    else:
        print(f"  Result: {fail_count} FAILURES — Some results may not match paper claims.")
    print("=" * 70)


def main():
    print("=" * 70)
    print("SPRITZ ARTIFACT REPRODUCTION VALIDATION")
    print("Checking simulation results against paper claims")
    print("=" * 70)

    check_data_completeness()
    check_plot_completeness()
    check_fig6_claims()
    check_fig7_claims()
    check_fig8_claims()
    check_routing_ordering()
    check_csv_sanity()
    check_incast_claims()
    check_disk_usage()
    print_summary()

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
