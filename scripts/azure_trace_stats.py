#!/usr/bin/env python3
"""Summarize Azure LLM inference traces: prompt lengths and arrival rates.

Usage: azure_trace_stats.py [trace.csv ...]   (default: data/azure/*.csv)

Reads CSVs with columns TIMESTAMP, ContextTokens, GeneratedTokens as data
only. Prints prompt-length percentiles, KV cache sizes for the project's
models, and arrival-rate statistics.
"""
import glob
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_workload import MODELS, read_trace  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main():
    paths = sys.argv[1:] or sorted(glob.glob(os.path.join(REPO, "data", "azure", "*.csv")))
    if not paths:
        sys.exit("no trace files given and none in data/azure/")
    P = (50, 90, 95, 99)
    for path in paths:
        rows = read_trace(path)
        t = [r[0] for r in rows]
        ctx = sorted(r[1] for r in rows)
        dur = t[-1] - t[0]
        gaps = [b - a for a, b in zip(t, t[1:])]
        mg = statistics.mean(gaps)
        cv = statistics.pstdev(gaps) / mg if mg > 0 else float("nan")
        per_s = [0] * (int(dur) + 1)
        for x in t:
            per_s[int(x - t[0])] += 1
        per_s_sorted = sorted(per_s)
        print(f"== {os.path.basename(path)}")
        print(f"  requests {len(rows):,} over {dur:,.1f} s "
              f"(mean {len(rows) / dur:.2f} req/s)")
        print(f"  inter-arrival mean {mg * 1e3:.1f} ms, CV {cv:.2f} (Poisson = 1)")
        print(f"  requests per 1 s bin: p50 {pct(per_s_sorted, 50):.0f}, "
              f"p99 {pct(per_s_sorted, 99):.0f}, max {per_s_sorted[-1]}")
        print("  ContextTokens: mean {:.0f}, ".format(statistics.mean(ctx)) +
              ", ".join(f"p{p} {pct(ctx, p):.0f}" for p in P) + f", max {ctx[-1]}")
        print(f"    > 4096: {sum(c > 4096 for c in ctx) / len(ctx) * 100:.1f}%, "
              f"> 8192: {sum(c > 8192 for c in ctx) / len(ctx) * 100:.1f}%")
        for name, (L, H, D) in sorted(MODELS.items()):
            bpt = 2 * L * H * D * 2
            print(f"    {name:<11} KV per request: mean {statistics.mean(ctx) * bpt / 1e9:.3f} GB, "
                  f"p50 {pct(ctx, 50) * bpt / 1e9:.3f} GB, p99 {pct(ctx, 99) * bpt / 1e9:.3f} GB")
        print(f"    llama3-70b offered KV rate at trace speed: "
              f"{sum(ctx) * 327680 * 8 / dur / 1e9:.1f} Gbps")


if __name__ == "__main__":
    main()
