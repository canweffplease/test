#!/usr/bin/env python3
"""Tables and figures for E2, E3 and E6 from parse.py summaries.

  exp_report.py e3 --out <stem> <summary.json> ...
  exp_report.py e2 --out <stem> [--fig NAME] <summary.json> ...
  exp_report.py e6 --out <stem> [--fig NAME] <summary.json> ...

Writes <stem>.md and <stem>.csv (and, for e6, <stem without _table>_order.csv);
figures go to results/figs/NAME.{png,pdf}. Called by scripts/e{2,3,6}.sh after
their runs; safe to rerun by hand on any subset of summaries.

Tags are <cc>_<topo>_<label>_r<i>[<variant>]_s<seed> (run.sh + the e*.sh
scripts). Statistics across seeds: "med [min-max]" is the median over seeds
of a per-run statistic, with its range. With 9-27 requests per run the p99
request is essentially the slowest request.

Reference bounds (policy independent, computed here from the matrix):

  floor      parse.py's floor: shard bytes / host link rate (x_floor = 1).
  iso        isolated-request bound: the request alone on the fabric, with
             perfect load balancing. Equals the floor on 1:1 trees; on
             oversubscribed trees it is floor x (the request's own
             oversubscription), e.g. 8 shards from one pod through 400 G of
             agg->core uplink on ft128_4os = 2 x floor (not 4 x: one TP
             instance only fills half its pod's hosts). x_iso = completion / iso.
  ideal      fluid, max-min fair, perfectly load-balanced fabric carrying the
             whole matrix (every host link, ToR uplink group and pod uplink
             group as one aggregate resource, same capacities as
             scripts/gen_workload.py). A request's ideal completion includes
             the queueing that any work-conserving fair network must impose
             (shared NICs, a saturated core). x_ideal = completion / ideal.
             x_ideal ~ 1: the fabric is saturated and the policy wastes
             nothing; x_ideal >> 1: the policy (or ECMP) wastes capacity.
  incast     (e6) K x floor: all K shards cross the receiver's 400 G downlink.
"""
import argparse, csv, json, math, os, re, statistics, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import gen_workload as gw          # topology model (capacities, tor/pod maps)

ORDER = ["eqds", "hpcc", "roce", "dcqcn"]
TAG_RE = re.compile(r"^([a-z]+)_(default|ft128_[148]os|fat_tree_128_[148]os)_(.+?)_r(\d+)(.*?)_s(\d+)$")
TOPO_LABEL = {"default": "ft16", "ft128_1os": "1:1", "ft128_4os": "4:1", "ft128_8os": "8:1"}


# --------------------------------------------------------------------------
# loading

def load(paths):
    runs = []
    for p in paths:
        try:
            s = json.load(open(p))
        except (OSError, ValueError) as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        m = TAG_RE.match(s["tag"])
        if not m:
            print(f"skip {p}: tag {s['tag']} not understood", file=sys.stderr)
            continue
        if s.get("lossless_violations"):
            print(f"skip {p}: lossless violations", file=sys.stderr)
            continue
        if s["n_flows_finished"] != s["n_flows"]:
            print(f"WARNING {p}: only {s['n_flows_finished']}/{s['n_flows']} flows finished",
                  file=sys.stderr)
        s["policy"], s["topo"], s["label"], _, s["variant"], seed = m.groups()
        s["seed"] = int(seed)
        s["runtopo"] = s["topo"]
        s["topo"] = s["topo"].replace("fat_tree_128_", "ft128_")   # run.sh file name -> alias
        d = os.path.dirname(os.path.abspath(p))
        s["reqs"] = list(csv.DictReader(open(os.path.join(d, s["requests_csv"]))))
        s["flows"] = list(csv.DictReader(open(os.path.join(d, s["flows_csv"]))))
        runs.append(s)
    return runs


def fnum(x):
    return float(x) if x not in ("", None) else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def mmm(xs, nd=2):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    if len(xs) == 1:
        return f"{xs[0]:.{nd}f}"
    return f"{statistics.median(xs):.{nd}f} [{min(xs):.{nd}f}-{max(xs):.{nd}f}]"


def pct(v, p):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def pol_key(p):
    return ORDER.index(p) if p in ORDER else 99


# --------------------------------------------------------------------------
# fluid max-min reference

def topo_model(name, link_gbps):
    return gw.get_topo(name, None, link_gbps)


def resources(T, s, d):
    r = [("host_up", s), ("host_down", d)]
    tier = T.tier(s, d)
    if tier in ("agg", "core"):
        r += [("tor_up", T.tor(s)), ("tor_down", T.tor(d))]
    if tier == "core":
        r += [("pod_up", T.pod(s)), ("pod_down", T.pod(d))]
    return r


def maxmin(active, res, cap):
    """Progressive filling. active: flow ids; res[f]: resources; cap: Gbps."""
    rem = {}
    users = defaultdict(set)
    for f in active:
        for r in res[f]:
            users[r].add(f)
            rem[r] = cap[r[0]]
    rate = {}
    free = set(active)
    while free:
        best, share = None, None
        for r, us in users.items():
            n = len(us & free)
            if n:
                sh = rem[r] / n
                if share is None or sh < share:
                    best, share = r, sh
        for f in list(users[best] & free):
            rate[f] = share
            free.discard(f)
            for r in res[f]:
                rem[r] -= share
    return rate


def fluid(flows, T):
    """flows: list of dict(id, src, dst, start_us, bytes). Returns {id: finish_us}."""
    cap = {k: gw.capacity(T, k) for k in
           ("host_up", "host_down", "tor_up", "tor_down", "pod_up", "pod_down")}
    res = {f["id"]: resources(T, f["src"], f["dst"]) for f in flows}
    pend = sorted(flows, key=lambda f: f["start_us"])
    left, fin, t, i = {}, {}, 0.0, 0
    while i < len(pend) or left:
        if not left:
            t = max(t, pend[i]["start_us"])
        while i < len(pend) and pend[i]["start_us"] <= t + 1e-9:
            left[pend[i]["id"]] = pend[i]["bytes"] * 8.0       # bits
            i += 1
        rate = maxmin(list(left), res, cap)                    # Gbps = 1e3 bits/us
        dt = min(left[f] / (rate[f] * 1e3) for f in left)
        if i < len(pend):
            dt = min(dt, pend[i]["start_us"] - t)
        t += dt
        for f in list(left):
            left[f] -= rate[f] * 1e3 * dt
            if left[f] <= 1e-6 * 8:                            # < 1 byte
                fin[f] = t
                del left[f]
    return fin


_REF = {}


def reference(run):
    """Per-request (floor, iso, ideal) completion in us, keyed by request_id."""
    key = (run["matrix"], run["topo"], run["link_gbps"])
    if key in _REF:
        return _REF[key]
    T = topo_model(run["topo"], run["link_gbps"])
    fl = [{"id": int(f["flow"]), "rid": f["request_id"], "src": int(f["src"]),
           "dst": int(f["dst"]), "start_us": float(f["start_us"]),
           "bytes": int(f["bytes"]), "floor": float(f["floor_us"])} for f in run["flows"]]
    fin = fluid(fl, T)
    byr = defaultdict(list)
    for f in fl:
        byr[f["rid"]].append(f)
    out = {}
    for rid, fs in byr.items():
        st = min(f["start_us"] for f in fs)
        iso_f = fluid([dict(f, start_us=0.0) for f in fs], T)
        out[rid] = {"floor": max(f["floor"] for f in fs),
                    "iso": max(iso_f.values()),
                    "ideal": max(fin[f["id"]] for f in fs) - st}
    _REF[key] = out
    return out


def run_stats(run):
    """Per-run request statistics against every reference."""
    ref = reference(run)
    xf, xi, xd, idl, isor = [], [], [], [], []
    ideal_miss = []
    for r in run["reqs"]:
        c = fnum(r["completion_us"])
        rf = ref[r["request_id"]]
        idl.append(rf["ideal"] / rf["floor"])
        isor.append(rf["iso"] / rf["floor"])
        dl = fnum(r["deadline_us"])
        if dl is not None:
            ideal_miss.append(int(fnum(r["start_us"]) + rf["ideal"] > dl + 1e-6))
        if c is None:
            continue
        xf.append(c / rf["floor"])
        xi.append(c / rf["iso"])
        xd.append(c / rf["ideal"])
    lc = run.get("log_counters") or {}
    return {
        "p50": pct(xf, 50), "p99": pct(xf, 99), "max": max(xf) if xf else None,
        "mean": sum(xf) / len(xf) if xf else None,
        "iso_ratio": med(isor),
        "xiso_p50": pct(xi, 50), "xiso_p99": pct(xi, 99),
        "ideal_p50": pct(idl, 50), "ideal_p99": pct(idl, 99),
        "xideal_p50": pct(xd, 50), "xideal_p99": pct(xd, 99),
        "miss": run.get("deadline_miss_rate"),
        "ideal_miss": sum(ideal_miss) / len(ideal_miss) if ideal_miss else None,
        "jain": run.get("jain_fairness"),
        "wall": run.get("wall_s"),
        # htsim_roce's "New:" is always 0 (RoceSrc never increments
        # _new_packets_sent); its "Rtx:" is real: go-back-N resends after a
        # NACK (roce.cpp:146), so 0 means no loss and no reordering
        "rtx": lc.get("rtx_pkts"),
        "new": None if run["policy"] in ("roce", "dcqcn") else lc.get("new_pkts"),
        "stale": lc.get("stale_nacks"),
        "nreq": run["n_requests_complete"], "nreq_all": run["n_requests"],
    }


def eff_load(run):
    """Offered load relative to this topology's bottleneck (sidecar meta)."""
    side = os.path.splitext(run["matrix"])[0] + ".json"
    if not os.path.exists(side):
        return None
    meta = json.load(open(side))["meta"]
    if run["topo"] in ("default", "ft128_1os"):
        return meta.get("offered_load_realized")
    for a in meta.get("also_topo", []):
        if a["topo"] == run["topo"]:
            return a["offered_load"]
    return None


def write_table(stem, header, rows, notes=""):
    with open(stem + ".csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([x for x in r])
    with open(stem + ".md", "w") as fh:
        if notes:
            fh.write(notes.rstrip() + "\n\n")
        fh.write("| " + " | ".join(header) + " |\n")
        fh.write("|" + "---|" * len(header) + "\n")
        for r in rows:
            fh.write("| " + " | ".join(str(x) for x in r) + " |\n")
    print(f"wrote {stem}.md / .csv")


def figs_setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.labelsize": 9,
                         "font.family": "DejaVu Sans", "pdf.fonttype": 42})
    from plot import STYLE, setup_axes, save
    return plt, STYLE, setup_axes, save


def style(STYLE, pol):
    return STYLE.get(pol, (pol.upper(), "#000000", "v"))


# --------------------------------------------------------------------------
# E3

def do_e3(args, runs):
    g = defaultdict(list)
    for r in runs:
        r["st"] = run_stats(r)
        g[(r["topo"], r["policy"] + r["variant"], r["load"])].append(r)
    hdr = ["topo", "policy", "load", "n", "req done", "p50 x_floor", "p99 x_floor",
           "max x_floor", "ideal p50/p99", "p50 x_ideal", "p99 x_ideal",
           "deadline miss", "ideal miss", "Jain", "Rtx pkts", "stale NACKs", "wall s"]
    rows = []
    for (topo, pol, load) in sorted(g, key=lambda k: (k[0], pol_key(k[1]), -k[2])):
        rs = g[(topo, pol, load)]
        S = [r["st"] for r in rs]
        rows.append([TOPO_LABEL.get(topo, topo), pol, load, len(rs),
                     f"{med([s['nreq'] for s in S]):.0f}/{S[0]['nreq_all']}",
                     mmm([s["p50"] for s in S]), mmm([s["p99"] for s in S]),
                     mmm([s["max"] for s in S]),
                     f"{S[0]['ideal_p50']:.2f} / {S[0]['ideal_p99']:.2f}",
                     mmm([s["xideal_p50"] for s in S]), mmm([s["xideal_p99"] for s in S]),
                     mmm([s["miss"] for s in S]), f"{S[0]['ideal_miss']:.2f}",
                     mmm([s["jain"] for s in S], 3),
                     mmm([s["rtx"] for s in S], 0), mmm([s["stale"] for s in S], 0),
                     mmm([s["wall"] for s in S], 0)])
    write_table(args.out, hdr, rows, notes=(
        "E3, per-request distance from floor. med [min-max] over seeds. "
        "`ideal` = fluid max-min fair, perfectly balanced fabric (scripts/exp_report.py); "
        "it is policy independent. x_ideal = completion / ideal completion. "
        "`ideal miss` = deadline miss rate of the ideal fabric (deadline = start + 2 x floor)."))

    if not args.fig:
        return
    plt, STYLE, setup_axes, save = figs_setup()
    for topo in sorted({k[0] for k in g}):
        fig, ax = plt.subplots(figsize=(5.0, 3.4))
        setup_axes(ax)
        pols = sorted({k[1] for k in g if k[0] == topo}, key=pol_key)
        loads = sorted({k[2] for k in g if k[0] == topo})
        anyrun = {L: next(g[k][0] for k in g if k[0] == topo and k[2] == L) for L in loads}
        ax.plot(loads, [anyrun[L]["st"]["ideal_p50"] for L in loads], color="#777777", ls="--",
                lw=1.2, label="ideal fair fabric p50")
        ax.plot(loads, [anyrun[L]["st"]["ideal_p99"] for L in loads], color="#777777", ls=":",
                lw=1.2, label="ideal fair fabric p99")
        for i, pol in enumerate(pols):
            lab, col, mk = style(STYLE, pol)
            dx = (i - (len(pols) - 1) / 2) * 0.8
            Ls = [L for L in loads if (topo, pol, L) in g]
            p50 = [med([r["st"]["p50"] for r in g[(topo, pol, L)]]) for L in Ls]
            p99 = [med([r["st"]["p99"] for r in g[(topo, pol, L)]]) for L in Ls]
            xs = [L + dx for L in Ls]
            ax.plot(xs, p50, color=col, marker=mk, lw=1.6, ms=5, label=f"{lab} p50")
            ax.plot(xs, p99, color=col, marker=mk, ls="none", mfc="white", ms=5, label=f"{lab} p99")
            ax.vlines(xs, p50, p99, color=col, alpha=0.4)
        ax.axhline(1.0, color="#444444", ls="--", lw=0.8)
        ax.set_xticks(loads)
        ax.set_xticklabels([f"{L}%" for L in loads])
        ax.set_xlabel("offered load (% of fabric bottleneck)")
        ax.set_ylabel("request completion / floor")
        ax.set_title(f"E3 {TOPO_LABEL.get(topo, topo)}: measured vs ideal fair fabric", fontsize=9)
        ax.legend(frameon=False, fontsize=7, loc="upper left")
        name = args.fig.replace("{topo}", "ft16" if topo == "default" else topo)
        save(fig, os.path.join(HERE, "results", "figs"), name)


# --------------------------------------------------------------------------
# E2

def e2_matrix(r):
    m = re.match(r"(e[23])load(\d+)", r["label"])
    if m:
        return m.group(1), int(m.group(2))
    if r["label"].startswith("load"):          # an E3 ft128_1os run reused
        return "e3", r["load"]
    return r["label"], None                    # victim / victimalone


def do_e2(args, runs):
    g = defaultdict(list)
    vict = defaultdict(list)
    for r in runs:
        fam, L = e2_matrix(r)
        if fam.startswith("victim"):
            vict[(r["topo"], r["policy"] + r["variant"], fam)].append(r)
            continue
        r["st"] = run_stats(r)
        r["eff"] = eff_load(r)
        g[(fam, L, r["topo"], r["policy"] + r["variant"])].append(r)
    hdr = ["matrix", "topo", "eff. load", "policy", "n", "p50 x_floor", "p99 x_floor",
           "iso bound (x floor)", "p50 x_iso", "p99 x_iso", "ideal p50/p99 (x floor)",
           "p50 x_ideal", "p99 x_ideal", "deadline miss", "Jain", "wall s"]
    rows = []
    topo_order = ["ft128_1os", "ft128_4os", "ft128_8os"]
    for k in sorted(g, key=lambda k: (k[0], k[1], topo_order.index(k[2]), pol_key(k[3]))):
        fam, L, topo, pol = k
        rs = g[k]
        S = [r["st"] for r in rs]
        name = f"{'interleaved' if fam == 'e2' else 'split'} load{L}"
        rows.append([name, TOPO_LABEL[topo], f"{rs[0]['eff']:.2f}", pol, len(rs),
                     mmm([s["p50"] for s in S]), mmm([s["p99"] for s in S]),
                     f"{S[0]['iso_ratio']:.2f}",
                     mmm([s["xiso_p50"] for s in S]), mmm([s["xiso_p99"] for s in S]),
                     f"{S[0]['ideal_p50']:.2f} / {S[0]['ideal_p99']:.2f}",
                     mmm([s["xideal_p50"] for s in S]), mmm([s["xideal_p99"] for s in S]),
                     mmm([s["miss"] for s in S]), mmm([s["jain"] for s in S], 3),
                     mmm([s["wall"] for s in S], 0)])
    write_table(args.out, hdr, rows, notes=(
        "E2, per-request distance from floor by topology. med [min-max] over seeds. "
        "eff. load = offered load / that topology's bottleneck (generator sidecar). "
        "iso bound = one request alone on that fabric, perfectly balanced "
        "(1:1 = 1, 4:1 = 2, 8:1 = 4 x floor); x_iso = completion / iso bound. "
        "ideal = fluid max-min fair fabric carrying the whole matrix; "
        "x_ideal = completion / ideal (1 = the policy wastes nothing)."))

    # victim diagnostic
    if vict:
        vh = ["topo", "policy", "matrix", "n", "victim 3->15 completion us",
              "victim x_floor", "congestor x_floor (median flow)", "congestor max x_floor"]
        vr = []
        for k in sorted(vict, key=lambda k: (k[0], pol_key(k[1]), k[2])):
            rs = vict[k]
            vc, cx, cm = [], [], []
            for r in rs:
                rx = []
                for f in r["flows"]:
                    c = fnum(f["completion_us"])
                    if c is None:
                        continue
                    if f["src"] == "3":
                        vc.append(c)
                    else:
                        rx.append(c / float(f["floor_us"]))
                cx += rx
                if rx:
                    cm.append(max(rx))
            vr.append([TOPO_LABEL[k[0]], k[1], k[2], len(rs), mmm(vc, 0),
                       mmm([c / 13421.8 for c in vc]),
                       f"{statistics.median(cx):.2f}" if cx else "-",
                       mmm(cm)])
        write_table(args.out.replace("_table", "") + "_victim", vh, vr, notes=(
            "PFC victim diagnostic (workloads/e2/victim_ft128.cm). The victim 3->15 "
            "stays inside pod 0 and crosses no oversubscribed link; its fluid ideal is "
            "the floor, 13,422 us. Six congestors push 600 G from pod 0 into pod 4."))

    if not args.fig:
        return
    plt, STYLE, setup_axes, save = figs_setup()
    for fam, figname in (("e2", args.fig), ("e3", args.fig + "_split")):
        loads = sorted({k[1] for k in g if k[0] == fam})
        if not loads:
            continue
        fig, axes = plt.subplots(2, len(loads), figsize=(3.0 * len(loads), 5.4),
                                 sharey="row", squeeze=False)
        for j, L in enumerate(loads):
            ax, bx = axes[0][j], axes[1][j]
            setup_axes(ax); setup_axes(bx)
            tps = [t for t in topo_order if any(k[:3] == (fam, L, t) for k in g)]
            xs = list(range(len(tps)))
            pols = sorted({k[3] for k in g if k[0] == fam and k[1] == L}, key=pol_key)
            ref = None
            ticks = []
            for t in tps:
                anyr = next(g[k][0] for k in g if k[:3] == (fam, L, t))
                ticks.append(f"{TOPO_LABEL[t]}\nload {anyr['eff']:.2f}")
            # policy independent references
            ip50 = [next(g[k][0]["st"]["ideal_p50"] for k in g if k[:3] == (fam, L, t)) for t in tps]
            ip99 = [next(g[k][0]["st"]["ideal_p99"] for k in g if k[:3] == (fam, L, t)) for t in tps]
            iso = [next(g[k][0]["st"]["iso_ratio"] for k in g if k[:3] == (fam, L, t)) for t in tps]
            ax.plot(xs, ip50, color="#777777", ls="--", lw=1.0, label="ideal fabric p50")
            ax.plot(xs, ip99, color="#777777", ls=":", lw=1.0, label="ideal fabric p99")
            ax.plot(xs, iso, color="#bbbbbb", ls="-", lw=3, alpha=0.5, zorder=0,
                    label="isolated-request bound")
            for i, pol in enumerate(pols):
                lab, col, mk = style(STYLE, pol)
                dx = (i - (len(pols) - 1) / 2) * 0.06
                p50 = [med([r["st"]["p50"] for r in g.get((fam, L, t, pol), [])]) for t in tps]
                p99 = [med([r["st"]["p99"] for r in g.get((fam, L, t, pol), [])]) for t in tps]
                q50 = [med([r["st"]["xideal_p50"] for r in g.get((fam, L, t, pol), [])]) for t in tps]
                q99 = [med([r["st"]["xideal_p99"] for r in g.get((fam, L, t, pol), [])]) for t in tps]
                xx = [x + dx for x in xs]
                ax.plot(xx, p50, color=col, marker=mk, lw=1.6, ms=5, label=f"{lab} p50")
                ax.plot(xx, p99, color=col, marker=mk, lw=1.0, ms=5, ls="none",
                        mfc="white", label=f"{lab} p99")
                ax.vlines(xx, p50, p99, color=col, alpha=0.4)
                bx.plot(xx, q50, color=col, marker=mk, lw=1.6, ms=5, label=f"{lab} p50")
                bx.plot(xx, q99, color=col, marker=mk, ls="none", mfc="white", ms=5,
                        label=f"{lab} p99")
                bx.vlines(xx, q50, q99, color=col, alpha=0.4)
            bx.axhline(1.0, color="#444444", ls="--", lw=1.0)
            for a in (ax, bx):
                a.set_xticks(xs)
                a.set_xticklabels(ticks)
                a.set_xlim(-0.4, len(xs) - 0.6)
            ax.set_title(f"{'interleaved' if fam == 'e2' else 'split (E3)'} matrix, "
                         f"{L}% of 1:1", fontsize=9)
            ax.set_yscale("log")
        axes[0][0].set_ylabel("request completion / floor")
        axes[1][0].set_ylabel("completion / ideal-fabric completion")
        h, l = axes[0][-1].get_legend_handles_labels()
        fig.legend(h, l, frameon=False, fontsize=7, loc="lower center", ncol=min(len(l), 5),
                   bbox_to_anchor=(0.5, 0.0))
        fig.tight_layout(rect=(0, 0.06, 1, 1))
        save(fig, os.path.join(HERE, "results", "figs"), figname)


# --------------------------------------------------------------------------
# E6

def do_e6(args, runs):
    g = defaultdict(list)
    order_rows = []
    for r in runs:
        m = re.match(r"incast(\d+)", r["label"])
        K = int(m.group(1)) if m else 1
        fl = [f for f in r["flows"] if f["finish_us"]]
        fin = sorted(float(f["finish_us"]) - float(f["start_us"]) for f in fl)
        floor = float(r["flows"][0]["floor_us"])
        bound = K * floor
        last = fin[-1]
        st = {"K": K, "last": last, "x_floor": last / floor, "x_bound": last / bound,
              "first_bound": fin[0] / bound, "spread": (fin[-1] - fin[0]) / fin[-1],
              "jain": r.get("jain_fairness"), "wall": r.get("wall_s"),
              "n": len(fl), "nall": len(r["flows"])}
        r["st"] = st
        g[(r["label"].split("_")[0] if not m else f"incast{K}", r["policy"] + r["variant"])].append(r)
        for rank, f in enumerate(sorted(fl, key=lambda f: float(f["finish_us"]))):
            c = float(f["finish_us"]) - float(f["start_us"])
            order_rows.append([r["tag"], r["policy"], K if m else "tp8", r["seed"], rank + 1,
                               f["src"], f["dst"], f"{c:.1f}", f"{c / floor:.3f}",
                               f"{c / bound:.3f}"])
    hdr = ["pattern", "K", "policy", "n", "flows done", "slowest flow us", "x_floor",
           "x_incast_bound", "first flow / bound", "finish spread (last-first)/last",
           "Jain", "wall s"]
    rows = []
    for k in sorted(g, key=lambda k: (g[k][0]["st"]["K"] if k[0] != "tp8" else 0, pol_key(k[1]))):
        rs = g[k]
        S = [r["st"] for r in rs]
        rows.append([k[0], S[0]["K"], k[1], len(rs), f"{S[0]['n']}/{S[0]['nall']}",
                     mmm([s["last"] for s in S], 0), mmm([s["x_floor"] for s in S]),
                     mmm([s["x_bound"] for s in S], 3), mmm([s["first_bound"] for s in S], 3),
                     mmm([s["spread"] for s in S], 3), mmm([s["jain"] for s in S], 3),
                     mmm([s["wall"] for s in S], 0)])
    write_table(args.out, hdr, rows, notes=(
        "E6. floor = 3,355 us per 167.8 MB shard at 400 G. incast bound = K x floor "
        "(all K shards share the receiver's 400 G downlink); for tp8 (8 disjoint "
        "pairs, no shared endpoint) K = 1. med [min-max] over seeds."))
    ostem = args.out.replace("_table", "") + "_order.csv"
    with open(ostem, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tag", "policy", "K", "seed", "finish_rank", "src", "dst",
                    "completion_us", "x_floor", "x_incast_bound"])
        w.writerows(order_rows)
    print(f"wrote {ostem}")

    if not args.fig:
        return
    plt, STYLE, setup_axes, save = figs_setup()
    fig, ax = plt.subplots(figsize=(5.0, 3.4))
    setup_axes(ax)
    pols = sorted({k[1] for k in g}, key=pol_key)
    for i, pol in enumerate(pols):
        lab, col, mk = style(STYLE, pol)
        ks = sorted({g[k][0]["st"]["K"] for k in g if k[1] == pol and k[0] != "tp8"})
        dx = (i - (len(pols) - 1) / 2) * 0.05
        y = [med([r["st"]["x_bound"] for r in g[(f"incast{K}", pol)]]) for K in ks]
        lo = [min(r["st"]["x_bound"] for r in g[(f"incast{K}", pol)]) for K in ks]
        hi = [max(r["st"]["x_bound"] for r in g[(f"incast{K}", pol)]) for K in ks]
        y1 = [med([r["st"]["first_bound"] for r in g[(f"incast{K}", pol)]]) for K in ks]
        xs = [math.log2(K) + dx for K in ks]
        ax.errorbar(xs, y, yerr=[[a - b for a, b in zip(y, lo)], [b - a for a, b in zip(y, hi)]],
                    color=col, marker=mk, lw=1.6, ms=5, capsize=2, label=f"{lab}, slowest flow")
        ax.plot(xs, y1, color=col, marker=mk, ls=":", mfc="white", ms=5, lw=1.0,
                label=f"{lab}, first flow to finish")
        if (("tp8", pol)) in g:
            t = [r["st"]["x_bound"] for r in g[("tp8", pol)]]
            ax.errorbar([0 + dx], [med(t)], yerr=[[med(t) - min(t)], [max(t) - med(t)]],
                        color=col, marker=mk, ms=6, mfc="white", capsize=2, ls="none")
    ax.axhline(1.0, color="#444444", ls="--", lw=1.0)
    ax.text(2.95, 1.0, "K x floor (receiver downlink)", ha="right", va="bottom", fontsize=8,
            color="#444444")
    ax.set_xticks([0, 1, 2, 3])
    ax.set_xticklabels(["1\n(TP=8, no incast)", "2", "4", "8"])
    ax.set_xlabel("fan-in K (senders into one 400 G downlink)")
    ax.set_ylabel("completion / (K x floor)")
    ax.legend(frameon=False, fontsize=7, loc="upper right")
    save(fig, os.path.join(HERE, "results", "figs"), args.fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exp", choices=["e2", "e3", "e6"])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True, help="table stem (writes .md and .csv)")
    ap.add_argument("--fig", help="figure basename under results/figs/")
    args = ap.parse_args()
    runs = load(args.inputs)
    if not runs:
        sys.exit("no usable summaries")
    {"e2": do_e2, "e3": do_e3, "e6": do_e6}[args.exp](args, runs)


if __name__ == "__main__":
    main()
