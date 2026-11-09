#!/usr/bin/env python3
"""E4 tables and figures (transport scheduling under incast, EQDS).

  e4_plot.py [--results results/e4] [--outdir results/figs]

Reads every results/e4/eqds_default_load<L>_<policy>_w<W>_s<W>_summary.json
(and its _requests.csv, its .log and its matrix sidecar) and writes

  results/e4/e4_runs.csv     one row per run
  results/e4/e4_table.csv    policy x load, medians over the seeds
  results/e4/e4_table.md     the same, as markdown
  results/e4/e4_ideal.csv    fluid-model reference, same arrivals and sizes
  results/figs/e4_load_vs_floor.{png,pdf}     (a) p50 / p99 x floor vs load
  results/figs/e4_cdf_load70.{png,pdf}        (b) CDF of x floor at load 70
  results/figs/e4_scatter_load70.{png,pdf}    (c) completion vs prompt, FIFO vs SRPT
  results/figs/e4_scatter_load70_edf.{png,pdf}    same, FIFO vs EDF variants

Deadline miss rate: a request misses iff completion > slack x floor, i.e.
x_floor > slack (single-shard requests; parse.py's met_deadline is the same
test against the sidecar deadline). `miss@k` is evaluated for every policy at
k = 1.5, 2, 4 from the per-request x_floor, so FIFO and SRPT can be scored
against the deadlines that each EDF variant schedules by. `miss_own` is
parse.py's deadline_miss_rate, against the matrix's own slack (2.0 except
edf1p5 and edf4).

Fluid model ("ideal"): one 400 Gbps server, the matrix's arrivals and sizes,
no RTT, no headers. ps = processor sharing (what round robin credit is
meant to be), srpt = preemptive shortest REMAINING size, sjf = preemptive
shortest ORIGINAL size (what static prio = bytes asks for), edf<k> =
preemptive earliest deadline, fcfs = arrival order.
"""
import argparse, csv, glob, json, os, re, statistics, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from plot import setup_axes, floor_line, save   # shared style helpers

LINK_BPS = 400e9
TAG_RE = re.compile(r"^eqds_default_load(\d+)_([a-z0-9]+)_w(\d+)_s(\d+)$")

# Okabe-Ito, fixed per scheduling policy
POL = {
    "fifo":   ("fair share (no prio)", "#0072B2", "o"),
    "srpt":   ("SRPT (prio = bytes)",  "#D55E00", "s"),
    "edf":    ("EDF, slack 2",         "#009E73", "^"),
    "edf1p5": ("EDF, slack 1.5",       "#CC79A7", "v"),
    "edf4":   ("EDF, slack 4",         "#56B4E9", "D"),
}
ORDER = list(POL)
SLACK = {"fifo": 2.0, "srpt": 2.0, "edf": 2.0, "edf1p5": 1.5, "edf4": 4.0}
KS = (1.5, 2.0, 4.0)
SHORT_MAX, LONG_MIN = 1024, 2734   # prompt tokens: <= trace median, >= trace p90


def pct(v, p):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def jain(xs):
    s2 = sum(x * x for x in xs)
    return sum(xs) ** 2 / (len(xs) * s2) if s2 else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


# ----------------------------------------------------------------------------
# fluid model

def fluid(jobs, policy, cap=LINK_BPS):
    """jobs: list of (arrival_s, bits, deadline_s). Returns completion_s list."""
    n = len(jobs)
    order = sorted(range(n), key=lambda i: jobs[i][0])
    rem = [float(j[1]) for j in jobs]
    done = [None] * n
    active, k, t = [], 0, 0.0
    while k < n or active:
        if not active:
            t = max(t, jobs[order[k]][0])
        while k < n and jobs[order[k]][0] <= t + 1e-15:
            active.append(order[k]); k += 1
        if policy == "ps":
            rates = {i: cap / len(active) for i in active}
        else:
            key = {"srpt": lambda i: rem[i], "sjf": lambda i: jobs[i][1],
                   "edf": lambda i: jobs[i][2], "fcfs": lambda i: jobs[i][0]}[policy]
            top = min(active, key=lambda i: (key(i), jobs[i][0], i))
            rates = {top: cap}
        dt_fin = min(rem[i] / r for i, r in rates.items())
        dt_arr = jobs[order[k]][0] - t if k < n else float("inf")
        dt = min(dt_fin, dt_arr)
        for i, r in rates.items():
            rem[i] -= r * dt
        t += dt
        for i in list(active):
            if rem[i] <= 1e-6 * jobs[i][1]:
                done[i] = t - jobs[i][0]
                active.remove(i)
    return done


def ideal_for_matrix(sidecar):
    sc = json.load(open(sidecar))
    flows = sc["flows"]
    out = {}
    floor = [f["bytes"] * 8 / LINK_BPS for f in flows]
    for pol in ("ps", "srpt", "sjf", "fcfs", "edf1.5", "edf2", "edf4"):
        if pol.startswith("edf"):
            k = float(pol[3:])
            jobs = [(f["start_ps"] / 1e12, f["bytes"] * 8, f["start_ps"] / 1e12 + k * fl)
                    for f, fl in zip(flows, floor)]
            c = fluid(jobs, "edf")
        else:
            jobs = [(f["start_ps"] / 1e12, f["bytes"] * 8, 0.0) for f in flows]
            c = fluid(jobs, pol)
        xs = [ci / fl for ci, fl in zip(c, floor)]
        toks = [f["prompt_tokens"] for f in flows]
        out[pol] = {"x": xs, "comp_us": [ci * 1e6 for ci in c], "tokens": toks}
    return out


# ----------------------------------------------------------------------------
# measured runs

def read_runs(resdir):
    runs = []
    for p in sorted(glob.glob(os.path.join(resdir, "eqds_default_load*_summary.json"))):
        s = json.load(open(p))
        m = TAG_RE.match(s["tag"])
        if not m:
            continue
        if s.get("lossless_violations"):
            print(f"skip {p}: lossless violations", file=sys.stderr); continue
        load, pol, w = int(m.group(1)), m.group(2), int(m.group(3))
        req = list(csv.DictReader(open(os.path.join(resdir, s["requests_csv"]))))
        rows = [r for r in req if r["x_floor"]]
        x = [float(r["x_floor"]) for r in rows]
        comp = [float(r["completion_us"]) for r in rows]
        tok = [int(r["prompt_tokens"]) for r in rows]
        log = s["log"]
        if not os.path.exists(log):
            log = os.path.join(resdir, os.path.basename(log))
        new = rtx = stale = None
        if os.path.exists(log):
            for line in open(log, errors="replace"):
                mm = re.match(r"New: (\d+) Rtx: (\d+)", line)
                if mm:
                    new, rtx = int(mm.group(1)), int(mm.group(2))
                mm = re.match(r"Stale NACKs ignored: (\d+)", line)
                if mm:
                    stale = int(mm.group(1))
        # receiver efficiency over its busy periods: bytes / (line rate x time
        # during which at least one flow into the receiver is unfinished)
        iv = sorted((float(r["start_us"]), float(r["finish_us"])) for r in rows)
        busy, cs, ce = 0.0, None, None
        for a, b in iv:
            if ce is None or a > ce:
                if ce is not None:
                    busy += ce - cs
                cs, ce = a, b
            else:
                ce = max(ce, b)
        busy += ce - cs
        busy_eff = sum(int(r["bytes"]) for r in rows) * 8 / (LINK_BPS * busy * 1e-6)
        short = [xi for xi, t in zip(x, tok) if t <= SHORT_MAX]
        long_ = [xi for xi, t in zip(x, tok) if t >= LONG_MIN]
        run = {
            "tag": s["tag"], "load": load, "policy": pol, "seed": w,
            "offered_load_realized": s.get("offered_load_realized"),
            "n_req": s["n_requests"], "n_done": s["n_requests_complete"],
            "p50": pct(x, 50), "p95": pct(x, 95), "p99": pct(x, 99), "max": max(x),
            "mean_x": sum(x) / len(x), "mean_comp_us": sum(comp) / len(comp),
            "miss_own": s.get("deadline_miss_rate"),
            "jain": s.get("jain_fairness"),
            "short_p50": pct(short, 50), "short_p99": pct(short, 99),
            "long_p50": pct(long_, 50), "long_p99": pct(long_, 99), "long_max": max(long_) if long_ else None,
            "n_short": len(short), "n_long": len(long_),
            "last_finish_us": s.get("last_finish_us"), "busy_eff": busy_eff,
            "new_pkts": new, "rtx_pkts": rtx, "stale_nacks": stale,
            "wall_s": s.get("wall_s"),
            "_x": x, "_comp": comp, "_tok": tok, "_matrix": s["matrix"],
        }
        for k in KS:
            run[f"miss@{k:g}"] = sum(1 for xi in x if xi > k) / len(x)
        runs.append(run)
    return runs


RUN_COLS = ["tag", "load", "policy", "seed", "offered_load_realized", "n_req", "n_done",
            "p50", "p95", "p99", "max", "mean_x", "mean_comp_us", "miss_own",
            "miss@1.5", "miss@2", "miss@4", "jain", "short_p50", "short_p99",
            "long_p50", "long_p99", "long_max", "n_short", "n_long",
            "last_finish_us", "busy_eff", "new_pkts", "rtx_pkts", "stale_nacks", "wall_s"]
TAB_COLS = ["p50", "p95", "p99", "mean_x", "mean_comp_us", "miss_own", "miss@1.5", "miss@2",
            "miss@4", "jain", "short_p50", "short_p99", "long_p50", "long_p99", "long_max",
            "busy_eff", "rtx_pkts", "wall_s"]


def f3(v):
    if v is None:
        return ""
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def write_tables(runs, ideal_rows, resdir):
    with open(os.path.join(resdir, "e4_runs.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(RUN_COLS)
        for r in sorted(runs, key=lambda r: (r["load"], ORDER.index(r["policy"]) if r["policy"] in ORDER else 9, r["seed"])):
            w.writerow([f3(r[c]) for c in RUN_COLS])
    groups = defaultdict(list)
    for r in runs:
        groups[(r["policy"], r["load"])].append(r)
    keys = sorted(groups, key=lambda k: (ORDER.index(k[0]) if k[0] in ORDER else 9, k[1]))
    table = []
    for k in keys:
        g = groups[k]
        row = {"policy": k[0], "load": k[1], "n_seeds": len(g)}
        for c in TAB_COLS:
            row[c] = med([r[c] for r in g])
        table.append(row)
    cols = ["policy", "load", "n_seeds"] + TAB_COLS
    with open(os.path.join(resdir, "e4_table.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(cols)
        for row in table:
            w.writerow([f3(row[c]) for c in cols])
    with open(os.path.join(resdir, "e4_ideal.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["load", "seed", "model", "p50", "p95", "p99", "mean_x", "jain_comp_per_token",
                    "short_p50", "long_p50", "long_max", "miss@1.5", "miss@2", "miss@4"])
        for r in ideal_rows:
            w.writerow([r["load"], r["seed"], r["model"]] + [f3(r[c]) for c in
                       ("p50", "p95", "p99", "mean_x", "jain", "short_p50", "long_p50", "long_max",
                        "miss@1.5", "miss@2", "miss@4")])
    # markdown: the headline table
    md = ["| policy | load | p50 x | p95 x | p99 x | mean x | miss (own slack) | miss@2 | Jain | short p50 | long p50 | long max | wall s |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in table:
        md.append("| {} | {} | {:.2f} | {:.2f} | {:.2f} | {:.2f} | {:.2f} | {:.2f} | {:.3f} | {:.2f} | {:.2f} | {:.2f} | {:.0f} |".format(
            row["policy"], row["load"], row["p50"], row["p95"], row["p99"], row["mean_x"],
            row["miss_own"], row["miss@2"], row["jain"], row["short_p50"], row["long_p50"],
            row["long_max"], row["wall_s"] or 0))
    # ideal medians over seeds
    ig = defaultdict(list)
    for r in ideal_rows:
        ig[(r["model"], r["load"])].append(r)
    md += ["", "Fluid model (ideal 400 Gbps server, same arrivals), medians over seeds:", "",
           "| model | load | p50 x | p99 x | mean x | Jain | short p50 | long p50 | long max | miss@2 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for k in sorted(ig, key=lambda k: (k[0], k[1])):
        g = ig[k]
        md.append("| {} | {} | {:.2f} | {:.2f} | {:.2f} | {:.3f} | {:.2f} | {:.2f} | {:.2f} | {:.2f} |".format(
            k[0], k[1], med([r["p50"] for r in g]), med([r["p99"] for r in g]),
            med([r["mean_x"] for r in g]), med([r["jain"] for r in g]),
            med([r["short_p50"] for r in g]), med([r["long_p50"] for r in g]),
            med([r["long_max"] for r in g]), med([r["miss@2"] for r in g])))
    open(os.path.join(resdir, "e4_table.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))
    return table


# ----------------------------------------------------------------------------
# figures

def style(pol):
    return POL.get(pol, (pol, "#000000", "x"))


def fig_load(runs, ideal_rows, outdir):
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.4), sharey=False)
    by = defaultdict(lambda: defaultdict(list))
    for r in runs:
        by[r["policy"]][r["load"]].append(r)
    ib = defaultdict(lambda: defaultdict(list))
    for r in ideal_rows:
        ib[r["model"]][r["load"]].append(r)
    pols = [p for p in ORDER if p in by]
    n = len(pols)
    for ax, stat, title in ((axes[0], "p50", "median request"), (axes[1], "p99", "p99 request")):
        setup_axes(ax)
        for i, p in enumerate(pols):
            label, color, marker = style(p)
            L = sorted(by[p])
            dx = (i - (n - 1) / 2) * 0.6
            ys = [med([r[stat] for r in by[p][l]]) for l in L]
            lo = [min(r[stat] for r in by[p][l]) for l in L]
            hi = [max(r[stat] for r in by[p][l]) for l in L]
            main = p in ("fifo", "srpt", "edf")
            ax.plot([l + dx for l in L], ys, color=color, marker=marker, markersize=5,
                    linewidth=1.6 if main else 1.0, alpha=1.0 if main else 0.75,
                    label=label, zorder=3)
            ax.vlines([l + dx for l in L], lo, hi, color=color, alpha=0.35, linewidth=1.0)
        for model, color, label in (("ps", POL["fifo"][1], "ideal PS"),
                                    ("srpt", POL["srpt"][1], "ideal SRPT")):
            L = sorted(ib[model])
            ax.plot(L, [med([r[stat] for r in ib[model][l]]) for l in L], color=color,
                    linestyle=":", linewidth=1.2, label=label, zorder=2)
        floor_line(ax)
        ax.set_yscale("log")
        ax.set_xticks(sorted({r["load"] for r in runs}))
        ax.set_xticklabels([f"{l}%" for l in sorted({r["load"] for r in runs})])
        ax.set_xlabel("offered load (% of receiver downlink)")
        ax.set_ylabel(f"{title}: completion / floor")
        ax.set_title(title, fontsize=9)
    axes[0].legend(frameon=False, fontsize=7, loc="upper left", ncol=1)
    fig.tight_layout()
    save(fig, outdir, "e4_load_vs_floor")
    plt.close(fig)


def fig_cdf(runs, outdir, load=70):
    fig, ax = plt.subplots(figsize=(5.0, 3.4))
    setup_axes(ax)
    for p in ("fifo", "srpt", "edf"):
        v = sorted(x for r in runs if r["policy"] == p and r["load"] == load for x in r["_x"])
        if not v:
            continue
        label, color, _ = style(p)
        n = len(v)
        ax.step([v[0]] + v, [0.0] + [(j + 1) / n for j in range(n)], where="post",
                color=color, linewidth=1.6, label=f"{label} (n={n})")
    floor_line(ax, vertical=True)
    ax.axvline(2.0, color="#999999", linestyle=":", linewidth=1.0)
    ax.text(2.0, 0.5, " deadline (2x)", fontsize=7, color="#777777", rotation=90, va="center")
    ax.set_xscale("log")
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("request completion / theoretical floor")
    ax.set_ylabel("fraction of requests")
    ax.set_title(f"load {load}%, 3 seeds pooled", fontsize=9)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    save(fig, outdir, f"e4_cdf_load{load}")
    plt.close(fig)


def fig_scatter(runs, outdir, pols, name, load=70):
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.4))
    for ax in axes:
        setup_axes(ax)
    for p in pols:
        label, color, marker = style(p)
        tok = [t for r in runs if r["policy"] == p and r["load"] == load for t in r["_tok"]]
        comp = [c / 1e3 for r in runs if r["policy"] == p and r["load"] == load for c in r["_comp"]]
        xs = [x for r in runs if r["policy"] == p and r["load"] == load for x in r["_x"]]
        if not tok:
            continue
        kw = dict(s=10, alpha=0.55, color=color, marker=marker, linewidths=0)
        axes[0].scatter(tok, comp, label=label, **kw)
        axes[1].scatter(tok, xs, label=label, **kw)
    t = [50, 9000]
    axes[0].plot(t, [tt * 327680 / 8 * 8 / LINK_BPS * 1e3 for tt in t], color="#444444",
                 linestyle="--", linewidth=1.0, label="theoretical floor")
    axes[0].set_xscale("log"); axes[0].set_yscale("log")
    axes[0].set_xlabel("prompt tokens"); axes[0].set_ylabel("KV transfer completion (ms)")
    axes[1].set_xscale("log"); axes[1].set_yscale("log")
    floor_line(axes[1])
    axes[1].set_xlabel("prompt tokens"); axes[1].set_ylabel("completion / floor")
    axes[0].legend(frameon=False, fontsize=7, loc="upper left")
    fig.suptitle(f"load {load}%, 3 seeds pooled, one request per point", fontsize=9)
    fig.tight_layout()
    save(fig, outdir, name)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(HERE, "results", "e4"))
    ap.add_argument("--outdir", default=os.path.join(HERE, "results", "figs"))
    args = ap.parse_args()
    plt.rcParams.update({"font.size": 9, "axes.labelsize": 9,
                         "font.family": "DejaVu Sans", "pdf.fonttype": 42})
    runs = read_runs(args.results)
    if not runs:
        sys.exit("no E4 summaries found")
    # fluid model, once per (load, seed) matrix (sizes/arrivals identical across policies)
    ideal_rows = []
    seen = {}
    for r in runs:
        key = (r["load"], r["seed"])
        if key in seen:
            continue
        side = os.path.splitext(r["_matrix"])[0] + ".json"
        seen[key] = ideal_for_matrix(side)
        for model, d in seen[key].items():
            x, tok, comp = d["x"], d["tokens"], d["comp_us"]
            row = {"load": key[0], "seed": key[1], "model": model,
                   "p50": pct(x, 50), "p95": pct(x, 95), "p99": pct(x, 99),
                   "mean_x": sum(x) / len(x),
                   "jain": jain([c / t for c, t in zip(comp, tok)]),
                   "short_p50": pct([xi for xi, t in zip(x, tok) if t <= SHORT_MAX], 50),
                   "long_p50": pct([xi for xi, t in zip(x, tok) if t >= LONG_MIN], 50),
                   "long_max": max([xi for xi, t in zip(x, tok) if t >= LONG_MIN] or [0])}
            for k in KS:
                row[f"miss@{k:g}"] = sum(1 for xi in x if xi > k * (1 + 1e-9)) / len(x)
            ideal_rows.append(row)
    write_tables(runs, ideal_rows, args.results)
    fig_load(runs, ideal_rows, args.outdir)
    fig_cdf(runs, args.outdir, 70)
    fig_scatter(runs, args.outdir, ("fifo", "srpt"), "e4_scatter_load70", 70)
    fig_scatter(runs, args.outdir, ("fifo", "edf1p5", "edf", "edf4"), "e4_scatter_load70_edf", 70)


if __name__ == "__main__":
    main()
