#!/usr/bin/env python3
"""Summarize a sweep's raw repetitions into median and spread.

Usage: summarize.py <raw file> <traffic-matrix.cm> [link_gbps]
"""
import re, statistics, sys
from collections import defaultdict

raw, tm = sys.argv[1], sys.argv[2]
link_bps = float(sys.argv[3]) * 1e9 if len(sys.argv) > 3 else 400e9

sizes = [int(m) for m in re.findall(r'\bsize\s+(\d+)', open(tm).read())]
floor_us = max(sizes) * 8 / link_bps * 1e6

vals = defaultdict(list)
for line in open(raw):
    cc, v = line.split()
    vals[cc].append(float(v))

print(f"floor = {floor_us:.1f} us  (size {max(sizes):,} B at {link_bps/1e9:.0f} Gbps)")
print()
print(f"{'policy':<8}{'n':>3}{'median us':>12}{'min':>10}{'max':>10}"
      f"{'x floor':>10}{'spread':>9}")
for cc in ("eqds", "hpcc", "roce", "dcqcn"):
    v = vals.get(cc)
    if not v:
        continue
    med = statistics.median(v)
    spread = (max(v) - min(v)) / med * 100
    print(f"{cc:<8}{len(v):>3}{med:>12.1f}{min(v):>10.1f}{max(v):>10.1f}"
          f"{med/floor_us:>10.2f}{spread:>8.0f}%")
print()
print("Spread is (max-min)/median across repetitions. Anything above a few")
print("percent means single runs are not trustworthy for that policy.")
