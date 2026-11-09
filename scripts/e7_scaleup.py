#!/usr/bin/env python3
"""E7: how much of the KV-transfer problem the scale-up domain removes on its own.

Analytic plus trace-driven placement model. No packet simulation. For a
cluster of H GPUs partitioned into NVLink/NVSwitch scale-up domains of D GPUs,
with prefill and decode instances of TP GPUs each, it computes which fraction of
prefill->decode KV transfers (and of KV bytes) stay inside a scale-up domain and
never touch the Ethernet fabric, how the rest splits over the fat-tree tiers
(same ToR / same pod / cross-pod), the offered load each tier sees at the Azure
traces' native arrival rate, and how far the request rate can grow before the
pod->core uplinks (or any fabric resource) saturate.

Two engines:

  closed form   decode capacity unconstrained (c = inf). For every prefill
                instance the placement policy defines a probability vector over
                decode instances; everything is an exact expectation over it.
  Monte Carlo   decode instances accept at most c concurrent requests. An event
                simulation replays a trace (arrival times, prompt lengths,
                output lengths) against the cluster; spills are counted.
                Repeated over seeds for confidence intervals. Also used, with
                the closed form inside, to average over random role layouts.

All modelling assumptions are listed in docs/E7.md. Stdlib + numpy (+ matplotlib
for the figures).

Usage:
  scripts/e7_scaleup.py                 # everything (~17 min, one core)
  scripts/e7_scaleup.py --no-mc         # closed form + load tables (~2 s)
  scripts/e7_scaleup.py --fig-only      # redraw figures from the CSVs
  scripts/e7_scaleup.py --reps 3 --quick   # smoke run
Outputs: results/e7/*.csv, results/figs/e7_offfabric.{png,pdf},
         results/figs/e7_crosspod.{png,pdf}
"""
import argparse
import csv
import heapq
import math
import os
import random
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from gen_workload import Topo, get_topo, parse_ts  # noqa: E402  (import only)

# Llama-3-70B: 2 (K,V) * 80 layers * 8 kv heads * 128 head dim * 2 bytes
BPT = 2 * 80 * 8 * 128 * 2          # 327,680 B/token = 320 KiB
RAILS = 8                           # NICs per HGX node; local GPU j -> rail j % 8

POLICIES = ["random", "locality", "rail", "locrail"]
POLICY_LABEL = {
    "random": "random",
    "locality": "locality-aware",
    "rail": "rail-aligned",
    "locrail": "locality + rail",
}
# wiring each policy is evaluated on (rail-aligned is a wiring+placement co-design)
POLICY_WIRING = {"random": "conv", "locality": "conv", "rail": "rail", "locrail": "rail"}

RATIOS = [(2, 1), (1, 1), (1, 2), (1, 3)]      # prefill : decode instances
MAIN_RATIOS = [(1, 1), (1, 2), (1, 3)]
D_SWEEP = [8, 16, 32, 36, 64, 72, 144, 288, 576]

# Okabe-Ito, colorblind safe (same palette as scripts/plot.py)
COLORS = {"random": "#D55E00", "rail": "#E69F00", "locality": "#0072B2",
          "locality_cap": "#56B4E9", "locrail": "#009E73"}
MARKERS = {"random": "D", "rail": "^", "locality": "o", "locality_cap": "s",
           "locrail": "v"}

T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
        2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
        2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def ci95(xs):
    xs = np.asarray(xs, float)
    m = float(xs.mean())
    if len(xs) < 2:
        return m, 0.0
    t = T975[min(len(xs) - 2, len(T975) - 1)]
    return m, t * float(xs.std(ddof=1)) / math.sqrt(len(xs))


def rstr(r):
    return f"{r[0]}:{r[1]}"


# ----------------------------------------------------------------------------
# topologies
# ----------------------------------------------------------------------------

def scaled_topo(base, nodes, name):
    """Same ToR/pod sizes and per-ToR / per-pod capacities, more pods."""
    return Topo(name, nodes, 3, base.hosts_per_tor, base.hosts_per_pod,
                base.host_gbps, base.tor_link_gbps, base.core_link_gbps,
                base.tor_up_gbps, base.pod_up_gbps,
                f"synthetic: {base.name} extended to {nodes} hosts")


def clusters():
    t1, t4 = get_topo("fat_tree_1024_1os", None, 0), get_topo("fat_tree_1024_4os", None, 0)
    return {
        "ft128": {"H": 128, "1:1": get_topo("ft128_1os", None, 0),
                  "4:1": get_topo("ft128_4os", None, 0)},
        "ft1024": {"H": 1024, "1:1": t1, "4:1": t4},
        "ft1152": {"H": 1152, "1:1": scaled_topo(t1, 1152, "ft1152_1os"),
                   "4:1": scaled_topo(t4, 1152, "ft1152_4os")},
    }


# ----------------------------------------------------------------------------
# cluster layout
# ----------------------------------------------------------------------------

class Layout:
    """GPUs, scale-up domains, instances. GPU g is fat-tree host g (the
    generator's convention). Domains are D consecutive GPUs; the last one is
    partial if D does not divide H. Instances (TP consecutive GPUs) are packed
    from the start of each domain and never straddle one; leftover GPUs idle."""

    def __init__(self, H, D, TP):
        self.H, self.D, self.TP = H, D, TP
        self.domains = []
        s = 0
        while s < H:
            self.domains.append((s, min(D, H - s)))
            s += D
        starts, dom = [], []
        for di, (s0, sz) in enumerate(self.domains):
            for j in range(sz // TP):
                starts.append(s0 + j * TP)
                dom.append(di)
        self.inst_start = np.array(starts)
        self.inst_dom = np.array(dom)
        self.n = len(starts)
        self.gpu_dom = np.repeat(np.arange(len(self.domains)),
                                 [sz for _, sz in self.domains])
        self.gpu_local = np.concatenate([np.arange(sz) for _, sz in self.domains])
        self.idle = H - self.n * TP

    def describe(self):
        full = sum(1 for _, sz in self.domains if sz == self.D)
        part = [sz for _, sz in self.domains if sz != self.D]
        s = f"{full} x {self.D}"
        if part:
            s += f" + {'+'.join(map(str, part))} partial"
        return s


def roles(n, ratio, layout, rng=None):
    """Boolean prefill mask over instance slots, prefill:decode = p:d.
    mixed    Bresenham spread: every window of p+d consecutive slots has exactly
             p prefill, so every domain holds both roles in the global ratio
             as closely as its instance count allows (best case for locality).
    pooled   the first p/(p+d) of the slots are prefill (gen_workload --placement
             split): the pools meet only in the boundary domain.
    shuffled the mixed role multiset in a uniformly random order."""
    p, d = ratio
    k = np.arange(n)
    mixed = ((k + 1) * p) // (p + d) - (k * p) // (p + d)
    if layout == "mixed":
        m = mixed
    elif layout == "pooled":
        m = (k < mixed.sum()).astype(int)
    elif layout == "shuffled":
        m = mixed.copy()
        rng.shuffle(m)
    else:
        raise ValueError(layout)
    m = m.astype(bool)
    if m.all() or not m.any():
        raise ValueError(f"layout {layout} ratio {ratio}: no prefill or no decode")
    return m


class Wiring:
    """Per-GPU ToR id and pod id plus per-ToR / per-pod uplink capacities.

    conv  conventional labelling, host = GPU id (what gen_workload uses):
          ToR = g // hosts_per_tor, pod = g // hosts_per_pod.
    rail  rail-optimized labelling (gen_workload --placement rail, and
          rail_128_1os.topo): local GPU j of every domain is on rail j % 8, and
          each rail is one pod-like group, so a shard whose source and
          destination have the same local index never crosses the core.
          Within a rail, ToRs group hosts_per_tor consecutive rail members.
          Capacities per host are those of the base fat tree (same
          oversubscription), scaled by the group's host count."""

    def __init__(self, kind, lay, topo):
        H = lay.H
        hpt, hpp = topo.hosts_per_tor, topo.hosts_per_pod
        tor_per_host = topo.tor_up_gbps / hpt
        pod_per_host = topo.pod_up_gbps / hpp
        self.kind = kind
        self.host_gbps = topo.host_gbps
        if kind == "conv":
            self.tor = np.arange(H) // hpt
            self.pod = np.arange(H) // hpp
        else:
            rail = lay.gpu_local % RAILS
            rank = np.zeros(H, int)
            for r in range(RAILS):
                idx = np.nonzero(rail == r)[0]
                rank[idx] = np.arange(len(idx))
            self.pod = rail
            self.tor = rail * (H + 1) + rank // hpt
            _, self.tor = np.unique(self.tor, return_inverse=True)
        self.tor_cap = np.bincount(self.tor) * tor_per_host
        self.pod_cap = np.bincount(self.pod) * pod_per_host


class Pairs:
    """P x Dn tables for every (prefill instance, decode instance) pair."""

    def __init__(self, lay, is_p, wire):
        self.lay, self.wire = lay, wire
        TP = lay.TP
        self.p_idx = np.nonzero(is_p)[0]
        self.d_idx = np.nonzero(~is_p)[0]
        sp, sd = lay.inst_start[self.p_idx], lay.inst_start[self.d_idx]
        self.P, self.Dn = len(sp), len(sd)
        self.same_dom = lay.inst_dom[self.p_idx][:, None] == lay.inst_dom[self.d_idx][None, :]
        n_tor = np.zeros((self.P, self.Dn), int)
        n_agg = np.zeros_like(n_tor)
        n_core = np.zeros_like(n_tor)
        allpod = np.ones((self.P, self.Dn), bool)
        self.shard_masks = []
        for i in range(TP):
            s, t = sp + i, sd + i
            ts = wire.tor[s][:, None] == wire.tor[t][None, :]
            ps = wire.pod[s][:, None] == wire.pod[t][None, :]
            n_tor += ts
            n_agg += ~ts & ps
            n_core += ~ps
            allpod &= ps
            self.shard_masks.append((s, t, ts, ps))
        off = ~self.same_dom
        self.n_tor, self.n_agg, self.n_core = n_tor * off, n_agg * off, n_core * off
        self.allpod = allpod   # every shard stays below the core

    def candidate_sets(self, policy):
        """Ordered preference list of boolean P x Dn masks (global last)."""
        return {"random": [],
                "locality": [self.same_dom],
                "rail": [self.allpod],
                "locrail": [self.same_dom, self.allpod]}[policy]

    def weights(self, policy):
        """Closed form, c = inf: W[p, d] = Pr(request goes p -> d), with the
        prefill instance uniform and the decode instance uniform over the
        first non-empty preference set."""
        W = np.full((self.P, self.Dn), 1.0 / self.Dn)
        done = np.zeros(self.P, bool)
        for m in self.candidate_sets(policy):
            cnt = m.sum(1)
            rows = (cnt > 0) & ~done
            W[rows] = m[rows] / cnt[rows, None]
            done |= rows
        return W / self.P

    def fractions(self, W):
        """Byte-weighted fractions (all requests' bytes are split into TP equal
        shards, so shard counts are byte fractions). W must sum to 1."""
        TP = self.lay.TP
        nv = float((W * self.same_dom).sum())
        tor = float((W * self.n_tor).sum()) / TP
        agg = float((W * self.n_agg).sum()) / TP
        core = float((W * self.n_core).sum()) / TP
        nv = min(max(nv, 0.0), 1.0)
        fab = max(1.0 - nv, 0.0)
        if fab < 1e-12:
            fab = 0.0
        return {"nvlink": nv, "fabric": fab, "tor": tor, "agg": agg,
                "core": core, "intra_pod": tor + agg}

    def fluid_fabric(self, rho):
        """Locality-aware, fluid limit (c large at fixed utilization rho): a
        domain with nP local prefill and nD local decode instances receives
        nP/P of the requests, i.e. a decode occupancy of rho*Dn*nP/P instances'
        worth, and can keep at most nD of it local. Fraction of bytes that
        must still cross the fabric = 1 - sum_dom (nP/P) min(1, nD*P/(rho*Dn*nP))."""
        dp = self.lay.inst_dom[self.p_idx]
        dd = self.lay.inst_dom[self.d_idx]
        nd = len(self.lay.domains)
        nP = np.bincount(dp, minlength=nd).astype(float)
        nD = np.bincount(dd, minlength=nd).astype(float)
        m = nP > 0
        loc = np.minimum(1.0, nD[m] * self.P / (rho * self.Dn * nP[m]))
        return float(max(0.0, 1.0 - (nP[m] / self.P * loc).sum()))

    def capacity_limits(self, W):
        """Aggregate KV rate (Gbps, NVLink bytes included) the cluster can
        offer with traffic matrix W before each class of fabric resource
        saturates, assuming perfect load balancing inside each link group
        (the generator's bottleneck model)."""
        TP = self.lay.TP
        w = self.wire
        H = self.lay.H
        up = np.zeros(H)
        down = np.zeros(H)
        tor_up = np.zeros(len(w.tor_cap))
        tor_dn = np.zeros(len(w.tor_cap))
        pod_up = np.zeros(len(w.pod_cap))
        pod_dn = np.zeros(len(w.pod_cap))
        Woff = W * ~self.same_dom / TP
        for s, t, ts, ps in self.shard_masks:
            np.add.at(up, s, Woff.sum(1))
            np.add.at(down, t, Woff.sum(0))
            m = Woff * ~ts
            np.add.at(tor_up, w.tor[s], m.sum(1))
            np.add.at(tor_dn, w.tor[t], m.sum(0))
            m = Woff * ~ps
            np.add.at(pod_up, w.pod[s], m.sum(1))
            np.add.at(pod_dn, w.pod[t], m.sum(0))

        def lim(cap, share):
            nz = share > 1e-15
            if not nz.any():
                return math.inf
            return float(np.min(np.broadcast_to(cap, share.shape)[nz] / share[nz]))
        out = {"nic": min(lim(w.host_gbps, up), lim(w.host_gbps, down)),
               "tor": min(lim(w.tor_cap, tor_up), lim(w.tor_cap, tor_dn)),
               "core": min(lim(w.pod_cap, pod_up), lim(w.pod_cap, pod_dn)),
               "pod_up_share_max": float(pod_up.max()) if len(pod_up) else 0.0}
        out["any"] = min(out["nic"], out["tor"], out["core"])
        out["binds"] = min(("nic", "tor", "core"), key=lambda k: out[k]) \
            if math.isfinite(out["any"]) else "none"
        return out


# ----------------------------------------------------------------------------
# traces
# ----------------------------------------------------------------------------

def load_trace(name):
    """Return dict with t (s from first), prompt tokens, generated tokens."""
    path = os.path.join(REPO, "data", "azure", f"AzureLLMInferenceTrace_{name}.csv")
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((parse_ts(r["TIMESTAMP"]), int(r["ContextTokens"]),
                             int(r["GeneratedTokens"])))
            except (ValueError, KeyError):
                continue
    rows.sort()
    t = np.array([r[0] for r in rows])
    return {"name": name, "t": t - t[0], "prompt": np.array([r[1] for r in rows]),
            "gen": np.array([r[2] for r in rows])}


def trace_load_stats(tr, prompt_override=None):
    """Native-speed request rate and KV rate (all KV bytes, before placement)."""
    t = tr["t"]
    dur = t[-1] - t[0]
    prompt = tr["prompt"] if prompt_override is None else np.full(len(t), prompt_override)
    byts = prompt.astype(float) * BPT
    rate = len(t) / dur
    gbps = byts.sum() * 8 / dur / 1e9
    per_s = np.bincount(t.astype(int), weights=byts, minlength=int(dur) + 1) * 8 / 1e9
    return {"req_s": rate, "mean_GB": byts.mean() / 1e9, "gbps": gbps,
            "p99_1s_gbps": float(np.percentile(per_s, 99)),
            "max_1s_gbps": float(per_s.max())}


TPOT_S = 0.05        # assumed decode time per output token (s); sets hold time
WINDOW_HOLDS = 40    # measured window, in mean decode hold times
WARM_HOLDS = 8       # warm-up discarded before the window, in mean hold times


def mc_workload(kind, traces, slots, rho, rng):
    """Arrivals for one MC repetition: (times s, hold s, bytes), warm-up first.

    The cluster has `slots` = decode instances x c concurrent-request slots and
    is driven at mean decode occupancy rho. One Azure trace is one front end
    offering only ~5 req/s, so the cluster is fed the superposition of K
    independent copies of the trace, each started at a uniformly random offset
    into the hour (circular), K = rho*slots / (trace rate x mean hold), with
    hold = GeneratedTokens x TPOT_S. Non-integer K is handled by thinning.
    This keeps each request's prompt and output length and the trace's own
    arrival-time structure; superposing many front ends smooths burstiness.
    fixed4k: Poisson arrivals, exponential holds (mean 1 s), 4096-token prompts."""
    if kind == "fixed4k":
        Eh = 1.0
        lam = rho * slots / Eh
        T = (WARM_HOLDS + WINDOW_HOLDS) * Eh
        n = rng.poisson(lam * T)
        t = np.sort(rng.uniform(0, T, n))
        return t, rng.exponential(Eh, n), np.full(n, 4096.0 * BPT), WARM_HOLDS * Eh
    tr = traces[kind]
    dur = tr["t"][-1]
    hold = tr["gen"] * TPOT_S
    Eh = hold.mean()
    rate = len(hold) / dur
    K = rho * slots / (rate * Eh)
    Kint = int(math.ceil(K))
    T = (WARM_HOLDS + WINDOW_HOLDS) * Eh
    if T > dur:
        raise ValueError("window longer than the trace")
    ts, hs, bs = [], [], []
    for _ in range(Kint):
        tt = (tr["t"] - rng.uniform(0, dur)) % dur
        m = (tt < T) & (rng.random(len(tt)) < K / Kint)
        ts.append(tt[m])
        hs.append(hold[m])
        bs.append(tr["prompt"][m].astype(float) * BPT)
    t = np.concatenate(ts)
    o = np.argsort(t, kind="stable")
    return t[o], np.concatenate(hs)[o], np.concatenate(bs)[o], WARM_HOLDS * Eh


# ----------------------------------------------------------------------------
# Monte Carlo event simulation with decode capacity c
# ----------------------------------------------------------------------------

def simulate(pairs, policy, c, t, hold, seed, warm_t):
    """Replay arrivals (times t, decode hold times) against the cluster; only
    requests arriving after warm_t are reported. Prefill instance uniform at
    random; decode instance uniform over FREE members of the first non-empty
    preference set, else uniform over free decode instances anywhere, else (all
    full) uniform over all decode instances (counted as overflow).
    Returns (p, d, spilled, overflow, warm-up count): spilled = the request's
    first preference set was non-empty but had no free slot."""
    rng = random.Random(seed)
    P, Dn = pairs.P, pairs.Dn
    N = len(t)
    t = t.tolist()
    hold = hold.tolist()
    sets = [[np.nonzero(m[p])[0].tolist() for p in range(P)]
            for m in pairs.candidate_sets(policy)]
    busy = [0] * Dn
    heap = []
    pc = [rng.randrange(P) for _ in range(N)]
    dc = [0] * N
    pref = [False] * N
    had = [False] * N
    over = [False] * N
    cap = c if c else 1 << 60
    for i in range(N):
        ti = t[i]
        while heap and heap[0][0] <= ti:
            busy[heapq.heappop(heap)[1]] -= 1
        p = pc[i]
        d = -1
        for lvl, s in enumerate(sets):
            cand = s[p]
            if not cand:
                continue
            if lvl == 0:
                had[i] = True
            free = [x for x in cand if busy[x] < cap]
            if free:
                d = free[rng.randrange(len(free))]
                pref[i] = lvl == 0
                break
        if d < 0:
            for _ in range(64):
                x = rng.randrange(Dn)
                if busy[x] < cap:
                    d = x
                    break
            if d < 0:
                free = [x for x in range(Dn) if busy[x] < cap]
                if free:
                    d = free[rng.randrange(len(free))]
                else:
                    d = rng.randrange(Dn)
                    over[i] = True
        busy[d] += 1
        dc[i] = d
        if c:
            heapq.heappush(heap, (ti + hold[i], d))
    k = int(np.searchsorted(np.asarray(t), warm_t))
    spilled = np.array(had[k:]) & ~np.array(pref[k:])
    return (np.array(pc[k:]), np.array(dc[k:]), spilled, np.array(over[k:]), k)


def mc_point(pairs, policy, c, rho, wl, traces, reps, seed0):
    out = {k: [] for k in ("tx_fabric", "bytes_fabric", "core", "intra_pod",
                           "spill", "overflow", "B_core", "B_any", "n_req")}
    for r in range(reps):
        rng = np.random.default_rng(seed0 + r)
        # c = inf has no occupancy; drive it with the c=16 workload
        arr, hold, byts, warm = mc_workload(wl, traces, pairs.Dn * (c or 16), rho, rng)
        p, d, spilled, over, k = simulate(pairs, policy, c, arr, hold, seed0 + r, warm)
        b = byts[k:]
        out["n_req"].append(len(b))
        W = np.zeros((pairs.P, pairs.Dn))
        np.add.at(W, (p, d), b)
        W /= W.sum()
        fr = pairs.fractions(W)
        nv = pairs.same_dom[p, d]
        out["tx_fabric"].append(1 - nv.mean())
        out["bytes_fabric"].append(fr["fabric"])
        out["core"].append(fr["core"])
        out["intra_pod"].append(fr["intra_pod"])
        out["spill"].append(spilled.mean())
        out["overflow"].append(over.mean())
        lim = pairs.capacity_limits(W)
        out["B_core"].append(lim["core"])
        out["B_any"].append(lim["any"])
    return {k: ci95(v) for k, v in out.items()}


def erlang_b(a, m):
    """Blocking probability of an M/G/m/m loss system with offered load a."""
    b = 1.0
    for k in range(1, m + 1):
        b = a * b / (k + a * b)
    return b


# ----------------------------------------------------------------------------
# experiment driver
# ----------------------------------------------------------------------------

def setup(H, D, TP, ratio, layout, policy, topo, rng=None):
    lay = Layout(H, D, TP)
    is_p = roles(lay.n, ratio, layout, rng)
    wire = Wiring(POLICY_WIRING[policy], lay, topo)
    return lay, Pairs(lay, is_p, wire)


def write_csv(path, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"wrote {os.path.relpath(path, REPO)} ({len(rows)} rows)")


def read_csv(path):
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for k, v in r.items():
                try:
                    r[k] = int(v)
                except ValueError:
                    try:
                        r[k] = float(v)
                    except ValueError:
                        pass
            out.append(r)
    return out


def fmt_k(x):
    if not math.isfinite(x):
        return "inf"
    return f"{x:,.0f}"


def closed_form_tables(CL, traces, loadstats, outdir):
    rows, loadrows = [], []
    for cname, cl in CL.items():
        H = cl["H"]
        Ds = D_SWEEP if cname == "ft1152" else [8, 72]
        for D in Ds:
            for TP in (4, 8):
                for ratio in RATIOS:
                    for layout in ("mixed", "pooled"):
                        for pol in POLICIES:
                            lay, pr = setup(H, D, TP, ratio, layout, pol, cl["1:1"])
                            W = pr.weights(pol)
                            fr = pr.fractions(W)
                            row = {"cluster": cname, "H": H, "D": D,
                                   "domains": lay.describe(), "TP": TP,
                                   "ratio": rstr(ratio), "layout": layout,
                                   "policy": pol, "wiring": POLICY_WIRING[pol],
                                   "n_prefill": pr.P, "n_decode": pr.Dn,
                                   "idle_gpus": lay.idle,
                                   "frac_tx_offfabric": max(fr["nvlink"], 0.0),
                                   "frac_bytes_offfabric": fr["nvlink"],
                                   "frac_bytes_fabric": fr["fabric"],
                                   "frac_bytes_same_tor": fr["tor"],
                                   "frac_bytes_same_pod": fr["agg"],
                                   "frac_bytes_cross_pod": fr["core"]}
                            rows.append(row)
                            if layout != "mixed" or ratio not in MAIN_RATIOS or D not in (8, 72):
                                continue
                            for os_name in ("1:1", "4:1"):
                                topo = cl[os_name]
                                if os_name != "1:1":
                                    lay, pr = setup(H, D, TP, ratio, layout, pol, topo)
                                    W = pr.weights(pol)
                                lim = pr.capacity_limits(W)
                                for tn, ls in loadstats.items():
                                    G = ls["gbps"]
                                    loadrows.append({
                                        "cluster": cname, "topo": topo.name, "oversub": os_name,
                                        "D": D, "TP": TP, "ratio": rstr(ratio),
                                        "layout": layout, "policy": pol, "trace": tn,
                                        "native_req_s": ls["req_s"], "mean_GB": ls["mean_GB"],
                                        "kv_gbps": G, "nvlink_gbps": G * fr["nvlink"],
                                        "fabric_gbps": G * fr["fabric"],
                                        "same_tor_gbps": G * fr["tor"],
                                        "same_pod_gbps": G * fr["agg"],
                                        "cross_pod_gbps": G * fr["core"],
                                        "busiest_pod_up_gbps": G * lim["pod_up_share_max"],
                                        "p99_1s_kv_gbps": ls["p99_1s_gbps"],
                                        "B_core_gbps": lim["core"], "B_any_gbps": lim["any"],
                                        "binds": lim["binds"],
                                        "k_core": lim["core"] / G, "k_any": lim["any"] / G,
                                        "max_req_s_core": lim["core"] / G * ls["req_s"],
                                        "max_req_s_any": lim["any"] / G * ls["req_s"]})
    write_csv(os.path.join(outdir, "e7_closedform.csv"), rows)
    write_csv(os.path.join(outdir, "e7_load.csv"), loadrows)
    return rows, loadrows


def shuffled_layouts(CL, reps, outdir):
    """Random role layouts: closed form per layout, mean and 95% CI over layouts."""
    rows = []
    cl = CL["ft1152"]
    for D in (8, 72):
        for TP in (4, 8):
            for ratio in MAIN_RATIOS:
                for pol in POLICIES:
                    fab, core = [], []
                    for r in range(reps):
                        rng = np.random.default_rng(1000 + r)
                        _, pr = setup(cl["H"], D, TP, ratio, "shuffled", pol, cl["1:1"], rng)
                        fr = pr.fractions(pr.weights(pol))
                        fab.append(fr["fabric"])
                        core.append(fr["core"])
                    m, h = ci95(fab)
                    mc, hc = ci95(core)
                    rows.append({"cluster": "ft1152", "D": D, "TP": TP,
                                 "ratio": rstr(ratio), "layout": "shuffled",
                                 "policy": pol, "layouts": reps,
                                 "frac_bytes_fabric": m, "ci95": h,
                                 "frac_bytes_cross_pod": mc, "ci95_cross_pod": hc})
    write_csv(os.path.join(outdir, "e7_shuffled.csv"), rows)
    return rows


def mc_tables(CL, traces, reps, outdir, quick):
    cl = CL["ft1152"]
    H = cl["H"]
    rows = []

    def run(tag, D, TP, ratio, pol, c, rho, wl):
        lay, pr = setup(H, D, TP, ratio, "mixed", pol, cl["1:1"])
        t0 = time.time()
        res = mc_point(pr, pol, c, rho, wl, traces, reps, seed0=11)
        cf = pr.fractions(pr.weights(pol))
        row = {"set": tag, "cluster": "ft1152", "D": D, "TP": TP, "ratio": rstr(ratio),
               "layout": "mixed", "policy": pol, "c": c if c else "inf", "rho": rho,
               "trace": wl, "reps": reps,
               "closedform_bytes_fabric": cf["fabric"],
               "fluid_bytes_fabric": pr.fluid_fabric(rho) if pol in ("locality", "locrail")
               else cf["fabric"]}
        for k, (m, h) in res.items():
            row[k] = m
            row[k + "_ci95"] = h
        rows.append(row)
        return row, time.time() - t0

    # 1. figure: locality-aware with capacity, across the D sweep
    Dlist = [8, 72] if quick else D_SWEEP
    for TP in (4, 8):
        for ratio in MAIN_RATIOS:
            for D in Dlist:
                run("figure", D, TP, ratio, "locality", 16, 0.8, "conv")
    # 2. headline: every policy at c=16, rho=0.8, plus c=inf check vs closed form
    for D in (8, 72):
        for TP in (4, 8):
            for pol in POLICIES:
                run("headline", D, TP, (1, 2), pol, 16, 0.8, "conv")
                run("check_cinf", D, TP, (1, 2), pol, None, 0.8, "conv")
    # 3. capacity / utilization / trace sweep for the locality policies
    cs = [16] if quick else [4, 16, 64]
    rhos = [0.8] if quick else [0.5, 0.8, 0.95]
    for D in (8, 72):
        for TP in (4, 8):
            for pol in ("locality", "locrail"):
                for c in cs:
                    for rho in rhos:
                        for wl in ("conv", "code", "fixed4k"):
                            run("capacity", D, TP, (1, 2), pol, c, rho, wl)
    # 4. ratio sensitivity at the capacity default
    for D in (8, 72):
        for TP in (4, 8):
            for ratio in RATIOS:
                run("ratio", D, TP, ratio, "locality", 16, 0.8, "conv")
    write_csv(os.path.join(outdir, "e7_mc.csv"), rows)
    return rows


# ----------------------------------------------------------------------------
# printing
# ----------------------------------------------------------------------------

def pick(rows, **kw):
    return [r for r in rows if all(str(r[k]) == str(v) for k, v in kw.items())]


def print_headline(cf, mc, load):
    print("\n=== Headline: H = 1152 GPUs (ft1152 = fat_tree_1024 extended to 18 pods), "
          "prefill:decode 1:2, mixed layout, Llama-3-70B ===")
    print("fraction of KV bytes on the Ethernet fabric (cross-pod share in brackets)")
    print(f"{'D':>4} {'TP':>3} {'policy':<16} {'c=inf':>15} {'fluid':>6} "
          f"{'c=16, rho=0.8, conv (95% CI)':>40} {'k_core 1:1':>11} {'k_core 4:1':>11}")
    for TP in (8, 4):
        for D in (8, 72):
            for pol in POLICIES:
                a = pick(cf, cluster="ft1152", D=D, TP=TP, ratio="1:2", layout="mixed",
                         policy=pol)[0]
                m = pick(mc, set="headline", D=D, TP=TP, policy=pol)[0]
                k1 = pick(load, cluster="ft1152", D=D, TP=TP, ratio="1:2", policy=pol,
                          trace="conv", oversub="1:1")[0]["k_core"]
                k4 = pick(load, cluster="ft1152", D=D, TP=TP, ratio="1:2", policy=pol,
                          trace="conv", oversub="4:1")[0]["k_core"]
                print(f"{D:>4} {TP:>3} {POLICY_LABEL[pol]:<16} "
                      f"{a['frac_bytes_fabric']:6.3f} [{a['frac_bytes_cross_pod']:5.3f}] "
                      f"{m['fluid_bytes_fabric']:6.3f} "
                      f"{m['bytes_fabric']:8.3f} +/- {m['bytes_fabric_ci95']:.3f} "
                      f"[{m['core']:5.3f}] spill {m['spill']:5.3f} "
                      f"{fmt_k(k1):>9} {fmt_k(k4):>9}")
            print()


def print_clusters(cf):
    print("=== Closed form, c = inf, mixed layout: fraction of KV bytes on the fabric ===")
    hdr = f"{'cluster':<7} {'domains':<22} {'TP':>3} {'P:D':>4} " + \
        " ".join(f"{POLICY_LABEL[p]:>16}" for p in POLICIES) + "   cross-pod (rand / loc)"
    print(hdr)
    for cname in ("ft128", "ft1024", "ft1152"):
        for D in (8, 72):
            for TP in (8, 4):
                for ratio in RATIOS:
                    rs = {p: pick(cf, cluster=cname, D=D, TP=TP, ratio=rstr(ratio),
                                  layout="mixed", policy=p)[0] for p in POLICIES}
                    r0 = rs["random"]
                    print(f"{cname:<7} {r0['domains']:<22} {TP:>3} {rstr(ratio):>4} " +
                          " ".join(f"{rs[p]['frac_bytes_fabric']:>16.3f}" for p in POLICIES) +
                          f"   {rs['random']['frac_bytes_cross_pod']:.3f} / "
                          f"{rs['locality']['frac_bytes_cross_pod']:.3f}")
        print()


def print_layouts(cf, sh):
    print("=== Role layout sensitivity, ft1152, c = inf: bytes on fabric ===")
    print(f"{'D':>4} {'TP':>3} {'P:D':>4} {'policy':<16} {'mixed':>7} {'pooled':>7} "
          f"{'shuffled (95% CI)':>20}")
    for D in (8, 72):
        for TP in (8, 4):
            for ratio in MAIN_RATIOS:
                for pol in ("random", "locality"):
                    a = pick(cf, cluster="ft1152", D=D, TP=TP, ratio=rstr(ratio),
                             layout="mixed", policy=pol)[0]["frac_bytes_fabric"]
                    b = pick(cf, cluster="ft1152", D=D, TP=TP, ratio=rstr(ratio),
                             layout="pooled", policy=pol)[0]["frac_bytes_fabric"]
                    s = pick(sh, D=D, TP=TP, ratio=rstr(ratio), policy=pol)[0]
                    print(f"{D:>4} {TP:>3} {rstr(ratio):>4} {POLICY_LABEL[pol]:<16} "
                          f"{a:7.3f} {b:7.3f} {s['frac_bytes_fabric']:9.3f} +/- {s['ci95']:.3f}")
    print()


def print_load(load, loadstats):
    print("=== Native-speed offered load (trace KV rate before placement) ===")
    for tn, ls in loadstats.items():
        print(f"  {tn:<8} {ls['req_s']:5.2f} req/s, mean {ls['mean_GB']:.3f} GB/request, "
              f"{ls['gbps']:6.1f} Gbps mean, {ls['p99_1s_gbps']:6.1f} Gbps p99 1-s bin, "
              f"{ls['max_1s_gbps']:6.1f} Gbps max 1-s bin")
    print("\n=== Per-tier fabric load at native speed (conv trace) and rate headroom, "
          "TP=8, 1:2, mixed ===")
    print(f"{'cluster':<7} {'os':>4} {'D':>3} {'policy':<16} {'fabric':>7} {'ToR':>6} {'pod':>6} "
          f"{'core':>6} Gbps | {'k_core':>8} {'k_any':>8} binds  max req/s(any)")
    for cname in ("ft128", "ft1024", "ft1152"):
        for os_name in ("1:1", "4:1"):
            for D in (8, 72):
                for pol in POLICIES:
                    r = pick(load, cluster=cname, oversub=os_name, D=D, TP=8, ratio="1:2",
                             policy=pol, trace="conv")[0]
                    print(f"{cname:<7} {os_name:>4} {D:>3} {POLICY_LABEL[pol]:<16} "
                          f"{r['fabric_gbps']:7.2f} {r['same_tor_gbps']:6.2f} "
                          f"{r['same_pod_gbps']:6.2f} {r['cross_pod_gbps']:6.2f}      | "
                          f"{fmt_k(r['k_core']):>8} {fmt_k(r['k_any']):>8} {r['binds']:<5} "
                          f"{fmt_k(r['max_req_s_any'])}")
        print()


def print_capacity(mc):
    print("=== Monte Carlo, ft1152, 1:2, mixed: bytes on fabric under decode capacity c ===")
    print(f"{'D':>3} {'TP':>3} {'policy':<16} {'c':>3} {'rho':>5} {'fluid':>6}  " +
          "  ".join(f"{t:>21}" for t in ("conv", "code", "fixed4k")))
    for D in (8, 72):
        for TP in (8, 4):
            for pol in ("locality", "locrail"):
                cs = sorted({r["c"] for r in pick(mc, set="capacity", D=D, TP=TP, policy=pol)})
                for c in cs:
                    rhos = sorted({r["rho"] for r in pick(mc, set="capacity", D=D, TP=TP,
                                                          policy=pol, c=c)})
                    for rho in rhos:
                        cells = []
                        for wl in ("conv", "code", "fixed4k"):
                            r = pick(mc, set="capacity", D=D, TP=TP, policy=pol, c=c,
                                     rho=rho, trace=wl)[0]
                            cells.append(f"{r['bytes_fabric']:.3f}+/-{r['bytes_fabric_ci95']:.3f}"
                                         f" [{r['core']:.2f}]")
                        print(f"{D:>3} {TP:>3} {POLICY_LABEL[pol]:<16} {c:>3} {rho:>5} "
                              f"{r['fluid_bytes_fabric']:6.3f}  " +
                              "  ".join(f"{x:>21}" for x in cells))
    print("  cells: mean +/- 95% CI half-width [cross-pod fraction]\n")


def print_ratio(mc):
    print("=== Ratio sensitivity, locality-aware, c=16, rho=0.8, conv, ft1152 mixed ===")
    print(f"{'D':>3} {'TP':>3} {'P:D':>4} {'inst/domain':>11} {'c=inf':>6} {'fluid':>6} "
          f"{'MC bytes on fabric':>20} {'spill':>6}")
    for D in (8, 72):
        for TP in (8, 4):
            for ratio in RATIOS:
                r = pick(mc, set="ratio", D=D, TP=TP, ratio=rstr(ratio))[0]
                print(f"{D:>3} {TP:>3} {rstr(ratio):>4} {D // TP:>11} "
                      f"{r['closedform_bytes_fabric']:6.3f} {r['fluid_bytes_fabric']:6.3f} "
                      f"{r['bytes_fabric']:11.3f} +/- {r['bytes_fabric_ci95']:.3f} "
                      f"{r['spill']:6.3f}")
    print()


def print_checks(mc):
    print("=== Checks ===")
    worst = 0.0
    for r in pick(mc, set="check_cinf"):
        dev = abs(r["bytes_fabric"] - r["closedform_bytes_fabric"])
        worst = max(worst, dev - r["bytes_fabric_ci95"])
    print(f"  MC with c = inf vs closed form: worst |difference| beyond the 95% CI "
          f"= {max(worst, 0):.4f} (0 means every point agrees)")
    # Erlang-B reference: D=8, TP=4, 1:1 -> one decode instance per node
    r = pick(mc, set="ratio", D=8, TP=4, ratio="1:1")
    if r:
        r = r[0]
        print(f"  D=8 TP=4 1:1 (one local decode instance of c=16 slots per node), "
              f"rho=0.8: MC spill {r['spill']:.3f}; isolated-node Erlang-B "
              f"B(12.8, 16) = {erlang_b(12.8, 16):.3f} (a lower bound: spilled-in "
              f"requests from other nodes also occupy local slots)")
    print()


# ----------------------------------------------------------------------------
# figures
# ----------------------------------------------------------------------------

def setup_axes(ax):
    ax.grid(True, which="major", color="#000000", alpha=0.08, linewidth=0.8)
    ax.grid(True, which="minor", color="#000000", alpha=0.04, linewidth=0.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(direction="out", length=3)


def figures(cf, mc, figdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(figdir, exist_ok=True)

    def series(pol, metric, TP, ratio):
        xs, ys = [], []
        for D in D_SWEEP:
            r = pick(cf, cluster="ft1152", D=D, TP=TP, ratio=rstr(ratio), layout="mixed",
                     policy=pol)
            if r:
                xs.append(D)
                ys.append(r[0][metric])
        return xs, ys

    for name, metric, mc_metric, pols, ylabel in (
            ("e7_offfabric", "frac_bytes_fabric", "bytes_fabric",
             ["random", "rail", "locality"], "fraction of KV bytes\non the Ethernet fabric"),
            ("e7_crosspod", "frac_bytes_cross_pod", "core",
             ["random", "rail", "locality", "locrail"],
             "fraction of KV bytes\ncrossing pod uplinks")):
        fig, axes = plt.subplots(2, 3, figsize=(10.0, 5.6), sharex=True, sharey=True)
        for ri, TP in enumerate((4, 8)):
            for ci, ratio in enumerate(MAIN_RATIOS):
                ax = axes[ri][ci]
                setup_axes(ax)
                for pol in pols:
                    xs, ys = series(pol, metric, TP, ratio)
                    ax.plot(xs, ys, color=COLORS[pol], marker=MARKERS[pol],
                            markersize=5 if pol in ("random", "locality") else 3.5,
                            linewidth=1.5, linestyle=(0, (4, 2)) if pol in ("rail", "locrail")
                            else "-", label=f"{POLICY_LABEL[pol]}, c = inf",
                            zorder=3 if pol in ("rail", "locrail") else 2)
                rs = sorted(pick(mc, set="figure", TP=TP, ratio=rstr(ratio)),
                            key=lambda r: r["D"])
                if rs:
                    ax.errorbar([r["D"] for r in rs], [r[mc_metric] for r in rs],
                                yerr=[r[mc_metric + "_ci95"] for r in rs],
                                color=COLORS["locality_cap"], marker=MARKERS["locality_cap"],
                                markersize=4, linewidth=1.2, linestyle="--", capsize=2,
                                label="locality-aware, c = 16, rho = 0.8 (MC, 95% CI)",
                                zorder=4)
                ax.set_xscale("log", base=2)
                ax.set_xticks(D_SWEEP)
                ax.set_xticklabels([str(d) if d not in (36, 64) else "" for d in D_SWEEP],
                                   fontsize=7)
                ax.minorticks_off()
                ax.set_ylim(-0.03, 1.03)
                for xv, lab in ((8, "HGX"), (72, "NVL72")):
                    ax.axvline(xv, color="#444444", linestyle=":", linewidth=0.8, zorder=1)
                    if ri == 0:
                        ax.text(xv, 1.03, lab, fontsize=7, color="#444444", ha="center",
                                va="bottom")
                ax.set_title(f"TP = {TP}, prefill:decode = {rstr(ratio)}", fontsize=9,
                             pad=12 if ri == 0 else 4)
                if ci == 0:
                    ax.set_ylabel(ylabel, fontsize=8)
                if ri == 1:
                    ax.set_xlabel("scale-up domain size D (GPUs)", fontsize=8)
        h, l = axes[0][0].get_legend_handles_labels()
        fig.legend(h, l, frameon=False, fontsize=8, loc="lower center", ncol=len(h),
                   bbox_to_anchor=(0.5, -0.03))
        fig.suptitle("Llama-3-70B KV transfers, 1152-GPU cluster, mixed role layout",
                     fontsize=10, y=1.0)
        fig.tight_layout(rect=(0, 0.04, 1, 0.98))
        for ext in ("png", "pdf"):
            p = os.path.join(figdir, f"{name}.{ext}")
            fig.savefig(p, dpi=300, bbox_inches="tight")
            print(f"wrote {os.path.relpath(p, REPO)}")
        plt.close(fig)


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=10, help="MC seeds per point (default 10)")
    ap.add_argument("--layouts", type=int, default=20,
                    help="random role layouts for the shuffled table (default 20)")
    ap.add_argument("--quick", action="store_true", help="smaller MC sweep")
    ap.add_argument("--outdir", default=os.path.join(REPO, "results", "e7"))
    ap.add_argument("--figdir", default=os.path.join(REPO, "results", "figs"))
    ap.add_argument("--no-mc", action="store_true", help="closed form only (no figure)")
    ap.add_argument("--fig-only", action="store_true",
                    help="redraw the figures from existing CSVs in --outdir")
    args = ap.parse_args()
    if args.fig_only:
        figures(read_csv(os.path.join(args.outdir, "e7_closedform.csv")),
                read_csv(os.path.join(args.outdir, "e7_mc.csv")), args.figdir)
        return

    t0 = time.time()
    CL = clusters()
    for cname, cl in CL.items():
        for os_name in ("1:1", "4:1"):
            print(f"{cname} {os_name}: {cl[os_name].summary()}")
    traces = {k: load_trace(k) for k in ("conv", "code")}
    loadstats = {"conv": trace_load_stats(traces["conv"]),
                 "code": trace_load_stats(traces["code"]),
                 "fixed4k": trace_load_stats(traces["conv"], prompt_override=4096)}
    cf, load = closed_form_tables(CL, traces, loadstats, args.outdir)
    sh = shuffled_layouts(CL, args.layouts, args.outdir)
    print_clusters(cf)
    print_layouts(cf, sh)
    print_load(load, loadstats)
    if args.no_mc:
        return
    mc = mc_tables(CL, traces, args.reps, args.outdir, args.quick)
    print_headline(cf, mc, load)
    print_capacity(mc)
    print_ratio(mc)
    print_checks(mc)
    figures(cf, mc, args.figdir)
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
