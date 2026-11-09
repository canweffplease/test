#!/usr/bin/env python3
"""Plot parse.py summaries.

  plot.py load [opts] <summary.json | glob> ...
      Distance from floor vs offered load, one line per congestion-control
      policy. Solid line + filled marker = median request (p50); lighter
      hollow marker joined by a thin whisker = p99 request. With several
      repetitions per point, each statistic is the median across reps.

  plot.py cdf --load 70 [opts] <summary.json | glob> ...
      Empirical CDF of per-request distance from floor at one load level,
      one curve per policy (requests pooled across reps). Shows the tail.

Options:
  --out NAME      output basename under results/figs/ (default derived)
  --outdir DIR    default <repo>/results/figs
  --topo T        keep only summaries whose tag contains _T_ (e.g. default)
  --load-from-tag / --load N   x position: parsed from "load<N>" in the tag
                  (default), or forced for every input
  --logy          log-scale y axis (automatic when the range exceeds 20x)
  --linear        force a linear y axis

Writes NAME.png (300 dpi) and NAME.pdf. Policies are identified by the tag
prefix (eqds_, hpcc_, roce_, dcqcn_); a run.sh variant suffix after the rep
(e.g. _ar for STRAT=ecmp_ar) becomes a separate line.
"""
import argparse, csv, glob, json, os, re, statistics, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Okabe-Ito, colorblind safe. Fixed per policy so every figure agrees.
STYLE = {
    "eqds":  ("EQDS",        "#0072B2", "o"),
    "hpcc":  ("HPCC",        "#009E73", "s"),
    "roce":  ("RoCE (no CC)", "#E69F00", "^"),
    "dcqcn": ("DCQCN",       "#D55E00", "D"),
}
EXTRA = ["#CC79A7", "#56B4E9", "#F0E442", "#000000"]
ORDER = list(STYLE)


def load_summaries(paths, topo=None):
    files = []
    for p in paths:
        g = sorted(glob.glob(p))
        files += g if g else ([p] if os.path.exists(p) else [])
    out = []
    for f in dict.fromkeys(files):
        try:
            s = json.load(open(f))
        except (OSError, ValueError) as e:
            print(f"skip {f}: {e}", file=sys.stderr)
            continue
        if topo and f"_{topo}_" not in s["tag"] + "_":
            continue
        if s.get("lossless_violations"):
            print(f"skip {f}: lossless violations", file=sys.stderr)
            continue
        if s.get("match_violations"):
            print(f"WARNING {f}: {s['match_violations']} flow-matching violations",
                  file=sys.stderr)
        if s.get("n_requests_complete", 0) < s.get("n_requests", 0):
            print(f"WARNING {f}: only {s['n_requests_complete']}/{s['n_requests']} "
                  f"requests completed", file=sys.stderr)
        s["_path"] = f
        out.append(s)
    return out


def series_key(s):
    """(policy, variant) where variant is whatever follows _r<rep> in the tag."""
    m = re.search(r"_r\d+(.*?)(?:_s\d+)?$", s["tag"])   # _s<seed> is the rep
    return s["policy"], (m.group(1) if m else "")


def style_for(key, i):
    pol, var = key
    label, color, marker = STYLE.get(pol, (pol.upper(), EXTRA[i % len(EXTRA)], "v"))
    if var:
        label = f"{label} {var.strip('_')}"
    return label, color, marker


def sort_keys(keys):
    return sorted(keys, key=lambda k: (ORDER.index(k[0]) if k[0] in ORDER else 99, k))


def setup_axes(ax):
    ax.grid(True, which="major", color="#000000", alpha=0.08, linewidth=0.8)
    ax.grid(True, which="minor", color="#000000", alpha=0.04, linewidth=0.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(direction="out", length=3)


def floor_line(ax, vertical=False):
    kw = dict(color="#444444", linestyle="--", linewidth=1.0, zorder=1)
    if vertical:
        ax.axvline(1.0, **kw)
        ax.text(1.0, 0.02, " theoretical floor", rotation=90, va="bottom",
                ha="right", fontsize=8, color="#444444",
                transform=ax.get_xaxis_transform())
    else:
        ax.axhline(1.0, **kw)
        ax.text(0.99, 1.0, "theoretical floor", va="bottom", ha="right",
                fontsize=8, color="#444444", transform=ax.get_yaxis_transform())


def save(fig, outdir, name):
    os.makedirs(outdir, exist_ok=True)
    for ext in ("png", "pdf"):
        p = os.path.join(outdir, f"{name}.{ext}")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print(f"wrote {p}")


def choose_log(ax, lo, hi, args):
    if args.linear:
        return
    if args.logy or (lo > 0 and hi / lo > 20):
        ax.set_yscale("log")


def plot_load(args, ss):
    pts = defaultdict(lambda: defaultdict(list))      # key -> load -> [summary]
    for s in ss:
        load = args.load if args.load is not None else s.get("load")
        if load is None:
            print(f"skip {s['_path']}: no load<N> in tag; pass --load", file=sys.stderr)
            continue
        pts[series_key(s)][load].append(s)
    if not pts:
        sys.exit("nothing to plot")

    fig, ax = plt.subplots(figsize=(5.0, 3.4))
    setup_axes(ax)
    keys = sort_keys(pts)
    n = len(keys)
    loads_all = sorted({l for k in keys for l in pts[k]})
    step = min((b - a for a, b in zip(loads_all, loads_all[1:])), default=10)
    lo, hi = 1.0, 1.0
    for i, key in enumerate(keys):
        label, color, marker = style_for(key, i)
        dx = (i - (n - 1) / 2) * step * 0.04              # dodge overlapping whiskers
        L = sorted(pts[key])
        p50 = [statistics.median(s["request_x_floor"]["p50"] for s in pts[key][l]) for l in L]
        p99 = [statistics.median(s["request_x_floor"]["p99"] for s in pts[key][l]) for l in L]
        xs = [l + dx for l in L]
        ax.plot(xs, p50, color=color, marker=marker, markersize=5, linewidth=1.6,
                label=label, zorder=3)
        ax.vlines(xs, p50, p99, color=color, alpha=0.45, linewidth=1.2, zorder=2)
        ax.plot(xs, p99, linestyle="none", marker=marker, markersize=5,
                markerfacecolor="white", markeredgecolor=color, alpha=0.7,
                markeredgewidth=1.0, zorder=2)
        lo, hi = min([lo] + p50), max([hi] + p99)
    floor_line(ax)
    choose_log(ax, lo, hi, args)
    ax.set_xticks(loads_all)
    ax.set_xticklabels([f"{l}%" for l in loads_all])
    ax.set_xlabel("offered load (% of fabric bottleneck)")
    ax.set_ylabel("request completion / theoretical floor")
    if ax.get_yscale() == "linear":
        ax.set_ylim(bottom=min(0.9, lo * 0.95))
    # legend: policies plus one key explaining the marker types
    h, l = ax.get_legend_handles_labels()
    h += [plt.Line2D([], [], color="#666666", marker="o", linewidth=1.6, markersize=5),
          plt.Line2D([], [], color="#666666", marker="o", linestyle="none",
                     markerfacecolor="white", markersize=5, alpha=0.7)]
    l += ["p50 request", "p99 request"]
    ax.legend(h, l, frameon=False, fontsize=8, loc="best", ncol=2)
    name = args.out or "e3_load_vs_floor" + (f"_{args.topo}" if args.topo else "")
    save(fig, args.outdir, name)


def plot_cdf(args, ss):
    if args.load is None:
        sys.exit("cdf mode needs --load N")
    groups = defaultdict(list)
    for s in ss:
        if s.get("load") != args.load and not args.force_load:
            continue
        p = os.path.join(os.path.dirname(s["_path"]), s["requests_csv"])
        with open(p) as fh:
            for r in csv.DictReader(fh):
                if r["x_floor"]:
                    groups[series_key(s)].append(float(r["x_floor"]))
    if not groups:
        sys.exit(f"no summaries at load {args.load}")
    fig, ax = plt.subplots(figsize=(5.0, 3.4))
    setup_axes(ax)
    lo, hi = 1.0, 1.0
    for i, key in enumerate(sort_keys(groups)):
        label, color, _ = style_for(key, i)
        v = sorted(groups[key])
        n = len(v)
        # step ECDF, starting at 0
        xs = [v[0]] + v
        ys = [0.0] + [(j + 1) / n for j in range(n)]
        ax.step(xs, ys, where="post", color=color, linewidth=1.6,
                label=f"{label} (n={n})")
        lo, hi = min(lo, v[0]), max(hi, v[-1])
    floor_line(ax, vertical=True)
    if not args.linear and (args.logy or hi / lo > 20):
        ax.set_xscale("log")
    else:
        ax.set_xlim(left=min(0.85, lo * 0.9))
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("request completion / theoretical floor")
    ax.set_ylabel("fraction of requests")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    name = args.out or f"e3_cdf_load{args.load}" + (f"_{args.topo}" if args.topo else "")
    save(fig, args.outdir, name)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["load", "cdf"])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--outdir", default=os.path.join(HERE, "results", "figs"))
    ap.add_argument("--topo")
    ap.add_argument("--load", type=int)
    ap.add_argument("--force-load", action="store_true",
                    help="cdf: use every input regardless of its tag's load")
    ap.add_argument("--logy", action="store_true")
    ap.add_argument("--linear", action="store_true")
    args = ap.parse_args()
    plt.rcParams.update({"font.size": 9, "axes.labelsize": 9,
                         "font.family": "DejaVu Sans", "pdf.fonttype": 42})
    ss = load_summaries(args.inputs, args.topo)
    if not ss:
        sys.exit("no usable summaries")
    (plot_load if args.mode == "load" else plot_cdf)(args, ss)


if __name__ == "__main__":
    main()
