#!/usr/bin/env python3
"""Decode a pfc_diag.sh run: where PFC paused the fabric, and which flows stalled.

  pfc_analyze.py --dat run.dat --idmap idmap.txt --log run.log --tm matrix.cm
                 --gbps G --pfc-high 150 --out <stem>

Log format (HTSIM logfile.cpp, as in scripts/validation_analyze.py): a text
preamble ending "# TRACE\\n", then 44-byte records
<double t_s, u32 type, u32 id, u32 ev+100*type, double v1, v2, v3>.

Records used, one of each per queue every 10 us (QueueLoggerSampling,
loggers.cpp:191-219):
  type 5 ev 500  QUEUE_RANGE   v1 = occupancy at the sample (bytes),
                               v2 / v3 = min / max occupancy seen at the
                               enqueue/service events since the last sample
                               (= v1 if there were no events)
  type 4 ev 400  CUM_TRAFFIC   v1 = cumulative arrived work, in seconds of
                               this queue's drain time (loggers.cpp:240)
and per RoCE sink every -logtime (RoceSinkLoggerSampling, loggers.cpp:1570):
  type 26        v1 = cumulative ACK (packets), v3 = delivered rate in
                 PACKETS/s (RoceSink::total_received() counts packets,
                 roce.cpp; loggers.cpp:1584), converted with the MTU.

Pause inference. Work served in a sample interval is
    served = d(cum arrived work) - d(occupancy) * 8 / rate.
A lossless egress queue (queue_lossless_output.cpp:150-180) or a host NIC
queue (FairPriorityQueue, queue.cpp:434-540) that holds packets during the
whole interval (min occupancy > 0) and still serves less than the interval
can only have been stopped by a PAUSE frame: neither has any other reason
to idle with a backlog (no CC, no scheduler gaps). So
    paused = interval - served,   counted only when min occupancy > 0
is a LOWER bound on PFC pause time (pauses in intervals where the queue also
emptied are not counted). An "episode" is a maximal run of samples with
paused > 50% of the interval.

Queue classes (fat_tree_topology.cpp setName): SRC<h>->LS<t> host NIC,
LS<t>->US<a> ToR uplink, US<a>->CS<c> agg uplink, CS<c>->US<a> core
downlink, US<a>->LS_<t> agg downlink, LS<t>->DST<h> ToR downlink.

Outputs <stem>_queues.csv (per queue), <stem>_flows.csv (per flow: stalled
fraction of its lifetime = 50 us sink samples with zero delivery), and
<stem>_pfc.md (per-class totals and the most paused queues).
"""
import argparse, collections, csv, re
import numpy as np

REC = np.dtype([("t", "<f8"), ("type", "<u4"), ("id", "<u4"), ("ev", "<u4"),
                ("v1", "<f8"), ("v2", "<f8"), ("v3", "<f8")])
CLASSES = [("host NIC (SRC->ToR)", r"SRC\d+->LS\d+"), ("ToR uplink (ToR->agg)", r"LS\d+->US\d+"),
           ("agg uplink (agg->core)", r"US\d+->CS\d+"), ("core downlink (core->agg)", r"CS\d+->US\d+"),
           ("agg downlink (agg->ToR)", r"US\d+->LS_\d+"), ("ToR downlink (ToR->host)", r"LS\d+->DST\d+")]


def qclass(name):
    for c, rx in CLASSES:
        if re.fullmatch(rx + r"\(\d+\)", name):
            return c
    return None


def main():
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--resummarize":
        return resummarize(sys.argv[2])
    ap = argparse.ArgumentParser()
    for a in ("--dat", "--idmap", "--log", "--tm", "--out"):
        ap.add_argument(a, required=True)
    ap.add_argument("--gbps", type=float, required=True)
    ap.add_argument("--pfc-high", type=int, default=150)
    args = ap.parse_args()

    raw = np.memmap(args.dat, dtype=np.uint8, mode="r")
    head = bytes(raw[:200000])
    k = head.index(b"# TRACE\n") + len(b"# TRACE\n")
    pre = head[:k].decode(errors="replace")
    n = (len(raw) - k) // REC.itemsize
    a = np.frombuffer(raw, dtype=REC, count=n, offset=k)
    pkt = int(re.search(r"# pktsize=(\d+)", pre)[1])
    idmap = {}
    for line in open(args.idmap):
        p = line.rstrip("\n").split(" ", 1)
        if len(p) == 2:
            idmap[int(p[0])] = p[1]

    # flows: matrix order per pair; sink ids ascend in matrix order
    flows = []
    for line in open(args.tm):
        m = re.match(r"\s*(\d+)->(\d+)\s+id\s+(\d+)\s+start\s+(\S+)\s+size\s+(\d+)", line)
        if m:
            flows.append(dict(src=int(m[1]), dst=int(m[2]), id=int(m[3]),
                              start_us=float(m[4]) / 1e6, bytes=int(m[5])))
    fins = collections.defaultdict(list)
    for line in open(args.log, errors="replace"):
        m = re.search(r"Flow Roce_(\d+)_(\d+) (\d+) finished at ([0-9.e+-]+)", line)
        if m:
            fins[(int(m[1]), int(m[2]))].append((int(m[3]), float(m[4])))
    sinks = collections.defaultdict(list)
    for i, nm in idmap.items():
        m = re.fullmatch(r"Roce_sink_(\d+)_(\d+)", nm)
        if m:
            sinks[(int(m[1]), int(m[2]))].append(i)
    byp = collections.defaultdict(list)
    for f in flows:
        byp[(f["src"], f["dst"])].append(f)
    for pr, fl in byp.items():
        sk = sorted(sinks.get(pr, []))
        fn = sorted(fins.get(pr, []))           # by source object id = matrix order
        for j, f in enumerate(fl):
            f["sink"] = sk[j] if j < len(sk) else None
            f["finish_us"] = fn[j][1] if j < len(fn) else None
    last_fin = max([f["finish_us"] for f in flows if f["finish_us"]] or [a["t"].max() * 1e6])

    # ---- queues
    bps = args.gbps * 1e9
    rng = a[(a["type"] == 5) & (a["ev"] == 500)]
    cum = a[(a["type"] == 4) & (a["ev"] == 400)]
    rng = rng[np.argsort(rng["id"], kind="stable")]
    cum = cum[np.argsort(cum["id"], kind="stable")]
    rows = []
    hi_bytes = args.pfc_high * pkt
    ids_r, starts_r = np.unique(rng["id"], return_index=True)
    ids_c, starts_c = np.unique(cum["id"], return_index=True)
    cidx = dict(zip(ids_c, starts_c))
    ends_r = list(starts_r[1:]) + [len(rng)]
    ends_c = dict(zip(ids_c, list(starts_c[1:]) + [len(cum)]))
    for qid, s0, s1 in zip(ids_r, starts_r, ends_r):
        name = idmap.get(int(qid), "")
        cl = qclass(name)
        if cl is None or qid not in cidx:
            continue
        R = rng[s0:s1]
        C = cum[cidx[qid]:ends_c[qid]]
        m = min(len(R), len(C))
        R, C = R[:m], C[:m]
        keep = R["t"] * 1e6 <= last_fin + 10
        R, C = R[keep], C[keep]
        if len(R) < 2:
            continue
        dt = np.diff(R["t"])
        darr = np.diff(C["v1"])
        dq = np.diff(R["v1"]) * 8 / bps
        served = np.clip(darr - dq, 0, None)
        backlog = R["v2"][1:] > 0
        paused = np.where(backlog, np.clip(dt - served, 0, None), 0.0)
        pf = paused / dt
        ep = int(np.sum((pf[1:] > 0.5) & (pf[:-1] <= 0.5)) + (pf[0] > 0.5))
        busy = served.sum()
        rows.append(dict(queue=name, cls=cl, paused_ms=paused.sum() * 1e3, episodes=ep,
                         served_ms=busy * 1e3,
                         util=busy / (R["t"][-1] - R["t"][0]),
                         max_pkts=R["v3"].max() / pkt,
                         frac_above_pfc_high=float(np.mean(R["v1"] > hi_bytes))))
    with open(args.out + "_queues.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in sorted(rows, key=lambda r: -r["paused_ms"]):
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})

    # ---- flows
    snk = a[a["type"] == 26]
    snk = snk[np.argsort(snk["id"], kind="stable")]
    sid, sst = np.unique(snk["id"], return_index=True)
    send = dict(zip(sid, list(sst[1:]) + [len(snk)]))
    sst = dict(zip(sid, sst))
    frows = []
    for f in flows:
        if f.get("sink") is None or f["finish_us"] is None or f["sink"] not in sst:
            continue
        S = snk[sst[f["sink"]]:send[f["sink"]]]
        t = S["t"] * 1e6
        live = (t > f["start_us"] + 60) & (t <= f["finish_us"])
        rate = S["v3"][live]
        frows.append(dict(src=f["src"], dst=f["dst"], id=f["id"], start_us=f["start_us"],
                          finish_us=f["finish_us"],
                          x_floor=(f["finish_us"] - f["start_us"]) / (f["bytes"] * 8 / bps * 1e6),
                          samples=int(live.sum()),
                          stalled_frac=float(np.mean(rate == 0)) if live.any() else float("nan"),
                          mean_gbps=float(rate.mean() * pkt * 8 / 1e9) if live.any() else float("nan")))
    with open(args.out + "_flows.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(frows[0]) if frows else ["src"])
        w.writeheader()
        for r in frows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})

    write_summary(args.out, rows, frows, last_fin, args.pfc_high, pkt)


def write_summary(stem, rows, frows, last_fin, pfc_high, pkt):
    T = last_fin / 1e3
    by = collections.defaultdict(list)
    for r in rows:
        by[r["cls"]].append(r)
    out = [f"# PFC diagnostic: {stem.split('/')[-1]}", "",
           f"Window 0 - {last_fin:.0f} us (last flow finish). {len(rows)} queues sampled every 10 us. "
           f"Paused time is a lower bound (scripts/pfc_analyze.py docstring). "
           f"PFC high threshold {pfc_high} pkts x {pkt} B. Active = utilization > 5% (ACK-only queues excluded). "
           f"paused share = paused / (paused + served): the fraction of the time a queue "
           f"had packets to send that PFC kept it silent.", "",
           "| queue class | queues (active) | active queues paused >1% of window | total paused ms | "
           "max paused ms (one queue) | pause episodes | mean util. of active | paused share of active |",
           "|---|---|---|---|---|---|---|---|"]
    for c, _ in CLASSES:
        rs = by.get(c, [])
        if not rs:
            continue
        act = [r for r in rs if r["util"] > 0.05] or rs
        ps = sum(r["paused_ms"] for r in act)
        sv = sum(r["served_ms"] for r in act)
        out.append(f"| {c} | {len(rs)} ({len(act)}) | {sum(r['paused_ms'] > 0.01 * T for r in act)} | "
                   f"{sum(r['paused_ms'] for r in rs):.1f} | {max(r['paused_ms'] for r in rs):.1f} | "
                   f"{sum(r['episodes'] for r in rs)} | {np.mean([r['util'] for r in act]):.3f} | "
                   f"{ps / (ps + sv) if ps + sv > 0 else 0:.3f} |")
    out += ["", "Most paused queues:", "", "| queue | class | paused ms | episodes | utilization | max occupancy pkts |",
            "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -r["paused_ms"])[:12]:
        out.append(f"| {r['queue']} | {r['cls']} | {r['paused_ms']:.2f} | {r['episodes']} | "
                   f"{r['util']:.3f} | {r['max_pkts']:.0f} |")
    if frows:
        sf = [r["stalled_frac"] for r in frows if r["samples"] and r["stalled_frac"] == r["stalled_frac"]]
        if sf:
            out += ["", f"Flows: {len(frows)}; fraction of 50 us sink samples with zero delivery "
                    f"while the flow was live: median {np.median(sf):.3f}, p90 {np.percentile(sf, 90):.3f}, "
                    f"max {max(sf):.3f}."]
    open(stem + "_pfc.md", "w").write("\n".join(out) + "\n")
    print("\n".join(out))


def resummarize(stem, pfc_high=150, pkt=4000):
    """Rebuild <stem>_pfc.md from <stem>_queues.csv / _flows.csv (run.dat is gone)."""
    rows = []
    for r in csv.DictReader(open(stem + "_queues.csv")):
        for k in ("paused_ms", "served_ms", "util", "max_pkts", "frac_above_pfc_high"):
            r[k] = float(r[k])
        r["episodes"] = int(r["episodes"])
        rows.append(r)
    frows = []
    try:
        for r in csv.DictReader(open(stem + "_flows.csv")):
            r["samples"] = int(r["samples"]); r["stalled_frac"] = float(r["stalled_frac"])
            r["finish_us"] = float(r["finish_us"])
            frows.append(r)
    except (OSError, KeyError, ValueError):
        pass
    last = max([r["finish_us"] for r in frows] or [0])
    write_summary(stem, rows, frows, last, pfc_high, pkt)


if __name__ == "__main__":
    main()
