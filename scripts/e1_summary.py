#!/usr/bin/env python3
"""Summarise E1 results (see scripts/e1.sh, docs/E1.md).

    e1_summary.py <results/e1>              write summary files, print tables
    e1_summary.py --links <trace.log>       reduce a -log queue_usage log to
                                            "queue mean_busy" lines (stdout)

Slowest shard = max completion over a run's flows (a TP transfer is done only
when its last shard lands). Median over repetitions; spread = (max-min)/min.
"""
import collections, csv, glob, os, re, statistics, sys

SIZE = 167772160
FLOOR = {"default": SIZE * 8 / 400e9 * 1e6, "ft128": SIZE * 8 / 100e9 * 1e6}


def floor_us(topo):
    return FLOOR["default"] if topo == "default" else FLOOR["ft128"]


def read_csv(fn):
    with open(fn) as f:
        return [(r["flow"], float(r["completion_us"])) for r in csv.DictReader(f)]


def violations(log):
    try:
        return open(log, errors="replace").read().count("LOSSLESS not working")
    except OSError:
        return None


def stats(vals):
    med = statistics.median(vals)
    return med, min(vals), max(vals), (max(vals) - min(vals)) / min(vals)


# ------------------------------------------------------------------ links
def reduce_links(log):
    tot, cnt = collections.defaultdict(float), collections.Counter()
    for line in open(log, errors="replace"):
        if not line[:1].isdigit():
            if line.startswith("Flow "):
                print("#", line.strip())
            continue
        p = line.split()
        if len(p) < 7:
            continue
        name = re.sub(r".*bytes\)", "", " ".join(p[1:-5]))
        try:
            tot[name] += float(p[-3])
        except ValueError:
            continue
        cnt[name] += 1
    for n in sorted(tot):
        print(f"{n} {tot[n] / cnt[n]:.3f}")


# ------------------------------------------------------------------ table
TAB = re.compile(r"^(roce|dcqcn|eqds|hpcc)_(default|ft128_\dos)_e1_(.+)_r(\d+)\.csv$")
SENS = re.compile(r"^dcqcn_sens_k(\d+)_r(\d+)\.csv$")
CTL = re.compile(r"^ctl100g_(roce|dcqcn|eqds)_(ft128_1os)_(.+)_r(\d+)\.csv$")

PAT_ORDER = ["single_sametor", "single_samepod", "single_crosspod", "tp8_samepod", "tp8_crosspod"]
TOPO_ORDER = ["ft128_1os", "ft128_4os", "ft128_8os", "default"]
CC_ORDER = ["roce", "dcqcn", "hpcc", "eqds"]


def collect(d, rx):
    groups = collections.defaultdict(list)
    for fn in sorted(glob.glob(os.path.join(d, "*.csv"))):
        m = rx.match(os.path.basename(fn))
        if not m:
            continue
        rows = read_csv(fn)
        v = violations(fn[:-4] + ".log")
        groups[m.groups()[:-1]].append((int(m.groups()[-1]), rows, v))
    return groups


def table(d):
    groups = collect(d, TAB)
    out = []
    for (cc, topo, pat), reps in groups.items():
        fl = floor_us(topo)
        slow = [max(t for _, t in rows) for _, rows, _ in reps]
        med, lo, hi, spread = stats(slow)
        # per-flow multiples of the floor in the median rep (collision signature)
        mrep = sorted(reps, key=lambda r: max(t for _, t in r[1]))[len(reps) // 2]
        mult = sorted(round(t / fl, 3) for _, t in mrep[1])
        out.append(dict(cc=cc, topo=topo, pattern=pat, n=len(reps), median_us=round(med, 1),
                        min_us=round(lo, 1), max_us=round(hi, 1), spread_pct=round(100 * spread, 1),
                        floor_us=round(fl, 1), x_floor=round(med / fl, 3),
                        violations=sum(v or 0 for _, _, v in reps),
                        per_flow_x_floor_median_rep=" ".join(f"{x:.3f}" for x in mult),
                        slowest_each_rep=" ".join(f"{s:.1f}" for s in slow)))
    out.sort(key=lambda r: (TOPO_ORDER.index(r["topo"]), PAT_ORDER.index(r["pattern"]),
                            CC_ORDER.index(r["cc"])))
    write(os.path.join(d, "summary.csv"), out)
    print("\n== E1 table (median slowest shard, x floor, spread over reps)")
    for topo in TOPO_ORDER:
        for pat in PAT_ORDER:
            cells = {r["cc"]: r for r in out if r["topo"] == topo and r["pattern"] == pat}
            if not cells:
                continue
            s = " | ".join(
                f"{cells[c]['median_us']:,.0f} us {cells[c]['x_floor']:.2f}x ({cells[c]['spread_pct']:.0f}%, n={cells[c]['n']})"
                if c in cells else "-" for c in CC_ORDER)
            print(f"| {topo} | {pat} | {s} |")
    print("\n== per-flow x floor (median rep)")
    for r in out:
        print(f"{r['cc']:6s} {r['topo']:10s} {r['pattern']:16s} {r['per_flow_x_floor_median_rep']}"
              f"   [slowest per rep: {r['slowest_each_rep']}]")
    return out


def sens(d):
    groups = collect(d, SENS)
    out = []
    for (k,), reps in sorted(groups.items(), key=lambda kv: int(kv[0][0])):
        fl = floor_us("default")
        slow = [max(t for _, t in rows) for _, rows, _ in reps]
        med, lo, hi, spread = stats(slow)
        mult = sorted(round(t / fl, 3) for _, t in reps[0][1])
        out.append(dict(ecn_k_pkts=int(k), n=len(reps), median_us=round(med, 1), x_floor=round(med / fl, 3),
                        spread_pct=round(100 * spread, 1), violations=sum(v or 0 for _, _, v in reps),
                        per_flow_x_floor=" ".join(f"{x:.3f}" for x in mult)))
    write(os.path.join(d, "sens_summary.csv"), out)
    print("\n== DCQCN -dcqcn K sweep, 16-node, TP=8 cross-pod")
    for r in out:
        print(r)


def ctl(d):
    groups = collect(d, CTL)
    out = []
    for (cc, topo, pat), reps in sorted(groups.items()):
        fl = floor_us(topo)
        slow = [max(t for _, t in rows) for _, rows, _ in reps]
        med, lo, hi, spread = stats(slow)
        mult = sorted(round(t / fl, 3) for _, t in reps[0][1])
        out.append(dict(cc=cc, topo=topo, pattern=pat, n=len(reps), median_us=round(med, 1),
                        x_floor=round(med / fl, 3), spread_pct=round(100 * spread, 1),
                        violations=sum(v or 0 for _, _, v in reps),
                        per_flow_x_floor=" ".join(f"{x:.3f}" for x in mult)))
    write(os.path.join(d, "ctl_summary.csv"), out)
    print("\n== 128-node controls with -linkspeed 100000")
    for r in out:
        print(r)


SEED = re.compile(r"^seed_(roce|dcqcn|eqds|hpcc)_(default|ft128_\dos)_(.+)_s(\d+)\.csv$")


def seeds(d):
    """Paired comparison across ECMP placements (-seed)."""
    res = collections.defaultdict(dict)
    for fn in sorted(glob.glob(os.path.join(d, "seed_*.csv"))):
        m = SEED.match(os.path.basename(fn))
        if not m:
            continue
        cc, topo, pat, sd = m.groups()
        res[(topo, pat)].setdefault(int(sd), {})[cc] = max(t for _, t in read_csv(fn)) / floor_us(topo)
    out, lines = [], []
    for (topo, pat), bys in sorted(res.items()):
        lines.append(f"== {topo} {pat}: slowest shard x floor per seed")
        for sd in sorted(bys):
            r = bys[sd]
            row = dict(topo=topo, pattern=pat, seed=sd, **{c: round(r.get(c, float("nan")), 3) for c in ("roce", "dcqcn", "eqds")})
            if "roce" in r and "dcqcn" in r:
                row["dcqcn_over_roce"] = round(r["dcqcn"] / r["roce"], 3)
            out.append(row)
            lines.append("  " + " ".join(f"{k}={v}" for k, v in row.items() if k not in ("topo", "pattern")))
        for c in ("roce", "dcqcn", "eqds"):
            v = [bys[s][c] for s in bys if c in bys[s]]
            if v:
                lines.append(f"  {c}: median {statistics.median(v):.3f}x  min {min(v):.3f}  max {max(v):.3f}  n={len(v)}")
        rat = [bys[s]["dcqcn"] / bys[s]["roce"] for s in bys if "roce" in bys[s] and "dcqcn" in bys[s]]
        if rat:
            lines.append(f"  dcqcn/roce paired: median {statistics.median(rat):.3f} min {min(rat):.3f} max {max(rat):.3f}"
                         f"  dcqcn slower in {sum(x > 1.001 for x in rat)}/{len(rat)} seeds")
    write(os.path.join(d, "seeds_summary.csv"), out)
    print("\n" + "\n".join(lines))


# ------------------------------------------------------------------ paths
def paths(d):
    """From trace/<topo>_<pattern>_f<i>.links: each flow's data path, the
    number of flows per link, and the work-conserving lower bound on the
    slowest shard, L_max x (per-flow wire time) + RTT."""
    lines = []
    groups = collections.defaultdict(dict)
    for fn in sorted(glob.glob(os.path.join(d, "trace", "*.links"))):
        m = re.match(r"(.+)_f(\d+)\.links$", os.path.basename(fn))
        busy = []
        for line in open(fn):
            if line.startswith("#"):
                continue
            q, u = line.rsplit(None, 1)
            # data path = queues busy most of the run; host NIC queue (SRC*) excluded
            if float(u) > 0.5 and not q.startswith("SRC"):
                busy.append(q)
        groups[m.group(1)][int(m.group(2))] = busy
    for g, flows in sorted(groups.items()):
        load = collections.Counter(q for qs in flows.values() for q in qs)
        lines.append(f"== {g}")
        for i in sorted(flows):
            hot = max((load[q] for q in flows[i]), default=0)
            lines.append(f"  flow {i}: max link load {hot}  path " + " ".join(flows[i]))
        shared = {q: n for q, n in load.items() if n > 1}
        lines.append("  shared links: " + (", ".join(f"{q} x{n}" for q, n in sorted(shared.items())) or "none"))
        lines.append(f"  L_max = {max(load.values()) if load else 0}")
    open(os.path.join(d, "paths.txt"), "w").write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


# ------------------------------------------------------------------ sinks
def sinks(d):
    """Goodput time series (10 us bins) -> per-run summary: per-flow mean rate in
    the first 1 ms, and, for each group of flows that share a bottleneck in the
    16-node trace, how often the group's aggregate is below 95% of 400 Gbps."""
    lines = []
    for fn in sorted(glob.glob(os.path.join(d, "sinks", "*.csv"))):
        series = collections.defaultdict(list)
        with open(fn) as f:
            for r in csv.DictReader(f):
                series[r["flow"]].append((float(r["t_us"]), int(r["cack_pkts"]), float(r["goodput_gbps"])))
        name = os.path.basename(fn)[:-4]
        lines.append(f"== {name}")
        done = {}
        for fl, s in series.items():
            last = max(c for _, c, _ in s)
            done[fl] = next((t for t, c, _ in s if c >= last), None)
        for fl in sorted(series, key=lambda x: int(x.split("_")[0])):
            s = series[fl]
            early = [g for t, _, g in s if 0 < t <= 1000]
            lines.append(f"  {fl:6s} mean goodput 0-1ms {statistics.mean(early):6.1f} Gbps, "
                         f"last ack by ~{done[fl]:.0f} us")
        # bottleneck groups from paths.txt for the 16-node tree (see docs/E1.md)
        for grp in (["0_8", "2_10"], ["1_9", "3_11"], ["4_12", "6_14", "7_15"]):
            if not all(g in series for g in grp):
                continue
            agg = collections.defaultdict(float)
            for g in grp:
                for t, c, gb in series[g]:
                    agg[t] += gb
            end = max(done[g] for g in grp)
            pts = [(t, v) for t, v in sorted(agg.items()) if 0 < t <= end - 20]
            below = sum(1 for _, v in pts if v < 0.95 * 400 * 4000 / 4064)
            mean = statistics.mean(v for _, v in pts)
            lines.append(f"  bottleneck {'+'.join(grp)}: aggregate goodput mean {mean:.1f} Gbps "
                         f"until {end:.0f} us; below 95% of payload line rate in {below}/{len(pts)} bins")
    open(os.path.join(d, "sinks_summary.txt"), "w").write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


EQDSX = re.compile(r"^eqdsx_(default|ft128_\dos)_tp8_crosspod_p(\d+)_q(\d+)_ls(\d+)\.out$")


def eqdsx(d):
    """EQDS spraying-width / buffer / -linkspeed grid (e1.sh eqdsx). A run is
    valid only if the binary exited 0 AND all 8 flows finished; aborted
    (rc 134, eqds.cpp:876) or stalled runs are reported, not averaged."""
    out, lines = [], ["== EQDS -paths / -q / -linkspeed grid, TP=8 cross-pod (single run each, deterministic)"]
    for fn in sorted(os.listdir(d)):
        m = EQDSX.match(fn)
        if not m:
            continue
        topo, p, q, ls = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        txt = open(os.path.join(d, fn)).read()
        rc = re.search(r"rc[= ]+(\d+)", txt)
        rc = int(rc.group(1)) if rc else -1
        log = os.path.join(d, fn[:-4] + ".log")
        fin, rtx, new = [], "", ""
        if os.path.exists(log):
            for line in open(log, errors="replace"):
                f = re.match(r"^Flow .* finished at ([0-9.]+)", line)
                if f:
                    fin.append(float(f.group(1)))
                r = re.match(r"^New: (\d+) Rtx: (\d+)", line)
                if r:
                    new, rtx = int(r.group(1)), int(r.group(2))
        valid = rc == 0 and len(fin) == 8
        fl = floor_us(topo)
        x = round(max(fin) / fl, 3) if valid else ""
        out.append(dict(topo=topo, paths=p, q=q, linkspeed_gbps=ls, rc=rc,
                        flows_finished=len(fin), valid=int(valid),
                        slowest_us=round(max(fin), 1) if valid else "", x_floor=x,
                        new_pkts=new, rtx_pkts=rtx))
        lines.append("  %-10s p%-4d q%-5d ls%-4d rc %-4d finished %d/8  %s  rtx %s" % (
            topo, p, q, ls, rc, len(fin), ("%.2fx" % x) if valid else "INVALID", rtx))
    write(os.path.join(d, "eqdsx_summary.csv"), out)
    print("\n" + "\n".join(lines))


# ------------------------------------------------------------------ seeds table
# Table runs made with E1_SEEDS (scripts/e1.sh): <cc>_<topo>_e1_<pattern>_s<seed>.csv
TABS = re.compile(r"^(roce|dcqcn|eqds|hpcc)_(default|ft128_\dos)_e1_(.+)_s(\d+)\.csv$")


def log_counters(log):
    """EQDS end-of-run counters (New/Rtx/RTS, stale NACKs); empty for others."""
    c = {}
    try:
        for line in open(log, errors="replace"):
            m = re.match(r"^New: (\d+) Rtx: (\d+) RTS: (\d+)", line)
            if m:
                c.update(new=int(m[1]), rtx=int(m[2]), rts=int(m[3]))
            m = re.match(r"^Stale NACKs ignored: (\d+)", line)
            if m:
                c["stale_nacks"] = int(m[1])
    except OSError:
        pass
    return c


def seeds_table(d):
    """Median over seeds (E1_SUMMARY_SEEDS, default 1-5) per cell. The
    collision class of a seed is slowest / (the policy's own uncontended
    single-flow cross-pod x floor on that fabric), rounded; for HPCC and
    DCQCN it is RoCE's class at the same seed (same ECMP placement)."""
    want = [int(x) for x in os.environ.get("E1_SUMMARY_SEEDS", "1 2 3 4 5").split()]
    runs = collections.defaultdict(dict)
    for fn in sorted(glob.glob(os.path.join(d, "*_e1_*_s*.csv"))):
        m = TABS.match(os.path.basename(fn))
        if not m:
            continue
        cc, topo, pat, sd = m.group(1), m.group(2), m.group(3), int(m.group(4))
        rows = read_csv(fn)
        log = fn[:-4] + ".log"
        runs[(cc, topo, pat)][sd] = (rows, violations(log), log_counters(log))
    if not runs:
        return
    single = {}
    for (cc, topo, pat), by in runs.items():
        if pat == "single_crosspod":
            v = [max(t for _, t in r[0]) / floor_us(topo) for s, r in by.items() if s in want]
            if v:
                single[(cc, "default" if topo == "default" else "ft128")] = statistics.median(v)
    out = []
    for (cc, topo, pat), by in runs.items():
        fl = floor_us(topo)
        sds = [s for s in want if s in by]
        if not sds:
            continue
        x1 = single.get((cc, "default" if topo == "default" else "ft128"))
        slow = {s: max(t for _, t in by[s][0]) for s in sds}
        med, lo, hi, spread = stats(list(slow.values()))
        cls = {s: (round(slow[s] / fl / x1) if x1 else "") for s in sds}
        # Pinned (per-flow ECMP) transports share RoCE's placement at a given
        # seed, so their class is RoCE's L_max at that seed: HPCC's own ratio
        # also contains its multi-bottleneck losses and would misclassify.
        rb, rx1 = runs.get(("roce", topo, pat), {}), single.get(("roce", "default" if topo == "default" else "ft128"))
        if cc in ("hpcc", "dcqcn") and rx1:
            for s in sds:
                if s in rb:
                    cls[s] = round(max(t for _, t in rb[s][0]) / fl / rx1)
        ctr = [by[s][2] for s in sds]
        def cmed(k):
            v = [c[k] for c in ctr if k in c]
            return int(statistics.median(v)) if v else ""
        nflows = {len(by[s][0]) for s in sds}
        out.append(dict(cc=cc, topo=topo, pattern=pat, seeds=" ".join(map(str, sds)), n=len(sds),
                        flows=" ".join(map(str, sorted(nflows))),
                        median_us=round(med, 1), min_us=round(lo, 1), max_us=round(hi, 1),
                        spread_pct=round(100 * spread, 1), floor_us=round(fl, 1), x_floor=round(med / fl, 3),
                        x_each_seed=" ".join(f"{slow[s] / fl:.3f}" for s in sds),
                        single_x=round(x1, 4) if x1 else "",
                        class_each_seed=" ".join(f"{cls[s]}x" for s in sds),
                        x_over_class=" ".join(f"{slow[s] / fl / cls[s]:.3f}" if cls[s] else "" for s in sds),
                        violations=sum(by[s][1] or 0 for s in sds),
                        new_pkts_median=cmed("new"), rtx_median=cmed("rtx"), rts_median=cmed("rts"),
                        stale_nacks_median=cmed("stale_nacks"),
                        rtx_each_seed=" ".join(str(c.get("rtx", "")) for c in ctr)))
    out.sort(key=lambda r: (TOPO_ORDER.index(r["topo"]), PAT_ORDER.index(r["pattern"]),
                            CC_ORDER.index(r["cc"])))
    write(os.path.join(d, "summary_seeds.csv"), out)
    print(f"\n== E1 table, seeds {want} (median slowest shard us, x floor, [min-max x], classes)")
    for r in out:
        xs = [float(x) for x in r["x_each_seed"].split()]
        print(f"| {r['cc']:5s} | {r['topo']:9s} | {r['pattern']:16s} | {r['median_us']:>9,.0f} | {r['x_floor']:.3f} | "
              f"[{min(xs):.3f}-{max(xs):.3f}] | {r['class_each_seed']} | viol {r['violations']} | rtx {r['rtx_median']} |")


SENSF = re.compile(r"^dcqcn_default_sens_(step|red)_k(\d+)_s(\d+)\.csv$")


def sens_final(d):
    """Final-DCQCN K sweep (e1.sh sweep with E1_SWEEP_MODES): median over seeds."""
    g = collections.defaultdict(dict)
    for fn in glob.glob(os.path.join(d, "dcqcn_default_sens_*_s*.csv")):
        m = SENSF.match(os.path.basename(fn))
        if m:
            g[(m[1], int(m[2]))][int(m[3])] = (max(t for _, t in read_csv(fn)), violations(fn[:-4] + ".log"))
    out = []
    fl = floor_us("default")
    for (mode, k), by in sorted(g.items()):
        sds = sorted(by)
        xs = [by[s][0] for s in sds]
        med = statistics.median(xs)
        out.append(dict(mode=mode, k_pkts=k, seeds=" ".join(map(str, sds)), median_us=round(med, 1),
                        x_floor=round(med / fl, 3), x_each_seed=" ".join(f"{x / fl:.3f}" for x in xs),
                        violations=sum(by[s][1] or 0 for s in sds)))
    write(os.path.join(d, "sens_summary_final.csv"), out)
    if out:
        print("\n== final DCQCN K sweep (step: -dcqcn K, ECN_STEP=1; red: Kmax = 4K KB)")
        for r in out:
            print(r)


def write(fn, rows):
    if not rows:
        return
    with open(fn, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    if sys.argv[1] == "--links":
        reduce_links(sys.argv[2])
        sys.exit(0)
    d = sys.argv[1]
    table(d)
    sens(d)
    ctl(d)
    seeds(d)
    paths(d)
    sinks(d)
    eqdsx(d)
    seeds_table(d)
    sens_final(d)
