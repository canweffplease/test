#!/usr/bin/env python3
"""M5. Turn an Azure trace into an HTSIM connection matrix.

Ties M1 to M4 together and writes two files.

    <out>.cm     what HTSIM reads
    <out>.json   everything the .cm format cannot carry, keyed by flow id

The .cm format has room for a source, a destination, a start time and a size.
It has nowhere to put a deadline, a request id or a shard index, and the
analysis needs all three, so they live in the sidecar.

Three traps this module exists to get right.

1.  start is in PICOSECONDS. A workload that looks a million times too fast
    is this.
2.  The Connections header must equal the line count exactly.
3.  A size field is a 32-bit signed int, so no flow may exceed 2147483647
    bytes. This fails loudly rather than emitting a wrapped value.

Window selection
----------------
HTSIM simulates a short window, 2 ms to a few seconds, while a rate-scaled
trace runs for tens of seconds and is not uniformly busy. The conv trace
compressed 174x is idle until 7.5 s and peaks at 12.5 s, so which window is
taken decides the offered load. --window-start auto picks the busiest window
by bytes and records the choice in the sidecar. Taking whatever the first
two seconds happen to be would silently make every result a different
experiment.

Usage
    python3 scripts/gen_workload.py \
        --trace data/AzureLLMInferenceTrace_conv.csv \
        --model llama2-13b --rate-scale 174 \
        --out workloads/conv_13b_x174
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import MODELS, MAX_FLOW_BYTES          # noqa: E402
from placement import Placement                     # noqa: E402
from prefill import PrefillPool, prefill_seconds    # noqa: E402
from trace import load_trace                        # noqa: E402

PS_PER_SECOND = 10 ** 12


DEFAULT_LINK_BPS = 100e9      # fat_tree_128_*.topo, Downlink_speed_Gbps 100


def capacity_bps(args, link_bps):
    """Offered load is measured against the DECODE side.

    Every host has one NIC. The prefill pool can only push what the decode
    pool can absorb, so the smaller of the two is the real capacity. With an
    uneven split, measuring at the prefill side produces workloads the
    receivers physically cannot take: a 13 prefill to 3 decode layout offers
    10.4 Tbps into 2.4 Tbps of receive capacity, which is a permanent 4.3 to
    1 incast no buffer setting can absorb. We found that the hard way.

    Splitwise's 4:1 prefill to decode ratio is a compute ratio, not a network
    one. A decode instance holds a request for its whole generation, so it
    serves fewer requests, but it still has exactly one NIC per GPU. For
    bandwidth the balanced split is close to 1:1.

    Stated explicitly because every load figure in the paper is relative to
    this definition.
    """
    decode_hosts = args.decode * args.tp
    prefill_hosts = args.prefill * args.tp
    return min(decode_hosts, prefill_hosts) * link_bps


def rate_scale_for_load(requests, model, args, link_bps):
    """Arrival compression factor that offers `args.load` of capacity.

        requests/sec = load * capacity / mean cache bits
        rate scale   = that rate / the trace's native rate
    """
    n = len(requests)
    span = requests[-1].arrival_s - requests[0].arrival_s
    if span <= 0:
        raise ValueError("trace has no duration")
    native_rate = (n - 1) / span
    mean_bits = sum(model.cache_bytes(r.prompt_tokens) for r in requests) / n * 8
    target_rate = args.load * capacity_bps(args, link_bps) / mean_bits
    return target_rate / native_rate, native_rate, target_rate, mean_bits


def pick_window(records, placement, model, window_s, step_s=0.1):
    """Return the start time of the busiest window, measured in bytes."""
    if not records:
        raise ValueError("no requests")
        
    # (flow_start, bytes) per request, bytes being the whole cache
    events = sorted((rec[3], model.cache_bytes(rec[0].prompt_tokens))
                    for rec in records)
    horizon = events[-1][0]
    best_t, best_b = 0.0, -1
    t = 0.0
    i = j = 0
    run = 0
    # sliding window over a sorted list
    while t <= max(0.0, horizon - window_s) + 1e-9:
        while j < len(events) and events[j][0] < t + window_s:
            run += events[j][1]
            j += 1
        while i < len(events) and events[i][0] < t:
            run -= events[i][1]
            i += 1
        if run > best_b:
            best_b, best_t = run, t
        t += step_s
    return best_t


def build(args):
    model = MODELS[args.model]
    requests, meta = load_trace(args.trace, duration=args.duration)

    calib = None
    if args.load is not None:
        if args.rate_scale != 1.0:
            raise ValueError("pass --load or --rate-scale, not both")
        rs, native, target, mean_bits = rate_scale_for_load(
            requests, model, args, args.link_bps)
        args.rate_scale = rs
        calib = {"target_load": args.load,
                 "native_rate_req_s": native,
                 "target_rate_req_s": target,
                 "mean_cache_bits": mean_bits,
                 "capacity_bps": capacity_bps(args, args.link_bps),
                 "rate_scale_from_trace_mean": rs}

    placement = Placement(nodes=args.nodes, tp=args.tp,
                          n_prefill=args.prefill, n_decode=args.decode,
                          numbering=args.numbering)
    if placement.n_prefill != args.prefill:
        raise AssertionError
    pool = PrefillPool(args.prefill)
    records = pool.run(requests, model, args.alpha_scale, args.rate_scale)

    if args.window_start == "auto":
        w0 = pick_window(records, placement, model, args.window)
    else:
        w0 = float(args.window_start)
    w1 = w0 + args.window

    # The emitted window is the busiest slice, which runs above the trace
    # mean, so a workload calibrated to a mean load of 0.5 would simulate at
    # more like 0.57. Calibrate against the window actually emitted, not the
    # whole trace, otherwise every quoted load is quietly wrong. One
    # correction pass is enough: re-running the pool shifts the busiest
    # window slightly, so this iterates until the window load is within a
    # percent of the target or it gives up and reports what it got.
    if calib is not None and args.calibrate_window:
        cap = capacity_bps(args, args.link_bps)
        for _ in range(8):
            emitted = sum(model.cache_bytes(rec[0].prompt_tokens)
                          for rec in records if w0 <= rec[3] < w1)
            window_load = emitted * 8 / args.window / cap
            if window_load <= 0:
                break
            calib["window_load_before"] = calib.get("window_load_before",
                                                    window_load)
            if abs(window_load - args.load) / args.load < 0.01:
                break
            args.rate_scale *= args.load / window_load
            records = pool.run(requests, model, args.alpha_scale,
                               args.rate_scale)
            if args.window_start == "auto":
                w0 = pick_window(records, placement, model, args.window)
            w1 = w0 + args.window
        calib["window_load_after"] = window_load
        calib["rate_scale_final"] = args.rate_scale

    flows = []        # (src, dst, start_ps, size, meta dict)
    fid = 0
    kept = skipped_size = 0
    for idx, (req, arrival, start, finish, wait, inst) in enumerate(records):
        if not (w0 <= finish < w1):
            continue
        kept += 1
        d_inst = placement.decode_for(idx)
        shard = model.shard_bytes(req.prompt_tokens, args.tp)
        if shard > MAX_FLOW_BYTES:
            skipped_size += 1
            raise ValueError(
                "request %d has a %d byte shard, over the 32-bit size field. "
                "Longest prompt allowed at tp=%d on %s is %d tokens."
                % (req.id, shard, args.tp, model.name,
                   model.max_tokens_per_flow(args.tp)))
        start_ps = int(round((finish - w0) * PS_PER_SECOND))
        for src, dst, _, size in placement.flows(inst, d_inst, finish,
                                                 req.prompt_tokens, model):
            fid += 1
            flows.append((src, dst, start_ps, size, {
                "flow_id": fid,
                "request_id": req.id,
                "shard": len(flows) % args.tp,
                "prompt_tokens": req.prompt_tokens,
                "bytes": size,
                "start_s": finish - w0,
                "arrival_s": arrival - w0,
                "prefill_wait_s": wait,
                "prefill_compute_s": finish - start,
                "deadline_s": arrival - w0 + args.ttft_slo,
                "prefill_instance": inst,
                "decode_instance": d_inst,
            }))

    if not flows:
        raise ValueError("window [%g, %g) contains no flows" % (w0, w1))

    flows.sort(key=lambda f: (f[2], f[0]))
    # renumber ids after sorting so ids ascend with start time
    for new_id, f in enumerate(flows, start=1):
        f[4]["flow_id"] = new_id

    config = {
        "trace": meta["name"],
        "model": model.name,
        "bytes_per_token": model.bytes_per_token,
        "tp": args.tp,
        "nodes": args.nodes,
        "prefill_instances": args.prefill,
        "decode_instances": args.decode,
        "numbering": args.numbering,
        "rate_scale": args.rate_scale,
        "target_load": args.load,
        "link_bps": args.link_bps,
        "capacity_bps": capacity_bps(args, args.link_bps),
        "capacity_measured_at": "decode side, min(decode, prefill) hosts x link",
        "calibration": calib,
        "alpha_scale": args.alpha_scale,
        "window_start_s": w0,
        "window_s": args.window,
        "window_selection": args.window_start,
        "ttft_slo_s": args.ttft_slo,
        "requests_in_window": kept,
        "flows": len(flows),
        "bytes_total": sum(f[3] for f in flows),
    }
    return flows, config, placement, model


def verify(flows, config, placement):
    """M7 in miniature. Every check that can be made without running HTSIM."""
    problems = []
    n = len(flows)
    if config["flows"] != n:
        problems.append("config flow count %d != %d" % (config["flows"], n))
    ids = [f[4]["flow_id"] for f in flows]
    if len(set(ids)) != n:
        problems.append("flow ids are not unique")
    if sorted(ids) != list(range(1, n + 1)):
        problems.append("flow ids are not 1..%d" % n)
    prev = -1
    for src, dst, start_ps, size, m in flows:
        if src == dst:
            problems.append("flow %d is self directed on node %d" % (m["flow_id"], src))
        if not (0 <= src < config["nodes"] and 0 <= dst < config["nodes"]):
            problems.append("flow %d has a node outside 0..%d"
                            % (m["flow_id"], config["nodes"] - 1))
        if size <= 0 or size > MAX_FLOW_BYTES:
            problems.append("flow %d size %d out of range" % (m["flow_id"], size))
        if start_ps < 0 or start_ps >= config["window_s"] * PS_PER_SECOND:
            problems.append("flow %d start %d ps outside the window"
                            % (m["flow_id"], start_ps))
        if start_ps < prev:
            problems.append("flow %d starts before the previous one" % m["flow_id"])
        prev = start_ps
    # every request must contribute exactly tp shards
    per_req = {}
    for _, _, _, _, m in flows:
        per_req[m["request_id"]] = per_req.get(m["request_id"], 0) + 1
    bad = [r for r, c in per_req.items() if c != config["tp"]]
    if bad:
        problems.append("%d requests do not have exactly tp=%d shards"
                        % (len(bad), config["tp"]))
    return problems


def write(flows, config, out_base):
    cm = out_base + ".cm"
    js = out_base + ".json"
    os.makedirs(os.path.dirname(cm) or ".", exist_ok=True)
    with open(cm, "w") as fh:
        fh.write("Nodes %d\n" % config["nodes"])
        fh.write("Connections %d\n" % len(flows))
        for src, dst, start_ps, size, m in flows:
            fh.write("%d->%d id %d start %d size %d\n"
                     % (src, dst, m["flow_id"], start_ps, size))
    with open(js, "w") as fh:
        json.dump({"config": config, "flows": [f[4] for f in flows]}, fh, indent=1)
    return cm, js


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trace", required=True)
    ap.add_argument("--model", default="llama2-13b", choices=sorted(MODELS))
    ap.add_argument("--tp", type=int, default=8)
    ap.add_argument("--nodes", type=int, default=128)
    ap.add_argument("--prefill", type=int, default=13)
    ap.add_argument("--decode", type=int, default=3)
    ap.add_argument("--numbering", default="conventional",
                    choices=["conventional", "rail"])
    ap.add_argument("--load", type=float, default=None,
                    help="target offered load as a fraction of DECODE side "
                         "capacity, e.g. 0.7. Sets --rate-scale for you")
    ap.add_argument("--link-bps", type=float, default=DEFAULT_LINK_BPS,
                    help="per host link speed, default 100e9 to match "
                         "fat_tree_128_*.topo")
    ap.add_argument("--no-calibrate-window", dest="calibrate_window",
                    action="store_false",
                    help="calibrate load on the whole trace rather than on "
                         "the window actually emitted")
    ap.add_argument("--rate-scale", type=float, default=1.0,
                    help="compress arrivals by this factor (M6 sets it from "
                         "a target load)")
    ap.add_argument("--alpha-scale", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--window", type=float, default=2.0,
                    help="seconds of traffic to emit")
    ap.add_argument("--window-start", default="auto",
                    help="'auto' for the busiest window, or a time in seconds")
    ap.add_argument("--ttft-slo", type=float, default=0.2,
                    help="TTFT budget recorded per flow for deadline analysis")
    ap.add_argument("--out", required=True, help="output path without extension")
    args = ap.parse_args(argv)

    flows, config, placement, model = build(args)
    problems = verify(flows, config, placement)
    if problems:
        print("VERIFY FAILED")
        for p in problems[:20]:
            print("  " + p)
        return 1

    cm, js = write(flows, config, args.out)
    span = config["window_s"]
    print("wrote %s and %s" % (cm, js))
    print("   requests   %d" % config["requests_in_window"])
    print("   flows      %d" % config["flows"])
    print("   window     %.2f s starting at %.2f s (%s)"
          % (span, config["window_start_s"], config["window_selection"]))
    print("   bytes      %.2f GB" % (config["bytes_total"] / 1e9))
    print("   offered    %.0f Gbps over %d prefill hosts"
          % (config["bytes_total"] * 8 / span / 1e9,
             config["prefill_instances"] * config["tp"]))
    cap = config["capacity_bps"]
    print("   load       %.3f of capacity (%.1f Gbps of %.0f Gbps, decode side)"
          % (config["bytes_total"] * 8 / span / cap,
             config["bytes_total"] * 8 / span / 1e9, cap / 1e9))
    if config["calibration"]:
        c = config["calibration"]
        achieved = config["bytes_total"] * 8 / span / cap
        print("   calibrated target %.2f, trace-mean scale x%.1f, final x%.1f"
              % (c["target_load"], c["rate_scale_from_trace_mean"],
                 c.get("rate_scale_final", c["rate_scale_from_trace_mean"])))
        if abs(achieved - c["target_load"]) / c["target_load"] > 0.02:
            print("   WARNING: achieved load %.3f is more than 2 percent off "
                  "the %.2f target. A window this short holds too few "
                  "requests for the load to be continuous, so it cannot be "
                  "hit exactly. Use a longer --window or quote the achieved "
                  "load, not the target."
                  % (achieved, c["target_load"]))
    print("   verify     %d checks passed" % 8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
