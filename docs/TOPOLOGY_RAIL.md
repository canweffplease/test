# Rail-optimized and rail-only topologies in HTSIM

This answers CLAUDE.md open item 6 ("is rail-optimized wiring expressible in the
.topo format?") and runs the first comparison. The reference design is Wang et
al., "How to Build Low-cost Networks for Large Language Models (without
Sacrificing Performance)?", arXiv 2307.12169. In their rail-optimized fabric,
GPU i of every HGX node attaches to rail switch i, and spines join the rails.
Their rail-only fabric removes the spines, so cross-rail traffic must first
cross NVLink inside the node.

Short answer:

- **Rail-optimized: yes.** It can be expressed exactly, with no simulator
  change. Use a 2-tier `.topo` whose leaves have 16 host ports, and label hosts
  as `host = 16*gpu + node`. The new files are `rail_128_1os.topo` and
  `rail_128_16os.topo`.
- **Rail-only: no.** The spine cannot be removed, and NVLink cannot be modelled.
  It does not need to be modelled for matched-index KV transfer, because that
  traffic never uses the spine. The 16:1 spine-starved variant gives the same
  1.02x as the 1:1 one.
- **Policies.** Rail-local placement brings every policy to its own
  single-flow cost (RoCE and DCQCN 1.016, EQDS 1.031, HPCC 1.055) on every
  seed. Policy only matters where flows still share links: the conventional
  labellings and the incast. EQDS's spraying wins wherever the hash is the
  problem (1.03-1.17 against RoCE's 2.03 on conventional labelling) and loses
  1.4-7.6 % where one link is forced (16:1 conventional, rail incast), because
  trimmed headers and its credit pacer cost bandwidth. DCQCN and HPCC are
  slower than RoCE wherever flows share a link, except the conventional
  incast: there DCQCN is faster than RoCE on 4 of 5 seeds and HPCC is within
  0.98-1.10 of it.

All `file:line` citations are relative to `HTSIM/htsim/sim/`. RoCE runs from
`build_start/datacenter/htsim_roce`; DCQCN (paper-faithful RP plus RED ECN)
from `build/`, HPCC (Algorithm 1) from `build_hpccfix/`, EQDS (fixed credit and
priority) from `build_eqds2/`. Section 4.1 has all four policies.


## 1. Can the FatTreeTopologyCfg `.topo` format express rail wiring?

**Not as a wiring option. It can as a labelling.**

- **The format has no per-host attachment field.** `read_cfg` accepts only
  `Nodes`, `Tiers`, `Podsize` in the header (`datacenter/fat_tree_topology.cpp:602-610`).
  Per tier it accepts only `downlink_speed_gbps, radix_up, radix_down,
  queue_up, queue_down, oversubscribed, bundle, switch_latency_ns,
  downlink_latency_ns`. Anything else is a hard error (`:712-713`). No keyword
  names which ToR a host attaches to.
- **The host-to-ToR map is fixed arithmetic.** It is
  `HOST_POD_SWITCH(h) = h / radix_down[TOR]` (`datacenter/fat_tree_topology.h:95-97`),
  and pod membership is `h / hosts_per_pod` (`:107-112`).
- **Routing reuses that same arithmetic.** The route builder uses it
  (`fat_tree_topology.cpp:1284-1360`). So does the FIB switch:
  - delivery to the host (`datacenter/fat_tree_switch.cpp:67-68`)
  - the ToR "directly connected, else route up" decision (`:419-427`)
  - agg down-routing (`:464-467, 510`)

  So a rail map `h -> h mod 8` cannot be introduced without editing all of
  these.

**What does work is a bijection between the two labellings.** The FatTree is a
graph where host h hangs off leaf `h / R`. A rail-optimized cluster of N nodes
x 8 GPUs is a graph where GPU (n, i) hangs off rail switch i. If R = N, the map
`phi(n, i) = R*i + n` takes rail i to leaf i:
`(R*i + n) / R = i`.

- **2 tiers: exact isomorphism.** Spine edges are "every leaf to every spine" in
  both graphs, so they map identically. Speeds, latencies, ECMP fan-out and
  hashing points are all preserved. For 16 nodes this is `rail_128_1os.topo`:
  - 8 leaves of 16 hosts plus 16 spines, 100 Gb/s, 1:1
  - the log confirms "ToR switches per pod: 8, Agg switches per pod: 16"

  The rail-labelled matrix is `workloads/kv128_rail.cm`
  (`16i -> 16i+8` = node 0 GPU i -> node 8 GPU i). The conventional-labelled
  matrix on the same graph is `workloads/kv128_conv.cm` (`i -> 64+i`). There,
  node n's 8 GPUs share leaf n/2, as in a ToR-per-rack cluster.
- **3-tier ft128 (4-host ToRs): isomorphic only to a different rail design.**
  - **Stripe labelling** (`gen_workload.py --placement rail`, the default
    `--rail-domain stripe`, `scripts/gen_workload.py:248-258`) gives
    `host = 16*i + n`. That puts rail i in pod i, not in ToR i. So
    `kv128_rail.cm` on ft128 is a fabric where each rail is a small 2-level Clos
    (4 ToRs plus 4 aggs), and rails meet at the cores. Matched-index traffic
    from node 0 to node 8 goes ToR 4i -> agg -> ToR 4i+2. It is rail-local
    (never reaches a core) but not leaf-local.
  - **`--rail-domain tor`** gives `host = 4*i + n` for 4 nodes. ToR i is then
    rail i (`workloads/rail/ft128_tp8_rail_tor.cm`, `4i -> 4i+2`). But rails
    0-3 share pod 0's aggs, so above the leaf this is not a rail-optimized spine
    (in Wang et al., rails meet only at the spine).
  - For rail-local traffic, neither difference matters.
- **What no labelling captures:**
  1. **NVLink.** HTSIM hosts are independent NICs, and nothing joins the 8 GPUs
     of a node. A rail-only fabric is two overlapping groupings (node via
     NVLink, rail via rail switch), which is not a tree.
  2. **Removing the spine.** `radix_up` must be > 0 below the top tier
     (`fat_tree_topology.cpp:276-278`), and the ToR sends every non-local
     destination up (`fat_tree_switch.cpp:427`). So rail-only cross-rail
     traffic, which in reality is relayed over NVLink, cannot be simulated.
     `rail_128_16os.topo`, with one spine and 16:1, is the closest stand-in. It
     still lets cross-rail traffic through at 1/16 capacity, instead of
     refusing it.

## 2. Does generic_topology.cpp read an explicit graph, and does any binary use it?

**It parses one, but cannot route it, and no binary we build uses it.**

- **The parser.** `GenericTopology::load` (`datacenter/generic_topology.cpp:18-29, 408-458`)
  reads `Hosts N / Switches N / Links N`, then `host`, `switch`, `queue` and
  `pipe` lines with `dst`/`src`/`reverse` attributes. So it is an explicit link
  list.
- **It cannot route.**
  - `get_bidir_paths` returns an empty vector, marked `// TBD` (`:461-465`), so
    no source route exists.
  - `get_neighbours` returns NULL (`:467-469`).
  - Its switches are the base `Switch`, whose `receivePacket` and `getNextHop`
    are `abort()` (`switch.h:43-49`), so ECMP_FIB forwarding is impossible too.
  - Queue types are `queue|random|composite|fairscheduler` only
    (`generic_topology.cpp:270-279`). RoCE needs lossless queues (PFC), and
    those are missing.
- **No binary instantiates it.**
  - It is compiled into `libhtsim_dc` (`datacenter/CMakeLists.txt:18`).
  - The only instantiation is `main_swift.cpp:210-215`, under
    `#ifdef GENERIC_TOPO`. That macro comes from `generic_topology.h:3`, whose
    include is commented out (`main_swift.cpp:25`), so the branch is dead code.
    `main_swift` uses the FatTree (`:176-187`, and `fat_tree_topology.h:3`
    defines `FAT_TREE`).
  - `htsim_roce` (`main_roce.cpp:212-214, 434-455`), `htsim_eqds`
    (`main_eqds.cpp:233-235, 508-509`), `htsim_hpcc` (`main_hpcc.cpp:162-164, 383-386`)
    and `htsim_uec` (`main_uec.cpp:329-331, 675-676`) all handle `-topo` by
    calling `FatTreeTopologyCfg::load`.
  - There is no `-topology_file` / `-generic` flag anywhere. The UEC binary
    does not support a generic graph either.

## 3. Relabelling: what is equivalent, what is not

Equivalent, and simulated below:

- **Matched-index TP KV transfer** (prefill GPU i -> decode GPU i) on a
  rail-optimized fabric. On the 2-tier rail graph this is exact (section 1).
  On ft128 it holds for a rail = pod (`railpod`) or a rail = ToR (`railtor`)
  design.
- **The same transfer on rail-only.** Rail-local flows never use the spine, so
  removing the spine cannot change them. The `rail_128_16os` runs show this
  empirically.
- **Matched-index incast** (K prefill instances -> 1 decode instance, every
  shard rail-local) on either design.

Not captured:

- **Non-matched-index traffic on rail-only.** Examples are prefill TP=8 ->
  decode TP=4 resharding, or any GPU i -> GPU j != i. Rail-only must relay it
  over NVLink, which is not modelled, and the stand-in instead sends it over a
  thin spine.
- **NVLink contention**, for example when NVLink relays coexist with
  tensor-parallel all-reduce.
- **Multi-leaf rails at the 2-tier level.** A rail bigger than one leaf's radix
  needs the 3-tier "rail = pod" form, whose spine wiring is a FatTree core, not
  a rail-optimized spine.

## 4. Comparison

**Setup.**

- RoCE without CC, `HTSIM_BUILD=build_start`, `-strat ecmp_host`, seeds 1-5.
- One Llama-3-70B 4K TP=8 transfer: 8 x 167,772,160 B, all at t=0, 100 Gb/s,
  floor 13,421.8 us. Incast is four such transfers into one decode node, 32
  flows.
- Every topology is passed by file name, so run.sh uses `-q 2000` for all of
  them (matched buffers), with `-end 200000`.
- Reproduce with `./scripts/rail.sh`. Results are in `results/rail/`;
  `summary.csv` has one row per run.

**Validity.** 75 RoCE runs (300 with the other three policies, section 4.1),
**0 with lossless violations, 0 with unfinished flows**.
Cross-check: `fat_tree_128_1os conv` gives 2.03/2.03/3.05/2.03/3.05. That is
identical to E1 section 4.3 (other build, `-q 500`), so the buffer change does
not move RoCE.

Slowest shard, x floor. The class column lists per-flow multiples of the
uncontended 1.016x, as `k x: number of flows`.

| topology | variant | flows src->dst | s1 | s2 | s3 | s4 | s5 | per-flow classes (s1; s2; s3; s4; s5) |
|---|---|---|---|---|---|---|---|---|
| ft128 1:1 | conv (cross-pod) | i -> 64+i | 2.03 | 2.03 | 3.05 | 2.03 | 3.05 | 1x:6 2x:2; 1x:2 2x:6; 1x:1 2x:4 3x:3; 1x:4 2x:4; 1x:3 2x:2 3x:3 |
| ft128 1:1 | samepod (one pod, 2 ToRs -> 2 ToRs) | i -> 8+i | 2.03 | 2.03 | 3.05 | 2.03 | 3.05 | 1x:6 2x:2; 1x:2 2x:6; 1x:3 2x:2 3x:3; 1x:4 2x:4; 1x:3 2x:2 3x:3 |
| ft128 1:1 | railpod (`kv128_rail.cm`, rail = pod) | 16i -> 16i+8 | 1.02 | 1.02 | 1.02 | 1.02 | 1.02 | 1x:8 all seeds |
| ft128 1:1 | railtor (rail = ToR) | 4i -> 4i+2 | 1.02 | 1.02 | 1.02 | 1.02 | 1.02 | 1x:8 all seeds |
| ft128 4:1 | conv | i -> 64+i | 3.05 | 3.05 | 3.05 | 5.08 | 4.07 | 2x:4 3x:4; 1x:1 2x:5 3x:2; 1x:1 2x:4 3x:3; 1x:3 4x:2 5x:3; 2x:4 4x:4 |
| ft128 4:1 | samepod | i -> 8+i | 2.03 | 2.03 | 3.05 | 3.05 | 2.03 | 1x:2 2x:6; 1x:4 2x:4; 1x:3 2x:2 3x:3; 1x:3 2x:2 3x:3; 2x:8 |
| ft128 4:1 | railpod | 16i -> 16i+8 | 1.02 | 1.02 | 1.02 | 1.02 | 1.02 | 1x:8 all seeds |
| ft128 4:1 | railtor | 4i -> 4i+2 | 1.02 | 1.02 | 1.02 | 1.02 | 1.02 | 1x:8 all seeds |
| **rail 2-tier 1:1** | conv labelling | i -> 64+i (leaf 0 -> leaf 4) | 2.03 | 2.03 | 1.02 | 2.03 | 2.03 | 1x:6 2x:2; 1x:4 2x:4; 1x:8; 1x:6 2x:2; 1x:2 2x:6 |
| **rail 2-tier 1:1** | rail labelling | 16i -> 16i+8 (rail-local) | 1.02 | 1.02 | 1.02 | 1.02 | 1.02 | 1x:8 all seeds |
| **rail 2-tier 1:1** | incast 4->1, conv labelling | nodes 0-3 -> node 8 | 5.19 | 5.62 | 5.36 | 6.01 | 6.34 | spread 2x-6x |
| **rail 2-tier 1:1** | incast 4->1, rail labelling | 16i+k -> 16i+15 | 4.06 | 4.06 | 4.06 | 4.06 | 4.06 | 4x:32 all seeds |
| rail 2-tier 16:1 (rail-only stand-in) | conv labelling | i -> 64+i | 8.13 | 8.13 | 8.13 | 8.13 | 8.13 | 8x:8 (one uplink) |
| rail 2-tier 16:1 | rail labelling | 16i -> 16i+8 | 1.02 | 1.02 | 1.02 | 1.02 | 1.02 | 1x:8 all seeds |
| rail 2-tier 16:1 | incast 4->1, rail labelling | 16i+k -> 16i+15 | 4.06 | 4.06 | 4.06 | 4.06 | 4.06 | 4x:32 all seeds |

### 4.1 All four policies

Same matrices, seeds 1-5 and buffers, each policy from its final build
(`POLICIES="roce dcqcn hpcc eqds" ./scripts/rail.sh`; per-policy build dirs
are rail.sh's defaults). HPCC's base RTT is set per fabric: `-hpcc_T 16.4` on
the 3-tier fat trees (12 us propagation + 6 x 0.725 us serialisation of
9,064 B at 100 Gb/s) and 10.9 on the 2-tier rail fabrics (8 + 4 x 0.725).
DCQCN uses run.sh's RED marking (Kmin 12 KB, Kmax 400 KB, Pmax 1 % at 100 G).
**300 runs, 0 with lossless violations, 0 with unfinished flows.**
The 75 DCQCN runs were rerun on the binary with the minimum-rate clamp
(`HTSIM/OUR_CHANGES.md`, "DCQCN: minimum rate clamp"): 68 are bit-identical;
16:1 conventional (all 5 seeds, 8.59-8.86 → 8.71-8.77, median 8.621 → 8.727)
and the 1:1 conventional incast seeds 4 and 5 (5.693 → 5.701, 5.745 → 5.682)
moved. Pre-clamp runs: `results/rail/dcqcn_preclamp/`.

Median slowest shard over seeds 1-5, x floor, [range] where the seeds differ.
The last two columns are paired ratios against RoCE at the same seed (same
ECMP placement for RoCE, DCQCN and HPCC).

| topology | variant | RoCE | DCQCN | HPCC | EQDS | DCQCN / RoCE, s1-s5 | HPCC / RoCE, s1-s5 |
|---|---|---|---|---|---|---|---|
| ft128 1:1 | conv | 2.033 [2.03-3.05] | 2.856 [2.43-3.70] | 2.706 [2.19-3.37] | 1.166 [1.15-1.17] | 1.20 1.40 1.21 1.30 1.18 | 1.08 1.33 1.10 1.33 1.08 |
| ft128 1:1 | samepod | 2.033 [2.03-3.05] | 2.737 [2.40-3.77] | 2.140 [2.14-3.23] | 1.166 [1.15-1.17] | 1.18 1.35 1.18 1.29 1.24 | 1.05 1.05 1.06 1.05 1.06 |
| ft128 1:1 | railpod | 1.017 | 1.017 | 1.055 | 1.031 | 1.00 (all) | 1.04 (all) |
| ft128 1:1 | railtor | 1.016 | 1.016 | 1.055 | 1.031 | 1.00 (all) | 1.04 (all) |
| ft128 4:1 | conv | 3.049 [3.05-5.08] | 3.684 [3.64-5.68] | 3.294 [3.27-5.47] | 2.089 [2.07-2.19] | 1.19 1.21 1.20 1.12 1.14 | 1.07 1.08 1.08 1.08 1.08 |
| ft128 4:1 | samepod | 2.033 [2.03-3.05] | 2.718 [2.61-3.69] | 2.141 [2.14-3.23] | 1.149 [1.09-1.21] | 1.34 1.30 1.18 1.21 1.28 | 1.05 1.05 1.06 1.06 1.05 |
| ft128 4:1 | railpod | 1.017 | 1.017 | 1.055 | 1.031 | 1.00 (all) | 1.04 (all) |
| ft128 4:1 | railtor | 1.016 | 1.016 | 1.055 | 1.031 | 1.00 (all) | 1.04 (all) |
| **rail 2-tier 1:1** | conv labelling | 2.033 [1.02-2.03] | 2.604 [1.02-2.75] | 2.237 [1.08-2.24] | **1.031** | 1.18 1.30 1.00 1.28 1.36 | 1.10 1.10 1.06 1.10 1.10 |
| **rail 2-tier 1:1** | rail labelling | 1.016 | 1.016 | 1.055 | 1.031 | 1.00 (all) | 1.04 (all) |
| **rail 2-tier 1:1** | incast 4->1, conv | 5.620 [5.19-6.34] | 5.701 [4.84-5.78] | 5.910 [5.47-6.26] | **4.120** | 0.95 0.86 1.08 0.95 0.90 | 1.05 0.99 1.10 1.04 0.98 |
| **rail 2-tier 1:1** | incast 4->1, rail | 4.064 | 4.769 [4.67-4.95] | 4.415 | 4.121 | 1.18 1.15 1.22 1.17 1.17 | 1.09 (all) |
| rail 2-tier 16:1 | conv labelling | 8.129 | 8.727 [8.71-8.77] | 9.091 | 8.748 [8.74-8.75] | 1.07 1.08 1.07 1.08 1.07 | 1.12 (all) |
| rail 2-tier 16:1 | rail labelling | 1.016 | 1.016 | 1.055 | 1.031 | 1.00 (all) | 1.04 (all) |
| rail 2-tier 16:1 | incast 4->1, rail | 4.064 | 4.769 [4.67-4.95] | 4.415 | 4.121 | 1.18 1.15 1.22 1.17 1.17 | 1.09 (all) |

Per-flow classes per seed for every policy are in `results/rail/summary.csv`
(`./scripts/rail.sh summary` prints the full per-seed table). The fat-tree
rows are the same runs' configurations as docs/E1.md's ft128 TP=8 cells and
agree with them to the 0.001 (same builds, seeds and buffers).

## 5. Why

**Rail placement removes the hash lottery, independent of CC.** Under
`ecmp_host`, each RoCE flow fixes one path id (`roce.cpp:66`). Every switch
picks an uplink with `freeBSDHash(flow_id, pathid, salt) % n`
(`datacenter/fat_tree_switch.cpp:339`), with salt drawn at `:18`.

E1 section 4.2 showed that RoCE's slowest shard is exactly L_max x T, where
L_max is the number of flows on the most-loaded hashed link. The conventional
rows show the same lottery here:

- **ft128 1:1:** L_max is 2 or 3.
- **ft128 4:1:** L_max is 3-5. Only 4 core paths leave the pod, so 8 flows
  cannot do better than 2.
- **2-tier graph, conventional labelling:** L_max is 1-2. With 8 flows over 16
  leaf uplinks, the birthday odds are P(L_max=1) = 0.12, P(2) = 0.71 and
  P(3) = 0.15. Seed 3 drew the 12% case.

A rail-local flow avoids the lottery for one of two reasons:

- **It never reaches a hashed link.** A ToR whose destination is attached
  delivers it directly (`fat_tree_switch.cpp:419-424`). This covers `railtor`
  and the 2-tier `rail` rows.
- **It does hash, but nothing shares its links.** In `railpod` each flow is
  alone in its own pod.

Either way L_max = 1, and the result is 1.016x (13,641 us) on every seed and
every oversubscription, including 16:1. That is the whole case for
rail-optimized TP fabrics, and it holds for matched-index KV transfer.

Congestion control plays no part. There is nothing to control: every link
carries at most one flow. Rail placement fixes what E1 showed CC cannot fix.
Only EQDS-style packet spraying gets close on a FatTree (1.15-1.17x on
ft128, E1 section 6.3).

**Same pod is not enough.** `samepod` (i -> 8+i) has the same 2x/3x classes as
cross-pod. Its 4 flows per source ToR still hash over 4 ToR uplinks. What saves
`kv128_rail.cm` on ft128 is that it spreads the flows one per pod, not that
they stay inside a pod. A rail placement must make each rail switch carry
**one flow per egress port**, not merely keep flows below the core.

**The spine is irrelevant for matched-index traffic.** `rail_128_16os` has
1/16 of the spine capacity, yet gives identical rail-labelled results, 1.02x
and 4.06x. That is the empirical half of Wang et al.'s rail-only argument: for
traffic that stays on its rail, the spine can be deleted. The same graph with
conventional labelling collapses to 8.13x, because all 8 flows share leaf 0's
single uplink.

**Incast is not fixed by rails.** With four prefill instances sending to one
decode instance under rail labelling, each decode GPU i receives 4 shards on
rail switch i's single downlink to it. The result is exactly 4 x 1.016 = 4.06x
on every seed and on both spine variants. That is the receiver-NIC drain floor
(53,687 us for the request set), so no topology or placement can beat it. The
only levers are fewer senders per decode instance (admission or scheduling) or
a bigger decode NIC.

Under conventional labelling the same incast is worse, 5.2-6.3x. Above the 4x
receiver floor, some link other than the receiver NIC must bind part of the
time. The candidates are:

- **The spine-to-leaf-4 downlinks.** 32 flows hash over 16 of them, so a
  downlink with more than 4 flows is slower than the receiver's 25 Gb/s share.
- **Port-fair PFC coupling** between flows that share an ingress (E1 section
  4.2).

This was not separated with a `queue_usage` trace (see next steps). Either way,
rail labelling removes it, because every shard of the incast stays on its rail
switch.

### Why each policy lands where it does (section 4.1)

**Rail-local rows: every policy at its own single-flow cost.** RoCE and
DCQCN 1.016, EQDS 1.031, HPCC 1.055 on every seed, every oversubscription and
both spine variants. Those are exactly the uncontended costs docs/E1.md
section 3 derives: headers for RoCE; DCQCN never sees a queue above Kmin, so
it never marks; EQDS pays headers plus the 1.47 % credit shortfall of its pull
pacer; HPCC runs at U = η/(1 - W_AI/W) = 0.955 of line by design. With one
flow per link there is nothing for any controller to do, so the topology
result is policy-independent up to that fixed cost.

**EQDS: spraying replaces the rail.** Under conventional labelling on the
2-tier rail fabric, EQDS gives **1.031 on every seed**, the rail-local value,
with 0 retransmissions. Each flow sprays its packets over 16 entropies, which
spread over all 16 spines, so no leaf uplink carries more than its share. On
ft128 the same spraying reaches only 1.15-1.17 (hash imbalance over the source
ToR's 4 uplinks, docs/E1.md section 6.3). With 16 spines the imbalance of
16 entropies is diluted enough that the leaf-0 uplinks never bind. In the
conventional incast EQDS gives 4.120, the receiver-drain floor
(4 x 1.030), while RoCE pays 5.2-6.3: spraying removes the spine-downlink
collisions, and EQDS's receiver-driven credit keeps the incast itself loss
free (0 retransmissions). EQDS **loses** where one link is forced:

- 16:1 conventional: 8 flows through leaf 0's single uplink. The bound is
  8 x 1.0157 = 8.125 (EQDS headers only). EQDS trims 1,630,803 packets at
  seed 1, five per data packet, and every trimmed header still crosses the
  bottleneck: 8.125 x (1 + 1,630,803 x 64 B / 1,363.3 MB) = 8.748, exactly
  the measured 8.748. RoCE pauses instead of trimming and gets 8.129.
- Rail incast: 4.121 = 4 x 1.030, the credit pacer's cost on a receiver-
  bound transfer, against RoCE's 4 x 1.016.

**HPCC: η plus W_AI, and an unfair incast.** On the hashed rows HPCC is
1.05-1.10 x RoCE per seed, the η cost per flow (docs/E1.md section 7), and 1.33
on the ft128 conventional seeds 2 and 4 where flows form a chain of shared
links and HPCC's max-U rule gives a Pareto-optimal rather than max-min split
(E1 section 7). The rail incast is HPCC's worst case: 4.415 on every seed
against a 4 x 1.060 = 4.24 η bound. Its per-flow classes (2x:8 3x:8 4x:16) show
the 4 flows into each decode GPU finishing at about 2.0, 3.2, 4.0 and 4.4 x
floor: deeply unfair, and the link is under-used while the unfair split
persists. Controls on the same matrix (`results/e1/diag_final/hpcc_railincast_*`):

| variant | W_AI | slowest shard | per-flow finish |
|---|---|---|---|
| default: 9 KB, T 10.9 us, N 10 | 681 B | 4.415 | 2.02 / 3.18 / 3.96 / 4.42 |
| 1 KB MTU | 681 B | 4.42 | fair, all at 59.1-59.3 ms |
| T 21.8 us | 1,363 B | 4.27 | |
| N = 5 | 1,363 B | 4.29 | |
| N = 2 | 3,406 B | 4.21 | |

Packet size changes the fairness but not the slowest shard. The shortfall
below η shrinks as W_AI grows, by T or by N alike, so it is tied to the
additive step being small (681 B is under one 9 KB packet) and not to packet
granularity. The exact mechanism was not traced. Under 16:1 conventional
(8 flows on one uplink) HPCC gives 9.091 = 8 x 1.136, also with an unfair
split (8x:6 9x:2).

**DCQCN: startup collapse, slow recovery.** Wherever two or more flows share a
link, DCQCN is 1.07-1.40 x RoCE at the same seed, and 1.15-1.22 x on the rail
incast. The mechanism is docs/E1.md section 5.2: all colliding flows start at
line rate, cross Kmax together, and all get a CNP in the same 50 us window,
again and again, so each is cut to 12.5 % of line or less in the first
150 us (traced on ft128 conventional, seed 1);
the paper's R_AI (0.1 % of line per 55 us) then needs tens of ms to recover,
per-flow α stays high so later CNPs cut deep, and at these speeds RED's
rate-proportional marking is mostly bypassed by forced marks above Kmax
(`HTSIM/OUR_CHANGES.md`, "DCQCN made faithful to the paper" and "DCQCN switch
marking: probabilistic (RED) ECN"). The rail incast is the fair-share case of
the same story: 4 line-rate flows into one 100 G NIC.

**The one place DCQCN beats RoCE: the conventional incast.** 0.86-0.95 x
RoCE on 4 of 5 seeds (1.08 on seed 3). RoCE is 5.2-6.3 there, far above the
4.06 receiver floor, so some links other than the receiver NICs bind part of
the time (section 5 above). That is the setting congestion control is built
for: with PFC alone a paused ingress carries flows to several destinations,
so a congested receiver also stalls flows headed for uncongested ones
(head-of-line blocking). DCQCN cuts the senders at the congestion point
instead, so fewer pauses propagate. This reading is consistent with the
numbers but was not traced (next steps, item 4). HPCC does not get the same
benefit (0.98-1.10 x RoCE).

## 6. What to do next

1. **Use the 2-tier rail graph as the topology axis's "rail optimized" entry.**
   Use `rail_128_1os` / `rail_128_16os` with rail-labelled matrices
   (`--topo rail_128_1os --placement rail`, whose default stripe = 16 hosts =
   one leaf). The conventional baseline on the same hardware is the same
   `.topo` with `--placement split`. That keeps switch count and cabling
   identical and changes only GPU-to-port wiring.
2. **Done: EQDS, HPCC and DCQCN on these matrices** (section 4.1). For
   rail-local traffic every policy collapses to its single-flow cost
   (1.016-1.055). On the incast rows RoCE sits on the 4.06 floor under rail
   labelling, EQDS 1.4 % above it, HPCC 8.6 % and DCQCN 17 %.
3. **Run E2/E3-style load on the rail graph.** Under Poisson arrivals,
   different requests share a rail, so collisions between requests return
   (several prefill GPU i's -> several decode GPU i's on one rail switch). That
   rail switch is a single non-blocking crossbar, so only same-destination
   incast should remain.
4. **Trace the conventional incast** with `-log queue_usage`, using the
   `e1.sh trace` method, to separate spine-downlink collisions from PFC
   coupling in the 5.2-6.3x. It also explains why DCQCN beats RoCE there
   (section 5, last paragraph), the only cell in this study where it does.
5. **Trace HPCC's rail incast** (`-hpcc_trace`) to find why the receiver
   link runs below η when W_AI is under one packet.
6. **Requirements for a true rail-only run (TODO, needs C++).** It cannot be
   expressed today. It would need either:
   - (a) FatTree changes: allow `Radix_Up 0` at tier 0
     (`fat_tree_topology.cpp:276`), and make the ToR drop or reject non-local
     destinations instead of routing up (`fat_tree_switch.cpp:427`); or
   - (b) a real GenericTopology, which needs all of:
     - `get_bidir_paths`, for example BFS k-shortest paths over the parsed
       graph (`generic_topology.cpp:461`), so `htsim_roce` can run a
       source-routed `-strat single|perm` (`main_roce.cpp:533-543`)
     - lossless queue types in `parse_queue` (`generic_topology.cpp:270`), for
       PFC
     - host ports wired to RoCE sources and sinks (the `Host` class aborts on
       `receivePacket`, `generic_topology.h:26`)
     - a `-generic <file>` branch in `main_roce.cpp` next to `:434`
     - per-node "NVLink switch" vertices of 8 GPUs at about 900 GB/s, so
       cross-rail traffic can be relayed GPU j -> NVLink -> GPU i -> rail i

   Only (b) answers the question rail-only actually raises, which is
   non-matched-index traffic such as TP resharding between prefill and decode.
   Out of scope until someone needs that workload.

## Files

- `HTSIM/htsim/sim/datacenter/topologies/rail_128_1os.topo`,
  `rail_128_16os.topo`: new, the only HTSIM-tree additions
- `workloads/rail/ft128_tp8_rail_tor.cm` (+ `.json`): rail = ToR on ft128
- `workloads/rail/rail128_incast4_rail.cm`, `rail128_incast4_split.cm` (+ `.json`):
  4-to-1 incast, rail and conventional labelling
- existing matrices reused: `workloads/kv128_conv.cm`, `workloads/kv128_rail.cm`,
  `workloads/e1/ft128_tp8_samepod.cm`
- `scripts/rail.sh`: regenerates missing matrices, runs missing jobs (`J`
  in parallel, default 4, resumable), writes `results/rail/summary.csv` and
  prints the table. `POLICIES="roce dcqcn hpcc eqds"` selects policies
  (default roce); `HTSIM_BUILD_<cc>` or `HTSIM_BUILD` override the per-policy
  build dirs (defaults roce=build_start, dcqcn=build, hpcc=build_hpccfix,
  eqds=build_eqds2); `HPCC_T` overrides the per-fabric base RTT
- `results/rail/`: 300 runs (`<cc>_<topo>_<variant>_s<seed>.*`, 75 per
  policy), `summary.csv` (cc, seed, slowest x floor, L_max, retransmissions,
  per-flow classes), `run_output.txt`
- `results/e1/diag_final/hpcc_railincast_*`: HPCC W_AI controls on the rail
  incast
