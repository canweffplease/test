#!/usr/bin/env python3
"""Parse an htsim run log into a per-flow CSV.

Usage: parse.py <logfile> <out.csv> <tag> <traffic-matrix.cm> [link_gbps]

Flow sizes come from the traffic matrix, NOT from the log. Different htsim
transports print different things under the label "total bytes" -- RoCE prints
a packet count there, EQDS prints real bytes -- so the log is not a reliable
source of size. The .cm file always is.

Refuses to emit results when the run reported lossless violations, because
those mean PFC failed and the numbers are not comparable.
"""
import csv, re, sys

log, out, tag, tm = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
link_bps = float(sys.argv[5]) * 1e9 if len(sys.argv) > 5 else 400e9

# sizes straight from the traffic matrix
sizes = [int(m) for m in re.findall(r'\bsize\s+(\d+)', open(tm).read())]
if not sizes:
    print(f"  REFUSING: no 'size' entries found in {tm}")
    sys.exit(1)
size = max(sizes)                       # per-shard size; shards are equal here
floor_us = size * 8 / link_bps * 1e6

text = open(log, errors="replace").read()
violations = text.count("LOSSLESS not working")

# permissive: handles "Flow X <id> finished at T ..." (RoCE/HPCC) and
# "Flow X flowId N eqdsSrc N finished at T ..." (EQDS)
flows = re.findall(r"Flow (\S+).*?finished at ([\d.]+)", text)

if violations:
    print(f"  REFUSING: {violations} lossless violations. "
          f"Raise -q / -pfc_thresholds and rerun.")
    sys.exit(1)
if not flows:
    print(f"  REFUSING: no completed flows found in {log}")
    sys.exit(1)
if len(flows) != len(sizes):
    print(f"  WARNING: {len(flows)} flows finished but matrix has {len(sizes)}")

with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["tag", "flow", "completion_us", "bytes", "floor_us", "x_floor"])
    for name, t in flows:
        w.writerow([tag, name, t, size, f"{floor_us:.1f}",
                    f"{float(t)/floor_us:.3f}"])

last = max(float(t) for _, t in flows)
print(f"  flows {len(flows)}  last shard {last:.1f} us  "
      f"floor {floor_us:.1f} us  = {last/floor_us:.2f}x floor")
print(f"  wrote {out}")
