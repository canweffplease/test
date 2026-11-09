#!/usr/bin/env python3
"""Summarize a sweep's raw repetitions into median and spread.

Usage: summarize.py <raw file> <traffic-matrix.cm> [link_gbps]

The raw file (written by sweep.sh) has one "<cc> <value>" line per rep, where
value is the "last shard" number parse.py prints: the completion time of the
run's worst request. Its x floor here is value / floor of the largest flow in
the matrix, which is exact whenever every request has the same floor (true
for all fixed-prompt matrices). For mixed prompt lengths use the second
table, read from parse.py's <tag>_summary.json files.

Second table: if <cc>_<topo>_<load>_r<i>[_strat][_s<seed>]_summary.json files
sit next to the raw file (sweep.sh names runs <load>_r<i>), the per-request
distance-from-floor distribution is summarized too: the median across reps
of each run's p50 / p99 / max request x_floor, plus deadline miss rate and
Jain's index. Runs with a STRAT or PATHS suffix are listed as separate
variants; the _s<seed> suffix is treated as the rep. These files are not reset by sweep.sh, so the table can
include older reps of the same configuration; the n column says how many.
"""
import glob, json, os, re, statistics, sys
from collections import defaultdict

ORDER = ("eqds", "hpcc", "roce", "dcqcn")

raw, tm = sys.argv[1], sys.argv[2]
link_bps = float(sys.argv[3]) * 1e9 if len(sys.argv) > 3 else 400e9

sizes = [int(m) for m in re.findall(r'\bsize\s+(\d+)', open(tm).read())]
floor_us = max(sizes) * 8 / link_bps * 1e6

vals = defaultdict(list)
for line in open(raw):
    parts = line.split()
    if len(parts) != 2:
        continue
    vals[parts[0]].append(float(parts[1]))

print(f"floor = {floor_us:.1f} us  (size {max(sizes):,} B at {link_bps/1e9:.0f} Gbps)")
if len(set(sizes)) > 1:
    print("NOTE: flow sizes differ; the x floor column below assumes the largest.")
print()
print("worst request per run (parse.py 'last shard'):")
print(f"{'policy':<8}{'n':>3}{'median us':>12}{'min':>10}{'max':>10}"
      f"{'x floor':>10}{'spread':>9}")
for cc in ORDER + tuple(sorted(set(vals) - set(ORDER))):
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

# ---- per-request distribution from the summary JSONs ----------------------
stem = os.path.basename(raw)
m = re.fullmatch(r"sweep_(.+)\.raw", stem)
if not m:
    sys.exit(0)
tl = m.group(1)                                   # "<topo>_<load>[<strat>]"
rows = defaultdict(list)                          # (cc, variant) -> summaries
tm_abs = os.path.abspath(tm)
rawdir = os.path.dirname(os.path.abspath(raw))
for p in glob.glob(os.path.join(rawdir, "*_r*_summary.json")):
    # tag = <cc>_<topo>_<load>_r<i>[_<strat>][_p<paths>][_s<seed>]; sweep.sh
    # passes SEED=i, so the seed is the rep and not a variant
    mm = re.fullmatch(r"([a-z]+)_(.+)_r\d+(.*?)(?:_s\d+)?_summary\.json", os.path.basename(p))
    if not mm:
        continue
    cc, topo_load, var = mm.groups()
    if topo_load + var != tl and topo_load != tl:
        continue
    try:
        s = json.load(open(p))
    except (OSError, ValueError):
        continue
    if os.path.abspath(s.get("matrix", "")) != tm_abs:
        continue
    rows[(cc, var)].append(s)
if not rows:
    sys.exit(0)


def med(key, sub=None):
    def f(ss):
        xs = [(s[key][sub] if sub else s[key]) for s in ss]
        xs = [x for x in xs if x is not None]
        return statistics.median(xs) if xs else None
    return f


def show(x, w, nd=2):
    return f"{'-':>{w}}" if x is None else f"{x:>{w}.{nd}f}"


print()
print("per-request distance from floor (median across reps of each statistic):")
print(f"{'policy':<16}{'n':>3}{'req':>5}{'p50':>8}{'p99':>8}{'max':>8}"
      f"{'miss':>7}{'jain':>7}")
for (cc, var) in sorted(rows, key=lambda k: (ORDER.index(k[0]) if k[0] in ORDER else 9, k[1])):
    ss = rows[(cc, var)]
    print(f"{(cc + var):<16}{len(ss):>3}{statistics.median(s['n_requests_complete'] for s in ss):>5.0f}"
          f"{show(med('request_x_floor', 'p50')(ss), 8)}"
          f"{show(med('request_x_floor', 'p99')(ss), 8)}"
          f"{show(med('request_x_floor', 'max')(ss), 8)}"
          f"{show(med('deadline_miss_rate')(ss), 7)}"
          f"{show(med('jain_fairness')(ss), 7, 3)}")
