# Experiment plan

Baseline is fat tree + ECMP + DCQCN + FIFO at 400 Gbps with matched buffers.
Change exactly one element per run. A full factorial is infeasible, so this is
a baseline-plus-single-variable design with a few targeted interaction runs.

| ID | Varies | Question answered | Priority | Status |
|---|---|---|---|---|
| E1 | Tier crossed, uncontended | Transfer cost at each topology tier. The reference every later result is read against. | Core | done, docs/E1.md |
| E2 | Topology, offered load | Contribution of topology alone. | Core | done, docs/E2.md |
| E3 | Congestion control, offered load | Contribution of congestion control alone. | Core | done, docs/E3.md |
| E4 | Scheduler, offered load | Contribution of transfer ordering alone. | Core | done (EQDS receiver priority), docs/E4.md |
| E5 | Topology x congestion control | Whether the best algorithm depends on the fabric beneath it. | Core | partly covered by E2 x policies; interaction runs not done |
| E6 | Communication pattern | Where each mechanism matters most, particularly incast. | High | done, docs/E6.md |
| E7 | Scale up domain size | How much of the problem hardware removes on its own. | High | done (analytic), docs/E7.md |
| E8 | Context length distribution | Whether rankings hold across transfer sizes. | Stretch | not started (driver `scripts/e8.sh` being written, no results yet) |
| E9 | Background training traffic | Behavior under multi-tenant contention. | Stretch | not started |

Rail-optimized topologies are covered separately in docs/TOPOLOGY_RAIL.md,
and transport validation against the papers in docs/VALIDATION.md.

E1 through E5 were targeted for Progress Report 2.

## Sequencing

E1 first, because every later number is reported as a multiple of it.
E5 last among the core set, because it needs E2 and E3 done.
E1, E3 and E6 do not need the workload generator and can run on the hand
written matrices in `workloads/` today.

## Runtime budget

Measured wall clock per simulation, one thread each:

- 8-flow matrices (`kv_llama70b_4k_tp8.cm`, `kv_incast_8to1.cm`), measured
  for the reference table with ~22 runs sharing 24 cores: HPCC 1-3 s,
  **DCQCN 2-5 s** (2-3 s typical), EQDS 2-11 s, RoCE 15-30 s. The old
  ~70-90 s DCQCN figure was a RoCE pacing chain that ran until `-end`; it is
  gone in the faithful DCQCN path. `-dcqcn_legacy` still has it: with
  `END=60000` it takes 90-120 s, and at the default `END` (2 s simulated) a
  run had not finished after 7 minutes.
- E3 load matrices (9-27 TP=8 requests): from seconds to about 600 s
  depending on policy and load. Measured so far: HPCC 7-36 s, EQDS
  25-283 s, RoCE 23-412 s (slowest is RoCE at 90 % on the 16-node tree);
  see the wall s column in docs/E3.md. Budget 600 s per run.
- Keep at most one run per core; parallel runs are safe because run.sh gives
  each one its own scratch directory.

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
so every policy looks similar in milliseconds. In the reference runs below
the raw gap between DCQCN and HPCC compresses from 1.83x on the TP=8
permutation (13,488 against 7,362 us) to 1.17x under incast (32,763 against
28,098 us), while distance from the incast bound still separates them
clearly at 1.22x against 1.05x. Raw time hides how much bandwidth a policy
wastes. (The checkpoint 1 version of this paragraph used one seed of the
legacy transports: 1.68x, 1.23x, and 1.30x against 1.05x.)

## Reference results

16-node fat tree (default auto-generated), 400 Gbps, one Llama-3-70B 4K cache
at TP=8 (eight 167,772,160 B shards, pods 0-1 to pods 2-3), matched buffers
`-q 500 -pfc_thresholds 80 150`, zero lossless violations. Floor per shard =
3,355 us. Metric is the **slowest shard**. Median over seeds 1-10; the class
columns are the median within each ECMP collision class (number of flows on
the busiest link). Final transports: paper-faithful DCQCN with RED ECN, HPCC
Algorithm 1, EQDS with the credit and priority fixes, RoCE with the start-time
fix. Full table, per-seed rows and incast: `results/reference/reference_table.md`.

| policy | n | median us | min | max | x floor | spread | 2x class (6 seeds) | 3x class (4 seeds) |
|---|---|---|---|---|---|---|---|---|
| EQDS | 10 | 4,409 | 4,159 | 4,787 | **1.31** | 14% | 4,395 (1.31x) | 4,422 (1.32x) |
| HPCC | 10 | 7,362 | 7,168 | 10,709 | **2.19** | 48% | 7,357 (2.19x) | 10,709 (3.19x) |
| RoCE, no CC | 10 | 6,842 | 6,831 | 10,240 | **2.04** | 50% | 6,842 (2.04x) | 10,240 (3.05x) |
| DCQCN | 10 | 13,488 | 10,302 | 15,009 | **4.02** | 35% | 11,433 (3.41x) | 14,588 (4.35x) |
| HPCC legacy (checkpoint 1 numbers, legacy transports) | 10 | 7,224 | 6,797 | 10,749 | **2.15** | 55% | 7,181 (2.14x) | 10,749 (3.20x) |
| DCQCN legacy, step ECN (checkpoint 1 numbers, legacy transports) | 10 | 8,530 | 8,006 | 12,209 | **2.54** | 49% | 8,221 (2.45x) | 12,010 (3.58x) |

Spread is (max - min) / median over seeds, and it is almost entirely the
collision class: RoCE and HPCC are flat within a class. EQDS sprays each flow
over its path entropies, so it ignores the class. DCQCN varies within a
class (3.07-4.47x in the 2x class). Seeds 1-10 gave six 2x and four 3x
placements; `results/determinism/ecmp_collision_model.py` predicts 6.3 and
3.3 (and 0.04 at 1x, 0.3 at 4x).

DCQCN is now the worst policy here, 4.02x against plain RoCE's 2.04x. The
legacy DCQCN (2.54x) was closer to RoCE because its R_AI of 5 % of line rate
recovers quickly after the startup collapse; the paper's 0.1 % takes
milliseconds to recover each cut.

Incast 8 to 1 (`kv_incast_8to1.cm`, same seeds), against the bound of
8 x 3,355.4 = 26,843.5 us through host 8's downlink:

| policy | median last finish us | x bound | median first finish us | first-to-last spread |
|---|---|---|---|---|
| RoCE, no CC | 27,286 | **1.016** | 13,649 | 13,637 us |
| EQDS | 27,645 | **1.030** | 27,644 | 2 us |
| HPCC | 28,098 | **1.047** | 27,310 | 780 us |
| DCQCN | 32,763 | **1.221** | 27,379 | 5,870 us |

Under incast the bound dominates and every policy but DCQCN is within 5 % of
it. RoCE finishes first but shares per ingress port, so its flows finish
staggered over 13.6 ms; EQDS and HPCC finish all eight shards together.

Reproduce with the command block at the top of
`results/reference/reference_table.md`, or approximately with
`./scripts/sweep.sh workloads/kv_llama70b_4k_tp8.cm default ref 10`.

**What changed since checkpoint 1.** The earlier table (EQDS 4,165, HPCC
6,797, RoCE 10,240, DCQCN 12,098 us, "spread 0 %" for three of them) was one
ECMP placement per policy, run on the legacy transports. RoCE and DCQCN were
seed 13, a 3x collision placement, run repeatedly, hence "0 %". HPCC
was reseeded from the wall clock, so its nine runs were nine placements,
hence "58 %". HPCC 6,797 us was a 2x placement and measured a window-overflow
bug, not HPCC. DCQCN used an unpublished R_AI of 5 % of line rate and a
shared alpha. Since then: HPCC Algorithm 1 replaced the compounding window,
DCQCN got per-flow alpha, paper step sizes and RED marking, EQDS stopped
crediting finished flows, and every policy now runs over the same ten
placements. The legacy rows above re-run the checkpoint 1 transports on
those same ten seeds.

### Runs are deterministic per seed; the spread is ECMP placement

Each binary gives bit-identical output for a given `-seed` (HPCC's
`srand(time(NULL))` was removed in commit 2521a60). The seed sets the ECMP
hash salts and path ids, i.e. which flows share a link, and on this matrix
that alone decides whether the slowest shard sits at 1x, 2x, 3x or 4x floor.
`results/determinism/ecmp_collision_model.py` predicts the class frequencies
(0.4 / 63.1 / 33.4 / 3.1 %) and 100 HPCC seeds plus 40 RoCE seeds match it.
The same seed gives the same placement in every ECMP policy, so seeds give
paired comparisons. Details: HTSIM/OUR_CHANGES.md, "Runs were not
deterministic" and "What the spread physically is".

Consequences:

- **Never report a single seed.** `sweep.sh` runs rep i with `-seed i` and
  reports median and spread.
- Any difference smaller than the seed spread is not a result; compare
  policies per seed (same placement) where possible.
- Repeating one seed is pointless: it reproduces the same run.

## Validation before trusting any comparison

- **Uncontended transfers complete close to the floor** (E1, docs/E1.md):
  isolated single flows run at 1.017-1.03x for RoCE, DCQCN and EQDS once
  `-linkspeed` matches the fabric. Faithful HPCC sits at 1/(0.95 x 0.993) =
  1.06x by design (its 95 % utilization target). Every gap above that has a
  mechanism tied to code.
- **Convergence vs the papers** (docs/VALIDATION.md, then the fidelity
  sections of HTSIM/OUR_CHANGES.md). VALIDATION.md found HTSIM's HPCC did not
  converge at all and DCQCN differed from the paper in four ways. After the
  fixes, HPCC reaches Jain 1.000 with 4 flows, holds its 95 % target and
  reclaims bandwidth within 50-90 us of a departure, as the paper describes.
  Faithful DCQCN converges more slowly than that (it rarely holds within
  10 % of fair share), consistent with the published comparison, and RED
  marking now makes faster flows receive more CNPs (correlation 0.36 against
  0.00 under step marking). EQDS reaches fair share within one 50 us bin.
- **Figures reproduced** qualitatively: the DCQCN paper's sawtooth queue and
  PFC unfairness motivation, and the HPCC paper's fast convergence after a
  departure. Exact numeric reproduction is not claimed.
- Every run reports zero lossless violations, or it does not count (parse.py
  refuses it).
