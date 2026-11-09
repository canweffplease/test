# Project context

**Title.** Evaluating How Different Datacenter Network Policies Handle
Disaggregated KV Cache Transfer

**Course.** CS 6250, Computer Networks

**Team.** Matthew Erickson, Vasili Fovos, Michael Pitts, Christopher Sarzen,
Quincy Winkler

---

## Problem

LLM inference has two phases. Prefill reads every prompt token in parallel and
computes a key-value pair per token per layer, which is the KV cache. Decode
then generates tokens one at a time, reading the whole cache from memory.

Prefill is compute-bound and decode is memory-bandwidth-bound, so production
systems run them on separate machines. That forces the KV cache across the
network before a single output token can be produced, putting it directly on
the critical path of time to first token.

KV cache traffic breaks the assumptions datacenter transports were built on.
Classic designs assume latency-sensitive flows are small and large flows are
background throughput. KV transfers are a small number of very large,
synchronized, latency-critical flows. Three properties make them unusual:

1. The size is known exactly before the transfer starts
2. The transfer is incomplete until every shard arrives
3. Each transfer carries a deadline set by the tolerated TTFT

We study transfers that must cross racks over a RoCE/Ethernet fabric. NVLink
transfers are considered only to measure what fraction of traffic they remove.

**Research question.** How far are the deployed defaults (ECMP, DCQCN, FIFO)
from the theoretical floor, and which alternative network policies close that
gap, under which conditions?

We do not propose a new mechanism. The contribution is a unified comparison.

---

## Motivation

Disaggregated serving is moving from research to production at Microsoft,
Moonshot AI and others, so KV traffic behavior on real fabrics matters now.

DistServe measured the interference that motivates the split: OPT-13B on one
A100 capped at 1.6 requests per second with both phases colocated, against 5.6
for prefill and 10 for decode once separated. They report serving 7.4x more
requests within latency SLOs.

But DistServe handles the resulting transfer by avoiding it. Its high
node-affinity algorithm assumes InfiniBand makes it negligible, and its low
node-affinity algorithm constrains placement so transfers stay on intra-node
NVLink. Its testbed had 25 Gbps cross-node links, so the reported "under 0.1
percent of latency" reflects NVLink, not a contended fabric.

We study the case DistServe is designed to avoid.

---

## Challenges

- ECMP hashes each flow to one path, which works for many small flows and fails
  for a few enormous ones
- Tensor-parallel sharding means a logical transfer finishes only when its
  slowest shard lands, so slowest-shard time matters more than mean throughput
- Incast on a decode instance is limited by that instance's downlink no matter
  how good the rest of the fabric is
- Policies at different layers interact, so the best scheduler may depend on
  the topology or the congestion control beneath it
- No production GPU cluster is available, so conclusions are only as good as
  the network model

---

## Scope boundary

**In scope:** topology, congestion control, load balancing, transport
scheduling. All networking mechanisms, all published at networking venues
(SIGCOMM, NSDI, INFOCOM).

**Out of scope:** cache compression, eviction, paged memory management. These
change what the cache *is* rather than how it moves, and are published at OS
and ML systems venues.

Our professor warned explicitly that work outside the scope of the class will
be penalized in future milestones. When in doubt, check the venue of the paper
you are citing.

---

## Key references

| Paper | Role |
|---|---|
| DistServe (OSDI '24) | Creates the traffic. Our motivation. |
| Mooncake (FAST '25) | Production proof. Its Messenger component moves KV over GPUDirect RDMA but does not arbitrate between competing transfers. |
| Jiang et al. survey (ACL Findings '26) | Names the gap: techniques evaluated in isolation, inconsistent metrics, tail latency rarely reported. |
| HPCC (SIGCOMM '19) | Our strongest congestion control so far. Also the fallback simulator. |
| DCQCN (SIGCOMM '15) | The deployed baseline. |
| EQDS (NSDI '22) | Receiver-driven, our third control architecture. |
| pFabric (SIGCOMM '13) | Size-aware scheduling. Needs exact flow sizes, which we uniquely have. |
| D3 (SIGCOMM '11) | Deadline-aware scheduling. |
| Al-Fares (SIGCOMM '08) | Fat tree, where oversubscription comes from. |
| Rail-only (arXiv 2307.12169) | Rail-optimized topology argument, made for training. We test it on inference. |
| NetKV (Ojewale, arXiv 2606.03910, 2026) | Closest competitor. Network-aware decode-instance selection; explicitly scoped to placement and states it requires no transport changes. That is our gap. |
