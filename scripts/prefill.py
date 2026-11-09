#!/usr/bin/env python3
"""M3. Prefill cost and the prefill pool queue, giving each flow its start time.

A request arrives at time t, but its KV cache does not exist until prefill
finishes, so the transfer cannot start at t.

    flow_start = arrival + queue_wait + prefill_compute

Prefill compute
---------------
    prefill_seconds(P) = alpha_scale * alpha * (P + R * P**2)

R is the model's attn_flop_ratio, derived from the architecture rather than
fitted. See models.Model.attn_flop_ratio.

alpha is anchored so a 4096-token prefill of Llama-3-70B takes 200 ms, and
scaled to the other models by parameter count, since all three are run at the
same tensor parallel width. --alpha-scale multiplies the whole thing.

Where the anchor comes from, and what it is worth. Splitwise reports P50 TTFT
for Llama-2-70B without batching of 155 ms on A100 and 84 ms on H100 for the
conversation trace, and 185 ms and 95 ms for coding. Those are end to end,
including queueing and batching that this module models separately, so they
are an upper bound on compute rather than a measurement of it. The 200 ms
anchor puts our compute-only figure at 47 ms for a 1020-token prompt, about
half the H100 end to end number, which is the right side to err on. Running
with --alpha-scale 2 reproduces an A100-like cluster and is the sensitivity
check that belongs in the paper.

Pool model
----------
N instances, each serving one request at a time, FIFO, each arrival taking
whichever instance frees soonest. Decode-side occupancy is not modelled: a
request holds a decode instance for its whole generation, so ignoring it
understates decode pool contention. Recorded here because it is an omission,
not an oversight.

Usage
    python3 scripts/prefill.py data/AzureLLMInferenceTrace_conv.csv \
        --model llama2-13b --instances 13 --rate 400 --plot results/queue.png
"""

import argparse
import heapq
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import MODELS                      # noqa: E402
from trace import load_trace, percentile       # noqa: E402

# Anchor: a 4096-token Llama-3-70B prefill takes this long.
ANCHOR_MODEL = "llama3-70b"
ANCHOR_TOKENS = 4096
ANCHOR_SECONDS = 0.200


def alpha_for(model):
    """Linear prefill coefficient, seconds per token, for one model."""
    ref = MODELS[ANCHOR_MODEL]
    ref_eff = ANCHOR_TOKENS + ref.attn_flop_ratio * ANCHOR_TOKENS ** 2
    ref_alpha = ANCHOR_SECONDS / ref_eff
    return ref_alpha * (model.params / ref.params)


def prefill_seconds(prompt_tokens, model, alpha_scale=1.0):
    r = model.attn_flop_ratio
    return alpha_scale * alpha_for(model) * (prompt_tokens + r * prompt_tokens ** 2)


class PrefillPool:
    """N single-server instances, shortest-free-time assignment."""

    def __init__(self, instances):
        if instances < 1:
            raise ValueError("need at least one prefill instance")
        self.instances = instances

    def run(self, requests, model, alpha_scale=1.0, rate_scale=1.0):
        """Return a list of records, one per request, in arrival order.

        rate_scale compresses arrival times, which is a preview of the M6
        load knob. It lives here so pool stability can be checked at the
        loads the experiments will actually use, rather than only at the
        trace's native rate.

        Each record is
            (request, arrival, start, finish, wait, instance)
        where finish is the flow start time.
        """
        free = [(0.0, i) for i in range(self.instances)]
        heapq.heapify(free)
        out = []
        for r in requests:
            arrival = r.arrival_s / rate_scale
            avail, inst = heapq.heappop(free)
            start = max(arrival, avail)
            svc = prefill_seconds(r.prompt_tokens, model, alpha_scale)
            finish = start + svc
            heapq.heappush(free, (finish, inst))
            out.append((r, arrival, start, finish, start - arrival, inst))
        return out


def summarise(records, pool, model):
    waits = sorted(rec[4] for rec in records)
    svcs = [rec[3] - rec[2] for rec in records]
    n = len(records)
    horizon = max(rec[3] for rec in records)
    span = records[-1][1] - records[0][1]
    busy = sum(svcs)
    # Backlog at the moment the last request arrives: how far behind the pool
    # has fallen. A stable pool ends near zero, an overloaded one does not.
    t_last = records[-1][1]
    backlog = sum(1 for rec in records if rec[2] > t_last)
    return {
        "requests": n,
        "instances": pool.instances,
        "arrival_span_s": span,
        "offered_rate": (n - 1) / span if span > 0 else float("nan"),
        "service_capacity": pool.instances / (busy / n),
        "utilisation": busy / (pool.instances * horizon),
        "mean_service_ms": 1000 * busy / n,
        "wait_mean_ms": 1000 * sum(waits) / n,
        "wait_p50_ms": 1000 * percentile(waits, 50),
        "wait_p99_ms": 1000 * percentile(waits, 99),
        "wait_max_ms": 1000 * waits[-1],
        "backlog_at_end": backlog,
        "drain_s": horizon - t_last,
    }


def print_report(s, model_name, alpha_scale):
    print("== %s, %d instances, alpha_scale %.2f"
          % (model_name, s["instances"], alpha_scale))
    print("   offered            %.2f req/s over %.1f s"
          % (s["offered_rate"], s["arrival_span_s"]))
    print("   pool capacity      %.1f req/s   (mean prefill %.1f ms)"
          % (s["service_capacity"], s["mean_service_ms"]))
    print("   utilisation        %.1f %%" % (100 * s["utilisation"]))
    print("   queue wait         mean %.1f ms, p50 %.1f, p99 %.1f, max %.1f"
          % (s["wait_mean_ms"], s["wait_p50_ms"], s["wait_p99_ms"],
             s["wait_max_ms"]))
    print("   backlog at end     %d requests, drains %.1f s after last arrival"
          % (s["backlog_at_end"], s["drain_s"]))
    if s["utilisation"] > 0.95 or s["backlog_at_end"] > 0.01 * s["requests"]:
        print("   UNSTABLE: the pool cannot keep up at this rate. Flow starts "
              "drift past the simulated window and the workload describes a "
              "cluster that is falling behind.")


def plot_queue(records, out_path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    arrivals = sorted(rec[1] for rec in records)
    starts = sorted(rec[2] for rec in records)
    # depth(t) = arrived so far minus started so far, evaluated at every event
    events = [(t, 1) for t in arrivals] + [(t, -1) for t in starts]
    events.sort()
    ts, depth, d = [], [], 0
    for t, delta in events:
        d += delta
        ts.append(t)
        depth.append(d)

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    a1.plot(ts, depth, linewidth=0.8)
    a1.set_ylabel("requests waiting")
    a1.set_title(title)
    a1.grid(alpha=0.3)
    a2.plot([rec[1] for rec in records],
            [1000 * rec[4] for rec in records], ".", markersize=1.5)
    a2.set_ylabel("queue wait (ms)")
    a2.set_xlabel("time (s)")
    a2.grid(alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fig.savefig(out_path, dpi=150)
    print("\nwrote %s" % out_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("trace")
    ap.add_argument("--model", default="llama2-13b", choices=sorted(MODELS))
    ap.add_argument("--instances", type=int, default=13,
                    help="prefill instances. 13 of 16 on a 128 node fabric at "
                         "TP=8 is the 4:1 prefill to decode split Splitwise "
                         "uses, rather than an even one")
    ap.add_argument("--alpha-scale", type=float, default=1.0,
                    help="2.0 gives an A100-like cluster")
    ap.add_argument("--rate-scale", type=float, default=1.0,
                    help="compress arrivals by this factor, previewing M6")
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--sweep", action="store_true",
                    help="report a grid of pool sizes and rate scales instead")
    ap.add_argument("--plot", metavar="PNG")
    args = ap.parse_args(argv)

    model = MODELS[args.model]
    requests, meta = load_trace(args.trace, duration=args.duration)
    print("%s, %d requests, %s\n" % (meta["name"], len(requests), model.name))

    if args.sweep:
        print("%-10s %-10s %10s %10s %10s %10s"
              % ("instances", "rate x", "offered", "util %", "wait p99", "stable"))
        for inst in (4, 8, 13, 16):
            for rs in (1, 10, 50, 100, 200):
                recs = PrefillPool(inst).run(requests, model,
                                             args.alpha_scale, rs)
                s = summarise(recs, PrefillPool(inst), model)
                stable = (s["utilisation"] <= 0.95
                          and s["backlog_at_end"] <= 0.01 * s["requests"])
                print("%-10d %-10d %9.1f/s %9.1f %8.1f ms %10s"
                      % (inst, rs, s["offered_rate"], 100 * s["utilisation"],
                         s["wait_p99_ms"], "yes" if stable else "NO"))
        return 0

    pool = PrefillPool(args.instances)
    records = pool.run(requests, model, args.alpha_scale, args.rate_scale)
    print_report(summarise(records, pool, model), model.name, args.alpha_scale)

    if args.plot:
        plot_queue(records, args.plot,
                   "%s, %s, %d instances, arrivals x%g"
                   % (meta["name"], model.name, args.instances, args.rate_scale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
