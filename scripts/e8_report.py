#!/usr/bin/env python3
"""E8 tables and figures: congestion control across transfer-size regimes.

  e8_report.py --out results/e8/e8 [--figtag e8] <summary.json> ...

Called by scripts/e8.sh. Tags are <cc>_default_<regime>_load<L>_r<i>_s<i>,
regime in fix4k | conv | code | l13b (see scripts/e8.sh).

Writes
  <out>_table.{md,csv}    per (regime, load, policy): med [min-max] over seeds
                          of p50 / p99 x_floor, the fluid-ideal reference and
                          x_ideal (scripts/exp_report.py), deadline miss,
                          Jain over completion/prompt_tokens (parse.py's) and
                          over x_floor, median excess time, wall clock
  <out>_ranking.{md,csv}  policy order by p50 and by p99 per (regime, load),
                          and whether it differs from fix4k at the same load
  <out>_sizebins.{md,csv} per policy, load 70, conv and code: x_floor and
                          excess time by prompt-length bin (3 seeds pooled)
  <out>_pair13b.{md,csv}  Llama-2-13B vs Llama-3-70B 4K, paired by
                          (request, seed): x ratio and excess-time ratio
  <out>_runs.csv          one row per run
  results/figs/<figtag>_xfloor_by_regime.{png,pdf}
  results/figs/<figtag>_scatter_code_load70.{png,pdf}
  results/figs/<figtag>_ranking.{png,pdf}
  with --iso <summaries> -- (isolated size ladder, scripts/e8.sh iso):
  <out>_iso.{md,csv}      per policy and size: x_floor and excess, and a
                          per-seed least-squares fit excess = a + b x floor
  results/figs/<figtag>_iso_size.{png,pdf}

"Excess" = completion - floor (us): the time the network added. If a
policy's cost is a fixed time per request, the excess is size independent
and x_floor = 1 + excess/floor falls as requests grow; if the cost is
proportional to size, x_floor is flat and the excess grows with size.

"Latency floor" = floor + BASE_LAT_US, the one-way propagation of a
cross-pod path on the default tree (6 hops x 1 us, main_*.cpp hop_latency
default) plus one MTU of store-and-forward per hop. No transport can beat
it; for a 30-token prompt (1.2 MB shard, floor 25 us) it is already 1.26x.
"""
import argparse, csv, os, statistics, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import exp_report as er              # load(), reference() (fluid ideal), pct, mmm

REGIMES = ["fix4k", "conv", "code", "l13b"]
RLABEL = {"fix4k": "70B fixed 4K", "conv": "70B Azure conv", "code": "70B Azure code",
          "l13b": "13B fixed 4K"}
ORDER = ["eqds", "hpcc", "roce", "dcqcn"]
PLABEL = {"eqds": "EQDS", "hpcc": "HPCC", "roce": "RoCE", "dcqcn": "DCQCN"}
BASE_LAT_US = 6 * 1.0 + 6 * 4096 * 8 / 400e3          # 6.49 us
BINS = [(0, 512, "<=512"), (513, 2048, "513-2048"), (2049, 4096, "2049-4096"),
        (4097, 10 ** 9, ">4096")]


def f(x):
    return er.fnum(x)


def jain(xs):
    xs = [x for x in xs if x is not None]
    s2 = sum(x * x for x in xs)
    return sum(xs) ** 2 / (len(xs) * s2) if xs and s2 else None


def regime_of(run):
    return run["label"].split("_load")[0]


def per_request(run):
    """Request rows with floor, ideal, completion, excess, prompt tokens."""
    ref = er.reference(run)
    out = []
    for r in run["reqs"]:
        c = f(r["completion_us"])
        if c is None:
            continue
        rf = ref[r["request_id"]]
        out.append({"rid": r["request_id"], "seed": run["seed"], "bytes": int(r["bytes"]),
                    "tokens": int(r["prompt_tokens"]), "floor": rf["floor"],
                    "ideal": rf["ideal"], "c": c, "x": c / rf["floor"],
                    "xi": c / rf["ideal"], "excess": c - rf["floor"],
                    "xlat": c / (rf["floor"] + BASE_LAT_US),
                    "miss": f(r["met_deadline"]) == 0})
    return out


def concurrency(run):
    """Time-averaged number of active flows per sending host NIC while it has
    at least one (busy-period average), averaged over the sending hosts."""
    ev = defaultdict(list)
    for fl in run["flows"]:
        a, b = f(fl["start_us"]), f(fl["finish_us"])
        if a is None or b is None:
            continue
        ev[fl["src"]] += [(a, 1), (b, -1)]
    vals = []
    for h, e in ev.items():
        e.sort()
        n, t0, area, busy = 0, None, 0.0, 0.0
        for t, d in e:
            if n > 0:
                area += n * (t - t0); busy += t - t0
            n += d; t0 = t
        if busy > 0:
            vals.append(area / busy)
    return sum(vals) / len(vals) if vals else None


def run_row(run):
    rq = per_request(run)
    xs = [q["x"] for q in rq]
    st = er.run_stats(run)
    return {
        "regime": regime_of(run), "load": run["load"], "policy": run["policy"],
        "seed": run["seed"], "nreq": len(rq), "nreq_all": run["n_requests"],
        "nflows": run["n_flows"], "realized": run.get("offered_load_realized"),
        "p50": er.pct(xs, 50), "p99": er.pct(xs, 99), "mean": sum(xs) / len(xs),
        "ideal_p50": st["ideal_p50"], "ideal_p99": st["ideal_p99"],
        "xi_p50": st["xideal_p50"], "xi_p99": st["xideal_p99"],
        "miss": run.get("deadline_miss_rate"), "ideal_miss": st["ideal_miss"],
        "jain_tok": run.get("jain_fairness"), "jain_x": jain(xs),
        "excess_p50": er.pct([q["excess"] for q in rq], 50),
        "conc": concurrency(run),
        "wall": run.get("wall_s"), "end_us": run.get("end_us"),
        "rtx": (run.get("log_counters") or {}).get("rtx_pkts"),
        "_rq": rq,
    }


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def rng(xs):
    xs = [x for x in xs if x is not None]
    return (min(xs), max(xs)) if xs else (None, None)


def order(cells, key):
    """Policies sorted by the median over seeds of `key`; '<' if the seed
    ranges are disjoint, '~' if they overlap (difference within seed spread)."""
    pols = sorted(cells, key=lambda p: med([r[key] for r in cells[p]]))
    s = PLABEL[pols[0]]
    for a, b in zip(pols, pols[1:]):
        _, amax = rng([r[key] for r in cells[a]])
        bmin, _ = rng([r[key] for r in cells[b]])
        s += (" < " if amax < bmin else " ~ ") + PLABEL[b]
    return pols, s


def paired_wins(cells, key, a, b):
    """Seeds on which policy a beats b on `key` (paired: same matrix, same salts)."""
    da = {r["seed"]: r[key] for r in cells.get(a, [])}
    db = {r["seed"]: r[key] for r in cells.get(b, [])}
    common = sorted(set(da) & set(db))
    return sum(da[s] < db[s] for s in common), len(common)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--figtag", default="e8")
    ap.add_argument("--iso", nargs="+", default=[])
    ap.add_argument("inputs", nargs="+")
    args = ap.parse_args()

    runs = [r for r in er.load(args.inputs) if r["n_flows_finished"] == r["n_flows"]]
    rows = [run_row(r) for r in runs]
    G = defaultdict(lambda: defaultdict(list))          # (regime, load) -> policy -> rows
    for r in rows:
        G[(r["regime"], r["load"])][r["policy"]].append(r)
    keys = sorted(G, key=lambda k: (-k[1], REGIMES.index(k[0])))

    # ---- per-run csv ---------------------------------------------------------
    cols = [c for c in rows[0] if not c.startswith("_")] if rows else []
    with open(args.out + "_runs.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in sorted(rows, key=lambda r: (r["regime"], r["load"], r["policy"], r["seed"])):
            w.writerow([r[c] for c in cols])

    # ---- main table ----------------------------------------------------------
    hdr = ["regime", "load", "realized", "policy", "seeds", "reqs", "p50 x_floor",
           "p99 x_floor", "ideal p50/p99", "p50 x_ideal", "p99 x_ideal", "p50 excess us",
           "deadline miss", "ideal miss", "Jain (c/tokens)", "Jain (x_floor)",
           "flows/NIC busy", "wall s"]
    T = []
    for k in keys:
        for p in sorted(G[k], key=er.pol_key):
            rs = G[k][p]
            T.append([RLABEL.get(k[0], k[0]), k[1], f"{rs[0]['realized']:.3f}", PLABEL[p],
                      len(rs), rs[0]["nreq_all"],
                      er.mmm([r["p50"] for r in rs]), er.mmm([r["p99"] for r in rs]),
                      f"{med([r['ideal_p50'] for r in rs]):.2f} / {med([r['ideal_p99'] for r in rs]):.2f}",
                      er.mmm([r["xi_p50"] for r in rs]), er.mmm([r["xi_p99"] for r in rs]),
                      er.mmm([r["excess_p50"] for r in rs], 0),
                      er.mmm([r["miss"] for r in rs]), f"{med([r['ideal_miss'] for r in rs]):.2f}",
                      er.mmm([r["jain_tok"] for r in rs], 3), er.mmm([r["jain_x"] for r in rs], 3),
                      er.mmm([r["conc"] for r in rs], 1), er.mmm([r["wall"] for r in rs], 0)])
    er.write_table(args.out + "_table", hdr, T,
                   "E8, per-request x_floor = completion / (shard bytes x 8 / 400 Gbps), "
                   "slowest shard. med [min-max] over seeds 1-3. `ideal` = fluid max-min "
                   "fair fabric on the same matrix (scripts/exp_report.py). excess = "
                   "completion - floor. Deadline = start + 2 x floor.")

    # ---- ranking -------------------------------------------------------------
    hdr = ["load", "regime", "order by p50", "order by p99", "p50 order = fix4k?",
           "p99 order = fix4k?", "EQDS<HPCC p50 (seeds)", "HPCC<RoCE p50", "RoCE<DCQCN p50"]
    RK, ranks = [], {}
    for L in sorted({k[1] for k in keys}, reverse=True):
        base = {}
        for R in REGIMES:
            c = G.get((R, L))
            if not c:
                continue
            p50o, p50s = order(c, "p50")
            p99o, p99s = order(c, "p99")
            ranks[(R, L)] = (p50o, p99o)
            if R == "fix4k":
                base = {"p50": p50o, "p99": p99o}
            same = lambda o, kk: "-" if R == "fix4k" or not base else ("yes" if o == base[kk] else "**no**")
            pw = []
            for a, b in (("eqds", "hpcc"), ("hpcc", "roce"), ("roce", "dcqcn")):
                n, m = paired_wins(c, "p50", a, b)
                pw.append(f"{n}/{m}" if m else "-")
            RK.append([L, RLABEL[R], p50s, p99s, same(p50o, "p50"), same(p99o, "p99")] + pw)
    er.write_table(args.out + "_ranking", hdr, RK,
                   "Policy order (best first) by the median over seeds of each run's p50 "
                   "(p99) x_floor. ' < ' = seed ranges disjoint, ' ~ ' = they overlap "
                   "(within seed spread). Last three columns: seeds on which the first "
                   "policy has the lower p50 (paired: same matrix, same ECMP salts).")

    # ---- size bins (load 70, trace regimes) -----------------------------------
    hdr = ["regime", "policy", "tokens", "n", "x_floor p50", "x_floor p90",
           "ideal x p50", "x_ideal p50", "x_lat p50", "excess p50 us", "excess p90 us", "miss"]
    SB = []
    for R in ("conv", "code"):
        c = G.get((R, 70), {})
        for p in sorted(c, key=er.pol_key):
            rq = [q for r in c[p] for q in r["_rq"]]
            for lo, hi, lab in BINS:
                qs = [q for q in rq if lo <= q["tokens"] <= hi]
                if not qs:
                    continue
                x = [q["x"] for q in qs]
                e = [q["excess"] for q in qs]
                SB.append([RLABEL[R], PLABEL[p], lab, len(qs), f"{er.pct(x, 50):.2f}",
                           f"{er.pct(x, 90):.2f}",
                           f"{er.pct([q['ideal'] / q['floor'] for q in qs], 50):.2f}",
                           f"{er.pct([q['xi'] for q in qs], 50):.2f}",
                           f"{er.pct([q['xlat'] for q in qs], 50):.2f}",
                           f"{er.pct(e, 50):.0f}", f"{er.pct(e, 90):.0f}",
                           f"{sum(q['miss'] for q in qs) / len(qs):.2f}"])
    if SB:
        er.write_table(args.out + "_sizebins", hdr, SB,
                       f"Load 70, seeds 1-3 pooled, by prompt length. x_lat = completion / "
                       f"(floor + {BASE_LAT_US:.2f} us one-way cross-pod latency).")

    # ---- Llama-2-13B vs Llama-3-70B, paired -----------------------------------
    hdr = ["load", "policy", "pairs", "x 70B p50", "x 13B p50", "x ratio 13B/70B p50",
           "excess 70B p50 us", "excess 13B p50 us", "excess ratio p50",
           "x 70B p99", "x 13B p99", "verdict"]
    PR = []
    for L in sorted({k[1] for k in keys}, reverse=True):
        a, b = G.get(("fix4k", L), {}), G.get(("l13b", L), {})
        for p in sorted(set(a) & set(b), key=er.pol_key):
            qa = {(q["seed"], q["rid"]): q for r in a[p] for q in r["_rq"]}
            qb = {(q["seed"], q["rid"]): q for r in b[p] for q in r["_rq"]}
            ks = sorted(set(qa) & set(qb))
            if not ks:
                continue
            xr = [qb[k]["x"] / qa[k]["x"] for k in ks]
            er_ = [qb[k]["excess"] / qa[k]["excess"] for k in ks if qa[k]["excess"] > 0]
            xa = [qa[k]["x"] for k in ks]
            xb = [qb[k]["x"] for k in ks]
            rr = er.pct(xr, 50)
            verdict = ("fixed-time (x falls)" if rr < 0.85 else
                       "proportional (x flat)" if rr < 1.1 else "super-proportional (x rises)")
            PR.append([L, PLABEL[p], len(ks), f"{er.pct(xa, 50):.2f}", f"{er.pct(xb, 50):.2f}",
                       f"{rr:.2f}", f"{er.pct([qa[k]['excess'] for k in ks], 50):.0f}",
                       f"{er.pct([qb[k]['excess'] for k in ks], 50):.0f}",
                       f"{er.pct(er_, 50):.2f}", f"{er.pct(xa, 99):.2f}", f"{er.pct(xb, 99):.2f}",
                       verdict])
    if PR:
        er.write_table(args.out + "_pair13b", hdr, PR,
                       "Same arrivals (13B window stretched 2.5x so the pattern in floor "
                       "units is identical), same request ids, same seeds: per-request "
                       "ratios, median over all (request, seed) pairs. Excess ratio 1.0 = "
                       "fixed-time cost, 2.5 = proportional to bytes.")

    figs(args, G, ranks)
    if args.iso:
        iso_report(args)


def iso_report(args):
    runs = [r for r in er.load(args.iso) if r["n_flows_finished"] == r["n_flows"]]
    D = defaultdict(lambda: defaultdict(dict))      # policy -> size label -> seed -> row
    for r in runs:
        q = per_request(r)[0]
        lab = r["label"].replace("iso_", "")
        D[r["policy"]][lab][r["seed"]] = q
    def tok(lab):
        m, t = lab.split("_t")
        return (int(t) * (2.5 if m == "m13b" else 1.0), lab)
    hdr = ["policy", "size", "shard MB", "floor us", "seeds", "x_floor med [min-max]",
           "excess us med [min-max]", "propagation bound x"]
    rows, fits = [], []
    for p in sorted(D, key=er.pol_key):
        for lab in sorted(D[p], key=tok):
            qs = list(D[p][lab].values())
            fl = qs[0]["floor"]
            rows.append([PLABEL[p], lab.replace("m70b_t", "70B ").replace("m13b_t", "13B "),
                         f"{qs[0]['bytes'] / 8 / 1e6:.1f}", f"{fl:.1f}", len(qs),
                         er.mmm([q["x"] for q in qs]), er.mmm([q["excess"] for q in qs], 0),
                         f"{(fl + BASE_LAT_US) / fl:.2f}"])
        # per-seed fit over all sizes: excess = a + b * floor
        seeds = sorted({s for lab in D[p] for s in D[p][lab]})
        A, Bs = [], []
        for sd in seeds:
            pts = [(D[p][lab][sd]["floor"], D[p][lab][sd]["excess"]) for lab in D[p] if sd in D[p][lab]]
            if len(pts) < 3:
                continue
            n = len(pts); sx = sum(x for x, _ in pts); sy = sum(y for _, y in pts)
            sxx = sum(x * x for x, _ in pts); sxy = sum(x * y for x, y in pts)
            b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
            A.append((sy - b * sx) / n); Bs.append(b)
        fits.append((p, A, Bs))
    notes = ("E8 isolated size ladder: one TP=8 request alone at t = 0, cross-pod, default "
             "tree, 400 G. excess = completion - floor. Fit (per seed, least squares over "
             "sizes): excess = a + b x floor; a = fixed per-request cost (us), b = "
             "size-proportional cost (fraction of floor).\n\n| policy | a (us) med [min-max] "
             "| b med [min-max] |\n|---|---|---|\n" +
             "".join(f"| {PLABEL[p]} | {er.mmm(A, 0)} | {er.mmm(Bs, 3)} |\n" for p, A, Bs in fits))
    er.write_table(args.out + "_iso", hdr, rows, notes)

    plt, STYLE, setup_axes, save = er.figs_setup()
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.4))
    for p in sorted(D, key=er.pol_key):
        lab_, col, mk = er.style(STYLE, p)
        labs = sorted(D[p], key=tok)
        fl = [next(iter(D[p][l].values()))["floor"] for l in labs]
        for ax, key in zip(axes, ("x", "excess")):
            v = [[q[key] for q in D[p][l].values()] for l in labs]
            m = [statistics.median(x) for x in v]
            ax.errorbar(fl, m, yerr=[[a - min(x) for a, x in zip(m, v)], [max(x) - a for a, x in zip(m, v)]],
                        color=col, marker=mk, ms=4, linewidth=1.2, capsize=2, label=lab_)
    import numpy as np
    ff = np.logspace(np.log10(5), np.log10(1e4), 100)
    axes[0].plot(ff, (ff + BASE_LAT_US) / ff, color="#444444", linestyle=":", linewidth=1.0,
                 label="propagation bound")
    axes[0].axhline(1.0, color="#444444", linestyle="--", linewidth=1.0)
    axes[1].plot(ff, [BASE_LAT_US] * len(ff), color="#444444", linestyle=":", linewidth=1.0)
    for ax, yl in zip(axes, ("distance from floor (x)", "excess = completion - floor (us)")):
        ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("request floor (us)")
        ax.set_ylabel(yl); setup_axes(ax)
    axes[0].legend(frameon=False, fontsize=7, loc="lower left", ncol=2)
    axes[0].set_title("one TP=8 request alone, seeds 1-5", fontsize=9)
    axes[1].set_title("fixed cost = flat line, proportional = slope 1", fontsize=9)
    fig.tight_layout()
    save(fig, os.path.join(HERE, "results", "figs"), f"{args.figtag}_iso_size")
    plt.close(fig)


def figs(args, G, ranks):
    plt, STYLE, setup_axes, save = er.figs_setup()
    import numpy as np
    out = os.path.join(HERE, "results", "figs")
    pols = [p for p in ORDER if any(p in G[k] for k in G)]

    # (1) p50 / p99 by regime at load 70
    regs = [R for R in REGIMES if (R, 70) in G]
    if regs:
        fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.4), sharey=True)
        w = 0.8 / max(1, len(pols))
        for ax, key, title in zip(axes, ("p50", "p99"), ("p50 request", "p99 request")):
            for j, p in enumerate(pols):
                lab, col, mk = er.style(STYLE, p)
                xs, ys, lo, hi = [], [], [], []
                for i, R in enumerate(regs):
                    rs = G[(R, 70)].get(p)
                    if not rs:
                        continue
                    v = [r[key] for r in rs]
                    xs.append(i + (j - (len(pols) - 1) / 2) * w)
                    ys.append(statistics.median(v)); lo.append(min(v)); hi.append(max(v))
                ys_, lo_, hi_ = np.array(ys), np.array(lo), np.array(hi)
                ax.bar(xs, ys_, w * 0.92, color=col, label=lab, zorder=2)
                ax.errorbar(xs, ys_, yerr=[ys_ - lo_, hi_ - ys_], fmt="none",
                            ecolor="#222222", elinewidth=0.8, capsize=2, zorder=3)
            # fluid ideal (policy independent): black tick per regime
            for i, R in enumerate(regs):
                anyp = next(iter(G[(R, 70)].values()))
                ideal = statistics.median([r["ideal_" + key] for r in anyp])
                ax.hlines(ideal, i - 0.42, i + 0.42, colors="#000000", linestyles=":",
                          linewidth=1.2, zorder=4, label="fluid ideal" if i == 0 else None)
            ax.axhline(1.0, color="#444444", linestyle="--", linewidth=1.0, zorder=1)
            ax.set_xticks(range(len(regs)))
            ax.set_xticklabels([RLABEL[R].replace(" ", "\n", 1) for R in regs])
            ax.set_title(f"{title}, load 70", fontsize=9)
            ax.set_yscale("log")
            setup_axes(ax)
        axes[0].set_ylabel("distance from floor (x)")
        axes[0].legend(frameon=False, fontsize=8, ncol=1, loc="upper left")
        save(fig, out, f"{args.figtag}_xfloor_by_regime")
        plt.close(fig)

    # (2) per-request scatter, code trace, load 70
    if ("code", 70) in G:
        c = G[("code", 70)]
        ps = [p for p in pols if p in c]
        fig, axes = plt.subplots(1, len(ps), figsize=(2.6 * len(ps), 3.0), sharey=True, sharex=True)
        axes = np.atleast_1d(axes)
        allq = [q for p in ps for r in c[p] for q in r["_rq"]]
        bmin = min(q["bytes"] for q in allq); bmax = max(q["bytes"] for q in allq)
        bb = np.logspace(np.log10(bmin), np.log10(bmax), 200)
        for ax, p in zip(axes, ps):
            lab, col, mk = er.style(STYLE, p)
            rq = [q for r in c[p] for q in r["_rq"]]
            ax.scatter([q["bytes"] / 1e9 for q in rq], [q["x"] for q in rq], s=9,
                       color=col, alpha=0.6, edgecolors="none", zorder=3)
            # binned median
            edges = np.logspace(np.log10(bmin), np.log10(bmax) + 1e-9, 8)
            mx, my = [], []
            for a, b in zip(edges, edges[1:]):
                v = [q["x"] for q in rq if a <= q["bytes"] < b]
                if len(v) >= 3:
                    mx.append(np.sqrt(a * b) / 1e9); my.append(statistics.median(v))
            ax.plot(mx, my, color="#000000", linewidth=1.2, zorder=4, label="binned median")
            ix, iy = [], []
            for a, b in zip(edges, edges[1:]):
                v = [q["ideal"] / q["floor"] for q in rq if a <= q["bytes"] < b]
                if len(v) >= 3:
                    ix.append(np.sqrt(a * b) / 1e9); iy.append(statistics.median(v))
            ax.plot(ix, iy, color="#777777", linewidth=1.0, linestyle="--", zorder=4,
                    label="fluid ideal, binned median")
            # latency floor (floor + one-way cross-pod latency) / floor
            fl = bb / 8 * 8 / 400e3                      # shard floor, us
            ax.plot(bb / 1e9, (fl + BASE_LAT_US) / fl, color="#444444", linestyle=":",
                    linewidth=1.0, zorder=2, label="propagation bound")
            ax.axhline(1.0, color="#444444", linestyle="--", linewidth=1.0, zorder=1)
            ax.set_xscale("log"); ax.set_yscale("log")
            ax.set_title(lab, fontsize=9)
            ax.set_xlabel("request bytes (GB, 8 shards)")
            setup_axes(ax)
        axes[0].set_ylabel("distance from floor (x)")
        axes[0].legend(frameon=False, fontsize=7, loc="upper right")
        fig.suptitle("Azure code-trace prompts, Llama-3-70B, load 70, seeds 1-3", fontsize=9)
        fig.tight_layout()
        save(fig, out, f"{args.figtag}_scatter_code_load70")
        plt.close(fig)

    # (3) ranking table as a figure: rank of each policy per (regime, load)
    if ranks:
        cols = [(R, L) for L in (70, 50) for R in REGIMES if (R, L) in ranks]
        fig, axes = plt.subplots(1, 2, figsize=(9.0, 2.6), sharey=True)
        for ax, which, title in zip(axes, (0, 1), ("rank by p50", "rank by p99")):
            M = np.full((len(pols), len(cols)), np.nan)
            for j, k in enumerate(cols):
                for rnk, p in enumerate(ranks[k][which]):
                    if p in pols:
                        M[pols.index(p), j] = rnk + 1
            ax.imshow(M, cmap="Greys", vmin=0.5, vmax=len(pols) + 1.5, aspect="auto")
            for i in range(len(pols)):
                for j in range(len(cols)):
                    if not np.isnan(M[i, j]):
                        ax.text(j, i, int(M[i, j]), ha="center", va="center", fontsize=9,
                                color="#ffffff" if M[i, j] >= 3 else "#000000")
            ax.set_xticks(range(len(cols)))
            ax.set_xticklabels([f"{R}\n{L}%" for R, L in cols], fontsize=7)
            ax.set_yticks(range(len(pols)))
            ax.set_yticklabels([PLABEL[p] for p in pols])
            ax.set_title(title + " (1 = best)", fontsize=9)
            ax.axvline(len([k for k in cols if k[1] == 70]) - 0.5, color="#000000", linewidth=0.8)
        fig.tight_layout()
        save(fig, out, f"{args.figtag}_ranking")
        plt.close(fig)


if __name__ == "__main__":
    main()
