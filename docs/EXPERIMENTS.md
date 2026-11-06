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

16-node fat tree (default auto-generated), 400 Gbps, one Llama-3-70B 4K cache
at TP=8 (eight 167,772,160 B shards), matched buffers `-q 500
-pfc_thresholds 80 150`, zero lossless violations. Floor per shard = 3,355 us.

Metric is the **slowest shard**, since a sharded transfer is not complete
until every shard lands. Median across repetitions.

| policy | n | median us | min | max | x floor | spread |
|---|---|---|---|---|---|---|
| EQDS | 9 | 4,164.7 | 4,164.7 | 4,164.7 | **1.24** | 0% |
| HPCC | 9 | 6,796.7 | 6,796.4 | 10,710.0 | **2.03** | 58% |
| RoCE, no CC | 9 | 10,240.0 | 10,240.0 | 10,240.0 | **3.05** | 0% |
| DCQCN | 2 | 12,097.6 | 12,097.6 | 12,097.6 | **3.61** | 0% |

Reproduce with:

    ./scripts/sweep.sh workloads/kv_llama70b_4k_tp8.cm default smoketest 5

### Two things to follow up

**DCQCN is worse than no congestion control at all.** 3.61x against 3.05x for
plain RoCE with only PFC. If that holds under load and across topologies it is
a headline result, since DCQCN is what production RoCE fabrics actually run.
Check it is not an artifact of our PFC threshold choice before believing it.

**EQDS is dramatically the best, at 1.24x floor.** Receiver-driven credit
rather than sender-side rate probing. Worth understanding why the gap is this
large, and whether it survives incast and oversubscription.

### Runs are not deterministic, and HPCC is the worst offender

Identical commands produce different results. HPCC ranged from 6,796 to
10,710 us across nine repetitions, a 58 percent spread, which is larger than
the gap between several of the policies. EQDS, RoCE and DCQCN were stable at
0 percent in this configuration.

Passing `-seed` did **not** fix it. HPCC with `-seed 42` still produced
10,749, 7,224 and 10,749 on three consecutive runs.

Consequences:

- **Never report a single run.** `sweep.sh` repeats and reports the median
  and spread for this reason.
- Any difference smaller than a policy's own spread is not a result.
- Check the spread column on every sweep. If it is more than a few percent,
  raise the repetition count before drawing a conclusion.
- Finding the source of the nondeterminism is worth an hour. It is probably
  path selection under ECMP, but it is not seeded the way the flag suggests.

An earlier version of this document reported HPCC at 2.13x from a single run.
That was the fifth-slowest shard of one sample, not a stable measurement.

## Validation before trusting any comparison

- Uncontended transfers in E1 complete close to the theoretical floor
- DCQCN converges more slowly than HPCC, consistent with published results
- Reproduce one figure from the HPCC paper and one from the DCQCN paper
- Every run reports zero lossless violations, or it does not count
