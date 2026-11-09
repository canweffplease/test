#!/usr/bin/env python3
"""Generate HTSIM connection matrices for disaggregated-inference KV cache transfer.

Each request is one prefill -> decode KV cache handoff. A request of P prompt
tokens on model M at tensor-parallel degree T becomes T concurrent flows of
ceil(P * bytes_per_token / T) bytes, prefill GPU i -> decode GPU i. One HTSIM
node is one GPU/NIC.

Writes <out>.cm (HTSIM format) and <out>.json (per-flow sidecar metadata with
request id, shard, tier crossed, floor and deadline). Stdlib only.

Quick examples (see workloads/README.md for the checked-in matrices):

  # E3: 70% of the cross-pod bottleneck on the 128-node 1:1 fat tree
  scripts/gen_workload.py --topo ft128_1os --model llama3-70b --prompt fixed:4096 \\
      --tp 8 --pattern tp --arrival poisson --load 0.7 --duration-ms 50 \\
      --fix-count --seed 1 --out workloads/e3_load70_ft128.cm

  # 8 single-rail prefill senders incasting to one decode GPU, 16-node default
  scripts/gen_workload.py --topo default --pattern incast --incast 8 --tp 8 \\
      --shards 1 --arrival fixed --requests 8 --out workloads/e6_incast8_ft16.cm

Offered load (--load L): the generator computes the bottleneck capacity B of
the chosen placement on the chosen topology, then sets the arrival rate so
that mean offered bits per second over the window equal L * B. B is the
largest aggregate throughput the placement can carry before the first
resource saturates, assuming perfect load balancing inside each link group:

    B = min over resources r of  capacity(r) / share(r)

where share(r) is the fraction of all simulated bytes that must cross r, and
the resources are every host NIC (up and down), every ToR's aggregate uplink
group (up and down), and every pod's aggregate agg->core uplink group (up and
down). For cross-pod pool-to-pool traffic this reduces to the uplink capacity
out of the sending pods (or the sending NICs, whichever is smaller); for
same-ToR traffic to the host link; for incast to the destination downlink.
The binding resource is printed. --bottleneck-gbps overrides B.
"""
import argparse
import csv
import datetime as _dt
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict

INT32_MAX = 2**31 - 1
PS_PER_S = 10**12

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOPODIR = os.path.join(REPO, "HTSIM", "htsim", "sim", "datacenter", "topologies")

# layers, kv_heads, head_dim
MODELS = {
    "llama3-8b": (32, 8, 128),
    "llama3-70b": (80, 8, 128),
    "llama2-13b": (40, 40, 128),
}

TIER_NAMES = {"tor": "same_tor", "agg": "same_pod", "core": "cross_pod"}


def die(msg):
    print(f"gen_workload: error: {msg}", file=sys.stderr)
    sys.exit(2)


def gb(nbytes):
    return nbytes / 1e9


# ----------------------------------------------------------------------------
# topology model
# ----------------------------------------------------------------------------

class Topo:
    """Host -> ToR -> pod mapping and aggregate link-group capacities.

    Mirrors FatTreeTopologyCfg in HTSIM/htsim/sim/datacenter/fat_tree_topology.cpp:
    host h sits on ToR h // hosts_per_tor and in pod h // hosts_per_pod.
    """

    def __init__(self, name, nodes, tiers, hosts_per_tor, hosts_per_pod,
                 host_gbps, tor_link_gbps, core_link_gbps,
                 tor_up_gbps, pod_up_gbps, desc):
        self.name = name
        self.nodes = nodes
        self.tiers = tiers
        self.hosts_per_tor = hosts_per_tor
        self.hosts_per_pod = hosts_per_pod
        self.host_gbps = host_gbps          # host <-> ToR link
        self.tor_link_gbps = tor_link_gbps  # one ToR <-> agg link
        self.core_link_gbps = core_link_gbps  # one agg <-> core link
        self.tor_up_gbps = tor_up_gbps      # all uplinks of one ToR
        self.pod_up_gbps = pod_up_gbps      # all agg->core uplinks of one pod (None for 2 tiers)
        self.desc = desc

    def tor(self, h):
        return h // self.hosts_per_tor

    def pod(self, h):
        return h // self.hosts_per_pod

    def tier(self, s, d):
        if s == d:
            return "host"
        if self.tor(s) == self.tor(d):
            return "tor"
        if self.tiers == 2 or self.pod(s) == self.pod(d):
            return "agg"
        return "core"

    def path_min_gbps(self, s, d):
        t = self.tier(s, d)
        g = self.host_gbps
        if t in ("agg", "core"):
            g = min(g, self.tor_link_gbps)
        if t == "core":
            g = min(g, self.core_link_gbps)
        return g

    def summary(self):
        pods = self.nodes // self.hosts_per_pod
        tors = self.nodes // self.hosts_per_tor
        s = (f"{self.name}: {self.nodes} hosts, {self.tiers} tiers, "
             f"{self.hosts_per_tor} hosts/ToR, {self.hosts_per_pod} hosts/pod "
             f"({tors} ToRs, {pods} pods), host link {self.host_gbps:g} Gbps, "
             f"ToR uplinks {self.tor_up_gbps:g} Gbps/ToR")
        if self.pod_up_gbps is not None:
            s += f", pod->core {self.pod_up_gbps:g} Gbps/pod"
        return s


def kary_fattree(nodes, gbps, tiers=3):
    """The auto-generated topology HTSIM builds when no -topo is given."""
    if tiers == 3:
        K = 0
        while K * K * K // 4 < nodes:
            K += 1
        if K * K * K // 4 != nodes:
            die(f"HTSIM cannot build a 3-tier K-ary fat tree with {nodes} nodes "
                f"(needs K^3/4)")
        hpt, hpp = K // 2, K * K // 4
        return Topo(f"default(K={K})", nodes, 3, hpt, hpp, gbps, gbps, gbps,
                    (K // 2) * gbps, (K // 2) * (K // 2) * gbps,
                    f"auto-generated 3-tier K={K} fat tree, all links {gbps:g} Gbps")
    K = 0
    while K * K // 2 < nodes:
        K += 1
    if K * K // 2 != nodes:
        die(f"HTSIM cannot build a 2-tier fat tree with {nodes} nodes")
    return Topo(f"default2(K={K})", nodes, 2, K // 2, nodes, gbps, gbps, gbps,
                (K // 2) * gbps, None, f"auto-generated leaf-spine K={K}")


def load_topo_file(path):
    """Parse an HTSIM .topo file (same keys as FatTreeTopologyCfg::read_cfg)."""
    nodes = tiers = podsize = None
    cur = None
    speed, rdown, rup, osub = {}, {}, {}, {}
    with open(path) as f:
        for line in f:
            tok = line.split()
            if not tok or tok[0].startswith("#"):
                continue
            k = tok[0].lower()
            if k == "nodes":
                nodes = int(tok[1])
            elif k == "tiers":
                tiers = int(tok[1])
            elif k == "podsize":
                podsize = int(tok[1])
            elif k == "tier":
                cur = int(tok[1])
            elif k == "downlink_speed_gbps":
                speed[cur] = float(tok[1])
            elif k == "radix_down":
                rdown[cur] = int(tok[1])
            elif k == "radix_up":
                rup[cur] = int(tok[1])
            elif k == "oversubscribed":
                osub[cur] = int(tok[1])
    if not nodes or not tiers or 0 not in rdown or 0 not in speed:
        die(f"could not parse topology {path}")
    name = os.path.splitext(os.path.basename(path))[0]
    s0 = speed[0]
    s1 = speed.get(1, s0)
    hpt = rdown[0]
    ntor = nodes // hpt
    tor_uplinks_total = nodes * s0 / (s1 * osub.get(0, 1))
    tor_up = tor_uplinks_total / ntor * s1
    if tiers == 2:
        return Topo(name, nodes, 2, hpt, nodes, s0, s1, s1, tor_up, None, path)
    if not podsize:
        die(f"{path}: 3-tier topology without Podsize")
    s2 = speed.get(2, s1)
    npods = nodes // podsize
    agg_uplinks_total = tor_uplinks_total * s1 / (s2 * osub.get(1, 1))
    pod_up = agg_uplinks_total / npods * s2
    return Topo(name, nodes, 3, hpt, podsize, s0, s1, s2, tor_up, pod_up, path)


def get_topo(spec, nodes, link_gbps):
    if spec in ("default", "ft16"):
        return kary_fattree(nodes or 16, link_gbps)
    if spec == "default2":
        return kary_fattree(nodes or 16, link_gbps, tiers=2)
    if os.path.isfile(spec):
        return load_topo_file(spec)
    alias = {"ft128_1os": "fat_tree_128_1os", "ft128_4os": "fat_tree_128_4os",
             "ft128_8os": "fat_tree_128_8os"}
    f = os.path.join(TOPODIR, alias.get(spec, spec) + ".topo")
    if os.path.isfile(f):
        t = load_topo_file(f)
        t.name = spec
        return t
    die(f"unknown topology {spec!r} (default | ft16 | ft128_1os | ft128_4os | "
        f"ft128_8os | <name in {TOPODIR}> | path/to/file.topo)")


# ----------------------------------------------------------------------------
# placement
# ----------------------------------------------------------------------------

def parse_ranges(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def chunk(hosts, w, what):
    if len(hosts) % w:
        die(f"{what}: {len(hosts)} hosts is not a multiple of {w} GPUs per instance")
    return [hosts[i:i + w] for i in range(0, len(hosts), w)]


def build_slots(topo, w, placement, rail_domain):
    """Candidate instance slots: each slot is a list of w hosts, GPU i first."""
    N = topo.nodes
    if placement == "rail":
        S = {"stripe": N // w, "tor": topo.hosts_per_tor,
             "pod": topo.hosts_per_pod}[rail_domain]
        if S <= 0 or (N // S) < w:
            die(f"rail placement: need {w} rail domains of {S} hosts, "
                f"topology has {N // max(S, 1)}")
        # instance j has GPU i at host (domain i start) + j
        return [[i * S + j for i in range(w)] for j in range(S)]
    return [list(range(k * w, (k + 1) * w)) for k in range(N // w)]


def assign_roles(args, topo, w):
    """Return (prefill_slots, decode_slots, eligible[p] -> list of decode idx)."""
    pat = args.pattern
    placement = args.placement
    if placement is None:
        placement = {"same_pod": "interleaved", "same_tor": "interleaved"}.get(pat, "split")

    if args.prefill_nodes or args.decode_nodes:
        if not (args.prefill_nodes and args.decode_nodes):
            die("--prefill-nodes and --decode-nodes must be given together")
        pre = chunk(parse_ranges(args.prefill_nodes), w, "--prefill-nodes")
        dec = chunk(parse_ranges(args.decode_nodes), w, "--decode-nodes")
        placement = "explicit"
    else:
        slots = build_slots(topo, w, placement, args.rail_domain)
        if pat == "permutation":
            pre, dec = slots, slots
        elif pat == "incast":
            k = args.incast
            if k is None:
                die("--pattern incast needs --incast K")
            pre = slots[:k]
            mid = topo.nodes // 2
            cand = [s for s in slots if mid in s and s not in pre] or \
                   [s for s in slots[::-1] if s not in pre]
            if len(pre) < k or not cand:
                die(f"incast: {k} senders + 1 receiver of {w} GPUs do not fit in "
                    f"{topo.nodes} nodes; try --shards 1 or a smaller --tp")
            dec = [cand[0]]
        elif placement == "interleaved":
            pre, dec = slots[0::2], slots[1::2]
        else:  # split / rail: first half prefill, second half decode
            h = len(slots) // 2
            pre, dec = slots[:h], slots[h:2 * h]
    if args.prefill_instances:
        pre = pre[:args.prefill_instances]
    if args.decode_instances and pat != "permutation":
        dec = dec[:args.decode_instances]
    if not pre or not dec:
        die("placement produced no prefill or no decode instances")

    for s in pre + dec:
        for h in s:
            if not 0 <= h < topo.nodes:
                die(f"host {h} out of range for {topo.nodes} nodes")
    if pat != "permutation":
        ph = {h for s in pre for h in s}
        dh = {h for s in dec for h in s}
        if ph & dh:
            die(f"prefill and decode pools overlap on hosts {sorted(ph & dh)[:8]}")

    want = {"cross_pod": "core", "same_pod": "agg", "same_tor": "tor"}.get(pat)
    rng = random.Random(args.seed + 7)
    if pat == "permutation":
        n = len(pre)
        if n < 2:
            die("permutation needs at least 2 instances")
        perm = list(range(n))
        while any(perm[i] == i for i in range(n)):
            rng.shuffle(perm)
        elig = [[perm[i]] for i in range(n)]
    else:
        elig = []
        for p in pre:
            e = []
            for di, d in enumerate(dec):
                tiers = {topo.tier(a, b) for a, b in zip(p, d)}
                if "host" in tiers:
                    continue
                if want and tiers != {want}:
                    continue
                e.append(di)
            if not e:
                die(f"pattern {pat}: prefill instance {p[0]}..{p[-1]} has no decode "
                    f"instance whose every shard crosses tier {want}. With {w} GPUs "
                    f"per instance and {topo.hosts_per_tor} hosts/ToR, "
                    f"{topo.hosts_per_pod} hosts/pod this placement cannot work; "
                    f"try --placement rail, a smaller --tp, or --shards")
            elig.append(e)
    return pre, dec, elig, placement


# ----------------------------------------------------------------------------
# bottleneck
# ----------------------------------------------------------------------------

def resource_shares(topo, pre, dec, elig):
    """Fraction of total bytes crossing each resource, under uniform prefill
    choice and uniform choice among each prefill's eligible decode instances."""
    share = defaultdict(float)
    P = len(pre)
    for pi, p in enumerate(pre):
        for di in elig[pi]:
            wgt = 1.0 / P / len(elig[pi]) / len(p)
            d = dec[di]
            for s, t in zip(p, d):
                share[("host_up", s)] += wgt
                share[("host_down", t)] += wgt
                tier = topo.tier(s, t)
                if tier in ("agg", "core"):
                    share[("tor_up", topo.tor(s))] += wgt
                    share[("tor_down", topo.tor(t))] += wgt
                if tier == "core":
                    share[("pod_up", topo.pod(s))] += wgt
                    share[("pod_down", topo.pod(t))] += wgt
    return share


def capacity(topo, kind):
    return {"host_up": topo.host_gbps, "host_down": topo.host_gbps,
            "tor_up": topo.tor_up_gbps, "tor_down": topo.tor_up_gbps,
            "pod_up": topo.pod_up_gbps, "pod_down": topo.pod_up_gbps}[kind]


def bottleneck(topo, pre, dec, elig):
    share = resource_shares(topo, pre, dec, elig)
    by_kind = {}
    for (kind, idx), sh in share.items():
        b = capacity(topo, kind) / sh
        if kind not in by_kind or b < by_kind[kind][0] - 1e-9:
            by_kind[kind] = (b, [idx], sh)
        elif abs(b - by_kind[kind][0]) <= 1e-9 * b:
            by_kind[kind][1].append(idx)
    B = min(v[0] for v in by_kind.values())
    binding = [k for k, v in by_kind.items() if abs(v[0] - B) <= 1e-6 * B]
    return B, binding, by_kind


def describe_bottleneck(topo, by_kind, binding):
    label = {"host_up": "sender NIC", "host_down": "receiver NIC",
             "tor_up": "ToR uplink group (out of sending ToR)",
             "tor_down": "ToR downlink group (into receiving ToR)",
             "pod_up": "pod agg->core uplinks (out of sending pod)",
             "pod_down": "pod core->agg downlinks (into receiving pod)"}
    unit = {"host_up": "host", "host_down": "host", "tor_up": "ToR",
            "tor_down": "ToR", "pod_up": "pod", "pod_down": "pod"}
    lines = []
    for kind in ("host_up", "host_down", "tor_up", "tor_down", "pod_up", "pod_down"):
        if kind not in by_kind:
            continue
        b, idx, sh = by_kind[kind]
        mark = "  <-- binds" if kind in binding else ""
        lines.append(f"    {label[kind]:<46} {capacity(topo, kind):>7g} Gbps each, "
                     f"busiest {unit[kind]} carries {sh * 100:5.2f}% of bytes "
                     f"-> {b:>8.0f} Gbps aggregate{mark}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# prompts and arrivals
# ----------------------------------------------------------------------------

def parse_ts(s):
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    s = s.replace("T", " ").rstrip("Z")
    frac = 0.0
    if "." in s:
        s, f = s.split(".", 1)
        frac = float("0." + f)
    t = _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return t.replace(tzinfo=_dt.timezone.utc).timestamp() + frac


def read_trace(path):
    """Return sorted list of (t_seconds, context_tokens). Data only, never executed."""
    rows = []
    with open(path, newline="") as f:
        rd = csv.DictReader(f)
        cols = {c.strip().lower(): c for c in (rd.fieldnames or [])}
        tcol, ccol = cols.get("timestamp"), cols.get("contexttokens")
        if not tcol or not ccol:
            die(f"{path}: need TIMESTAMP and ContextTokens columns, got {rd.fieldnames}")
        for r in rd:
            try:
                rows.append((parse_ts(r[tcol]), int(float(r[ccol]))))
            except (ValueError, TypeError):
                continue
    if not rows:
        die(f"{path}: no usable rows")
    rows.sort()
    t0 = rows[0][0]
    return [(t - t0, c) for t, c in rows]


def make_prompt_sampler(spec, trace_rows, maxp, rng):
    def clamp(p):
        p = max(1, int(round(p)))
        return min(p, maxp) if maxp else p
    if spec.startswith("fixed:"):
        v = clamp(int(spec.split(":", 1)[1]))
        return lambda: v, "fixed"
    if spec.startswith("lognormal:"):
        mu, sigma = (float(x) for x in spec.split(":", 1)[1].split(","))
        return (lambda: clamp(rng.lognormvariate(mu, sigma))), "lognormal"
    if spec == "trace":
        if not trace_rows:
            die("--prompt trace needs --trace <csv>")
        vals = [clamp(c) for _, c in trace_rows]
        return (lambda: rng.choice(vals)), "trace-empirical"
    die(f"bad --prompt {spec!r} (fixed:N | lognormal:mu,sigma | trace)")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Prints the bottleneck, rate, mean request size, request count "
               "and window. See workloads/README.md.")
    g = ap.add_argument_group("topology")
    g.add_argument("--topo", default="default",
                   help="default|ft16 (auto K-ary fat tree), ft128_1os|ft128_4os|"
                        "ft128_8os, a name in the topologies dir, or a .topo path")
    g.add_argument("--nodes", type=int, help="must match the topology (checked)")
    g.add_argument("--link-gbps", type=float, default=400,
                   help="link speed for the auto-generated default topology (400)")
    g.add_argument("--also-topo", default="",
                   help="comma list of other topologies to report this matrix's "
                        "offered load against (same node count)")

    g = ap.add_argument_group("model and sharding")
    g.add_argument("--model", default="llama3-70b", choices=sorted(MODELS))
    g.add_argument("--dtype-bytes", type=int, default=2)
    g.add_argument("--bytes-per-token", type=int, help="override the model table")
    g.add_argument("--tp", type=int, default=8, help="tensor-parallel degree T")
    g.add_argument("--shards", type=int,
                   help="simulate only GPU indices 0..N-1 of each TP group (a rail "
                        "slice); flow size is still ceil(P*bpt/T). Default T")
    g.add_argument("--split-oversize", action="store_true",
                   help="split shards over 2^31-1 bytes into concurrent sub-flows "
                        "instead of erroring")

    g = ap.add_argument_group("placement")
    g.add_argument("--pattern", default="tp",
                   choices=["tp", "incast", "permutation", "same_tor", "same_pod",
                            "cross_pod"])
    g.add_argument("--placement", choices=["split", "interleaved", "rail"],
                   help="split: first half of instance slots prefill, second half "
                        "decode (cross-pod on fat trees). interleaved: alternate "
                        "slots. rail: GPU i of every instance in rail domain i. "
                        "Default split (interleaved for same_tor/same_pod)")
    g.add_argument("--rail-domain", default="stripe", choices=["stripe", "tor", "pod"],
                   help="rail domain for --placement rail: stripe = nodes/T "
                        "consecutive hosts (= pod on ft128, = ToR on ft16), "
                        "or literally a ToR / a pod")
    g.add_argument("--incast", type=int, help="number of prefill instances for incast")
    g.add_argument("--prefill-nodes", help="explicit hosts, e.g. 0-63 (chunked into instances)")
    g.add_argument("--decode-nodes", help="explicit hosts, e.g. 64-127")
    g.add_argument("--prefill-instances", type=int, help="use only the first N prefill instances")
    g.add_argument("--decode-instances", type=int, help="use only the first N decode instances")
    g.add_argument("--assign", default="rr", choices=["rr", "random"],
                   help="request -> instance assignment. rr rotates decode partners "
                        "so (src,dst) pairs stay unique for the first P*D requests")

    g = ap.add_argument_group("arrivals and prompts")
    g.add_argument("--arrival", default="poisson", choices=["poisson", "fixed", "trace"])
    g.add_argument("--rate", type=float, help="requests per second")
    g.add_argument("--load", type=float, help="offered load as a fraction of the bottleneck")
    g.add_argument("--bottleneck-gbps", type=float, help="override the computed bottleneck")
    g.add_argument("--duration-ms", type=float, default=50.0, help="arrival window")
    g.add_argument("--requests", type=int,
                   help="fixed: number of requests (with --interval-us). Others: cap")
    g.add_argument("--interval-us", type=float, default=None,
                   help="fixed arrivals: spacing; 0 = all at t=0 (burst)")
    g.add_argument("--fix-count", action="store_true",
                   help="poisson: draw exactly round(rate*window) arrivals, i.i.d. "
                        "uniform in the window (a Poisson process conditioned on its "
                        "count) so realized load equals the target")
    g.add_argument("--start-offset-us", type=float, default=0.0)
    g.add_argument("--prompt", help="fixed:N | lognormal:mu,sigma | trace "
                                    "(default fixed:4096, or trace with --arrival trace)")
    g.add_argument("--max-prompt", type=int, help="clamp prompt length")
    g.add_argument("--trace", help="CSV with TIMESTAMP, ContextTokens[, GeneratedTokens]")
    g.add_argument("--trace-skip-s", type=float, default=0.0,
                   help="start replay this many trace seconds in")
    g.add_argument("--time-scale", type=float,
                   help="sim seconds per trace second (default 1, or derived from --load)")
    g.add_argument("--seed", type=int, default=1)

    g = ap.add_argument_group("deadlines and priority")
    g.add_argument("--deadline-slack", type=float, default=2.0,
                   help="deadline = start + slack * (bytes / path line rate)")
    g.add_argument("--prio", default="none", choices=["none", "srpt", "edf"],
                   help="srpt: prio = flow bytes; edf: prio = deadline in us")

    ap.add_argument("--out", required=True, help="output .cm path; sidecar .json alongside")
    args = ap.parse_args()

    # ---- topology / model ---------------------------------------------------
    topo = get_topo(args.topo, args.nodes, args.link_gbps)
    if args.nodes and args.nodes != topo.nodes:
        die(f"--nodes {args.nodes} does not match topology {topo.name} ({topo.nodes})")
    if topo.nodes % topo.hosts_per_pod or topo.nodes % topo.hosts_per_tor:
        die("topology host counts are not consistent")

    if args.bytes_per_token:
        bpt = args.bytes_per_token
    else:
        L, H, D = MODELS[args.model]
        bpt = 2 * L * H * D * args.dtype_bytes
    T = args.tp
    W_sh = args.shards or T
    if not 1 <= W_sh <= T:
        die("--shards must be in 1..tp")

    rng = random.Random(args.seed)
    trace_rows = read_trace(args.trace) if args.trace else None
    if args.arrival == "trace" and not trace_rows:
        die("--arrival trace needs --trace <csv>")
    prompt_spec = args.prompt or ("trace" if args.arrival == "trace" else "fixed:4096")

    pre, dec, elig, placement = assign_roles(args, topo, W_sh)
    B_calc, binding, by_kind = bottleneck(topo, pre, dec, elig)
    B = args.bottleneck_gbps or B_calc

    def shard_bytes(p):
        return math.ceil(p * bpt / T)

    def req_bytes(p):
        return shard_bytes(p) * W_sh  # simulated bytes per request

    window_s = args.duration_ms / 1e3
    ps_off = int(round(args.start_offset_us * 1e6))

    # ---- arrivals -------------------------------------------------------------
    times, prompts = [], []
    rate = args.rate
    mean_req = None
    note = ""
    if args.arrival in ("poisson", "fixed"):
        sampler, _ = make_prompt_sampler(prompt_spec, trace_rows, args.max_prompt, rng)
        if prompt_spec.startswith("fixed:"):
            mean_req = req_bytes(sampler())
        else:
            mc = random.Random(args.seed + 1)
            msampler, _ = make_prompt_sampler(prompt_spec, trace_rows, args.max_prompt, mc)
            mean_req = sum(req_bytes(msampler()) for _ in range(200000)) / 200000
        if args.arrival == "fixed" and args.requests is not None and args.rate is None \
                and args.load is None:
            iv = (args.interval_us or 0.0) / 1e6
            times = [k * iv for k in range(args.requests)]
            rate = (1 / iv) if iv > 0 else None
        else:
            if args.load is not None:
                if rate is not None:
                    die("give --rate or --load, not both")
                rate = args.load * B * 1e9 / (mean_req * 8)
            if rate is None:
                die("need --rate or --load (or --arrival fixed --requests N)")
            if args.arrival == "fixed":
                n = int(math.floor(rate * window_s + 1e-9))
                if args.requests is not None:
                    n = min(n, args.requests)
                times = [k / rate for k in range(n)]
            elif args.fix_count:
                n = int(round(rate * window_s))
                times = sorted(rng.uniform(0, window_s) for _ in range(n))
            else:
                t = rng.expovariate(rate)
                while t < window_s:
                    times.append(t)
                    t += rng.expovariate(rate)
            if args.requests is not None:
                times = times[:args.requests]
        prompts = [sampler() for _ in times]
    else:  # trace replay
        rows = [(t - args.trace_skip_s, c) for t, c in trace_rows if t >= args.trace_skip_s]
        if not rows:
            die("--trace-skip-s is past the end of the trace")
        t0 = rows[0][0]
        rows = [(t - t0, c) for t, c in rows]
        if prompt_spec == "trace":
            plist = [min(max(1, c), args.max_prompt) if args.max_prompt else max(1, c)
                     for _, c in rows]
        else:
            sampler, _ = make_prompt_sampler(prompt_spec, trace_rows, args.max_prompt, rng)
            plist = [sampler() for _ in rows]
        if args.load is not None:
            target = args.load * B * 1e9 * window_s / 8
            cum, k = 0, 0
            while k < len(rows) and cum + req_bytes(plist[k]) <= target:
                cum += req_bytes(plist[k])
                k += 1
            if k < len(rows) and (target - cum) > (cum + req_bytes(plist[k]) - target):
                cum += req_bytes(plist[k])
                k += 1
            if k >= len(rows):
                die(f"trace too short: all {len(rows)} requests give only "
                    f"{gb(cum):.2f} GB, need {gb(target):.2f} GB")
            if k < 2:
                die("load too low for this window: fewer than 2 trace requests")
            # window ends midway between the last kept and first dropped arrival
            span = (rows[k - 1][0] + rows[k][0]) / 2
            scale = window_s / span if span > 0 else 1.0
            note = (f"trace time scaled by {scale:.3g} (1 trace s = {scale * 1e3:.3g} sim ms; "
                    f"{span:.1f} trace s replayed)")
        else:
            scale = args.time_scale or 1.0
            note = f"trace time scaled by {scale:g}"
        for (t, _), p in zip(rows, plist):
            st = t * scale
            if st >= window_s:
                break
            times.append(st)
            prompts.append(p)
        if args.requests is not None:
            times, prompts = times[:args.requests], prompts[:args.requests]
        mean_req = sum(req_bytes(p) for p in prompts) / max(1, len(prompts))
        rate = len(times) / window_s

    if not times:
        die("no requests generated (rate too low or window too short?)")

    # ---- flows -----------------------------------------------------------------
    flows, requests = [], []
    P, Dn = len(pre), len(dec)
    cnt = [0] * P
    for r, (t, p) in enumerate(zip(times, prompts)):
        if args.assign == "rr":
            pi = r % P
            e = elig[pi]
            di = e[(cnt[pi] + pi) % len(e)]
            cnt[pi] += 1
        else:
            pi = rng.randrange(P)
            di = rng.choice(elig[pi])
        start = ps_off + int(round(t * 1e9)) * 1000  # ns resolution, in ps
        sb = shard_bytes(p)
        parts = 1
        if sb > INT32_MAX:
            if not args.split_oversize:
                die(f"request {r}: shard of {sb:,} B ({p} tokens / TP {T}) exceeds "
                    f"2^31-1; use --max-prompt, larger --tp, or --split-oversize")
            parts = math.ceil(sb / INT32_MAX)
        rq_flows = []
        for i, (s, d) in enumerate(zip(pre[pi], dec[di])):
            tier = topo.tier(s, d)
            gbps = topo.path_min_gbps(s, d)
            floor_ps = math.ceil(sb * 8 * 1000 / gbps)  # bytes*8 / (gbps*1e9) * 1e12
            deadline = start + int(math.ceil(args.deadline_slack * floor_ps))
            remaining = sb
            for part in range(parts):
                b = min(remaining, math.ceil(sb / parts))
                remaining -= b
                fl = {"request_id": r, "shard": i, "part": part, "src": s, "dst": d,
                      "bytes": b, "start_ps": start, "prompt_tokens": p,
                      "tier_crossed": TIER_NAMES[tier], "floor_ps": floor_ps,
                      "deadline_ps": deadline}
                if args.prio == "srpt":
                    fl["prio"] = b
                elif args.prio == "edf":
                    fl["prio"] = deadline // 10**6
                rq_flows.append(fl)
        flows.extend(rq_flows)
        requests.append({"request_id": r, "start_ps": start, "prompt_tokens": p,
                         "prefill_instance": pi, "decode_instance": di,
                         "prefill_hosts": pre[pi], "decode_hosts": dec[di],
                         "bytes": sum(f["bytes"] for f in rq_flows),
                         "deadline_ps": max(f["deadline_ps"] for f in rq_flows)})

    flows.sort(key=lambda f: (f["start_ps"], f["request_id"], f["shard"], f["part"]))
    seen = Counter()
    for k, f in enumerate(flows, 1):
        f["flow_id"] = k
        key = (f["src"], f["dst"])
        f["pair_seq"] = seen[key]  # nth flow on this src->dst pair, in start order
        seen[key] += 1
    if not args.split_oversize:
        for f in flows:
            del f["part"]

    # ---- validate --------------------------------------------------------------
    for f in flows:
        if not (0 <= f["src"] < topo.nodes and 0 <= f["dst"] < topo.nodes):
            die(f"flow {f['flow_id']} node out of range")
        if f["src"] == f["dst"]:
            die(f"flow {f['flow_id']} has src == dst")
        if not 0 < f["bytes"] <= INT32_MAX:
            die(f"flow {f['flow_id']} size {f['bytes']} out of int32 range")
        if "prio" in f and not 0 <= f["prio"] <= INT32_MAX:
            die(f"flow {f['flow_id']} prio {f['prio']} out of int32 range")

    # ---- write -----------------------------------------------------------------
    out = args.out
    base = out[:-3] if out.endswith(".cm") else out
    cm_path, js_path = base + ".cm", base + ".json"
    os.makedirs(os.path.dirname(os.path.abspath(cm_path)), exist_ok=True)
    with open(cm_path, "w") as fh:
        fh.write(f"Nodes {topo.nodes}\nConnections {len(flows)}\n")
        for f in flows:
            line = (f"{f['src']}->{f['dst']} id {f['flow_id']} start {f['start_ps']} "
                    f"size {f['bytes']}")
            if "prio" in f:
                line += f" prio {f['prio']}"
            fh.write(line + "\n")
    check_cm(cm_path, topo.nodes)

    total = sum(f["bytes"] for f in flows)
    realized = total * 8 / window_s / (B * 1e9) if window_s > 0 else float("nan")
    burst = args.arrival == "fixed" and (rate is None)
    pairs_reused = sum(1 for v in seen.values() if v > 1)

    also = []
    for name in [x for x in args.also_topo.split(",") if x]:
        ot = get_topo(name, topo.nodes, args.link_gbps)
        if ot.nodes != topo.nodes:
            die(f"--also-topo {name} has {ot.nodes} nodes, not {topo.nodes}")
        ob, obind, _ = bottleneck(ot, pre, dec, elig)
        also.append({"topo": name, "bottleneck_gbps": ob, "binding": obind,
                     "offered_load": (total * 8 / window_s / (ob * 1e9)) if not burst else None})

    meta = {
        "command": " ".join([os.path.relpath(sys.argv[0], REPO)] + sys.argv[1:]),
        "topology": topo.summary(), "nodes": topo.nodes,
        "model": args.model, "bytes_per_token": bpt, "tp": T, "shards_simulated": W_sh,
        "pattern": args.pattern, "placement": placement,
        "prefill_instances": pre, "decode_instances": dec,
        "arrival": args.arrival, "prompt": prompt_spec, "seed": args.seed,
        "window_ps": int(window_s * PS_PER_S), "start_offset_ps": ps_off,
        "bottleneck_gbps": B, "bottleneck_computed_gbps": B_calc,
        "bottleneck_binding": binding if not args.bottleneck_gbps else ["override"],
        "rate_rps": rate, "target_load": args.load,
        "offered_load_realized": None if burst else realized,
        "mean_request_bytes": mean_req, "requests": len(requests), "flows": len(flows),
        "total_bytes": total, "deadline_slack": args.deadline_slack,
        "deadline_definition": "start_ps + slack * floor_ps; floor_ps = bytes*8 / "
                               "min line rate on the flow's path (uncontended floor)",
        "prio": args.prio, "note": note, "also_topo": also,
        "log_matching": "HTSIM logs name flows <Proto>_<src>_<dst>; match on (src,dst) "
                        "and pair_seq (order of start among flows on that pair)",
    }
    with open(js_path, "w") as fh:
        json.dump({"meta": meta, "requests": requests, "flows": flows}, fh, indent=1)

    # ---- report ----------------------------------------------------------------
    pr = print
    pr(f"topology   {topo.summary()}")
    pr(f"model      {args.model}: {bpt:,} B/token; TP={T}, simulating {W_sh} shard(s)/request")
    pr(f"placement  pattern={args.pattern} placement={placement}: {len(pre)} prefill x "
       f"{len(dec)} decode instances of {W_sh} host(s)")
    pr(f"           prefill hosts {fmt_hosts(pre)}  decode hosts {fmt_hosts(dec)}")
    pr(f"bottleneck {B:,.0f} Gbps" + (" (override; computed " + f"{B_calc:,.0f})"
                                       if args.bottleneck_gbps else ""))
    pr(describe_bottleneck(topo, by_kind, binding))
    for a in also:
        ol = f", this matrix = {a['offered_load']:.2f} offered load" if a["offered_load"] is not None else ""
        pr(f"  on {a['topo']}: bottleneck {a['bottleneck_gbps']:,.0f} Gbps "
           f"({'/'.join(a['binding'])}){ol}")
    if burst:
        pr(f"arrivals   burst: {len(requests)} requests, interval "
           f"{args.interval_us or 0:g} us; drain floor at bottleneck = "
           f"{total * 8 / (B * 1e9) * 1e6:,.0f} us")
    else:
        exp_n = rate * window_s
        pr(f"arrivals   {args.arrival}{' (count fixed)' if args.fix_count else ''}: "
           f"rate {rate:,.1f} req/s, window {args.duration_ms:g} ms, expected "
           f"{exp_n:.1f} requests, generated {len(requests)}")
        if note:
            pr(f"           {note}")
        tgt = f"target {args.load:.2f}, " if args.load is not None else ""
        pr(f"load       mean request {gb(mean_req):.3f} GB; offered "
           f"{total * 8 / window_s / 1e9:,.0f} Gbps = {tgt}realized {realized:.3f} of bottleneck")
    tiers = Counter(f["tier_crossed"] for f in flows)
    pr(f"output     {cm_path}: {len(flows)} flows, {gb(total):.2f} GB total, "
       f"tiers {dict(tiers)}, max flow {max(f['bytes'] for f in flows):,} B")
    pr(f"           {js_path}: per-flow metadata; {pairs_reused} src->dst pair(s) carry "
       f">1 flow (see pair_seq)")


def fmt_hosts(slots):
    hs = sorted({h for s in slots for h in s})
    out, i = [], 0
    while i < len(hs):
        j = i
        while j + 1 < len(hs) and hs[j + 1] == hs[j] + 1:
            j += 1
        out.append(f"{hs[i]}-{hs[j]}" if j > i else f"{hs[i]}")
        i = j + 1
    return ",".join(out)


def check_cm(path, nodes):
    """Re-read the file the way connection_matrix.cpp does and sanity check it."""
    n = conns = None
    lines = 0
    ids = set()
    with open(path) as f:
        for line in f:
            tok = line.split()
            if not tok:
                continue
            if tok[0] == "Nodes":
                n = int(tok[1])
            elif tok[0] == "Connections":
                conns = int(tok[1])
            elif "->" in tok[0]:
                lines += 1
                s, d = (int(x) for x in tok[0].split("->"))
                kv = dict(zip(tok[1::2], tok[2::2]))
                if set(kv) - {"id", "start", "size", "prio"}:
                    die(f"{path}: unexpected token in {line.strip()}")
                if not (0 <= s < nodes and 0 <= d < nodes):
                    die(f"{path}: node out of range: {line.strip()}")
                if not 0 < int(kv["size"]) <= INT32_MAX:
                    die(f"{path}: bad size: {line.strip()}")
                fid = int(kv["id"])
                if fid == 0 or fid in ids:
                    die(f"{path}: bad or duplicate id {fid}")
                ids.add(fid)
    if n != nodes or conns != lines:
        die(f"{path}: header Nodes {n} Connections {conns} vs {lines} flow lines")


if __name__ == "__main__":
    main()
