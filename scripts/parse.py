#!/usr/bin/env python3
"""Parse an htsim run log into per-flow and per-request CSVs.

Usage: parse.py <logfile> <out.csv> <tag> <traffic-matrix.cm> [link_gbps]

Flow sizes come from the traffic matrix or its sidecar, NOT from the log.
Different htsim transports print different things under the label "total
bytes": RoCE prints a packet count there, EQDS prints real bytes. The .cm
file always is right.

If <traffic-matrix>.json exists, which gen_workload.py always writes, this
also

  * gives every flow its own floor, since a generated workload has a
    different cache size per request rather than one size for all flows,
  * groups shards back into requests and reports the request completion
    time, which is the MAX over its shards, because a decode instance cannot
    start until the last shard lands,
  * checks deadlines,
  * refuses if the simulator did not finish every flow that was emitted.

Refuses to emit results when the run reported lossless violations, because
those mean PFC failed and the numbers are not comparable.
"""
import csv
import json
import os
import re
import sys

log, out, tag, tm = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
link_bps = float(sys.argv[5]) * 1e9 if len(sys.argv) > 5 else 400e9

text = open(log, errors="replace").read()
violations = text.count("LOSSLESS not working")
if violations:
    print("  REFUSING: %d lossless violations. "
          "Raise -q / -pfc_thresholds and rerun." % violations)
    sys.exit(1)

# "Flow Roce_3_67 flowId 12 2989 finished at 13650.5 ..."   (roce, hpcc)
# "Flow eqdsSrc flowId 12 ... finished at 13650.5 ..."      (eqds)
# Older binaries print no flowId, so it stays optional.
done = re.findall(r"Flow (\S+)(?:\s+flowId\s+(\d+))?.*?finished at ([\d.]+)",
                  text)
if not done:
    print("  REFUSING: no completed flows found in %s" % log)
    sys.exit(1)

sidecar = os.path.splitext(tm)[0] + ".json"
meta = None
if os.path.exists(sidecar):
    with open(sidecar) as fh:
        blob = json.load(fh)
    meta = {f["flow_id"]: f for f in blob["flows"]}
    config = blob["config"]
else:
    config = None

sizes = [int(m) for m in re.findall(r"\bsize\s+(\d+)", open(tm).read())]
if not sizes:
    print("  REFUSING: no 'size' entries found in %s" % tm)
    sys.exit(1)

if meta is not None and all(fid for _, fid, _ in done):
    # ---- sidecar path: per-flow floors and request level results ----
    missing = set(meta) - {int(fid) for _, fid, _ in done}
    if missing:
        print("  REFUSING: %d of %d flows never finished (ids %s%s)"
              % (len(missing), len(meta), sorted(missing)[:5],
                 " ..." if len(missing) > 5 else ""))
        sys.exit(1)

    rows = []
    for name, fid, t in done:
        m = meta[int(fid)]
        floor_us = m["bytes"] * 8 / link_bps * 1e6
        rows.append({
            "tag": tag, "flow": name, "flow_id": int(fid),
            "request_id": m["request_id"], "shard": m["shard"],
            "prompt_tokens": m["prompt_tokens"], "bytes": m["bytes"],
            "start_us": m["start_s"] * 1e6,
            "completion_us": float(t),
            "duration_us": float(t) - m["start_s"] * 1e6,
            "floor_us": round(floor_us, 1),
            "x_floor": round((float(t) - m["start_s"] * 1e6) / floor_us, 3),
            "deadline_us": m["deadline_s"] * 1e6,
            "met_deadline": int(float(t) <= m["deadline_s"] * 1e6),
        })

    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # group shards back into requests
    by_req = {}
    for r in rows:
        q = by_req.setdefault(r["request_id"], [])
        q.append(r)
    reqs = []
    for rid, shards in sorted(by_req.items()):
        last = max(s["completion_us"] for s in shards)
        start = min(s["start_us"] for s in shards)
        floor = shards[0]["floor_us"]
        reqs.append({
            "tag": tag, "request_id": rid, "shards": len(shards),
            "prompt_tokens": shards[0]["prompt_tokens"],
            "bytes_total": sum(s["bytes"] for s in shards),
            "start_us": round(start, 1),
            "completion_us": round(last, 1),
            "duration_us": round(last - start, 1),
            "floor_us": floor,
            "x_floor": round((last - start) / floor, 3),
            "deadline_us": round(shards[0]["deadline_us"], 1),
            "met_deadline": int(last <= shards[0]["deadline_us"]),
            "slowest_minus_fastest_us": round(
                last - min(s["completion_us"] for s in shards), 1),
        })
    req_out = os.path.splitext(out)[0] + "_requests.csv"
    with open(req_out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(reqs[0]))
        w.writeheader()
        w.writerows(reqs)

    xs = sorted(r["x_floor"] for r in reqs)
    met = sum(r["met_deadline"] for r in reqs)
    straggle = sorted(r["slowest_minus_fastest_us"] for r in reqs)
    print("  flows %d  requests %d" % (len(rows), len(reqs)))
    print("  x floor  median %.2f  p99 %.2f  max %.2f"
          % (xs[len(xs) // 2], xs[min(len(xs) - 1, int(0.99 * len(xs)))], xs[-1]))
    print("  deadlines met %d/%d (%.1f %%)"
          % (met, len(reqs), 100.0 * met / len(reqs)))
    print("  shard straggle median %.1f us, max %.1f us"
          % (straggle[len(straggle) // 2], straggle[-1]))
    if config and config.get("target_load") is not None:
        print("  target load %.2f" % config["target_load"])
    print("  wrote %s and %s" % (out, req_out))
    sys.exit(0)

# ---- fallback: no sidecar, one size for every flow ----
size = max(sizes)
floor_us = size * 8 / link_bps * 1e6
if len(done) != len(sizes):
    print("  WARNING: %d flows finished but matrix has %d"
          % (len(done), len(sizes)))
with open(out, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["tag", "flow", "completion_us", "bytes", "floor_us", "x_floor"])
    for name, _fid, t in done:
        w.writerow([tag, name, t, size, "%.1f" % floor_us,
                    "%.3f" % (float(t) / floor_us)])
last = max(float(t) for _, _f, t in done)
print("  flows %d  last shard %.1f us  floor %.1f us  = %.2fx floor"
      % (len(done), last, floor_us, last / floor_us))
print("  wrote %s" % out)
