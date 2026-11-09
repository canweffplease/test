#!/usr/bin/env python3
"""Analyze the convergence validation runs written by scripts/validation.sh.

Usage: validation_analyze.py <results/validation> <converge_stagger.cm> <figdir>

Inputs per run tag <cc>_s<seed> (cc in dcqcn, hpcc, eqds, roce, hpccpfc):
  <tag>.log             run stdout (per-ACK HPCC CWND lines filtered out)
  raw/<tag>.dat.gz      HTSIM binary logfile (-o), see "Log format" below
  raw/<tag>.idmap.gz    idmap.txt the run wrote (object id -> name)

Log format (HTSIM logfile.cpp): a text preamble ending "# TRACE\\n", then
44-byte records <double time_s, u32 type, u32 id, u32 ev, double v1, v2, v3>
(ev is stored as ev + 100*type). Records used:
  type 26/28/41 (ROCE_SINK / HPCC_SINK / EQDS_SINK), the per-sink samplers
      (-log sink, every -logtime = 10 us): v1 = sink->cumulative_ack().
      Units differ: RoCE/DCQCN count packets (RoceSink::_cumulative_ack is a
      packet seqno, roce.cpp:438), HPCC counts bytes (hpcc.cpp:498), EQDS
      counts packets (_expected_epsn, eqds.h:383). Converted to bytes with a
      per-run unit = flow size / final cumulative ACK of flows that finished.
  type 5 ev 0 (QUEUE_APPROX / QUEUE_RANGE), QueueLoggerSampling every 10 us
      (-log tor_downqueue, loggers.cpp:191): v1 = queue occupancy in bytes at
      the sample, v2/v3 = min/max over the interval.
Flow start/finish times come from the run stdout ("startflow ... at",
"... starting at", "... finished at"), not from the matrix.

Outputs: <out>/convergence.csv, <out>/queues.csv, <out>/phases.csv,
<out>/summary.md, <out>/timeseries/<tag>_{rate,queue}.csv,
<figdir>/validation_convergence.{png,pdf}, <figdir>/validation_queue.{png,pdf}.
"""
import csv, glob, gzip, os, re, sys
from collections import defaultdict
import numpy as np

LINK_GBPS = 400.0
SAMPLE_US = 10.0          # -logtime 0.01 ms and the queue logger period
BIN_US = 50.0             # throughput bin
TOL = 0.10                # "within 10% of fair share"
HOLD_US = 300.0           # must stay within tolerance this long to count
CC_ORDER = ["dcqcn", "hpcc", "eqds", "roce", "hpccpfc"]
CC_LABEL = {"dcqcn": "DCQCN", "hpcc": "HPCC", "eqds": "EQDS",
            "roce": "RoCE, no CC (PFC only)", "hpccpfc": "HPCC, PFC 80/150"}
SINK_TYPES = {26, 28, 41}   # ROCE_SINK, HPCC_SINK, EQDS_SINK (loggertypes.h)
REC = np.dtype([("t", "<f8"), ("type", "<u4"), ("id", "<u4"), ("ev", "<u4"),
                ("v1", "<f8"), ("v2", "<f8"), ("v3", "<f8")])
# reference palette slots 1-4 (light), see the dataviz skill's palette.md
FLOW_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def read_matrix(path):
    flows = []
    for line in open(path):
        m = re.match(r"\s*(\d+)->(\d+)\s+id\s+(\d+).*?size\s+(\d+)", line)
        if m:
            flows.append(dict(src=int(m[1]), dst=int(m[2]), id=int(m[3]), size=int(m[4])))
    return flows


def read_dat(path):
    raw = gzip.open(path, "rb").read()
    k = raw.index(b"# TRACE\n") + len(b"# TRACE\n")
    pre = raw[:k].decode(errors="replace")
    n = (len(raw) - k) // REC.itemsize
    return pre, np.frombuffer(raw[k:k + n * REC.itemsize], dtype=REC)


def read_stdout(path, dst):
    starts, fins = {}, {}
    for line in open(path, errors="replace"):
        m = re.search(r"startflow [A-Za-z]+_(\d+)_(\d+) at ([-0-9.e+]+)", line) or \
            re.search(r"Flow [A-Za-z]+_(\d+)_(\d+) flowId \d+ .*?starting at ([-0-9.e+]+)", line)
        if m and int(m[2]) == dst:
            starts.setdefault(int(m[1]), float(m[3]))
        m = re.search(r"Flow [A-Za-z]+_(\d+)_(\d+) .*?finished at ([-0-9.e+]+)", line)
        if m and int(m[2]) == dst:
            fins[int(m[1])] = float(m[3])
    viol = sum(1 for l in open(path, errors="replace") if "LOSSLESS not working" in l)
    return starts, fins, viol


def analyze_run(out, tag, flows):
    dst = flows[0]["dst"]
    pre, a = read_dat(os.path.join(out, "raw", tag + ".dat.gz"))
    idmap = {}
    for line in gzip.open(os.path.join(out, "raw", tag + ".idmap.gz"), "rt"):
        p = line.rstrip("\n").split(" ", 1)
        if len(p) == 2:
            idmap[int(p[0])] = p[1]
    starts, fins, viol = read_stdout(os.path.join(out, tag + ".log"), dst)
    mtu = int(re.search(r"# pktsize=(\d+)", pre)[1])
    sink_of = {}
    for m in re.finditer(r": [A-Za-z]+_sink_(\d+)_(\d+)=(\d+)", pre):
        if int(m[2]) == dst:
            sink_of[int(m[1])] = int(m[3])
    T = round(a["t"].max() * 1e6 / SAMPLE_US) + 1           # samples
    tgrid = np.arange(T) * SAMPLE_US                         # us
    cum = {}
    units = []
    for f in flows:
        r = a[(a["id"] == sink_of[f["src"]]) & np.isin(a["type"], list(SINK_TYPES))]
        idx = np.rint(r["t"] * 1e6 / SAMPLE_US).astype(int)
        c = np.zeros(T)
        c[idx] = r["v1"]
        c = np.maximum.accumulate(c)
        cum[f["src"]] = c
        if f["src"] in fins and c[-1] > 0:
            units.append(f["size"] / c[-1])
    unit = float(np.median(units))
    for k in cum:
        cum[k] = cum[k] * unit
    # throughput per BIN_US bin (Gbps)
    step = int(BIN_US / SAMPLE_US)
    nb = (T - 1) // step
    bt = np.arange(nb) * BIN_US                              # bin start, us
    rate = {k: (c[step:nb * step + 1:step] - c[0:nb * step:step]) * 8 / (BIN_US * 1e3)
            for k, c in cum.items()}
    # queues
    qrec = a[(a["type"] == 5) & (a["ev"] == 500)]
    qid = [i for i, n in idmap.items() if re.fullmatch(r"LS\d+->DST%d\(0\)" % dst, n)][0]
    qq = qrec[qrec["id"] == qid]
    qd = np.zeros(T)
    qd[np.rint(qq["t"] * 1e6 / SAMPLE_US).astype(int)] = qq["v1"]
    switch_ids = [i for i, n in idmap.items()
                  if re.match(r"(LS|US|CS)\d+->", n) and not n.startswith("Pipe")]
    sw = qrec[np.isin(qrec["id"], switch_ids)]
    qtot = np.zeros(T)
    np.add.at(qtot, np.rint(sw["t"] * 1e6 / SAMPLE_US).astype(int), sw["v1"])
    qmat = {}
    for i in switch_ids:
        r = sw[sw["id"] == i]
        if len(r):
            x = np.zeros(T)
            x[np.rint(r["t"] * 1e6 / SAMPLE_US).astype(int)] = r["v1"]
            qmat[idmap[i]] = x
    peak = defaultdict(float)
    for i, v in zip(sw["id"], sw["v3"]):
        peak[int(i)] = max(peak[int(i)], v)
    over = a[(a["type"] == 5) & (a["ev"] == 501) & (a["id"] == qid)]
    qbuf = float(over["v3"].max()) if len(over) else float("nan")
    return dict(tag=tag, starts=starts, fins=fins, viol=viol, mtu=mtu, unit=unit,
                tgrid=tgrid, bt=bt, rate=rate, qd=qd, qtot=qtot, qname=idmap[qid],
                qbuf=qbuf, qmat=qmat, peak={idmap[i]: v for i, v in peak.items()}, end_us=tgrid[-1])


def active_at(run, flows, t0, t1):
    """srcs of flows active over the whole of [t0, t1) (us)."""
    return [f["src"] for f in flows
            if run["starts"].get(f["src"], 1e18) <= t0 and run["fins"].get(f["src"], 1e18) >= t1]


def events(run, flows):
    ev = [(t, "join", s) for s, t in run["starts"].items()]
    ev += [(t, "leave", s) for s, t in run["fins"].items()]
    ev.sort()
    return ev


def converge(run, flows, t_ev, t_next, who):
    """First bin start t >= t_ev from which every flow in `who` stays within
    TOL of 400/n for HOLD_US, with the hold window ending before t_next.
    Returns (t - t_ev) in us, or None."""
    bt = run["bt"]
    act = active_at(run, flows, t_ev + 1e-6, t_ev + 1e-6)
    n = len(act)
    fair = LINK_GBPS / n
    hold = int(HOLD_US / BIN_US)
    ok = np.ones(len(bt), bool)
    for s in who:
        r = run["rate"][s]
        ok &= np.abs(r - fair) <= TOL * fair
    first = np.searchsorted(bt, t_ev - 1e-9)       # first bin starting at/after the event
    for i in range(first, len(bt) - hold + 1):
        if bt[i] + hold * BIN_US > t_next:
            return None
        if ok[i:i + hold].all():
            return bt[i] - t_ev
    return None


def run_metrics(run, flows):
    ev = events(run, flows)
    src_to_id = {f["src"]: f["id"] for f in flows}
    rows = []
    nleave = 0
    for k, (t, kind, s) in enumerate(ev):
        if kind == "leave" and k == len(ev) - 1:
            continue
        t_next = ev[k + 1][0] if k + 1 < len(ev) else run["end_us"]
        act = active_at(run, flows, t + 1e-6, t + 1e-6)
        if not act:
            continue
        c_new = converge(run, flows, t, t_next, [s]) if kind == "join" else None
        c_all = converge(run, flows, t, t_next, act)
        # Jain's index and per-flow mean rate over the second half of the phase
        h0, h1 = t + (t_next - t) / 2, t_next
        sel = (run["bt"] >= h0) & (run["bt"] + BIN_US <= h1)
        means = [float(run["rate"][a][sel].mean()) if sel.any() else float("nan") for a in act]
        x = np.array(means)
        jain = float(x.sum() ** 2 / (len(x) * (x ** 2).sum())) if sel.any() else float("nan")
        if kind == "leave":
            nleave += 1
        label = f"join flow{src_to_id[s]}" if kind == "join" else f"departure {nleave}"
        rows.append(dict(tag=run["tag"], event=label, flow=src_to_id[s], t_event_us=round(t, 1),
                         n_active=len(act), fair_gbps=round(LINK_GBPS / len(act), 1),
                         phase_us=round(t_next - t, 1),
                         conv_new_us="" if kind != "join" else ("" if c_new is None else round(c_new, 1)),
                         conv_new_ok="" if kind != "join" else int(c_new is not None),
                         conv_all_us="" if c_all is None else round(c_all, 1),
                         conv_all_ok=int(c_all is not None),
                         jain_2nd_half=round(jain, 4),
                         agg_gbps_2nd_half=round(float(np.nansum(x)), 1),
                         rates_2nd_half=" ".join(f"{src_to_id[a]}:{m:.1f}" for a, m in zip(act, means))))
    return rows


def queue_metrics(run, flows):
    ev = events(run, flows)
    joins = sorted(t for t, k, _ in ev if k == "join")
    leaves = sorted(t for t, k, _ in ev if k == "leave")
    tg, qd, qt = run["tgrid"], run["qd"], run["qtot"]
    out = []
    windows = [("all flows active", joins[-1] + 500, leaves[0] if leaves else run["end_us"]),
               ("whole run", 0, leaves[-1] if leaves else run["end_us"])]
    for name, a, b in windows:
        sel = (tg >= a) & (tg < b)
        if not sel.any():
            continue
        d, s = qd[sel] / 1e3, qt[sel] / 1e3
        hot = max(run["qmat"].items(), key=lambda kv: np.median(kv[1][sel]))
        out.append(dict(tag=run["tag"], window=name, t0_us=round(a, 1), t1_us=round(b, 1),
                        queue=run["qname"], mtu=run["mtu"], buffer_kb=round(run["qbuf"] / 1e3, 1),
                        q_median_kb=round(float(np.median(d)), 1),
                        q_p99_kb=round(float(np.percentile(d, 99)), 1),
                        q_max_kb=round(float(d.max()), 1),
                        q_median_pkts=round(float(np.median(d)) * 1e3 / run["mtu"], 1),
                        fabric_median_kb=round(float(np.median(s)), 1),
                        fabric_p99_kb=round(float(np.percentile(s, 99)), 1),
                        hottest_queue=hot[0],
                        hottest_median_kb=round(float(np.median(hot[1][sel])) / 1e3, 1)))
    return out


def fair_step(run, flows, t):
    n = np.array([len(active_at(run, flows, x, x + BIN_US)) for x in t])
    return np.where(n > 0, LINK_GBPS / np.maximum(n, 1), np.nan)


def style(ax):
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def plot_convergence(runs, flows, figdir, ccs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(ccs), 1, figsize=(8.0, 2.1 * len(ccs) + 0.6), sharex=True)
    tmax = max(max(r["fins"].values(), default=r["end_us"]) for r in runs.values()) / 1e3 + 0.5
    for ax, cc in zip(axes, ccs):
        run = runs[cc]
        style(ax)
        t = run["bt"]
        fair = fair_step(run, flows, t)
        ax.step((t + BIN_US) / 1e3, fair, where="pre", color=INK2, lw=1.0, ls=(0, (4, 3)),
                label="fair share 400/n")
        for j, f in enumerate(flows):
            s = f["src"]
            st, fi = run["starts"].get(s), run["fins"].get(s, run["end_us"])
            if st is None:
                continue
            sel = (t + BIN_US > st) & (t < fi)
            ax.plot((t[sel] + BIN_US / 2) / 1e3, run["rate"][s][sel], color=FLOW_COLORS[j], lw=1.3,
                    label=f"flow {f['id']} ({s}→{f['dst']})")
            ax.axvline(st / 1e3, color=FLOW_COLORS[j], lw=0.6, ls=":")
        ax.set_ylim(0, 440)
        ax.set_yticks([0, 100, 133, 200, 400])
        ax.set_yticklabels(["0", "100", "133", "200", "400"])
        ax.set_ylabel("goodput, Gbps", color=INK2, fontsize=8)
        ax.set_title(f"{CC_LABEL[cc]}  ({run['tag']})", loc="left", fontsize=9, color=INK)
    axes[-1].set_xlim(0, tmax)
    axes[-1].set_xlabel("time (ms)", color=INK2, fontsize=8)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", ncol=5, fontsize=7.5, frameon=False,
               bbox_to_anchor=(0.5, 1.0), labelcolor=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(figdir, f"validation_convergence.{ext}"), dpi=160)
    plt.close(fig)


def plot_queue(runs, flows, figdir, ccs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(ccs), 1, figsize=(8.0, 2.0 * len(ccs) + 0.6), sharex=True)
    tmax = max(max(r["fins"].values(), default=r["end_us"]) for r in runs.values()) / 1e3 + 0.5
    for ax, cc in zip(axes, ccs):
        run = runs[cc]
        style(ax)
        tg = run["tgrid"] / 1e3
        ax.plot(tg, run["qd"] / 1e3, color=FLOW_COLORS[0], lw=0.9,
                label=f"receiver ToR downlink ({run['qname']})")
        ax.plot(tg, run["qtot"] / 1e3, color=FLOW_COLORS[1], lw=0.7, alpha=0.8,
                label="sum over all switch queues")
        if cc == "dcqcn":
            k = 100 * run["mtu"] / 1e3
            ax.axhline(k, color=INK2, lw=0.9, ls=(0, (4, 3)))
            ax.text(tmax - 0.1, k, f"ECN mark threshold K = 100 pkts = {k:.0f} KB",
                    ha="right", va="bottom", fontsize=7, color=INK2)
        for f in flows:
            if f["src"] in run["starts"]:
                ax.axvline(run["starts"][f["src"]] / 1e3, color=GRID, lw=0.8, ls=":")
        top = max(float(run["qtot"].max()), float(run["qd"].max())) / 1e3
        ax.set_ylim(0, max(top * 1.15, 10))
        ax.set_ylabel("KB queued", color=INK2, fontsize=8)
        ax.set_title(f"{CC_LABEL[cc]}  ({run['tag']}), egress buffer per queue "
                     f"{run['qbuf']/1e3:.0f} KB = 500 x {run['mtu']} B; y-scale differs per panel",
                     loc="left", fontsize=8.5, color=INK)
    axes[-1].set_xlim(0, tmax)
    axes[-1].set_xlabel("time (ms)", color=INK2, fontsize=8)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", ncol=2, fontsize=7.5, frameon=False, labelcolor=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(figdir, f"validation_queue.{ext}"), dpi=160)
    plt.close(fig)


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    out, tm, figdir = sys.argv[1], sys.argv[2], sys.argv[3]
    plot_seed = os.environ.get("PLOT_SEED", "1")
    os.makedirs(figdir, exist_ok=True)
    os.makedirs(os.path.join(out, "timeseries"), exist_ok=True)
    flows = read_matrix(tm)
    conv, queues = [], []
    runs_plot = {}
    tags = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(out, "*_s*.log")))
    bad = []
    for tag in tags:
        cc = tag.rsplit("_s", 1)[0]
        if not os.path.exists(os.path.join(out, "raw", tag + ".dat.gz")):
            continue
        run = analyze_run(out, tag, flows)
        if run["viol"]:
            bad.append((tag, run["viol"]))
            print(f"  INVALID {tag}: {run['viol']} lossless violations, skipped")
            continue
        missing = [f["src"] for f in flows if f["src"] not in run["starts"]]
        if missing:
            print(f"  WARNING {tag}: flows from {missing} never started")
        conv += run_metrics(run, flows)
        queues += queue_metrics(run, flows)
        if tag.endswith("_s" + plot_seed):
            runs_plot[cc] = run
            with open(os.path.join(out, "timeseries", tag + "_rate.csv"), "w") as fh:
                fh.write("bin_start_us," + ",".join(f"flow{f['id']}_gbps" for f in flows) + ",fair_gbps\n")
                fair = fair_step(run, flows, run["bt"])
                for i, t in enumerate(run["bt"]):
                    fh.write(f"{t:.0f}," + ",".join(f"{run['rate'][f['src']][i]:.2f}" for f in flows)
                             + f",{fair[i]:.2f}\n")
            with open(os.path.join(out, "timeseries", tag + "_queue.csv"), "w") as fh:
                fh.write(f"t_us,{run['qname']}_bytes,all_switch_queues_bytes\n")
                for t, q, s in zip(run["tgrid"], run["qd"], run["qtot"]):
                    fh.write(f"{t:.0f},{q:.0f},{s:.0f}\n")
            top = sorted(run["peak"].items(), key=lambda kv: -kv[1])[:4]
            print(f"  {tag}: unit {run['unit']:.1f} B/ack-unit, peak queues "
                  + ", ".join(f"{n} {v/1e3:.0f} KB" for n, v in top))
    write_csv(os.path.join(out, "convergence.csv"), conv)
    write_csv(os.path.join(out, "queues.csv"), queues)

    # summary over seeds
    lines = ["# Convergence validation summary (generated by scripts/validation_analyze.py)", ""]
    lines.append(f"Tolerance +-{TOL:.0%} of 400/n Gbps, held for {HOLD_US:.0f} us, {BIN_US:.0f} us bins. "
                 "'nc' = not within tolerance before the next event. Values: median [min-max] over seeds, "
                 "nc counted separately.")
    lines.append("")
    by = defaultdict(list)
    for r in conv:
        by[(r["tag"].rsplit("_s", 1)[0], r["event"])].append(r)

    def fmt(vals, n_total):
        v = [float(x) for x in vals if x != ""]
        nc = n_total - len(v)
        s = f"{np.median(v):.0f} [{min(v):.0f}-{max(v):.0f}]" if v else "-"
        return s + (f", nc {nc}/{n_total}" if nc else "")
    lines.append("| policy | event | n after | fair Gbps | new flow within 10% (us) | all flows within 10% (us) | Jain, 2nd half of phase | aggregate Gbps, 2nd half |")
    lines.append("|---|---|---|---|---|---|---|---|")
    ccs_present = [c for c in CC_ORDER if any(k[0] == c for k in by)]
    for cc in ccs_present:
        keys = sorted([k for k in by if k[0] == cc], key=lambda k: np.median([r["t_event_us"] for r in by[k]]))
        for k in keys:
            rs = by[k]
            ev = rs[0]["event"]
            if ev.startswith("departure"):
                ev += " (flow " + "/".join(str(r["flow"]) for r in rs) + ")"
            nact = rs[0]["n_active"]
            newc = fmt([r["conv_new_us"] for r in rs], len(rs)) if k[1].startswith("join") else ""
            allc = fmt([r["conv_all_us"] for r in rs], len(rs))
            j = [r["jain_2nd_half"] for r in rs]
            ag = [r["agg_gbps_2nd_half"] for r in rs]
            lines.append(f"| {CC_LABEL[cc]} | {ev} | {nact} | {LINK_GBPS/nact:.0f} | {newc} | {allc} | "
                         f"{np.median(j):.3f} [{min(j):.3f}-{max(j):.3f}] | {np.median(ag):.0f} |")
    lines.append("")
    lines.append("## Receiver ToR downlink queue, KB (median over seeds of each statistic)")
    lines.append("")
    lines.append("| policy | window | median | p99 | max | median pkts | all switch queues median | all switch p99 | fullest queue (median KB), per seed |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    qb = defaultdict(list)
    for r in queues:
        qb[(r["tag"].rsplit("_s", 1)[0], r["window"])].append(r)
    for cc in ccs_present:
        for w in ("all flows active", "whole run"):
            rs = qb.get((cc, w))
            if not rs:
                continue
            med = lambda key: np.median([r[key] for r in rs])
            lines.append(f"| {CC_LABEL[cc]} | {w} | {med('q_median_kb'):.1f} | {med('q_p99_kb'):.1f} | "
                         f"{med('q_max_kb'):.1f} | {med('q_median_pkts'):.1f} | {med('fabric_median_kb'):.1f} | "
                         f"{med('fabric_p99_kb'):.1f} | "
                         + "; ".join(f"{r['hottest_queue']} {r['hottest_median_kb']:.0f}" for r in rs) + " |")
    lines.append("")
    lines.append("## Flow completion times (us), per seed")
    lines.append("")
    if bad:
        lines.append("Invalid runs (lossless violations): " + ", ".join(f"{t} ({v})" for t, v in bad))
    with open(os.path.join(out, "summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    # FCT table appended separately (needs stdout only)
    with open(os.path.join(out, "summary.md"), "a") as fh:
        fh.write("| run | " + " | ".join(f"flow{f['id']} start" for f in flows) + " | "
                 + " | ".join(f"flow{f['id']} finish" for f in flows) + " |\n")
        fh.write("|---" * (1 + 2 * len(flows)) + "|\n")
        for tag in tags:
            st, fi, v = read_stdout(os.path.join(out, tag + ".log"), flows[0]["dst"])
            fh.write(f"| {tag} | " + " | ".join(f"{st.get(f['src'], float('nan')):.0f}" for f in flows) + " | "
                     + " | ".join(f"{fi.get(f['src'], float('nan')):.0f}" for f in flows) + " |\n")
    print(open(os.path.join(out, "summary.md")).read())
    main_ccs = [c for c in ["dcqcn", "hpcc", "eqds", "roce"] if c in runs_plot]
    if main_ccs:
        plot_convergence(runs_plot, flows, figdir, main_ccs)
        plot_queue(runs_plot, flows, figdir, main_ccs)
        print(f"figures: {figdir}/validation_convergence.png, {figdir}/validation_queue.png")


if __name__ == "__main__":
    main()
