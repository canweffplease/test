# Traffic matrices

HTSIM connection-matrix format, one line per flow:

    <src>-><dst> id <N> start <picoseconds> size <bytes>

`start` is in picoseconds. All four transports (RoCE, DCQCN, HPCC, EQDS)
honor it since commit 2521a60; before that, RoCE, DCQCN and EQDS read it as
microseconds and HPCC ignored it. parse.py refuses a run whose logged start
times disagree with the matrix.

An optional `prio <N>` sets the flow's scheduling priority. **Lower is more
urgent.** It only has an effect in `htsim_eqds`. There, priority is strict
among flows into the same receiver (commit a4e533e): the receiver gives
every new-data credit to its most urgent flow until all of that flow's
remaining bytes are credited, with round robin among equal priorities. Not
prioritized: retransmission credit (served first), each flow's speculative
first window, and switch queues (FIFO). Priority is static per flow, so
`prio = size` is shortest-flow-first (not remaining-size SRPT), and
`prio = deadline` is EDF. The value is a 32-bit int, so scale anything
above 2.1e9 (use KB or us). `htsim_roce` and `htsim_hpcc` ignore it. The
default is 2000000. See HTSIM/OUR_CHANGES.md, "Transport scheduling" and
"EQDS pull pacer", and `test_prio/`.

## kv_llama70b_4k_tp8.cm

One Llama-3-70B KV cache for a 4096-token prompt, sharded at tensor-parallel
degree 8 into eight concurrent flows between matched GPU index pairs.

    bytes/token = 2 * 80 layers * 8 kv_heads * 128 head_dim * 2 bytes = 327,680
    4K cache    = 4096 * 327,680 = 1,342,177,280 bytes (1.34 GB)
    per shard   = 167,772,160 bytes

Theoretical floor per shard at 400 Gbps = 167772160*8/400e9 = 3,355 us.

## kv_incast_8to1.cm

Same bytes, but all eight shards converge on node 8. Bandwidth-bound: the
whole 1.34 GB crosses one 400 Gbps downlink, so the floor is 26,843 us no
matter what the congestion control does.

## test_prio/

Priority smoke test for `htsim_eqds`: 8:1 incast into node 8 on the default
tree, four 41.9 MB flows (even sources) and four 167.8 MB flows (odd).
`incast_mixed_noprio.cm` has no `prio`, `incast_mixed_prio_size.cm` sets
prio = size (shortest first), `incast_mixed_prio_inverse.cm` makes the large
flows urgent. With strict priority the small flows finish at ~6.9 ms,
~3.4 ms and ~17.3 ms respectively, in `results/test_prio_fixed/`. The
earlier soft-priority build gave ~3.9 ms for prio = size, in
`results/test_prio/`.

## Caution

The size field is a 32-bit int, so a single flow cannot exceed ~2.1 GB.
At TP=8 that caps you around a 50K-token context on Llama-3-70B.

---

# Generated matrices (`scripts/gen_workload.py`)

The hand-written matrices above carry eight concurrent flows. That is not
enough offered load to study the topology axis. (1:1 and 4:1 gave identical
times at seed 13, but that was a collision coincidence; see the Correction in
HTSIM/OUR_CHANGES.md, "Oversubscribed fat trees".) The generator produces
Poisson (or trace-driven) request streams at a stated fraction of a stated
bottleneck capacity.

Every `<name>.cm` has a sidecar `<name>.json` with `meta` (the exact command,
topology, bottleneck, rate, realized load), `requests` (one entry per KV
handoff: prefill/decode hosts, bytes, deadline) and `flows`, one entry per line
of the `.cm`:

    flow_id request_id shard src dst bytes start_ps prompt_tokens
    tier_crossed floor_ps deadline_ps pair_seq [prio]

- `floor_ps` = bytes x 8 / (slowest line rate on the flow's path), the
  uncontended floor parse.py already uses.
- `deadline_ps` = `start_ps + deadline_slack x floor_ps` (slack defaults to 2.0).
  A request's deadline is the max over its shards.
- `pair_seq` is the flow's position among flows sharing the same src->dst
  pair, in start order. HTSIM logs name flows `Roce_<src>_<dst>`,
  `HPCC_<src>_<dst>`, `Eqds_<src>_<dst>`. EQDS and (since 2521a60) HPCC also
  print the matrix `flowId`, which parse.py uses. RoCE/DCQCN are matched by
  object id in matrix order. (src, dst, pair_seq) is the fallback when no
  id is printed.

## Model

    bytes_per_token = 2 * layers * kv_heads * head_dim * dtype_bytes
    llama3-8b   (32, 8, 128)  131,072 B/token   4K prompt 0.537 GB
    llama3-70b  (80, 8, 128)  327,680 B/token   4K prompt 1.342 GB
    llama2-13b  (40,40, 128)  819,200 B/token   4K prompt 3.355 GB

A request of P tokens at TP degree T is T concurrent flows of
`ceil(P * bpt / T)` bytes, prefill GPU i -> decode GPU i. One HTSIM node is
one GPU/NIC, so a TP=8 instance occupies 8 hosts.

## Topology model

The generator mirrors `fat_tree_topology.cpp`: host h is on ToR
`h / hosts_per_tor` and in pod `h / hosts_per_pod`.

| topology | hosts/ToR | hosts/pod | pods | host link | ToR uplinks | pod -> core |
|---|---|---|---|---|---|---|
| `default` (auto, K=4, 16 nodes) | 2 | 4 | 4 | 400 G | 2 x 400 = 800 G | 4 x 400 = 1,600 G |
| `ft128_1os` | 4 | 16 | 8 | 100 G | 4 x 100 = 400 G | 16 x 100 = 1,600 G |
| `ft128_4os` | 4 | 16 | 8 | 100 G | 4 x 100 = 400 G | 4 x 100 = 400 G |
| `ft128_8os` | 4 | 16 | 8 | 100 G | 1 x 100 = 100 G | 2 x 100 = 200 G |

The default 16-node tree has **2** hosts per ToR, not 4 (K/2 with K=4), and
pods of 4 hosts. A TP=8 instance there spans two whole pods. The 4:1 file puts
its oversubscription at agg->core (`Tier 1 Oversubscribed 4`, `Radix_Up 1`).
The 8:1 file splits it, 4:1 at ToR and 2:1 at agg.

## Bottleneck definition (what `--load` is a fraction of)

The generator computes B, the largest aggregate throughput the placement can
carry before its first resource saturates. It assumes perfect load balancing
inside each link group.

    B = min over resources r of  capacity(r) / share(r)

`share(r)` is the fraction of all simulated bytes that must cross r, given
uniform choice of prefill instance and uniform choice among that instance's
allowed decode partners. The resources are every host NIC (up and down), every
ToR's uplink group, and every pod's agg->core uplink group, each in both
directions. In the common cases:

- **cross-pod pool to pool**: the uplink capacity out of the sending pods, or
  the sending NICs if those are smaller.
- **same-ToR**: the host link.
- **incast**: the receiver's downlink.

`--load L` sets the Poisson rate to `L x B / (8 x mean request bytes)`. The
generator prints the per-resource table and marks the binding row. Pass
`--bottleneck-gbps X` to state load against any other capacity, and
`--also-topo a,b` to see what the same matrix means on other fabrics.

Two caveats:

- **This is an idealized upper bound.** ECMP hash collisions saturate
  individual links before the aggregate, so measured knee points will sit
  below L = 1.
- **`--fix-count` is used for every E3 and E2 matrix.** With only 9-27
  requests per run, a raw Poisson count would swing the realized load by
  +/-30%. With the flag, the generator draws exactly `round(rate x window)`
  arrivals, i.i.d. uniform in the window. That is a Poisson process
  conditioned on its count. Because the same seed is used throughout, each
  load level's arrivals are a superset of the lower level's: 50% contains all
  of 30%'s requests plus more. That gives common random numbers across the
  load axis.

## Synopsis

    scripts/gen_workload.py --out workloads/NAME.cm
      [--topo default|ft16|ft128_1os|ft128_4os|ft128_8os|<file.topo>] [--nodes N]
      [--link-gbps 400] [--also-topo T1,T2]
      [--model llama3-8b|llama3-70b|llama2-13b] [--bytes-per-token B] [--dtype-bytes 2]
      [--tp T] [--shards N] [--split-oversize]
      [--pattern tp|incast|permutation|same_tor|same_pod|cross_pod]
      [--placement split|interleaved|rail] [--rail-domain stripe|tor|pod]
      [--incast K] [--prefill-nodes 0-63] [--decode-nodes 64-127]
      [--prefill-instances P] [--decode-instances D] [--assign rr|random]
      [--arrival poisson|fixed|trace] [--rate R | --load L] [--bottleneck-gbps X]
      [--duration-ms 50] [--fix-count] [--requests N] [--interval-us U]
      [--prompt fixed:4096|lognormal:mu,sigma|trace] [--max-prompt N]
      [--trace file.csv] [--trace-skip-s S] [--time-scale X]
      [--deadline-slack 2.0] [--prio none|srpt|edf] [--seed 1]

**Placements**

- `split` (the default): the first half of the instance slots are prefill and
  the second half decode. On both fat trees that makes the pools cross-pod.
- `interleaved`: alternates prefill and decode slots, so every pod holds both.
- `rail`: puts GPU i of every instance in rail domain i. The default domain is
  a stripe of nodes/T hosts, which is a pod on ft128 (matching
  `kv128_rail.cm`) and a ToR on the 16-node tree. `--rail-domain tor|pod`
  forces one or the other.

**Patterns**

- `same_tor`, `same_pod`, `cross_pod`: restrict decode partners so that every
  shard crosses exactly that tier. The generator errors out if the placement
  cannot do that. For example, TP=8 cannot be same-ToR on 4-host ToRs.
- `permutation`: a random derangement of instances.
- `incast`: the first K slots all send to one decode instance, the one
  containing host N/2.

**Options**

- `--shards N`: simulates only GPU indices 0..N-1 of each request, a rail
  slice. Flows keep the per-shard size.
- `--prio srpt`: writes `prio <bytes>`. `--prio edf` writes
  `prio <deadline in us>`. Only `htsim_eqds` acts on it (strict receiver
  priority, see the top of this file). RoCE, DCQCN and HPCC ignore it.

**Validation (run on every write)**

- `Nodes` matches the topology.
- Every node id is in range and src != dst.
- Sizes are in 1..2^31-1. Oversize shards are an error unless
  `--split-oversize` is given.
- Ids are unique and nonzero.
- `Connections` equals the number of flow lines.

Starts are rounded to 1 ns. HTSIM reads `start` with `stof` (float32), so
starts near 100 ms are quantized to about 8 ns, which is negligible.

## Checked-in generated matrices

All use Llama-3-70B, fixed 4096-token prompts (1.342 GB per request) and
TP=8, so every flow is 167,772,160 B. The cost column scales the ~70 s
legacy DCQCN reference run (one 1.34 GB request) linearly by total bytes. It
is out of date: legacy DCQCN ran until `-end`, and the current DCQCN stops
when its flows finish. Measured on the e3 ft16 set, the current DCQCN takes
about 1-2 min per run (`wall_s` in each `_summary.json`).

| file | requests | flows | GB | bottleneck | target / realized load | rough DCQCN cost |
|---|---|---|---|---|---|---|
| e3_load30_ft128 | 9 | 72 | 12.1 | 6,400 G | 0.30 / 0.302 | ~10 min |
| e3_load50_ft128 | 15 | 120 | 20.1 | 6,400 G | 0.50 / 0.503 | ~18 min |
| e3_load70_ft128 | 21 | 168 | 28.2 | 6,400 G | 0.70 / 0.705 | ~25 min |
| e3_load90_ft128 | 27 | 216 | 36.2 | 6,400 G | 0.90 / 0.906 | ~32 min |
| e3_load30_ft16 | 9 | 72 | 12.1 | 3,200 G | 0.30 / 0.302 | ~10 min |
| e3_load50_ft16 | 15 | 120 | 20.1 | 3,200 G | 0.50 / 0.503 | ~18 min |
| e3_load70_ft16 | 21 | 168 | 28.2 | 3,200 G | 0.70 / 0.705 | ~25 min |
| e3_load90_ft16 | 27 | 216 | 36.2 | 3,200 G | 0.90 / 0.906 | ~32 min |
| e2_load70_ft128 | 21 | 168 | 28.2 | 6,400 G (1:1) | 0.70 / 0.705 on 1:1, 1.41 on 4:1 | ~25 min |
| e6_incast2_ft16 | 2 | 2 | 0.34 | 400 G | burst, floor 6,711 us | - |
| e6_incast4_ft16 | 4 | 4 | 0.67 | 400 G | burst, floor 13,422 us | - |
| e6_incast8_ft16 | 8 | 8 | 1.34 | 400 G | burst, floor 26,844 us | - |

### E3 on the 128-node fat tree: `e3_load{30,50,70,90}_ft128.cm`

    for L in 30 50 70 90; do
      scripts/gen_workload.py --topo ft128_1os --model llama3-70b --prompt fixed:4096 \
        --tp 8 --pattern tp --arrival poisson --load 0.$L --duration-ms 50 \
        --fix-count --seed 1 --also-topo ft128_4os,ft128_8os \
        --out workloads/e3_load${L}_ft128.cm
    done

- **Pools.** Prefill is hosts 0-63 (pods 0-3, 8 instances). Decode is hosts
  64-127 (pods 4-7, 8 instances). Every flow crosses the core.
- **Bottleneck.** 4 sending pods x 1,600 G of agg->core uplinks = **6,400
  Gbps**. That ties with the 64 sending NICs x 100 G, and with the ToR uplink
  groups, so the 1:1 fabric is non-blocking for this pattern.
- **Rate.** 0.7 x 6,400 G / 10.737 Gbit per request = 417 req/s. Over the
  50 ms window that is about 21 requests.
- **Request assignment.** Prefill instances are taken round-robin. Decode
  partners rotate per prefill, so all 216 flows at 90% use distinct
  (src, dst) pairs and log matching is unambiguous.
- **Same matrix on other fabrics.** On 4:1 this is 1.21 / 2.01 / 2.82 / 3.62x
  overload. On 8:1 it is 2.42 to 7.25x. Use E3 on 1:1 only, and E2 for
  topology.

### E3 on the 16-node default tree: `e3_load{30,50,70,90}_ft16.cm`

    for L in 30 50 70 90; do
      scripts/gen_workload.py --topo default --model llama3-70b --prompt fixed:4096 \
        --tp 8 --pattern tp --arrival poisson --load 0.$L --duration-ms 100 \
        --fix-count --seed 1 --out workloads/e3_load${L}_ft16.cm
    done

- **Pools.** One prefill instance (hosts 0-7, pods 0-1) and one decode
  instance (hosts 8-15, pods 2-3). Those are the only TP=8 placements that
  fit in 16 nodes.
- **Bottleneck.** 2 sending pods x 1,600 G = 8 NICs x 400 G = **3,200
  Gbps**.
- **Window.** 100 ms, double the ft128 window, so B x window, and therefore
  request and flow counts, match the ft128 set exactly.
- **Caveat: one instance pair.** All requests share the same 8 (src, dst)
  pairs, so `pair_seq` is needed to match log lines. Requests that overlap in
  time queue on the same 8 NICs. At 90% load a good part of the measured
  completion time is NIC queueing that any policy would see.

### E2: `e2_load70_ft128.cm`

    scripts/gen_workload.py --topo ft128_1os --model llama3-70b --prompt fixed:4096 \
      --tp 8 --pattern cross_pod --placement interleaved --arrival poisson --load 0.7 \
      --duration-ms 50 --fix-count --seed 1 --also-topo ft128_4os,ft128_8os \
      --out workloads/e2_load70_ft128.cm

`e2_load30_ft128.cm` and `e2_load50_ft128.cm` use the same command with
`--load 0.3` and `--load 0.5`. The exact command is in each `.json` sidecar.

Every pod holds one prefill instance (its first two ToRs) and one decode
instance (its last two ToRs). Each request goes to a decode instance in a
different pod, so every byte crosses the core. Each pod sends and receives
1/8 of the traffic.

| fabric | NIC limit | pod->core limit | B | offered 4,510 G is |
|---|---|---|---|---|
| 1:1 | 64 x 100 = 6,400 G | 8 pods x 1,600 G = 12,800 G | 6,400 G (NICs and ToR uplinks bind) | **0.70**, core links at 35% |
| 4:1 | 6,400 G | 8 pods x 400 G = 3,200 G | 3,200 G (core binds) | **1.41**, saturated |
| 8:1 | 6,400 G | 8 x 200 G = 1,600 G, ToR uplinks 16 x 100 G = 1,600 G | 1,600 G | **2.82** |

On 1:1 the network is at most 70% loaded anywhere and the core is at half
that. On 4:1 each pod tries to push 564 Gbps through 400 Gbps of core uplink
for the whole 50 ms window, so a backlog builds and drains after the window
closes. Any difference between the 1:1 and 4:1 runs is the oversubscription.
The split placement used by E3 would give 2.82x on 4:1. Interleaving was
chosen so that 4:1 is clearly saturated without being absurdly overloaded.

### E6: `e6_incast{2,4,8}_ft16.cm`

    for K in 2 4 8; do
      scripts/gen_workload.py --topo default --model llama3-70b --prompt fixed:4096 \
        --tp 8 --shards 1 --pattern incast --incast $K --arrival fixed \
        --requests $K --interval-us 0 --out workloads/e6_incast${K}_ft16.cm
    done

K prefill instances each send their shard-0 flow (167,772,160 B) to decode
GPU 0, all at t=0. Senders are hosts 0..K-1, the receiver is host 8, and all
flows cross the core.

16 nodes cannot hold K+1 full TP=8 instances, so these matrices simulate one
rail of the TP group (`--shards 1`). The bottleneck is the receiver's 400 G
downlink, which a single rail models exactly. The drain floor is K x 3,355 us.
`e6_incast8_ft16.cm` is flow-for-flow identical to `kv_incast_8to1.cm`.

## Azure LLM inference trace

Source: https://github.com/Azure/AzurePublicDataset
(`AzureLLMInferenceDataset2023.md`).

- **2023 release, downloaded** into `data/azure/` (about 1 MB, committed):
  - https://raw.githubusercontent.com/Azure/AzurePublicDataset/master/data/AzureLLMInferenceTrace_conv.csv
  - https://raw.githubusercontent.com/Azure/AzurePublicDataset/master/data/AzureLLMInferenceTrace_code.csv
- **2024 week-long release, not downloaded.** The files are 0.69 GB and
  1.14 GB. They are gitignored in case someone fetches them:
  - https://github.com/Azure/AzurePublicDataset/releases/download/dataset-llm-2024/AzureLLMInferenceTrace_code_1week.csv
  - https://github.com/Azure/AzurePublicDataset/releases/download/dataset-llm-2024/AzureLLMInferenceTrace_conv_1week.csv

The columns are `TIMESTAMP, ContextTokens, GeneratedTokens`. `--arrival
trace` accepts any CSV with those columns. TIMESTAMP may be a datetime or
plain seconds.

`scripts/azure_trace_stats.py` (2023 files, about 1 hour each, 2023-11-16):

| | conv | code |
|---|---|---|
| requests | 19,366 over 3,502 s | 8,819 over 3,436 s |
| mean rate | 5.53 req/s | 2.57 req/s |
| inter-arrival CV | 1.09 (close to Poisson) | 13.2 (very bursty) |
| per-1 s count p50 / p99 / max | 5 / 13 / 16 | 0 / 28 / 67 |
| ContextTokens mean / p50 / p90 / p95 / p99 / max | 1155 / 1020 / 2734 / 4083 / 4142 / 14050 | 2048 / 1469 / 5188 / 7303 / 7436 / 7437 |
| prompts > 4096 tokens | 2.1% | 14.1% |
| Llama-3-70B KV per request, mean / p99 | 0.378 / 1.357 GB | 0.671 / 2.437 GB |
| Llama-3-70B KV rate at trace speed | 16.7 Gbps | 13.8 Gbps |

At recorded speed the traces offer only about 15 Gbps of KV traffic, three
orders of magnitude below these fabrics. That makes sense, since each trace
is one deployment's front end. Replaying one against a fabric needs time
compression. With `--arrival trace --load L`, the generator picks the
compression so that the replayed prefix fills the window at L x B, and prints
the factor. For example, `--load 0.5 --duration-ms 100` on the code trace
replays 31 trace seconds as 100 ms, a 313x compression. `--prompt trace` with
`--arrival poisson` instead samples prompt lengths from the trace's empirical
distribution.

Example (not checked in):

    scripts/gen_workload.py --topo default --arrival trace \
      --trace data/azure/AzureLLMInferenceTrace_code.csv --max-prompt 8192 \
      --load 0.5 --duration-ms 100 --out workloads/trace_code_load50_ft16.cm
