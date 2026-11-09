#!/usr/bin/env python3
"""M4. Placement, node numbering and tensor parallel sharding.

One logical KV cache transfer becomes TP concurrent flows, one per tensor
parallel rank, between matched rank indices of the prefill and decode
instance. The transfer is complete only when the slowest shard lands, which
is why the analysis takes the max over a request's shards and not the mean.

Node numbering
--------------
HTSIM maps hosts to top of rack switches by integer division,

    tor(host) = host / radix_down_tier0

so which host IDs an instance occupies decides which switch it hangs off.
Rail optimised placement is therefore a renumbering, not a different topology
file.

    conventional:  node = instance * TP + rank
    rail:          node = rank * n_instances + instance

On fat_tree_128 (radix_down 4 at tier 0, podsize 16, so 32 ToRs in 8 pods):

    conventional   an instance occupies 8 consecutive hosts, which is 2 ToRs,
                   and a transfer between instance 0 and instance 14 crosses
                   the fabric.
    rail           all the rank r endpoints land in hosts [16r, 16r+15],
                   which is exactly one pod, so every rank r flow stays
                   inside pod r and never touches the top tier.

That is the mechanism the rail optimised literature claims, and on this
topology the alignment is exact.

Pools
-----
Prefill and decode instances are disjoint sets of instance indices, so no
flow is ever self directed. The split defaults to 13 prefill and 3 decode,
roughly 4:1, following Splitwise's cluster designs rather than an even split.

Usage
    python3 scripts/placement.py --numbering conventional
    python3 scripts/placement.py --numbering rail --show 2
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import MODELS   # noqa: E402

# fat_tree_128_*.topo
DEFAULT_NODES = 128
DEFAULT_RADIX_DOWN = 4      # hosts per ToR
DEFAULT_PODSIZE = 16        # hosts per pod


class Placement:
    def __init__(self, nodes=DEFAULT_NODES, tp=8, n_prefill=13, n_decode=3,
                 numbering="conventional", radix_down=DEFAULT_RADIX_DOWN,
                 podsize=DEFAULT_PODSIZE):
        if nodes % tp != 0:
            raise ValueError("tp=%d does not divide %d nodes" % (tp, nodes))
        self.nodes = nodes
        self.tp = tp
        self.n_instances = nodes // tp
        if n_prefill + n_decode > self.n_instances:
            raise ValueError(
                "%d prefill + %d decode instances needs %d nodes, have %d"
                % (n_prefill, n_decode, (n_prefill + n_decode) * tp, nodes))
        if n_prefill < 1 or n_decode < 1:
            raise ValueError("need at least one instance in each pool")
        self.n_prefill = n_prefill
        self.n_decode = n_decode
        if numbering not in ("conventional", "rail"):
            raise ValueError("numbering must be conventional or rail")
        self.numbering = numbering
        self.radix_down = radix_down
        self.podsize = podsize

        # Disjoint by construction, so a self directed flow is impossible.
        self.prefill_instances = list(range(n_prefill))
        self.decode_instances = list(range(n_prefill, n_prefill + n_decode))

    def node(self, instance, rank):
        if not 0 <= rank < self.tp:
            raise ValueError("rank %d out of range for tp=%d" % (rank, self.tp))
        if not 0 <= instance < self.n_instances:
            raise ValueError("instance %d out of range" % instance)
        if self.numbering == "conventional":
            n = instance * self.tp + rank
        else:
            n = rank * self.n_instances + instance
        assert 0 <= n < self.nodes
        return n

    def tor(self, node):
        return node // self.radix_down

    def pod(self, node):
        return node // self.podsize

    def flows(self, prefill_inst, decode_inst, start_s, prompt_tokens, model):
        """TP flows for one request. Raises if the shard breaks the 2.1 GB cap."""
        if prefill_inst == decode_inst:
            raise ValueError("self directed transfer on instance %d" % prefill_inst)
        shard = model.check_flow_size(prompt_tokens, self.tp)
        return [(self.node(prefill_inst, r), self.node(decode_inst, r),
                 start_s, shard) for r in range(self.tp)]

    def decode_for(self, request_index):
        """Round robin over the decode pool. This becomes the placement
        variable later, which is why it is a method and not inline."""
        return self.decode_instances[request_index % self.n_decode]

    def locality(self):
        """Over every prefill to decode instance pair, how much traffic stays
        inside a ToR and inside a pod. This is the number that distinguishes
        the two numberings."""
        same_tor = same_pod = total = 0
        for p in self.prefill_instances:
            for d in self.decode_instances:
                for r in range(self.tp):
                    s, t = self.node(p, r), self.node(d, r)
                    total += 1
                    if self.tor(s) == self.tor(t):
                        same_tor += 1
                    if self.pod(s) == self.pod(t):
                        same_pod += 1
        return {"pairs": total,
                "same_tor": same_tor / total,
                "same_pod": same_pod / total}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nodes", type=int, default=DEFAULT_NODES)
    ap.add_argument("--tp", type=int, default=8)
    ap.add_argument("--prefill", type=int, default=13)
    ap.add_argument("--decode", type=int, default=3)
    ap.add_argument("--numbering", default="conventional",
                    choices=["conventional", "rail"])
    ap.add_argument("--model", default="llama2-13b", choices=sorted(MODELS))
    ap.add_argument("--show", type=int, default=1,
                    help="print the flows for this many example requests")
    args = ap.parse_args(argv)

    pl = Placement(args.nodes, args.tp, args.prefill, args.decode,
                   args.numbering)
    model = MODELS[args.model]

    print("%d nodes, tp=%d, %d instances (%d prefill, %d decode), %s numbering"
          % (pl.nodes, pl.tp, pl.n_instances, pl.n_prefill, pl.n_decode,
             pl.numbering))
    print("prefill instances %s on nodes %d..%d"
          % (pl.prefill_instances,
             min(pl.node(i, r) for i in pl.prefill_instances for r in range(pl.tp)),
             max(pl.node(i, r) for i in pl.prefill_instances for r in range(pl.tp))))
    print("decode  instances %s" % pl.decode_instances)

    loc = pl.locality()
    print("\nlocality over all %d prefill-to-decode rank pairs" % loc["pairs"])
    print("   same ToR  %5.1f %%" % (100 * loc["same_tor"]))
    print("   same pod  %5.1f %%" % (100 * loc["same_pod"]))

    for i in range(args.show):
        p = pl.prefill_instances[i % pl.n_prefill]
        d = pl.decode_for(i)
        print("\nrequest %d, 1469 token prompt on %s, prefill inst %d -> decode inst %d"
              % (i, model.name, p, d))
        for src, dst, _, size in pl.flows(p, d, 0.0, 1469, model):
            print("   %3d -> %3d   %10d B   ToR %2d -> %2d   pod %d -> %d"
                  % (src, dst, size, pl.tor(src), pl.tor(dst),
                     pl.pod(src), pl.pod(dst)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
