#!/usr/bin/env python3
"""Parse an htsim run log into a per-flow CSV.

Usage: parse.py <logfile> <out.csv> <tag> [link_gbps]

Refuses to emit results when the run reported lossless violations, because
those mean PFC failed and the numbers are not comparable.
"""
import csv, re, sys

log, out, tag = sys.argv[1], sys.argv[2], sys.argv[3]
link_bps = float(sys.argv[4]) * 1e9 if len(sys.argv) > 4 else 400e9

text = open(log, errors="replace").read()
violations = text.count("LOSSLESS not working")
flows = re.findall(r"Flow (\S+)(?: \d+)? finished at ([\d.]+) total bytes (\d+)", text)

if violations:
    print(f"  REFUSING: {violations} lossless violations. "
          f"Raise -q / -pfc_thresholds and rerun.")
    sys.exit(1)
if not flows:
    print("  REFUSING: no completed flows found. Check the log.")
    sys.exit(1)

times = [float(t) for _, t, _ in flows]
size = int(flows[0][2])
floor_us = size * 8 / link_bps * 1e6

with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["tag", "flow", "completion_us", "bytes", "floor_us", "x_floor"])
    for name, t, b in flows:
        w.writerow([tag, name, t, b, f"{floor_us:.1f}", f"{float(t)/floor_us:.3f}"])

last = max(times)
print(f"  flows {len(flows)}  last shard {last:.1f} us  "
      f"floor {floor_us:.1f} us  = {last/floor_us:.2f}x floor")
print(f"  wrote {out}")
