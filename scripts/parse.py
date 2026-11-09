#!/usr/bin/env python3
"""Parse an htsim run log into per-flow and per-request results.

Usage: parse.py <logfile> <out.csv> <tag> <traffic-matrix.cm> [link_gbps]

Writes three files:
  <out.csv>                  one row per flow
  <out stem>_requests.csv    one row per request (slowest-shard rule)
  <out stem>_summary.json    run-level metrics, read by plot.py / summarize.py

and prints the line sweep.sh greps:
  flows N  last shard X us  floor Y us  = Zx floor
where Z is the WORST per-request distance from floor, X that request's
completion time and Y its floor. For the old single-request matrices
(all flows start at 0) this is exactly the old "slowest shard" number.

Inputs
------
Flow sizes and start times come from the traffic matrix, NOT from the log.
Different htsim transports print different things under "total bytes" (RoCE
prints a packet count, EQDS prints real bytes), so the log is not a reliable
source of size. If <matrix>.json exists next to the .cm (written by
scripts/gen_workload.py) it supplies request_id, shard, deadline, tier and
prompt length. Without it every flow is its own request (request_id = flow
index), which keeps the old hand-written matrices working.

Metrics (all times in microseconds)
-------
  flow completion   = finish - start            (start from the matrix)
  flow floor        = bytes * 8 / link_bps
  request completion= max(finish over shards) - min(start over shards)
  request floor     = max(shard floor)
  x_floor           = completion / floor        (1.0 = network added nothing)
  met_deadline      = request finish <= deadline (sidecar deadline_ps)
  Jain's index      over request completion / prompt_tokens (TTFT normalized
                    by prompt length); over request x_floor when the sidecar
                    has no prompt lengths.
Percentiles use linear interpolation between order statistics (numpy's
default). With 9-27 requests per run, p99 is essentially the max.

Matching log lines to matrix flows
----------------------------------
htsim names flows <Proto>_<src>_<dst>, not by matrix id, and the E3 ft16
matrices reuse the same 8 (src,dst) pairs many times. Matching per transport:
  EQDS   "Flow Eqds_s_d flowId <id> ..."  -> the matrix id. Exact.
  RoCE / DCQCN  "Flow Roce_s_d <logid> ..." -> htsim object id, allocated in
         matrix order. If every flow finished, sorting ids gives matrix order
         exactly. Otherwise ids are ranked within each (src,dst) pair, which
         is exact unless an unfinished flow sits between finished ones.
  HPCC   flowId when the finish line carries one (it does since commit 2521a60);
         otherwise name only: the k-th finish on a pair is matched to the k-th start on
         that pair (pair_seq). This assumes flows on one pair do not overtake
         each other, reasonable for equal-size flows under FIFO / fair
         sharing. A match with finish < start is counted as a violation.
For the exact methods the summary also counts "overtakes" (pairs where a
later-started flow finished first), which tells you how safe the HPCC
assumption is.

Start-time check
----------------
Every transport logs when it actually starts a flow. Those are compared,
per pair in order, with the matrix start times; a skew above 2 us means the
binary did not honour the matrix start times (seen with HPCC, which starts
every flow at t=0, and with unit bugs in RoCE/EQDS connect()), and the run
is refused.

Refuses (exit 1) when the run reported lossless violations, when no flow
completed, or when logged start times disagree with the matrix.
"""
import csv, json, math, os, re, sys
from collections import defaultdict

START_SKEW_TOL_US = 2.0   # matrix starts are float32 in htsim; ~8 ns at 100 ms


def die(msg):
    print(f"  REFUSING: {msg}")
    sys.exit(1)


def pct(vals, p):
    """Linear-interpolated percentile (numpy 'linear'), p in [0,100]."""
    v = sorted(vals)
    if not v:
        return None
    k = (len(v) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def stats(vals):
    if not vals:
        return {k: None for k in ("min", "p50", "p95", "p99", "max", "mean")}
    return {"min": min(vals), "p50": pct(vals, 50), "p95": pct(vals, 95),
            "p99": pct(vals, 99), "max": max(vals),
            "mean": sum(vals) / len(vals)}


def jain(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    s2 = sum(x * x for x in xs)
    return (sum(xs) ** 2) / (len(xs) * s2) if s2 > 0 else None


def fmt(x, nd=3):
    return "" if x is None else f"{x:.{nd}f}"


# --------------------------------------------------------------------------
# traffic matrix (+ optional sidecar)

def read_matrix(path):
    flows = []
    for line in open(path):
        tok = line.split()
        if not tok or "->" not in tok[0]:
            continue
        s, d = tok[0].split("->")
        f = {"idx": len(flows), "src": int(s), "dst": int(d), "id": None,
             "start_ps": 0, "bytes": None}
        i = 1
        while i < len(tok):
            if tok[i] == "id":
                f["id"] = int(tok[i + 1]); i += 1
            elif tok[i] == "start":
                f["start_ps"] = int(float(tok[i + 1])); i += 1
            elif tok[i] == "size":
                f["bytes"] = int(tok[i + 1]); i += 1
            i += 1
        flows.append(f)
    return flows


def attach_sidecar(flows, tm):
    side = os.path.splitext(tm)[0] + ".json"
    if not os.path.exists(side):
        for f in flows:
            f.update(request_id=f["idx"], shard=0, deadline_ps=None,
                     tier="", prompt_tokens=None)
        return None
    sc = json.load(open(side))
    by_id = {s["flow_id"]: s for s in sc["flows"]}
    if len(by_id) != len(flows):
        die(f"sidecar {side} has {len(by_id)} flows, matrix has {len(flows)}")
    for f in flows:
        s = by_id.get(f["id"])
        if s is None or (s["src"], s["dst"]) != (f["src"], f["dst"]) \
                or s["bytes"] != f["bytes"]:
            die(f"sidecar {side} disagrees with matrix at flow id {f['id']}")
        # the sidecar holds the exact picosecond start; the .cm copy is the
        # same number (or a rescaled one, in hand-made test matrices)
        f.update(request_id=s["request_id"], shard=s["shard"],
                 start_ps=s["start_ps"], deadline_ps=s.get("deadline_ps"),
                 tier=s.get("tier_crossed", ""),
                 prompt_tokens=s.get("prompt_tokens"),
                 side_floor_ps=s.get("floor_ps"))
    return sc


# --------------------------------------------------------------------------
# log

FIN_RE = re.compile(r"Flow ([A-Za-z]+)_(\d+)_(\d+) (.*?)\s*finished at ([-+0-9.eE]+)")
START_RE = [
    re.compile(r"startflow [A-Za-z]+_(\d+)_(\d+) at ([-+0-9.eE]+)"),          # roce, hpcc
    re.compile(r"Flow [A-Za-z]+_(\d+)_(\d+) flowId \d+ .*?starting at ([-+0-9.eE]+)"),  # eqds
]


def read_log(path):
    fins, starts = [], defaultdict(list)
    violations, done, end_us = 0, False, None
    with open(path, errors="replace") as fh:
        for line in fh:
            if "LOSSLESS not working" in line:
                violations += line.count("LOSSLESS not working")
                continue
            if "finished at" in line:
                m = FIN_RE.search(line)   # htsim progress dots can prefix the line
                if m:
                    proto, s, d, mid, t = m.groups()
                    fid = re.search(r"flowId (\d+)", mid)
                    lid = re.fullmatch(r"(\d+)", mid.strip())
                    fins.append({"proto": proto, "pair": (int(s), int(d)),
                                 "t_us": float(t),
                                 "flowid": int(fid.group(1)) if fid else None,
                                 "logid": int(lid.group(1)) if lid else None})
                continue
            if "start" in line:
                for r in START_RE:
                    m = r.search(line)
                    if m:
                        starts[(int(m.group(1)), int(m.group(2)))].append(float(m.group(3)))
                        break
            if line.startswith("endtime(us)"):
                end_us = float(line.split()[1])
            if line.rstrip().endswith("Done"):
                done = True
    return fins, starts, violations, done, end_us


def match(flows, fins):
    """Assign finish times (us) to matrix flows. Returns (method, n_unmatched)."""
    by_pair = defaultdict(list)                    # pair -> flows in start order
    for f in sorted(flows, key=lambda f: (f["start_ps"], f["idx"])):
        by_pair[(f["src"], f["dst"])].append(f)
    for f in flows:
        f["finish_us"] = None
    unmatched = 0

    if fins and all(x["flowid"] is not None for x in fins) \
            and all(f["id"] is not None for f in flows):
        method = "flowid"
        by_id = {f["id"]: f for f in flows}
        for x in fins:
            f = by_id.get(x["flowid"])
            if f is None or (f["src"], f["dst"]) != x["pair"]:
                unmatched += 1
                continue
            f["finish_us"] = x["t_us"]
        return method, unmatched

    if fins and all(x["logid"] is not None for x in fins):
        if len(fins) == len(flows):
            method = "logid-global"
            for f, x in zip(sorted(flows, key=lambda f: f["idx"]),
                            sorted(fins, key=lambda x: x["logid"])):
                if (f["src"], f["dst"]) != x["pair"]:
                    unmatched += 1
                    continue
                f["finish_us"] = x["t_us"]
            if not unmatched:
                return method, 0
            for f in flows:                        # fall through to per-pair
                f["finish_us"] = None
            unmatched = 0
        method = "logid-pair"
        by_pair_idx = defaultdict(list)
        for f in sorted(flows, key=lambda f: f["idx"]):
            by_pair_idx[(f["src"], f["dst"])].append(f)
        fp = defaultdict(list)
        for x in fins:
            fp[x["pair"]].append(x)
        for pair, xs in fp.items():
            fl = by_pair_idx.get(pair, [])
            for k, x in enumerate(sorted(xs, key=lambda x: x["logid"])):
                if k >= len(fl):
                    unmatched += 1
                    continue
                fl[k]["finish_us"] = x["t_us"]
        return method, unmatched

    method = "fifo-pair"
    fp = defaultdict(list)
    for x in fins:                                 # log order = finish order
        fp[x["pair"]].append(x)
    for pair, xs in fp.items():
        fl = by_pair.get(pair, [])
        for k, x in enumerate(xs):
            if k >= len(fl):
                unmatched += 1
                continue
            fl[k]["finish_us"] = x["t_us"]
    return method, unmatched


def check_starts(flows, starts):
    """Max |logged start - matrix start| over flows, matching per pair in order."""
    if not starts:
        return None, 0
    by_pair = defaultdict(list)
    for f in sorted(flows, key=lambda f: (f["start_ps"], f["idx"])):
        by_pair[(f["src"], f["dst"])].append(f["start_ps"] / 1e6)
    worst, n = 0.0, 0
    for pair, logged in starts.items():
        want = by_pair.get(pair, [])
        for a, b in zip(sorted(logged), want):
            # htsim prints times with 6 significant digits: allow for that
            worst = max(worst, abs(a - b) - 1e-5 * abs(b)); n += 1
    return worst, n


def overtakes(flows):
    """Pairs on which a later-started flow finished strictly earlier."""
    by_pair = defaultdict(list)
    for f in flows:
        if f["finish_us"] is not None:
            by_pair[(f["src"], f["dst"])].append(f)
    n = 0
    for fl in by_pair.values():
        fl.sort(key=lambda f: (f["start_ps"], f["idx"]))
        for a, b in zip(fl, fl[1:]):
            if b["finish_us"] < a["finish_us"] - 1e-6:
                n += 1
    return n


# --------------------------------------------------------------------------

def main():
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(2)
    log, out, tag, tm = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    link_gbps = float(sys.argv[5]) if len(sys.argv) > 5 else 400.0
    link_bps = link_gbps * 1e9
    stem = out[:-4] if out.endswith(".csv") else out
    req_out, sum_out = stem + "_requests.csv", stem + "_summary.json"

    flows = read_matrix(tm)
    if not flows or any(f["bytes"] is None for f in flows):
        die(f"no flows, or a flow without 'size', in {tm}")
    sidecar = attach_sidecar(flows, tm)

    fins, starts, violations, done, end_us = read_log(log)
    if violations:
        die(f"{violations} lossless violations. Raise -q / -pfc_thresholds and rerun.")
    if not fins:
        die(f"no completed flows found in {log}")

    skew, n_skew = check_starts(flows, starts)
    if skew is not None and skew > START_SKEW_TOL_US:
        die(f"logged flow start times differ from the matrix by up to "
            f"{skew:.1f} us ({n_skew} starts compared). The binary did not "
            f"honour the matrix 'start' values, so completion times are "
            f"meaningless.")

    method, unmatched = match(flows, fins)

    # per-flow
    match_viol = 0
    for f in flows:
        f["start_us"] = f["start_ps"] / 1e6
        f["floor_us"] = f["bytes"] * 8 / link_bps * 1e6
        f["deadline_us"] = f["deadline_ps"] / 1e6 if f.get("deadline_ps") is not None else None
        if f["finish_us"] is None:
            f["completion_us"] = f["x_floor"] = f["met"] = None
            continue
        f["completion_us"] = f["finish_us"] - f["start_us"]
        if f["completion_us"] < -1e-3:
            match_viol += 1
        f["x_floor"] = f["completion_us"] / f["floor_us"]
        f["met"] = None if f["deadline_us"] is None else int(f["finish_us"] <= f["deadline_us"])

    floor_mismatch = [f for f in flows if f.get("side_floor_ps")
                      and abs(f["side_floor_ps"] / 1e6 - f["floor_us"]) > 0.01 * f["floor_us"]]

    # per-request
    reqs = defaultdict(list)
    for f in flows:
        reqs[f["request_id"]].append(f)
    rows = []
    for rid in sorted(reqs):
        fl = reqs[rid]
        fin = [f for f in fl if f["finish_us"] is not None]
        complete = len(fin) == len(fl)
        start = min(f["start_us"] for f in fl)
        floor = max(f["floor_us"] for f in fl)
        r = {"request_id": rid, "n_shards": len(fl), "n_finished": len(fin),
             "start_us": start, "floor_us": floor,
             "bytes": sum(f["bytes"] for f in fl),
             "prompt_tokens": fl[0].get("prompt_tokens"),
             "tier": "+".join(sorted({f["tier"] for f in fl if f["tier"]})),
             "finish_us": None, "completion_us": None, "x_floor": None,
             "deadline_us": None, "met_deadline": None, "slowest_shard": None}
        dl = [f["deadline_us"] for f in fl if f["deadline_us"] is not None]
        r["deadline_us"] = max(dl) if dl else None
        if complete:
            last = max(fl, key=lambda f: f["finish_us"])
            r["finish_us"] = last["finish_us"]
            r["completion_us"] = r["finish_us"] - start
            r["x_floor"] = r["completion_us"] / floor
            r["slowest_shard"] = last["shard"]
            if r["deadline_us"] is not None:
                r["met_deadline"] = int(r["finish_us"] <= r["deadline_us"])
        rows.append(r)

    done_rows = [r for r in rows if r["completion_us"] is not None]
    if not done_rows:
        die(f"no request completed all its shards in {log}")

    # write per-flow CSV
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tag", "flow", "request_id", "shard", "src", "dst",
                    "start_us", "finish_us", "completion_us", "bytes",
                    "floor_us", "x_floor", "deadline_us", "met_deadline", "tier"])
        for f in sorted(flows, key=lambda f: f["idx"]):
            w.writerow([tag, f["id"] if f["id"] is not None else f["idx"],
                        f["request_id"], f["shard"], f["src"], f["dst"],
                        fmt(f["start_us"]), fmt(f["finish_us"]),
                        fmt(f["completion_us"]), f["bytes"], fmt(f["floor_us"], 1),
                        fmt(f["x_floor"]), fmt(f["deadline_us"]),
                        "" if f["met"] is None else f["met"], f["tier"]])

    with open(req_out, "w", newline="") as fh:
        w = csv.writer(fh)
        cols = ["request_id", "n_shards", "n_finished", "start_us", "finish_us",
                "completion_us", "bytes", "floor_us", "x_floor", "deadline_us",
                "met_deadline", "prompt_tokens", "tier", "slowest_shard"]
        w.writerow(["tag"] + cols)
        for r in rows:
            w.writerow([tag] + [fmt(r[c]) if isinstance(r[c], float) else
                                ("" if r[c] is None else r[c]) for c in cols])

    # summary
    xs = [r["x_floor"] for r in done_rows]
    cs = [r["completion_us"] for r in done_rows]
    with_dl = [r for r in done_rows if r["met_deadline"] is not None]
    if all(r["prompt_tokens"] for r in done_rows):
        jbasis = "completion_us/prompt_tokens"
        jx = [r["completion_us"] / r["prompt_tokens"] for r in done_rows]
    else:
        jbasis = "x_floor"
        jx = xs
    worst = max(done_rows, key=lambda r: r["x_floor"])
    flow_done = [f for f in flows if f["finish_us"] is not None]

    m = re.search(r"load(\d+)", tag)
    meta = (sidecar or {}).get("meta", {})
    summary = {
        "tag": tag,
        "policy": tag.split("_")[0],
        "load": int(m.group(1)) if m else None,
        "offered_load_realized": meta.get("offered_load_realized"),
        "log": os.path.abspath(log),
        "matrix": os.path.abspath(tm),
        "sidecar": sidecar is not None,
        "link_gbps": link_gbps,
        "flows_csv": os.path.basename(out),
        "requests_csv": os.path.basename(req_out),
        "n_flows": len(flows),
        "n_flows_finished": len(flow_done),
        "n_requests": len(rows),
        "n_requests_complete": len(done_rows),
        "slowest_flow_us": max(f["completion_us"] for f in flow_done),
        "last_finish_us": max(f["finish_us"] for f in flow_done),
        "request_x_floor": stats(xs),
        "request_completion_us": stats(cs),
        "flow_x_floor": stats([f["x_floor"] for f in flow_done]),
        "mean_x_floor": sum(xs) / len(xs),
        "worst_request": worst["request_id"],
        "deadline_miss_rate": (sum(1 - r["met_deadline"] for r in with_dl) / len(with_dl))
                              if with_dl else None,
        "n_deadline_checked": len(with_dl),
        "jain_fairness": jain(jx),
        "jain_basis": jbasis,
        "lossless_violations": violations,
        "match_method": method,
        "match_unmatched": unmatched,
        "match_violations": match_viol,
        "pair_overtakes": overtakes(flows),
        "start_skew_max_us": skew,
        "sim_end_flag_us": end_us,
        "sim_done": done,
        "wall_s": float(os.environ["WALL_S"]) if os.environ.get("WALL_S") else None,
    }
    with open(sum_out, "w") as fh:
        json.dump(summary, fh, indent=1)

    # report
    if len(flow_done) != len(flows):
        endtxt = f" ({end_us:.0f} us)" if end_us else ""
        print(f"  WARNING: only {len(flow_done)} of {len(flows)} flows finished "
              f"({len(rows) - len(done_rows)} requests incomplete, excluded from "
              f"the percentiles). The simulation probably hit -end{endtxt}.")
        print(f"  WARNING: raise it by changing '-end 2000000' (microseconds) in "
              f"scripts/run.sh, or check the log for a stall.")
    if unmatched:
        print(f"  WARNING: {unmatched} finish lines could not be matched to a matrix flow")
    if match_viol:
        print(f"  WARNING: {match_viol} flows matched with finish < start "
              f"(method {method}); the per-pair ordering assumption failed")
    if floor_mismatch:
        print(f"  WARNING: link {link_gbps:g} Gbps gives a floor that differs >1% from "
              f"the sidecar's floor_ps for {len(floor_mismatch)} flows")
    print(f"  flows {len(flow_done)}  last shard {worst['completion_us']:.1f} us  "
          f"floor {worst['floor_us']:.1f} us  = {worst['x_floor']:.2f}x floor")
    rx = summary["request_x_floor"]
    rc = summary["request_completion_us"]
    dm = summary["deadline_miss_rate"]
    print(f"  requests {len(done_rows)}/{len(rows)}  x floor p50 {rx['p50']:.2f}  "
          f"p95 {rx['p95']:.2f}  p99 {rx['p99']:.2f}  max {rx['max']:.2f}  |  "
          f"completion p50 {rc['p50']:.1f} us  p99 {rc['p99']:.1f} us")
    print(f"  deadline miss {'n/a' if dm is None else f'{dm:.2f}'}  "
          f"jain {summary['jain_fairness']:.3f}  match {method}"
          f"{'' if skew is None else f'  start skew {skew:.3f} us'}"
          f"  overtakes {summary['pair_overtakes']}")
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()
