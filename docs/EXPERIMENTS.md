# Experiment plan

Baseline is fat tree + ECMP + DCQCN + FIFO at 400 Gbps with matched buffers.
Change exactly one element per run. A full factorial is infeasible, so this is
a baseline-plus-single-variable design with a few targeted interaction runs.

| ID | Varies | Question answered | Priority | Status |
|---|---|---|---|---|
| E1 | Tier crossed, uncontended | Transfer cost at each topology tier. The reference every later result is read against. | Core | not started |
| E2 | Topology, offered load | Contribution of topology alone. | Core | blocked on topo files |
| E3 | Congestion control, offered load | Contribution of congestion control alone. | Core | not started |
| E4 | Scheduler, offered load | Contribution of transfer ordering alone. | Core | blocked on set_priority |
| E5 | Topology x congestion control | Whether the best algorithm depends on the fabric beneath it. | Core | blocked on E2, E3 |
| E6 | Communication pattern | Where each mechanism matters most, particularly incast. | High | matrix ready |
| E7 | Scale up domain size | How much of the problem hardware removes on its own. | High | not started |
| E8 | Context length distribution | Whether rankings hold across transfer sizes. | Stretch | not started |
| E9 | Background training traffic | Behavior under multi-tenant contention. | Stretch | not started |

E1 through E5 are targeted for Progress Report 2.

## Sequencing

E1 first, because every later number is reported as a multiple of it.
E5 last among the core set, because it needs E2 and E3 done.
E1, E3 and E6 do not need the workload generator and can run on the hand
written matrices in `workloads/` today.

## Runtime budget

DCQCN takes roughly 90 seconds per run. HPCC takes about 1. Size sweeps around
DCQCN. E3 at four policies by four load levels is 16 runs, mostly fast, with
the four DCQCN runs dominating at about 6 minutes total.

## Metrics per run

Primary is **distance from theoretical floor** = measured completion / (size /
bandwidth). 1.0 means the network added nothing. Normalizes across transfer
sizes, link speeds and topologies, which raw milliseconds do not.

Secondary: p50/p95/p99 TTFT, deadline miss rate against decode batch
boundaries, decode stall time in GPU-seconds, fairness via Jain's index over
TTFT normalized by prompt length.

Always take the **max** across a request's shards, never the mean. A
tensor-parallel transfer is not done until its slowest shard lands.

## Why distance from floor and not just raw time

Under incast the fabric is bandwidth-bound and the physical floor dominates,
so every policy looks similar in milliseconds. In our preliminary runs the raw
gap between DCQCN and HPCC compressed from 1.68x on a permutation pattern to
1.23x under incast, while distance from floor still separated them clearly at
1.30x against 1.05x. Raw time hides how much bandwidth a policy wastes.

## Preliminary results

16-node fat tree, 400 Gbps, one Llama-3-70B 4K cache at TP=8, matched buffers,
zero lossless violations.

| Pattern | DCQCN | HPCC | Floor | DCQCN | HPCC |
|---|---|---|---|---|---|
| TP matched pairs | 12,098 us | 7,137 us | 3,355 us | 3.61x | 2.13x |
| 8 to 1 incast | 33,183 us | 26,971 us | 25,600 us | 1.30x | 1.05x |

Reproduce the first row with:

    ./scripts/run.sh dcqcn default smoketest workloads/kv_llama70b_4k_tp8.cm
    ./scripts/run.sh hpcc  default smoketest workloads/kv_llama70b_4k_tp8.cm

## Validation before trusting any comparison

- Uncontended transfers in E1 complete close to the theoretical floor
- DCQCN converges more slowly than HPCC, consistent with published results
- Reproduce one figure from the HPCC paper and one from the DCQCN paper
- Every run reports zero lossless violations, or it does not count
