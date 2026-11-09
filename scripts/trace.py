#!/usr/bin/env python3
"""M1. Read an Azure LLM inference trace into request records and report it.

Usage
    python3 scripts/trace.py data/AzureLLMInferenceTrace_conv.csv
    python3 scripts/trace.py data/*.csv --hist results/prompt_hist.png

The two traces come from
https://github.com/Azure/AzurePublicDataset (AzureLLMInferenceDataset2023),
CC-BY, and must be cited as Patel et al., "Splitwise: Efficient generative
LLM inference using phase splitting", ISCA 2024.

Columns are TIMESTAMP, ContextTokens, GeneratedTokens. Both files were
collected on 2023-11-16, not the 2023-11-11 the dataset page states.

Design decisions, recorded here because they affect every number downstream.

1.  Timestamps carry seven fractional digits, which datetime's %f cannot
    take. Rather than truncating a digit, the date is converted with
    toordinal() and the time of day is summed in seconds, so full precision
    is kept and the parser still works across a midnight boundary.

2.  Arrival times are normalised so the first request is at 0.0. Vidur's
    trace replay generator does not normalise, but it also assumes a trace
    whose arrival column is already a relative offset. Normalising here means
    a flow start time is directly a simulation time, with no epoch offset to
    carry around.

3.  Malformed, missing or non-positive token counts raise rather than being
    dropped or clipped. An audit of both files found zero such rows, so a
    future one means either a new trace version or a parser bug, and both are
    worth stopping for. Vidur clips to a minimum of one token, which is the
    right call for a simulator that must not crash mid-sweep, but the wrong
    call for a generator run once by hand.

4.  --duration truncates on the original timestamps, before any load
    rescaling in M6. Filtering after rescaling would change the request count
    every time the load changed, which would confound the two variables.
"""

import argparse
import csv
import math
import os
import sys
from datetime import datetime

SECONDS_PER_DAY = 86400


class Request:
    """One inference request. Immutable once built."""

    __slots__ = ("id", "arrival_s", "prompt_tokens", "output_tokens")

    def __init__(self, rid, arrival_s, prompt_tokens, output_tokens):
        self.id = rid
        self.arrival_s = arrival_s
        self.prompt_tokens = prompt_tokens
        self.output_tokens = output_tokens

    def __repr__(self):
        return "Request(id=%d, t=%.6f, prompt=%d, out=%d)" % (
            self.id, self.arrival_s, self.prompt_tokens, self.output_tokens)


def parse_timestamp(s):
    """'2023-11-16 18:15:46.6805900' to absolute seconds, full precision.

    datetime's %f accepts at most six fractional digits and these have seven,
    so the fraction is handled as a float instead of being truncated.
    """
    s = s.strip()
    try:
        date_part, time_part = s.split(" ")
        hms, _, frac = time_part.partition(".")
        h, m, sec = hms.split(":")
        day = datetime.strptime(date_part, "%Y-%m-%d").date().toordinal()
        total = (day * SECONDS_PER_DAY
                 + int(h) * 3600
                 + int(m) * 60
                 + int(sec))
        if frac:
            total += int(frac) / (10.0 ** len(frac))
        return float(total)
    except Exception as exc:
        raise ValueError("cannot parse TIMESTAMP %r (%s)" % (s, exc))


def load_trace(path, duration=None):
    """Read a trace CSV into a list of Request in arrival order.

    duration, in seconds, keeps only requests arriving within that many
    seconds of the first one. It is applied to the original timestamps.

    Returns (requests, meta) where meta carries what the file looked like
    before filtering, so a truncated run can still report the full trace.
    """
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"TIMESTAMP", "ContextTokens", "GeneratedTokens"} - set(
            reader.fieldnames or [])
        if missing:
            raise ValueError("%s is missing column(s) %s, found %s"
                             % (path, sorted(missing), reader.fieldnames))

        raw = []
        for lineno, row in enumerate(reader, start=2):
            ts = parse_timestamp(row["TIMESTAMP"])
            prompt = _positive_int(row["ContextTokens"], "ContextTokens", path, lineno)
            out = _positive_int(row["GeneratedTokens"], "GeneratedTokens", path, lineno)
            raw.append((ts, prompt, out))

    if not raw:
        raise ValueError("%s has a header but no rows" % path)

    # The files are already sorted, verified by audit, but a trace that is not
    # would silently produce nonsensical inter-arrival times, so check.
    for i in range(1, len(raw)):
        if raw[i][0] < raw[i - 1][0]:
            raise ValueError("%s: TIMESTAMP goes backwards at row %d"
                             % (path, i + 2))

    t0 = raw[0][0]
    full_span = raw[-1][0] - t0

    requests = []
    for idx, (ts, prompt, out) in enumerate(raw):
        rel = ts - t0
        if duration is not None and rel > duration:
            break
        requests.append(Request(idx, rel, prompt, out))

    meta = {
        "path": path,
        "name": os.path.basename(path),
        "rows_in_file": len(raw),
        "full_duration_s": full_span,
        "first_timestamp_abs_s": t0,
        "duration_filter_s": duration,
        "requests_kept": len(requests),
    }
    return requests, meta


def _positive_int(value, column, path, lineno):
    value = (value or "").strip()
    if value == "":
        raise ValueError("%s line %d: %s is empty" % (path, lineno, column))
    try:
        n = int(value)
    except ValueError:
        raise ValueError("%s line %d: %s is %r, not an integer"
                         % (path, lineno, column, value))
    if n <= 0:
        raise ValueError("%s line %d: %s is %d, must be positive. A zero "
                         "token request would become a zero byte flow."
                         % (path, lineno, column, n))
    return n


def percentile(sorted_vals, q):
    """Nearest-rank percentile on an already sorted list. q in [0, 100]."""
    if not sorted_vals:
        return float("nan")
    k = max(0, min(len(sorted_vals) - 1,
                   int(math.ceil(q / 100.0 * len(sorted_vals))) - 1))
    return sorted_vals[k]


def summarise(requests, meta):
    prompts = sorted(r.prompt_tokens for r in requests)
    outs = sorted(r.output_tokens for r in requests)
    span = requests[-1].arrival_s - requests[0].arrival_s if len(requests) > 1 else 0.0
    gaps = [requests[i].arrival_s - requests[i - 1].arrival_s
            for i in range(1, len(requests))]
    n = len(requests)
    return {
        "name": meta["name"],
        "requests": n,
        "rows_in_file": meta["rows_in_file"],
        "span_s": span,
        "full_duration_s": meta["full_duration_s"],
        "rate_req_s": (n - 1) / span if span > 0 else float("nan"),
        "prompt_mean": sum(prompts) / n,
        "prompt_p50": percentile(prompts, 50),
        "prompt_p99": percentile(prompts, 99),
        "prompt_min": prompts[0],
        "prompt_max": prompts[-1],
        "out_mean": sum(outs) / n,
        "out_p99": percentile(outs, 99),
        "gap_mean_ms": 1000 * sum(gaps) / len(gaps) if gaps else float("nan"),
        "gap_max_ms": 1000 * max(gaps) if gaps else float("nan"),
    }


def print_report(s):
    print("== %s" % s["name"])
    print("   requests            %d   (rows in file %d)"
          % (s["requests"], s["rows_in_file"]))
    print("   span                %.1f s  (%.1f min)"
          % (s["span_s"], s["span_s"] / 60.0))
    if abs(s["span_s"] - s["full_duration_s"]) > 1e-6:
        print("   full trace          %.1f s  (truncated by --duration)"
              % s["full_duration_s"])
    print("   arrival rate        %.2f req/s" % s["rate_req_s"])
    print("   inter-arrival       mean %.1f ms, max %.1f ms"
          % (s["gap_mean_ms"], s["gap_max_ms"]))
    print("   prompt tokens       mean %.0f, p50 %d, p99 %d, min %d, max %d"
          % (s["prompt_mean"], s["prompt_p50"], s["prompt_p99"],
             s["prompt_min"], s["prompt_max"]))
    print("   generated tokens    mean %.0f, p99 %d"
          % (s["out_mean"], s["out_p99"]))


def plot_hist(traces, out_path):
    """Prompt length distribution for each trace on one figure."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4))
    hi = max(max(r.prompt_tokens for r in reqs) for reqs, _ in traces)
    bins = range(0, hi + 250, 250)
    for reqs, meta in traces:
        label = meta["name"].replace("AzureLLMInferenceTrace_", "").replace(".csv", "")
        ax.hist([r.prompt_tokens for r in reqs], bins=bins,
                histtype="step", linewidth=1.6, label=label)
    ax.set_yscale("log")
    ax.set_xlabel("prompt length (tokens)")
    ax.set_ylabel("requests (log scale)")
    ax.set_title("Azure LLM inference trace, prompt length distribution")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fig.savefig(out_path, dpi=150)
    print("\nwrote %s" % out_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("traces", nargs="+", help="trace CSV file(s)")
    ap.add_argument("--duration", type=float, default=None,
                    help="keep only the first N seconds of each trace")
    ap.add_argument("--hist", metavar="PNG",
                    help="write the prompt length histogram here")
    ap.add_argument("--head", type=int, default=0,
                    help="also print the first N parsed requests")
    args = ap.parse_args(argv)

    loaded = []
    for path in args.traces:
        reqs, meta = load_trace(path, duration=args.duration)
        loaded.append((reqs, meta))
        print_report(summarise(reqs, meta))
        if args.head:
            for r in reqs[:args.head]:
                print("   %r" % r)
        print()

    if args.hist:
        plot_hist(loaded, args.hist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
