# Evaluating How Different Datacenter Network Policies Handle Disaggregated KV Cache Transfer

Matthew Erickson, Vasili Fovos, Michael Pitts, Christopher Sarzen, Quincy Winkler

Progress Report 2

## 1. Problem Definition

Large Language Model inference has two phases. Prefill processes every prompt token in parallel and computes a key-value vector pair per token per layer, which together form the KV cache. Decode then generates tokens one at a time, reading the entire cache at each step. Prefill is compute-bound and decode is memory-bandwidth-bound, so production systems run the phases on separate machines and move the cache between them over the network, placing the transfer on the critical path of time to first token (TTFT).

We study how existing datacenter network mechanisms handle this transfer, varying topology, congestion control, load balancing, and transport scheduling. KV traffic differs from what datacenter transports were designed for: each transfer's size is known in advance, a transfer is useless until all of its bytes arrive, and each carries a deadline set by the tolerated TTFT. We focus on cross-rack transfers over RoCE/Ethernet, measuring how far the deployed defaults (ECMP, DCQCN, FIFO) are from the floor, the size divided by bottleneck bandwidth, and which alternatives narrow the gap under which conditions. We propose no new mechanism; the contribution is a unified comparison on one simulator, workload model, and set of metrics.

**Contributions in this period.** We built a unified, deterministic, validated harness: four congestion-control policies, four load-balancing strategies, six fabrics, and three transfer orderings on one simulator with matched buffers, driven by a generator that replays the Azure LLM inference traces at an explicitly defined offered load. We found that the simulator's DCQCN and HPCC did not implement their papers and that its HPCC never converged; we reimplemented both, keeping the old code behind legacy flags that show what our first report measured. The mechanistic findings: ECMP placement dominates tensor-parallel transfer time and its seed spread is predictable; PFC without congestion control beats paper-faithful DCQCN on 3 ms transfers; EQDS's advantage is mostly per-packet spraying; rail placement removes the collision penalty for every policy; receiver-side SRPT reaches 1.10x floor at the median at the cost of long prompts; and scale-up domain size alone removes almost no fabric traffic.

## 2. Motivation

Datacenter networks assume that latency-sensitive flows are small; KV cache transfers are a few very large, synchronized flows that are nonetheless latency-critical, and one 4096-token Llama-3-70B prompt produces 1.34 GB that must cross the fabric before decoding begins. Disaggregation has moved from DistServe and Splitwise into production systems such as Mooncake, yet each candidate mechanism was evaluated on its own simulator and workload, so a deployer cannot tell which one helps KV traffic. Exact sizes and deadlines also make KV traffic a favorable setting for information-aware scheduling.

## 3. Challenges

ECMP hashes each flow onto one path, so one collision halves the bandwidth of two large flows for their whole duration, and tensor-parallel sharding compounds this because a transfer completes only when its slowest shard arrives. Incast onto one decode instance is limited by its downlink, and policies at different layers interact. Finally, conclusions are only as reliable as the simulator, and two of our four transports did not implement their papers (Section 4.1).

## 4. Methodology

### 4.1 Simulation platform and fidelity

Our platform is HTSIM, the Ultra Ethernet Consortium's packet-level reference simulator, in the ETH Zurich SPCL fork at commit ac1aea6; every change is a patch documented with evidence in `HTSIM/OUR_CHANGES.md`. `htsim_roce` runs RoCE with priority flow control (PFC) alone and, with `-dcqcn`, DCQCN; `htsim_hpcc` and `htsim_eqds` run HPCC and EQDS, all on every fabric.

**Simulator defects.** Stock DCQCN never terminated (`_last_alpha_update` was never assigned in `dcqcn.cpp`), and no `.topo` file could load (`fat_tree_topology.cpp` zeroed the parsed queue sizes). EQDS aborted on oversubscribed fabrics because a NACK for an already re-queued packet tripped an `abort()` in `eqds.cpp` and the receiver's reorder bitmap aliased; it now completes on every fabric. Start times were read in the wrong unit or ignored. The last fix is a minimum-rate clamp at R_AI in `dcqcn.cpp`: one E8 run halved an integer rate to zero and crashed, and in staggered runs deep cuts had silenced flows for up to 24 ms, leaving one E2 run unfinished. The clamp moved most staggered runs by up to 5 percent at the p50; every DCQCN number in this report uses it, and the crashed E8 cell now has three seeds.

**Harness defects.** Our parser and run script assumed 400 Gbps on the 100 Gbps 128-node files, and transports derive pacing and timeouts from `-linkspeed`: EQDS got a 399 us minimum retransmission timeout (the root cause of the stale-NACK abort) and ran one uncontended flow at 1.33x floor instead of 1.03x. At `-q 500` the 128-node load matrices produced 10^4 to 10^5 lossless violations, because a 4-host ToR's 8 ingress ports can each hold about 155 packets before a pause takes effect; those trees now run at `-q 2000`. The parser refuses any run with a lossless violation, an incomplete flow, or a start-time mismatch.

**DCQCN fidelity.** HTSIM's DCQCN shared one alpha across flows, compared packets against a 10 MB byte threshold (so hyper increase never fired), used an R_AI fifty times the paper's, and marked ECN with a step at 400 KB. The default is now per-flow alpha, a byte counter in bytes, R_AI of 0.1 percent of line rate, and RED in `queue_lossless_output.cpp` with Kmin 50 KB (the paper's 1 us of queueing at 400 Gbps), Kmax 400 KB (below the PFC threshold), and Pmax 1 percent. DCQCN runs now take 2 to 5 s instead of minutes.

**HPCC fidelity.** We found nine deviations from Algorithm 1 of Li et al. The window update compounded on every ACK, about 66 times per RTT, so the window overflowed to 1.8 x 10^19 bytes 200 us into a flow and dumped 488 MB into the sender's NIC, invisible to in-band telemetry (INT); the INT reference was never refreshed. We reimplemented the sender in `hpcc.cpp` with base RTT T of 13.1 us (16-node) or 16.4 us (128-node) and W_AI = W_init(1 - eta)/N with N = 10.

**EQDS priority.** `set_priority` in `main_eqds.cpp` called a method that never existed. Implementing receiver credit priority exposed finished flows that kept receiving credit, halving the next flow into a receiver, and priority that was only a tie-break; it is now strict, and with no priorities set logs are byte-identical. RoCE and HPCC ignore priorities. `-dcqcn_legacy` and `-hpcc_legacy` reproduce the checkpoint 1 transports bit for bit on the TP=8 matrix; legacy DCQCN incast moved under the clamp (seed 1, 34,979 to 34,553 us).

### 4.2 KV cache traffic model

Our generator produces flows analytically. Cache bytes per token are 2 x layers x KV heads x head dimension x 2 bytes, in decimal units because link rates are decimal.

Table 1. KV cache size per token and per 4096-token prompt, decimal bytes.

| Model | Layers | KV heads | Head dim | Bytes per token | 4096-token cache |
|---|---|---|---|---|---|
| Llama-3-8B | 32 | 8 | 128 | 131,072 | 0.54 GB |
| Llama-3-70B | 80 | 8 | 128 | 327,680 | 1.34 GB |
| Llama-2-13B | 40 | 40 | 128 | 819,200 | 3.36 GB |

Llama-3-70B is the default. Llama-2-13B replaces OPT-13B from our first report; it predates grouped-query attention, so its cache is 2.5x larger per token, which varies transfer size alone. A request of P tokens at tensor-parallel degree T becomes T concurrent flows of P x bytes-per-token / T bytes from prefill GPU i to decode GPU i; at TP=8 a 4096-token Llama-3-70B request is eight 167,772,160-byte flows. Each simulated host is one GPU and NIC. Placements are split (prefill and decode in different pods), interleaved, and rail. Prompt lengths are fixed or drawn from the Azure 2023 traces: the conversation trace has 19,366 requests with an inter-arrival coefficient of variation of 1.09, and the code trace 8,819 requests, a coefficient of variation of 13.2, and 14.1 percent of prompts above 4096 tokens. Arrivals are Poisson, or trace arrivals compressed to the requested load, since each trace offers only 14 to 17 Gbps at recorded speed.

Offered load is a fraction of an explicit bottleneck capacity B: the minimum, over every NIC, ToR uplink group, and pod-to-core uplink group in each direction, of capacity divided by the share of bytes that must cross it. For split traffic on the 128-node 1:1 tree B is 6,400 Gbps, so 70 percent load is 417 requests per second. B assumes perfect balancing within link groups, so ECMP collisions saturate links below a nominal load of 1.0. Each run draws a fixed number of arrivals uniformly in the window, and with a common seed each load level's requests are a superset of the level below. Each flow's deadline is its start plus 2.0 times its floor, and results report the network component of TTFT.

### 4.3 Policies evaluated

The baseline is a fat tree with ECMP, DCQCN, and FIFO ordering; each experiment changes one element while simulator, topology, buffers, and traffic are held constant.

Table 2. Policies evaluated, by layer, with implementation status.

| Layer | Policy | Rationale | Status |
|---|---|---|---|
| Congestion control | DCQCN | Deployed RoCE default; ECN feedback, timer-driven recovery | Paper-faithful; legacy flag kept |
| | HPCC | Rates computed from in-band telemetry | Paper-faithful (Algorithm 1); legacy flag kept |
| | EQDS | Receiver credit, per-packet spraying, trimming | Running on all fabrics |
| | RoCE without CC | PFC only | Running |
| Load balancing | ECMP | Deployed default | Running |
| | Switch adaptive routing | Flowlet rerouting on local queue depth | Running |
| | Host adaptive routing | Per-packet spraying | Running |
| Scheduling | Fair share, SRPT (pFabric style), EDF (D3 style) | Size and deadline ordering | Running (EQDS receiver priority) |

### 4.4 Experimental settings

The 16-node tree is K=4 with two hosts per ToR, four per pod, four core paths between pods, and 400 Gbps links. The 128-node trees use 100 Gbps links, four hosts per ToR, and 16 per pod, at 1:1, 4:1 (at aggregation-to-core), and 8:1 (4:1 at the ToR, 2:1 at aggregation). Rail-optimized wiring is expressed exactly as a 2-tier fabric with 16-port leaves plus a host relabeling (`rail_128_1os.topo`, and `rail_128_16os.topo` with one spine as a rail-only stand-in). Offered load is 30, 50, 70, and 90 percent of B.

### 4.5 Experiment plan and status

Table 3. Experiment plan and status at this checkpoint.

| ID | Varies | Question answered | Status |
|---|---|---|---|
| E1 | Tier crossed, uncontended | Transfer cost per tier | Done (all four, seeds 1-5) |
| E2 | Topology, offered load | Contribution of topology | Done (all four, seeds 1-3) |
| E3 | Congestion control, load | Contribution of congestion control | Done (all four, seeds 1-3) |
| E4 | Scheduler, load | Contribution of transfer ordering | Done (EQDS) |
| E5 | Topology x congestion control | Whether the best policy depends on the fabric | Covered by E2 with all four policies |
| E6 | Communication pattern | Where each mechanism matters under incast | Done (all four, seeds 1-3) |
| E7 | Scale-up domain size | How much hardware removes on its own | Done (analytic and Monte Carlo) |
| E8 | Context length distribution | Whether rankings hold across sizes | Done (all four, seeds 1-3) |
| E9 | Background training traffic | Multi-tenant contention | Not started |

### 4.6 Measurement pipeline

The harness runs each binary with matched buffers and the topology's link speed, and a parser converts each log into per-flow, per-request, and per-run results. A flow's floor is its size divided by the slowest line rate on its path, and a request completes when its slowest shard arrives. We report p50, p95, and p99 of distance from floor (with 9 to 27 requests per run, p99 is effectively the maximum), deadline miss rate, and Jain's index over completion normalized by prompt length. Load experiments also report x_ideal, completion divided by that of the same matrix in a fluid, max-min fair fabric with perfectly balanced link groups, which separates waste from unavoidable queueing. Every configuration is a median over seeds with its range.

## 5. Related Work

**DistServe (Zhong et al., OSDI 2024)** creates the traffic we study, separating prefill and decode to serve up to 7.4x more requests within latency SLOs at the cost of moving the cache on every request. Its placements assume InfiniBand makes the transfer negligible or keep it on NVLink, so its reported overhead of under 0.1 percent reflects NVLink; we study the contended case it avoids.

**KV cache survey (Jiang et al., Findings of ACL 2026).** The survey identifies our gap: systems handle bandwidth without modeling contention among concurrent KV transfers, and tail latency is rarely reported. The surveyed work varies the cache technique with the network fixed; we do the reverse.

**HPCC (Li et al., SIGCOMM 2019)** gives senders precise link load from in-network telemetry, so they compute rates directly, converging quickly with near-empty queues and reporting completion times up to 95 percent shorter than DCQCN; convergence matters because one shard takes only 3.36 ms at 400 Gbps. Our first report placed HPCC at 2.03x floor with a 58 percent spread across repetitions. Both were artifacts: the binary reseeded from the wall clock, so each repetition drew a different ECMP placement, and the compounding window made HPCC a PFC-paced NIC backlog. With the paper's algorithm and ten fixed seeds HPCC sits at 2.19x, 1.06 to 1.10 times the number of flows on the busiest link, and reproduces the paper's fast reclaim after a departure.

**DCQCN (Zhu et al., SIGCOMM 2015)** is the deployed RoCE congestion control and our baseline; its parameters (R_AI of 40 Mbps, a 10 MB byte counter, RED with Pmax 1 percent) are tuned for long-lived flows. **EQDS (Olteanu et al., NSDI 2022)** moves queueing to the edge with receiver-granted credit, per-packet spraying, and payload trimming. **pFabric (Alizadeh et al., SIGCOMM 2013)** shows that prioritizing by remaining size achieves near-optimal completion times, at the tail for short flows and on average for long ones, given sizes that the end hosts know, which KV transfers supply; we apply the same ordering at the receiver's credit pacer. **D3 (Wilson et al., SIGCOMM 2011)** apportions bandwidth by explicit rate control according to flow deadlines, and shows by example that earliest-deadline-first ordering alone can miss deadlines that a rate allocation would meet. **Splitwise (Patel et al., ISCA 2024)** and **Mooncake (Qin et al., FAST 2025)** deploy disaggregated inference with separate prefill and decode pools; Splitwise supplies the Azure traces we replay, and Mooncake's KV transfer component (Messenger, over GPUDirect RDMA) does not arbitrate between competing transfers. **Rail-only networks (Wang et al., 2023)** argue that because most LLM training traffic is between GPUs of the same rank, a rail-only fabric can remove the spine layer entirely; KV transfer is also between matched ranks. **NetKV (Ojewale, 2026)**, the closest competing work, selects decode instances with network awareness to reduce contention, a placement decision that leaves the transport mechanisms we evaluate unaddressed. We exclude cache compression, eviction, and paged memory management, which change what the cache is rather than how it moves.

## 6. Evaluation and Preliminary Results

### 6.1 Metrics

The primary metric is request completion time at the slowest shard divided by the floor; 1.0 means the network added nothing. Floor normalization matters most when the network is bandwidth-bound: the raw DCQCN-to-HPCC ratio shrinks from 1.83 on the TP=8 permutation (13,488 against 7,362 us) to 1.16 under 8-to-1 incast, while distance from the incast bound still separates them at 1.22x against 1.05x.

### 6.2 Validation against the papers

We reproduced the convergence experiment both papers use: four flows enter host 8 from outside its ToR, joining at 0, 2, 4, and 6 ms and sized to leave last-in-first-out at 16 to 22 ms under fair sharing (fair share 400/n Gbps). Seeds 1 to 5 on the 16-node tree, zero lossless violations (`docs/VALIDATION.md`).

Table 4. Convergence test against the HPCC and DCQCN papers, 16-node tree, median of seeds 1-5.

| Seed median | HPCC legacy | HPCC | DCQCN legacy | DCQCN (RED) | EQDS | RoCE, no CC |
|---|---|---|---|---|---|---|
| Jain, 2 / 3 / 4 flows | 0.632 / 0.474 / 0.395 | 0.971 / 0.969 / 1.000 | 0.98-1.00 | 0.978 / 0.735 / 0.871 | 0.984 / 1.000 / 1.000 | 1.000 / 0.888 / 0.728 |
| Aggregate goodput, 2 / 3 / 4 flows | 397 Gbps | 378 / 378 / 379 Gbps | 293 (2) / 335 (4) Gbps | 185 / 305 / 358 Gbps | 388 Gbps | 394 Gbps |
| Receiver queue, median / p99 | 100-109 / 190 KB | 18.1 / 27.2 KB | 4.1 / 728 KB | 4.1 / 142 KB | 4.2 / 4.2 KB | 914 / 1,291 KB |
| Peak sender NIC backlog | 486-488 MB | 9 KB | | | | |
| Survivor to line rate after last departure | 611 us (1/5 never) | 88 us | 485 us | 4,409 us (3/5 never) | 27 us | 15 us |
| Makespan, seed 1 (ideal 22.0 ms) | | 23.3 ms | | 29.2 ms | 22.7 ms | 22.4 ms |

Figure: results/figs/validation_convergence.png (Validation, final transports: per-flow goodput against time as four flows join and leave, seed 1); results/figs/validation_convergence_legacy.png (Validation, checkpoint 1 transports: the same experiment)

**Why HPCC now matches its paper.** With the reference window updated once per RTT in `hpcc.cpp`, Wc x eta/U is one correction per RTT, so it tracks utilization instead of running away, and the NIC holds at most one packet. Refreshing the INT reference on every ACK lets incumbents cut within an RTT of a join, draining the newcomer's 200 KB first window in 70 to 80 us, and the last survivor reclaims the link in 88 us. Goodput sits at the fixed point U = eta/(1 - W_AI/W) = 0.955 of line (Section 6.4). Fairness is slower, because a newcomer starts at line rate and the multiplicative cut preserves window ratios: at two flows it holds 222 Gbps against 156, and it reaches 10 percent of fair share 1.75 ms after the fourth join (350 us with N = 2). The paper describes exactly this: rapid convergence to a Pareto-optimal allocation, then slower convergence to fairness.

**Why faithful DCQCN converges slowly.** A line-rate newcomer drives the queue through the RED band in about 10 us, less than one 50 us CNP window, so 98 to 99 percent of marks are forced and every flow halves two or three times with alpha near 1; the two-flow phase averages 185 Gbps. Recovery at 7.3 Gbps per millisecond means the last survivor needs 4.4 ms to regain line rate. DCQCN thus converges more slowly than HPCC on every measure, as published. RED restores rate-proportional marking between transients (rate-to-CNP correlation 0.36, against 0.00 under step), but fairness is incomplete, and the legacy Jain near 1.00 was lockstep oscillation from a shared alpha. RoCE reproduces the PFC port unfairness that motivated DCQCN, splitting 197, 98, 51, and 48 Gbps by ingress port.

**DCQCN marking sensitivity.** On the 16-node TP=8 transfer, seeds 1 to 5 (`docs/E1.md`), faithful step marking at K = 25 to 200 packets gives 3.36x to 3.41x floor, since each cut's size is set by alpha, not K, and step at K = 400 equals RoCE seed by seed, because PFC pauses each ingress before the queue reaches 1.6 MB: the best DCQCN never marks, and is RoCE. RED with Kmax from 100 KB to 1.6 MB gives 3.02x to 3.49x and Pmax from 0.1 to 10 percent 3.47x to 4.32x, because one startup mark at alpha = 1 already halves the rate. The recovery step is what matters: legacy step sizes on the faithful transport give 2.71x.

### 6.3 Determinism, the reference table, and the ECMP collision model

Identical commands initially gave HPCC times from 6,796 to 10,710 us over nine repetitions, even with an explicit seed. The HPCC source constructor called `srand(time(NULL))`, and because `rng.cpp` replaces the C library generator with one global generator seeded from `-seed`, this reseeded the simulation from the wall clock. With it removed every binary is bit-identical per seed. What the seed changes is ECMP placement: `fat_tree_switch.cpp` maps each flow to an uplink with `freeBSDHash(flow_id, pathid, salt) mod n`, identically under every ECMP policy, so seeds pair comparisons.

On the reference transfer each flow has four paths, and the slowest shard is set by the collision class k, the number of flows on the busiest link.

Table 5. Reference transfer, one 4096-token Llama-3-70B cache at TP=8 across pods of the 16-node tree, slowest shard over seeds 1-10 (floor 3,355 us).

| Policy, seeds 1-10 | Median | x floor | Range | 2x class (6 seeds) | 3x class (4 seeds) |
|---|---|---|---|---|---|
| EQDS | 4,409 us | 1.31 | 4,159-4,787 us | 1.31 | 1.32 |
| RoCE, no CC | 6,842 us | 2.04 | 6,831-10,240 us | 2.04 | 3.05 |
| HPCC | 7,362 us | 2.19 | 7,168-10,709 us | 2.19 | 3.19 |
| DCQCN | 13,488 us | 4.02 | 10,302-15,009 us | 3.41 | 4.35 |
| HPCC, legacy (checkpoint 1 transport) | 7,224 us | 2.15 | 6,797-10,749 us | 2.14 | 3.20 |
| DCQCN, legacy, step ECN (checkpoint 1 transport) | 8,530 us | 2.54 | 8,006-12,209 us | 2.45 | 3.58 |

Table 6. ECMP collision classes on the reference transfer, fluid model against observed seeds.

| Collision class | 1x | 2x | 3x | 4x |
|---|---|---|---|---|
| Fluid model probability | 0.004 | 0.631 | 0.334 | 0.031 |
| Expected / observed of 10 seeds | 0.04 / 0 | 6.3 / 6 | 3.3 / 4 | 0.3 / 0 |
| HPCC, 100 seeds / RoCE, 40 seeds | 0.01 / 0.00 | 0.64 / 0.675 | 0.34 / 0.30 | 0.01 / 0.025 |

**Why the spread is a result.** `results/determinism/ecmp_collision_model.py` enumerates all 4^8 placements and solves a max-min fair fluid model. A pod pair is collision-free only if its four flows use distinct cores and the two flows sharing a ToR use different aggregation switches, probability 1/16, so both pairs are clean with probability 1/256. Because the flows are equal and start together, a collision divides one 400 Gbps link among k flows for the whole transfer: RoCE lands at exactly k x 1.017 on every seed and HPCC at k x 1.06 to 1.10. Within a placement congestion control moves the result by a few percent; across placements ECMP moves it by a factor of two or three. EQDS ignores the class because it sprays (Section 6.5). DCQCN varies within a class by up to 1.4x floor, because which flow RED cuts first depends on packet timing.

**What checkpoint 1 measured.** The earlier table (EQDS 4,165, HPCC 6,797, RoCE 10,240, DCQCN 12,098 us) was one placement per policy on the legacy transports. RoCE and DCQCN were seed 13, a 3x placement; RoCE's median over 40 seeds is 2.04x. HPCC 6,797 us was one wall-clock 2x placement with the overflowing window, and legacy DCQCN's 3.61x reflected an R_AI fifty times the paper's.

### 6.4 E1: uncontended per-tier cost

E1 asks what one transfer costs at each tier with no competing traffic, for single 167,772,160-byte flows and TP=8 shard sets, with the final transports and seeds 1 to 5 (`docs/E1.md`). Cells show the median slowest shard in x floor with the seed range; singles are identical on every seed.

Table 7. E1 uncontended cost per tier, median slowest shard in x floor [seed range], seeds 1-5.

| Transfer, seeds 1-5 | RoCE | DCQCN | HPCC | EQDS |
|---|---|---|---|---|
| 16-node single, same ToR / same pod / cross pod | 1.017 / 1.018 / 1.020 | 1.017 / 1.018 / 1.020 | 1.056 / 1.057 / 1.049 | 1.032 / 1.034 / 1.037 |
| 128-node single, same ToR / same pod / cross pod | 1.016 / 1.017 / 1.017 | 1.016 / 1.017 / 1.017 | 1.055 / 1.055 / 1.059 | 1.031 / 1.031 / 1.032 |
| 16-node TP=8 cross pod | 2.04 [2.04-3.05] | 3.44 [3.07-4.36] | 2.19 [2.14-3.19] | 1.32 [1.24-1.34] |
| 128-node 1:1 TP=8 same pod | 2.03 [2.03-3.05] | 2.74 [2.40-3.77] | 2.14 [2.14-3.23] | 1.17 [1.15-1.17] |
| 128-node 1:1 TP=8 cross pod | 2.03 [2.03-3.05] | 2.86 [2.43-3.70] | 2.71 [2.19-3.37] | 1.17 [1.15-1.17] |
| 128-node 4:1 TP=8 same pod | 2.03 [2.03-3.05] | 2.72 [2.61-3.69] | 2.14 [2.14-3.23] | 1.15 [1.09-1.21] |
| 128-node 4:1 TP=8 cross pod | 3.05 [3.05-5.08] | 3.68 [3.64-5.68] | 3.29 [3.27-5.47] | 2.09 [2.07-2.19] |
| 128-node 8:1 TP=8 same pod | 4.07 | 4.77 [4.65-4.86] | 4.31 | 4.21 |
| 128-node 8:1 TP=8 cross pod | 5.18 [4.07-6.10] | 5.74 [4.72-6.89] | 5.60 [4.69-6.62] | 4.36 [4.12-4.65] |

The 16-node TP=8 row covers seeds 1 to 5; the reference table (Section 6.3) covers seeds 1 to 10, whose extra 3x placements raise DCQCN's median to 4.02x.

**Why single flows cost 1.6 to 5.9 percent.** A RoCE source paces at line rate from the first packet (`roce.cpp`) with 64 bytes of header per 4,000-byte payload, so completion is 41,944 frames at line rate plus one base RTT, matching every single flow to 0.1 us; a lone DCQCN flow is never marked and behaves identically. EQDS adds a 1.47 percent credit shortfall, because its pacer in `eqds.cpp` computes pull intervals from a 4,160-byte MTU while sources spend 4,150 bytes per packet. HPCC's Algorithm 1 (`hpcc.cpp`) has the fixed point U = eta/(1 - W_AI/W), so a lone flow runs at 0.955 of line and lands at 1.055.

**Why TP=8 is set by the hash.** With every flow pinned to one path, RoCE's slowest shard is exactly k x 1.017 on every seed: PFC pauses each ingress above 150 packets and resumes below 80 (`queue_lossless_input.cpp`), so the bottleneck never idles. k is 2 or 3 on 1:1 trees, at least 2 on 4:1 cross pod, and 4 on 8:1. Our first report's claim that 1:1 and 4:1 tie because eight flows never saturate a pod was a seed-13 coincidence; over seeds, 4:1 binds.

**Why DCQCN is slower than RoCE on every seed.** DCQCN is 1.42 to 1.69 times RoCE at the same seed on the 16-node tree and 1.09 to 1.40 times at 100 Gbps, and only colliding flows are hurt. They start at line rate, so their shared queue passes Kmax within 8 us, every packet is force-marked, and the one-CNP-per-50-us rule in `dcqcn.cpp` cuts all of them in the same windows: traced flows fell from 400 to 25 and 50 Gbps within 170 us. Each cut also resets the target rate, so recovery at 7.3 Gbps per millisecond needs about 20 ms to regain a fair half, and alpha stays at 0.45 to 0.60, so later CNPs still remove 22 to 30 percent each.

**Why HPCC trails RoCE, by 35 percent on chains.** HPCC shares RoCE's placements and finishes a k-flow link near 1.06k. On 1:1 cross pod, seeds 2 and 4, four flows form a chain of shared links; HPCC reacts to the most-utilized hop, so the two middle flows, which see two shared links, finish at 2.50x and 2.71x while the end flows finish at 1.74x and 1.79x, the Pareto-optimal rather than max-min allocation the paper predicts with multiple bottlenecks.

**Why EQDS sits 3 to 7 percent above the bound on oversubscribed trees.** EQDS's 1.17x on 1:1 is hash imbalance of 16 entropies over a source ToR's four uplinks. Where a cut is forced, EQDS trims rather than pauses, and each trimmed header still crosses the bottleneck: on 8:1 same pod, 4.063 x (1 + trims x 64 bytes / bytes sent) = 4.208 against a measured 4.210, so EQDS loses to RoCE there (4.21 against 4.07). With no competing traffic, rate controllers can only remove bandwidth; the only lever that wins in E1 is load balancing.

### 6.5 Load balancing: spraying and adaptive routing

We tested whether EQDS's advantage is congestion control or load balancing by varying only its spray width on the reference transfer: 2.21x floor with one path entropy, 2.22x with 2, 1.48x with 4, 1.24x with 16, and 1.15x with 256. With one entropy EQDS lands where RoCE's collisions do, so its advantage is mostly spraying: `eqds.cpp` gives each packet the next entropy and every switch re-hashes per packet. We then asked whether adaptive routing recovers the collision loss for transports that do not spray (16-node tree, x floor).

Table 8. Load balancing on the 16-node tree, slowest shard in x floor.

| Transport | Workload | ECMP | Switch AR | Host AR, 50% spray |
|---|---|---|---|---|
| RoCE, no CC | TP=8 permutation | 2.04 (40 seeds) | 1.06 (12 seeds, max 1.10) | 37-51 (3 seeds) |
| RoCE, no CC | 8-to-1 incast | 1.02 | 1.10 | 11-33 |
| HPCC faithful | TP=8 permutation | 2.19 (10 seeds) | 2.19 (30 seeds, 0 reroutes) | 0 of 8 flows done at 100 ms |

**Why adaptive routing helps RoCE and not HPCC.** In `fat_tree_switch.cpp`, switch adaptive routing places a flow's first packet on the least-loaded uplink, but at t = 0 every queue is empty, so the initial placement is the ECMP lottery. Later packets stick to that port unless 10 us have passed since the flow's previous packet, and a line-rate flow sends every 0.1 to 0.2 us, so rerouting needs both a gap and a standing queue. RoCE supplies both: a collision builds a queue until PFC pauses the senders at about 42 us, the pause opens a gap, and the flowlet moves to an idle core, at the cost of about 1 percent of packets retransmitted. HPCC keeps queues near empty and never pauses, so no flow moved. Under incast the bottleneck is the downlink, so rerouting only adds reordering. Host adaptive routing hard-codes a 50 percent coin flip per packet (its threshold argument is never read), and the reordering makes a go-back-N receiver retransmit endlessly; spraying helps only a transport built for it, as EQDS is.

### 6.6 Rail placement

On rail-optimized wiring, traffic from prefill GPU i to decode GPU i stays on rail i. We compared conventional and rail labelings of the same hardware for one TP=8 transfer at 100 Gbps (floor 13,421.8 us) and a 4-to-1 incast, all four final transports, seeds 1 to 5, 300 runs with zero lossless violations (`docs/TOPOLOGY_RAIL.md`).

Table 9. Rail placement, one TP=8 transfer and a 4-to-1 incast at 100 Gbps, median slowest shard in x floor [seed range], seeds 1-5.

| Topology and labeling | RoCE | DCQCN | HPCC | EQDS |
|---|---|---|---|---|
| 3-tier 1:1, conventional | 2.03 [2.03-3.05] | 2.86 [2.43-3.70] | 2.71 [2.19-3.37] | 1.17 [1.15-1.17] |
| 3-tier 4:1, conventional | 3.05 [3.05-5.08] | 3.68 [3.64-5.68] | 3.29 [3.27-5.47] | 2.09 [2.07-2.19] |
| 2-tier rail fabric 1:1, conventional | 2.03 [1.02-2.03] | 2.60 [1.02-2.75] | 2.24 [1.08-2.24] | 1.031 |
| 2-tier rail fabric 16:1, conventional | 8.13 | 8.73 [8.71-8.77] | 9.09 | 8.75 |
| Rail labeling, any of the six fabrics | 1.016 | 1.016 | 1.055 | 1.031 |
| 4-to-1 incast, conventional labeling | 5.62 [5.19-6.34] | 5.70 [4.84-5.78] | 5.91 [5.47-6.26] | 4.12 |
| 4-to-1 incast, rail labeling (bound 4.06) | 4.06 | 4.77 [4.67-4.95] | 4.42 | 4.12 |

**Why rail placement removes the lottery for every policy.** A rail-local flow either never reaches a hashed link, because a ToR delivers directly to an attached destination (`fat_tree_switch.cpp`), or hashes onto links no other flow uses. Either way k = 1, and every policy lands on its E1 single-flow cost on every seed and oversubscription, including a spine with 1/16 of full capacity, the empirical half of the rail-only argument. Rails do not fix incast: four shards into each decode GPU share its downlink, so RoCE sits on the 4 x 1.016 receiver-drain floor with either spine.

**Why EQDS matches the rail without one, and where it loses.** Under conventional labeling on the 2-tier fabric EQDS gives 1.031 on every seed, because its 16 entropies spread over all 16 spines, and in the conventional incast it reaches the 4.12 receiver floor where RoCE pays 5.2x to 6.3x. It loses where one link is forced: on 16:1 all eight flows share one uplink while each receiver still credits its own downlink (`main_eqds.cpp`), the uplink trims 1,630,803 packets (`compositequeue.cpp`), and with each trimmed header crossing the bottleneck, 8.125 x (1 + trims x 64 bytes / bytes sent) = 8.748, exactly the measurement.

**Why DCQCN and HPCC trail RoCE except in one cell.** Wherever flows share a link, DCQCN is 1.07 to 1.40 times RoCE at the same seed and HPCC 1.05 to 1.10 times, 1.33 on chains; in HPCC's rail incast the four flows into each decode GPU finish at 2.0x to 4.4x floor. DCQCN beats RoCE only in the conventional incast (0.86 to 0.95 times on 4 of 5 seeds), plausibly because PFC pauses there block flows bound for uncongested receivers; this has not been traced.

### 6.7 E3: congestion control under load

E3 sweeps TP=8 Llama-3-70B requests with Poisson arrivals from 30 to 90 percent of B on the 16-node tree (one prefill and one decode instance) and the 128-node 1:1 tree (split placement, eight instances each), seeds 1 to 3.

Table 10. E3 per-request distance from floor against offered load, median [seed range] over seeds 1-3.

| Fabric | Policy | p50, 30% | 50% | 70% | 90% | p99, 70% | p99, 90% |
|---|---|---|---|---|---|---|---|
| 16-node | ideal fabric | 1.00 | 2.29 | 2.69 | 3.06 | 3.75 | 5.10 |
| | EQDS | 1.28 [1.28-1.30] | 2.95 [2.88-3.02] | 3.41 [3.40-3.52] | 3.93 [3.85-3.93] | 4.44 | 5.91 |
| | HPCC | 3.72 [3.22-3.86] | 5.69 [5.13-6.47] | 7.74 [7.22-9.36] | 11.80 [11.57-12.56] | 11.65 | 15.74 |
| | RoCE, no CC | 3.46 [3.35-3.72] | 6.87 [5.30-7.30] | 8.65 [7.27-11.94] | 14.41 [13.51-17.09] | 13.48 | 17.94 |
| | DCQCN | 5.54 [5.31-6.17] | 7.83 [7.65-8.96] | 12.19 [11.06-12.39] | 18.39 [16.90-18.81] | 14.94 | 22.41 |
| 128-node 1:1 | ideal fabric | 1.00 | 1.00 | 1.00 | 1.07 | 1.17 | 1.32 |
| | EQDS | 1.26 [1.21-1.26] | 1.31 [1.24-1.35] | 1.35 [1.33-1.36] | 1.72 [1.72-1.74] | 1.70 | 2.41 |
| | HPCC | 3.18 [2.96-3.27] | 4.13 [3.98-4.41] | 4.84 [4.41-5.07] | 6.11 [6.11-6.73] | 8.30 | 10.08 |
| | RoCE, no CC | 3.04 [2.91-3.05] | 4.05 [3.56-4.60] | 4.81 [3.72-5.60] | 6.17 [6.15-7.03] | 6.71 | 10.40 |
| | DCQCN | 3.54 [3.43-3.76] | 4.51 [4.06-4.55] | 5.40 [5.20-5.76] | 6.36 [6.28-7.46] | 8.80 | 8.37 |

Figure: results/figs/e3_ft16_load_vs_floor.png (E3, 16-node tree: p50 and p99 distance from floor against offered load, per policy and the ideal fabric); results/figs/e3_ft128_1os_load_vs_floor.png (E3, 128-node 1:1 tree: p50 and p99 distance from floor against offered load); results/figs/e3_ft128_1os_cdf_load70.png (E3, 128-node 1:1 tree: distribution of per-request distance from floor at 70 percent load)

**Why RoCE is at 3x already at 30 percent.** The ideal fabric is at 1.00, so RoCE's low-load excess is all ECMP collisions: a TP=8 instance on the 128-node tree puts four flows from each ToR onto four uplinks, collision-free with probability 4!/4^4 = 9.4 percent, so every request misses the 2x deadline. Above 30 percent requests overlap on hosts, and a pause stops every source on a NIC (`queue.cpp`), so a shard behind a collided uplink stalls its host's other shards; host NICs are paused 26 to 47 percent of their backlogged time at 70 percent load.

**Why HPCC helps only where requests share hosts.** HPCC inherits RoCE's collision classes at 95 percent of line rate. INT prevents pauses (share at most 0.005), so a shard on a free path takes NIC capacity a collided shard cannot use, and on the 16-node tree, where all requests share eight host pairs, HPCC beats RoCE by 10 to 18 percent from 50 percent load up. On the 128-node tree requests rarely share a sender, and HPCC's p99 at 70 percent is worse (8.30 against 6.71) because newcomers start at line rate and hold an outsized share for about 1.75 ms, letting later requests overtake earlier ones in 13 to 25 percent of overlapping pairs.

**Why DCQCN is slowest at the median yet has the best pinned-path tail at 90 percent.** Every arrival repeats the startup collapse of Section 6.4: 87 percent of marks are forced above Kmax and collided flows take 3 to 5 halvings in their first 300 us, then need about 10 ms to recover to 90 percent of fair share. That is about one floor at 100 Gbps but three at 400 Gbps, where every arrival also cuts all eight host pairs, so DCQCN is 1.43 to 1.84 times RoCE at 30 percent on the 16-node tree against 1.17 to 1.23 on the 128-node tree. What DCQCN removes is PFC: Kmax sits below the 600 KB pause threshold, so marks precede pauses (pause share at most 0.009). On the 128-node tree at 90 percent, where RoCE's pauses spread across the fabric, DCQCN has the best pinned-path p99 (8.37 against RoCE's 10.40) at a 3 percent higher p50.

**Why EQDS tracks the ideal.** Spraying removes the collision classes, and on the 16-node tree EQDS's x_ideal stays at 1.25 to 1.28 because its pacer round-robins pulls across overlapping requests, the processor sharing the fluid ideal assumes. The residual is spray imbalance: an uplink drawing more than its share of entropies fills to the trim point, and 3.5 to 4.1 percent of packets are retransmitted. The first-order effect in E3 is load balancing: pinned-path transports are 1.9 to 4.3 times worse than the sprayed one at the median, while congestion control moves them by tens of percent.

### 6.8 E2 and E5: topology, and whether the best policy depends on it

E2 runs one interleaved matrix on the 1:1, 4:1, and 8:1 trees, where the 30 percent level offers 0.30, 0.60, and 1.21 of capacity and the 70 percent level 0.70, 1.41, and 2.82; a request alone is bounded at 1, 2, and 4 times the floor. A victim micro-test adds a flow that crosses no oversubscribed link beside six congestors leaving its pod. Seeds 1 to 3, all four policies (`docs/E2.md`).

Table 11. E2 p50 distance from floor by tree at 30 and 70 percent load, interleaved placement, seeds 1-3.

| Load | Tree | Ideal | EQDS | HPCC | RoCE, no CC | DCQCN |
|---|---|---|---|---|---|---|
| 30% | 1:1 | 1.00 | 1.14 [1.12-1.15] (1.14) | 2.25 [2.20-3.27] (2.25) | 2.12 [2.06-3.05] (2.12) | 3.09 [2.82-3.64] (3.09) |
| | 4:1 | 2.00 | 2.22 [2.20-2.25] (1.11) | 4.41 [4.40-4.41] (2.20) | 4.13 [4.13-4.14] (2.06) | 4.78 [4.78-4.88] (2.39) |
| | 8:1 | 4.00 | 4.54 [4.38-4.59] (1.12) | 5.67 [5.65-6.68] (1.42) | 5.19 [5.17-6.18] (1.30) | 5.83 [5.77-6.73] (1.43) |
| 70% | 1:1 | 1.00 | 1.16 [1.15-1.17] (1.15) | 4.60 [4.00-4.81] (4.01) | 3.57 [3.55-4.07] (3.51) | 4.58 [4.48-4.63] (4.50) |
| | 4:1 | 3.09 | 3.68 [3.68-3.73] (1.23) | 6.58 [6.04-7.72] (2.40) | 7.86 [6.48-7.92] (2.52) | 7.21 [7.11-7.36] (2.44) |
| | 8:1 | 9.02 | 11.55 [11.43-12.21] (1.28) | 12.97 [12.76-13.43] (1.53) | 12.71 [12.33-13.48] (1.43) | 12.86 [12.63-13.33] (1.47) |

Cells: p50 [seed range] (x_ideal), x floor; Ideal is the fluid fabric's p50.

Table 12. E2 diagnostics: waste against the ideal fabric, PFC pauses, the victim flow (x floor), and EQDS retransmissions, seeds 1-3.

| Measure | EQDS | HPCC | RoCE, no CC | DCQCN |
|---|---|---|---|---|
| p50 x_ideal, all 15 cells | 1.08-1.31 | 1.21-4.13 | 1.18-4.05 | 1.18-4.51 |
| Host NIC pause share, 4:1 at 70%, seed 1 | no PFC | 0.012 | 0.60 | 0.002 |
| Victim flow, 1:1 / 4:1, median [range] | 1.07 [1.06-1.17] / 1.06 [1.05-1.18] | 1.06 [1.06-2.08] / 1.22 [1.06-2.08] | 1.02 [1.02-1.96] / 1.96 [1.02-2.41] | 1.02 [1.02-2.55] / 1.51 [1.02-2.36] |
| Retransmitted / new packets, 1:1 / 4:1 / 8:1 | 3-5% / 46-250% / 74-673% | 0 | 0 | 0 |

Figure: results/figs/e2_topology.png (E2, interleaved placement: p50 and p99 distance from floor and from the ideal fabric on each tree, per policy); results/figs/e2_topology_split.png (E2, split placement, same layout)

**Why pinned-path transports waste less on worse fabrics.** Eight cross-pod shards want 800 Gbps and a 4:1 pod has 400 Gbps of core uplink, so even alone a request takes 2x floor. RoCE's x_ideal falls from 3.51 on 1:1 to 2.52 on 4:1 and 1.43 on 8:1 at 70 percent because its waste is path choice: on 4:1 collisions load some core uplinks while others idle, while on 8:1 every flow of a ToR must share its one uplink, as an ideal fabric would. PFC still idles scarce links, because a pause applied by `queue_lossless_output.cpp` to a whole single-class queue also blocks packets bound for an idle uplink; on 4:1 seed 1 RoCE's victim was paused for 6.26 ms and took 2.41x floor.

**Why EQDS shows the price of oversubscription itself.** Spraying keeps EQDS within 1.08 to 1.31 times the ideal, so its tree-to-tree ratio at 30 percent (1.95 and 3.98) matches the isolated bounds of 2 and 4. It pays in retransmissions, because each receiver's pacer credits its own 100 Gbps downlink (`main_eqds.cpp`) while a cross-pod shard can get about 50 Gbps on 4:1 and 25 Gbps on 8:1, so the excess is trimmed at the oversubscribed hop (`compositequeue.cpp`). Trims cost the bottleneck only headers, so goodput holds, but EQDS's lead over RoCE shrinks to 2 to 13 percent on 8:1. With no PFC its victim stays at 1.05 to 1.18.

**Why the best pinned-path policy depends on the fabric.** HPCC and DCQCN remove PFC (host NIC pause share 0.012 and 0.002 against RoCE's 0.60), which wins exactly where PFC collapsed: on 4:1 at 70 percent their medians are 6.58 and 7.21 against 7.86, and their victims 1.22 and 1.51 against 1.96. Elsewhere they lose. HPCC's 5 percent headroom costs 4 to 9 percent at 30 percent; on 1:1 at 70 percent, where eight requests arrive within 12.8 ms, line-rate newcomers squeeze incumbents that recover by 1 KB per RTT (N = 2 brings it level with RoCE); and flows crossing two busy links see the worse hop. DCQCN's roughly 10 ms startup penalty is fixed, so it costs 19 to 46 percent over RoCE on 1:1 at 30 percent but only 9 to 12 percent on 8:1.

### 6.9 E6: incast

E6 converges K shards from different hosts on one decode GPU of the 16-node tree; the bound is K x 3,355 us through its 400 Gbps downlink. Seeds 1 to 3.

Table 13. E6 incast onto one decode GPU, slowest and first shard over the K x floor bound, median of seeds 1-3.

| Policy | Last / bound, K = 2 | K = 4 | K = 8 | First / bound, K = 8 | Sharing |
|---|---|---|---|---|---|
| RoCE, no CC | 1.018 | 1.017 | 1.016 | 0.508 | per ingress port |
| HPCC | 1.066 | 1.059 | 1.047 | 1.022 | near equal for K >= 4; 1.7:1 at K = 2 |
| EQDS | 1.033 | 1.031 | 1.030 | 1.030 | exact round robin |
| DCQCN | 1.459 | 1.500 | 1.222 | 1.025 | per flow, finishes spread by up to 19% |

Figure: results/figs/e6_incast.png (E6: slowest and first shard over the incast bound against K, with the TP=8 permutation at K = 1)

**Why all but DCQCN are within 7 percent of the bound.** The receiver's downlink is the only bottleneck. PFC keeps it fully busy for RoCE, so the 1.6 percent is headers; HPCC holds it at its eta target; EQDS's pacer never offers more than the downlink drains, so nothing is trimmed and the 3 percent is E1's credit and header overhead. DCQCN cuts all K flows together: the shared queue passes Kmax within microseconds, every packet is force-marked, and each flow takes 3, 5 to 6, and 10 halvings in the first half millisecond at K = 2, 4, and 8, down to 25, 8, and 0.8 percent of the link (at K = 8 the clamp). Recovery to 90 percent of fair share takes 5 to 16 ms whatever K is, so the penalty depends on the bound: 1.46 and 1.50 at K = 2 and 4, whose bounds are 6.7 and 13.4 ms, but 1.22 at K = 8, which amortizes one collapse over 26.8 ms.

**Why arrival order differs.** The K shards may belong to different requests, so who finishes first matters. RoCE's shares are per ingress port, because every backlogged ingress cycles through the same pause hysteresis in `queue_lossless_input.cpp`: with K = 4 one flow had 200 Gbps throughout while three shared the rest. HPCC's multiplicative cut preserves window ratios, so at K = 2 a small U bias between flows splits them 1.7:1; with N = 2 they finish within 0.5 percent. EQDS gives each flow every K-th pull, exact processor sharing. DCQCN is fair per flow (Jain at least 0.994), but RED cuts flows one at a time after startup, spreading their finishes by 7 to 19 percent.

### 6.10 E4: transport scheduling

E4 asks whether ordering by size or deadline beats fair sharing. Eight prefill hosts send one shard per request to one decode host (16-node tree), with Azure conversation-trace prompt lengths and Poisson arrivals at 50, 70, and 90 percent of the receiver's 400 Gbps downlink (106 to 191 requests), seeds 1 to 3; only the priority field changes, using EQDS strict receiver priority.

Table 14. E4 transport scheduling on EQDS, per-request distance from floor, median of seeds 1-3.

| Policy | p50, 50% | 70% | 90% | p99, 90% | Jain, 90% | Long-prompt p99, 90% |
|---|---|---|---|---|---|---|
| Fair share | 1.83 | 3.26 | 6.46 | 14.88 | 0.774 | 11.2 |
| SRPT (prio = bytes) | 1.10 | 1.13 | 1.18 | 22.47 | 0.311 | 24.4 |
| EDF, 2x slack | 1.12 | 1.70 | 5.58 | 70.24 | 0.255 | 5.0 |
| EDF, 4x slack | 1.10 | 1.28 | 2.99 | 37.57 | 0.249 | 6.0 |
| Ideal processor sharing | 1.76 | 2.69 | 5.78 | 12.4 | 0.77 | |
| Ideal shortest-job-first | 1.00 | 1.00 | 1.00 | 14.8 | 0.30 | |

Figure: results/figs/e4_fixed_load_vs_floor.png (E4, EQDS with receiver priority: p50 and p99 distance from floor against load, per scheduling policy); results/figs/e4_fixed_cdf_load70.png (E4: distribution of per-request distance from floor at 70 percent load)

**Why SRPT reaches 1.1x and what it costs.** For N flows on one link, processor sharing finishes the k-th smallest at (s_1 + ... + s_{k-1} + (N - k + 1) s_k)/C and shortest-first at (s_1 + ... + s_k)/C: both finish the last byte together, but shortest-first minimizes mean completion and gives the largest flows nothing back (Bansal and Harchol-Balter). The measured median sits 0.10 to 0.18 above the fluid model, the per-flow cost of one RTT, headers, and the first window. Static size priority is shortest-job-first, so a nearly finished long flow still ranks behind a fresh shorter one; long-prompt p99 is worse than under fair share in 9 of 9 cells, and Jain falls to 0.311, matching the fluid model's 0.30. Before the pull-pacer fixes, zombie credit and soft priority put a wall at about 2.4x under every policy and only 2.3 percent of requests finished below 2x at 70 percent; afterwards 79 percent do under SRPT.

**Why EDF has the worst tail.** With deadline = arrival + k x floor, EDF serves i before j when a_i + k f_i < a_j + k f_j, so small k approaches arrival order and large k approaches shortest-first. Under overload every waiting deadline has passed, so EDF serves the backlog oldest first and a new small request queues behind all of it, the domino effect D3 warns against: at 2x slack the p99 is 70.24 at 90 percent and short requests suffer most (p50 8.1x against 3.6x for long prompts). Receiver priority orders credit only; switches remain FIFO.

### 6.11 E7: scale-up domain size

E7 computes the fraction of KV bytes that cross the Ethernet fabric when the NVLink domain holds D = 8 GPUs (HGX) or 72 (NVL72), analytically and with a trace-driven Monte Carlo for finite decode capacity, on a 1,152-GPU cluster with Llama-3-70B and a 1:2 prefill-to-decode ratio.

Table 15. E7 fraction of KV bytes crossing the Ethernet fabric, 1,152 GPUs, 1:2 prefill to decode.

| Placement | D = 8, TP = 8 | D = 72, TP = 8 | D = 8, TP = 4 | D = 72, TP = 4 |
|---|---|---|---|---|
| Random | 1.000 | 0.938 | 0.995 | 0.938 |
| Rail-aligned | 1.000 (none on the core) | 0.938 (none on the core) | 1.000 | 0.938 |
| Locality-aware, unlimited capacity | 1.000 | 0.000 | 0.000 | 0.000 |
| Locality-aware, 16 slots at 80% utilization | 1.000 | 0.006 | 0.577 | 0.000 |

Figure: results/figs/e7_offfabric.png (E7: fraction of KV bytes crossing the Ethernet fabric against scale-up domain size, per placement); results/figs/e7_crosspod.png (E7: fraction of KV bytes crossing pod-to-core uplinks)

**Why domain size alone removes almost nothing.** Under domain-oblivious placement a transfer stays on NVLink only if its decode instance happens to share the prefill instance's domain, so the fabric fraction is about 1 - D/H: an NVL72 rack removes 6.2 percent of the bytes. With D = TP = 8 a domain holds one instance, so every transfer leaves the node; this is the Splitwise and Mooncake shape our packet simulations study. Locality helps only when a domain holds a balanced set of instances: at D = 72 and TP = 8 a rack holds three prefill and six decode instances and keeps 99.4 percent of bytes on NVLink, while at D = 8 and TP = 4 each prefill instance needs two decode instances' capacity and its node has one, so 58 percent still leaves. NVL72 makes cross-fabric transfer a placement choice rather than removing it.

### 6.12 E8: context length distribution

E8 asks whether the rankings hold when the size distribution changes: fixed 4K Llama-3-70B prompts, Azure conversation and code prompt lengths (medians near 1,030 and 1,950 tokens), and fixed 4K Llama-2-13B (2.5x the bytes), with the same Poisson arrivals at 50 and 70 percent load on the 16-node tree, seeds 1 to 3 (`docs/E8.md`). All E8 cells use the clamped binary.

Table 16. E8 per-request distance from floor by prompt-length regime and load, 16-node tree, seeds 1-3.

| Regime, load | EQDS | HPCC | RoCE, no CC | DCQCN | Ideal |
|---|---|---|---|---|---|
| 70B fixed 4K, 50% | 2.95 [2.88-3.02] / 3.82 | 5.69 [5.13-6.47] / 7.72 | 6.87 [5.30-7.30] / 8.76 | 7.83 [7.65-8.96] / 10.58 | 2.29 / 3.17 |
| 70B conversation, 50% | 1.78 [1.73-1.83] / 5.90 | 5.48 [5.40-5.78] / 9.70 | 5.77 [5.44-6.56] / 12.71 | 16.22 [14.97-16.62] / 33.85 | 1.37 / 3.70 |
| 70B code, 50% | 3.55 [3.40-3.64] / 8.74 | 6.68 [6.64-9.07] / 11.45 | 8.58 [8.05-11.52] / 15.40 | 17.05 [14.35-17.73] / 33.07 | 2.80 / 5.00 |
| 13B fixed 4K, 50% | 2.94 [2.87-3.02] / 3.82 | 5.63 [5.01-6.19] / 7.57 | 5.30 [5.14-7.25] / 8.77 | 6.18 [5.68-6.82] / 8.55 | 2.29 / 3.17 |
| 70B fixed 4K, 70% | 3.41 [3.40-3.52] / 4.44 | 7.74 [7.22-9.36] / 11.65 | 8.65 [7.27-11.94] / 13.48 | 12.19 [11.06-12.39] / 14.94 | 2.69 / 3.75 |
| 70B conversation, 70% | 3.70 [3.63-3.81] / 9.05 | 10.11 [9.15-10.98] / 19.10 | 14.84 [12.87-17.86] / 29.98 | 33.24 [32.92-33.49] / 58.19 | 2.22 / 5.72 |
| 70B code, 70% | 3.46 [3.26-3.48] / 13.98 | 7.71 [7.67-9.22] / 12.27 | 10.99 [9.79-13.84] / 20.64 | 16.76 [16.54-18.21] / 30.99 | 2.30 / 6.00 |
| 13B fixed 4K, 70% | 3.39 [3.38-3.53] / 4.43 | 7.60 [7.08-8.96] / 11.56 | 9.42 [8.58-10.26] / 13.47 | 8.96 [8.82-10.89] / 13.89 | 2.68 / 3.75 |

Cells: p50 [seed range] / p99, x floor.

Table 17. E8 size dependence: paired Llama-2-13B against Llama-3-70B on identical arrivals, and one request alone on the fabric (x floor, seeds 1-5).

| Measure | EQDS | HPCC | RoCE, no CC | DCQCN |
|---|---|---|---|---|
| Paired 13B / 70B p50 ratio, 50% / 70% | 1.00 / 1.00 | 0.99 / 0.99 | 1.00 / 1.00 | 0.80 / 0.80 |
| Isolated request, fixed cost per request | 29 us | 36 us | 13 us | 906 us |
| Isolated, 0.66 / 10.5 / 168 / 419 MB per shard | 2.78 / 1.51 / 1.32 / 1.31 | 3.24 / 2.45 / 2.19 / 2.18 | 3.24 / 2.14 / 2.04 / 2.03 | 3.24 / 6.57 / 3.44 / 2.77 |

Figure: results/figs/e8_xfloor_by_regime.png (E8: p50 and p99 distance from floor at 70 percent load, per regime and policy); results/figs/e8_iso_size.png (E8, one request alone: distance from floor and excess time against transfer floor); results/figs/e8_ranking.png (E8: rank of each policy by p50 and p99, per regime and load)

**The ranking holds.** EQDS is first by p50 in all eight cells with disjoint seed ranges, and DCQCN is last in all six Llama-3-70B cells; every other change of order is within the seed spread. The one p99 reversal, HPCC ahead of EQDS on the code trace at 70 percent (12.27 against 13.98), is a metric effect: EQDS's worst requests are 13- to 33-token prompts whose speculative first window lands on a receiver its pacer already keeps busy, is trimmed, and is resent, costing 90 to 220 us on a floor of about 10 us.

**Why distance from the floor is size-invariant except for DCQCN.** For EQDS, HPCC, and RoCE the paired Llama-2-13B ratio is 0.99 to 1.00, so network TTFT grows in proportion to context length. Their fixed costs are small; EQDS's 29 us is a 64-packet speculative window followed by a wait of about one 13 us RTT for the first pull (`eqds.cpp`), which matters only below about 10 MB per shard. DCQCN alone has a large fixed cost, the recovery time of Section 6.4: shards under 3 MB finish before the first CNP and match RoCE, shards of 10 to 42 MB are cut early and never recover (6.0x to 6.6x), and larger ones amortize the climb, so its ratio is 0.80. Its cuts scale with arrivals: every new flow re-enters at line rate (`dcqcn.cpp`) and its marks cut every flow on the queue, so with 3.5x as many requests DCQCN's x_ideal rises from 4.7 on fixed 4K to 14.7 on the conversation trace. HPCC's cost alone rises with request size (x_ideal 4.13 to 7.45 across conversation size bins), because newcomers enter at line rate while squeezed flows recover by W_AI per update (`hpcc.cpp`), and long requests live through the most arrivals; penalizing large requests gives it the best Jain index on the traces and the best code-trace p99.

## 7. Remaining work and schedule

Every experiment except E9 is complete. Three choices remain to be stated and checked: HPCC's N (N = 2 converges to fairness five times faster than HTSIM's default of 10 and closes HPCC's 1:1 gap in E2; the paper sizes N as the expected number of concurrent flows per link and used 100 in its evaluation), RED thresholds on 100 Gbps trees, and whether EQDS needs its sender-side congestion control on oversubscribed cores. Two mechanisms are untraced: why DCQCN beats RoCE in the conventional rail incast, and why HPCC runs below eta in the rail incast. E9 and a UEC transport are stretch goals, scheduling in RoCE and HPCC needs new priority support, and a true rail-only fabric needs a spineless topology with NVLink relay. A Vidur-based prefill model will state deadlines against full TTFT, and we will report the simulator defects upstream before the December 1 presentation and the December 8 final report.

## References

Agrawal, A., Kedia, N., Mohan, J., Panwar, A., Kwatra, N., Gulavani, B. S., Ramjee, R., and Tumanov, A. 2024. Vidur: A Large-Scale Simulation Framework for LLM Inference. In *Proceedings of Machine Learning and Systems (MLSys 2024)*.

Al-Fares, M., Loukissas, A., and Vahdat, A. 2008. A Scalable, Commodity Data Center Network Architecture. In *Proceedings of ACM SIGCOMM 2008*, 63-74.

Alizadeh, M., Yang, S., Sharif, M., Katti, S., McKeown, N., Prabhakar, B., and Shenker, S. 2013. pFabric: Minimal Near-Optimal Datacenter Transport. In *Proceedings of ACM SIGCOMM 2013*, 435-446.

Bansal, N., and Harchol-Balter, M. 2001. Analysis of SRPT Scheduling: Investigating Unfairness. In *Proceedings of ACM SIGMETRICS 2001*, 279-290.

Grattafiori, A., et al. 2024. The Llama 3 Herd of Models. arXiv preprint arXiv:2407.21783.

Handley, M., Raiciu, C., Agache, A., Voinescu, A., Moore, A. W., Antichi, G., and Wójcik, M. 2017. Re-architecting Datacenter Networks and Stacks for Low Latency and High Performance. In *Proceedings of ACM SIGCOMM 2017*, 29-42.

Jiang, J., Yang, P., Zhang, R., and Liu, F. 2026. Towards Efficient Large Language Model Serving: A Survey on System-Aware KV Cache Optimization. In *Findings of the Association for Computational Linguistics: ACL 2026*, 38450-38476.

Li, Y., Miao, R., Liu, H. H., Zhuang, Y., Feng, F., Tang, L., Cao, Z., Zhang, M., Kelly, F., Alizadeh, M., and Yu, M. 2019. HPCC: High Precision Congestion Control. In *Proceedings of ACM SIGCOMM 2019*, 44-58.

Ojewale, M. A. (2026). NetKV: Network-Aware Decode Instance Selection for Disaggregated LLM Inference. arXiv preprint arXiv:2606.03910.

Olteanu, V., Eran, H., Dumitrescu, D., Popa, A., Baciu, C., Silberstein, M., Nikolaidis, G., Handley, M., and Raiciu, C. 2022. An Edge-Queued Datagram Service for All Datacenter Traffic. In *19th USENIX Symposium on Networked Systems Design and Implementation (NSDI 22)*, 761-777.

Patel, P., Choukse, E., Zhang, C., Shah, A., Goiri, I., Maleki, S., and Bianchini, R. 2024. Splitwise: Efficient Generative LLM Inference Using Phase Splitting. In *Proceedings of the 51st Annual International Symposium on Computer Architecture (ISCA 2024)*, 118-132. https://doi.org/10.1109/ISCA59077.2024.00019. Azure LLM inference traces: https://github.com/Azure/AzurePublicDataset.

Qin, R., Li, Z., He, W., Cui, J., Ren, F., Zhang, M., Wu, Y., Zheng, W., and Xu, X. 2025. Mooncake: Trading More Storage for Less Computation - A KVCache-centric Architecture for Serving LLM Chatbot. In *23rd USENIX Conference on File and Storage Technologies (FAST 25)*, 155-170.

SPCL, ETH Zurich. 2026. HTSIM: discrete-event network simulator, SPCL fork of the Ultra Ethernet Consortium HTSIM repository. Software, https://github.com/spcl/HTSIM, commit ac1aea6.

Touvron, H., et al. 2023. Llama 2: Open Foundation and Fine-Tuned Chat Models. arXiv preprint arXiv:2307.09288.

Wang, W., Ghobadi, M., Shakeri, K., Zhang, Y., and Hasani, N. 2023. Rail-only: A Low-Cost High-Performance Network for Training LLMs with Trillion Parameters. arXiv preprint arXiv:2307.12169.

Wilson, C., Ballani, H., Karagiannis, T., and Rowstron, A. 2011. Better Never than Late: Meeting Deadlines in Datacenter Networks. In *Proceedings of ACM SIGCOMM 2011*, 50-61.

Zhong, Y., Liu, S., Chen, J., Hu, J., Zhu, Y., Liu, X., Jin, X., and Zhang, H. 2024. DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving. In *18th USENIX Symposium on Operating Systems Design and Implementation (OSDI 24)*, 193-210.

Zhu, Y., Eran, H., Firestone, D., Guo, C., Lipshteyn, M., Liron, Y., Padhye, J., Raindel, S., Yahia, M. H., and Zhang, M. 2015. Congestion Control for Large-Scale RDMA Deployments. In *Proceedings of ACM SIGCOMM 2015*, 523-536.
