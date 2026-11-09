#!/usr/bin/env python3
"""Convergence analysis for the HPCC fidelity runs (HTSIM/OUR_CHANGES.md, HPCC fidelity section).

Usage: hpcc_fidelity_analyze.py <rundir> <converge_stagger.cm> <figdir>

A thin wrapper around scripts/validation_analyze.py (unchanged): same
convergence / Jain / queue metrics and the same summary.md layout, but for
the HPCC variants run by the fidelity study instead of the four policies of
docs/VALIDATION.md. <rundir> holds <tag>.log and raw/<tag>.{dat,idmap}.gz
exactly as validation.sh writes them, with tags
  hpcclegacy_s<seed>   -hpcc_legacy (original HTSIM window update)
  hpcc_s<seed>         paper Algorithm 1, defaults (T = 13.1 us, N = 10)
  hpccT12_s<seed>      paper Algorithm 1 with -hpcc_T 12 (the old HTSIM T)
  hpccN2_s<seed>       paper Algorithm 1 with -hpcc_N 2 (5x larger W_AI)
Adds two tables validation_analyze.py does not compute:
  - the sender NIC queues (SRC<n>->LS<m>), which INT never sees and where the
    legacy window overflow parked 486 MB;
  - utilization convergence, the property the HPCC paper claims is fast
    (MIMD, "rapid convergence to a Pareto optimal rate allocation, followed
    by a slower convergence to fairness"): after each join/departure, the
    time until aggregate goodput stays within 10% of HPCC's own target
    eta x 400 x 9000/9064 = 377 Gbps for 300 us, plus the peak receiver
    downlink queue in the 500 us after the event and the time from the
    event until that queue is back at <= 2 packets.
"""
import os, re, sys, gzip
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_analyze as va  # noqa: E402

ORDER = ["hpcclegacy", "hpcc", "hpccT12", "hpccN2"]
va.CC_ORDER = ORDER
va.CC_LABEL.update({"hpcclegacy": "HPCC legacy (-hpcc_legacy)",
                    "hpcc": "HPCC paper Alg. 1 (default, T=13.1us, N=10)",
                    "hpccT12": "HPCC paper Alg. 1, T=12us",
                    "hpccN2": "HPCC paper Alg. 1, N=2 (W_AI 15.6 KB)"})
_pc, _pq = va.plot_convergence, va.plot_queue
va.plot_convergence = lambda runs, flows, figdir, ccs: _pc(runs, flows, figdir, [c for c in ORDER if c in runs])
va.plot_queue = lambda runs, flows, figdir, ccs: _pq(runs, flows, figdir, [c for c in ORDER if c in runs])


def nic_queues(out, tag, flows):
    """Peak and 'all flows active' median of every sender NIC queue (KB)."""
    pre, a = va.read_dat(os.path.join(out, "raw", tag + ".dat.gz"))
    idmap = {}
    for line in gzip.open(os.path.join(out, "raw", tag + ".idmap.gz"), "rt"):
        p = line.rstrip("\n").split(" ", 1)
        if len(p) == 2:
            idmap[int(p[0])] = p[1]
    starts, fins, _ = va.read_stdout(os.path.join(out, tag + ".log"), flows[0]["dst"])
    t0 = max(starts.values()) + 500
    t1 = min(fins.values()) if fins else 1e18
    q = a[(a["type"] == 5) & (a["ev"] == 500)]
    res = []
    for f in flows:
        name = [n for n in idmap.values() if re.fullmatch(r"SRC%d->LS\d+\(0\)" % f["src"], n)][0]
        qid = [i for i, n in idmap.items() if n == name][0]
        r = q[q["id"] == qid]
        tt = r["t"] * 1e6
        sel = (tt >= t0) & (tt < t1)
        res.append((f["id"], name, float(r["v3"].max()) / 1e3 if len(r) else 0.0,
                    float(np.median(r["v1"][sel])) / 1e3 if sel.any() else 0.0))
    return res


TARGET = 0.95 * va.LINK_GBPS * 9000 / 9064


def util_conv(run, flows):
    ev = va.events(run, flows)
    bt = run["bt"]
    agg = sum(run["rate"][f["src"]] for f in flows)
    hold = int(va.HOLD_US / va.BIN_US)
    ok = np.abs(agg - TARGET) <= va.TOL * TARGET
    tg, qd = run["tgrid"], run["qd"]
    rows = []
    for k, (t, kind, s) in enumerate(ev):
        if k == 0 or (kind == "leave" and k == len(ev) - 1):
            continue
        t_next = ev[k + 1][0] if k + 1 < len(ev) else run["end_us"]
        c = None
        for i in range(np.searchsorted(bt, t - 1e-9), len(bt) - hold + 1):
            if bt[i] + hold * va.BIN_US > t_next:
                break
            if ok[i:i + hold].all():
                c = bt[i] - t
                break
        w = (tg >= t) & (tg < min(t + 500, t_next))
        pk = float(qd[w].max()) / 1e3 if w.any() else float("nan")
        ipk = np.argmax(np.where(w, qd, -1))
        after = np.nonzero((tg > tg[ipk]) & (qd <= 2 * 9064 + 1))[0]
        drain = float(tg[after[0]] - t) if len(after) else float("nan")
        rows.append((kind, s, c, pk, drain))
    return rows


def main():
    out, tm, figdir = sys.argv[1:4]
    os.makedirs(figdir, exist_ok=True)
    sys.argv = [sys.argv[0], out, tm, figdir]
    va.main()
    flows = va.read_matrix(tm)
    tags = sorted(os.path.basename(p)[:-4] for p in __import__("glob").glob(os.path.join(out, "*_s*.log")))
    lines = ["", "## Sender NIC queues (KB): peak over the run / median while all 4 flows active", "",
             "| run | " + " | ".join(f"flow{f['id']} NIC peak" for f in flows) + " | "
             + " | ".join(f"flow{f['id']} NIC median" for f in flows) + " |",
             "|---" * (1 + 2 * len(flows)) + "|"]
    for tag in tags:
        if not os.path.exists(os.path.join(out, "raw", tag + ".dat.gz")):
            continue
        r = nic_queues(out, tag, flows)
        lines.append(f"| {tag} | " + " | ".join(f"{x[2]:.0f}" for x in r) + " | "
                     + " | ".join(f"{x[3]:.0f}" for x in r) + " |")
    lines += ["", "## Utilization convergence after each event (paper's 'fast convergence')", "",
              f"Aggregate goodput within 10% of {TARGET:.0f} Gbps (eta x 400 x payload fraction), held 300 us; "
              "receiver downlink LS4->DST8 peak in the 500 us after the event, and time from the event until "
              "it is back at <= 2 packets. Median [min-max] over seeds.", "",
              "| policy | event | aggregate within 10% of target (us) | queue peak after event (KB) | queue back to <= 2 pkts (us) |",
              "|---|---|---|---|---|"]
    from collections import defaultdict
    by = defaultdict(list)
    for tag in tags:
        if not os.path.exists(os.path.join(out, "raw", tag + ".dat.gz")):
            continue
        run = va.analyze_run(out, tag, flows)
        sid = {f["src"]: f["id"] for f in flows}
        nj = nl = 0
        for kind, s, c, pk, dr in util_conv(run, flows):
            if kind == "join":
                nj += 1; ev = f"join flow{sid[s]}"
            else:
                nl += 1; ev = f"departure {nl}"
            by[(tag.rsplit("_s", 1)[0], ev)].append((c, pk, dr))

    def med(v):
        v = [x for x in v if x is not None and x == x]
        return f"{np.median(v):.0f} [{min(v):.0f}-{max(v):.0f}]" if v else "-"
    for cc in ORDER:
        for key in sorted([k for k in by if k[0] == cc], key=lambda k: (k[1].startswith("dep"), k[1])):
            r = by[key]
            nc = sum(1 for x in r if x[0] is None)
            lines.append(f"| {va.CC_LABEL[cc]} | {key[1]} | {med([x[0] for x in r])}{f', nc {nc}/{len(r)}' if nc else ''} | "
                         f"{med([x[1] for x in r])} | {med([x[2] for x in r])} |")
    with open(os.path.join(out, "summary.md"), "a") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
