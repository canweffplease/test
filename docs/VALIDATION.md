# Validation: congestion-control convergence against the HPCC and DCQCN papers

This covers two items of "Validation before trusting any comparison" in
docs/EXPERIMENTS.md: "DCQCN converges more slowly than HPCC, consistent with
published results" and "Reproduce one figure from the HPCC paper and one from
the DCQCN paper".

This version uses the **final, paper-faithful transports**, with the matrix's
picosecond start times fed directly (no start-time workaround). The
checkpoint 1 results, measured on the old `build/` binaries with the
start-time workarounds, are kept as "Legacy" at the end and in
`results/validation/legacy/`.

**Verdict.**

- **HPCC (Algorithm 1) now reproduces the paper's headline behaviour.**
  Near-zero queues (18 KB median at the receiver, 2 packets), utilisation at
  the η target (378-383 Gbps goodput against 377 designed), survivors reclaim
  freed bandwidth in 88-114 us after the last two departures, and Jain 0.97-1.00.
  Fairness after a join converges slowly (1.75 ms for the newcomer at the 4th
  join, never within ±10 % at the 3rd), which is what the paper predicts for
  a small W_AI ("rapid convergence to a Pareto optimal rate allocation,
  followed by a slower convergence to fairness"). The legacy binary did none
  of this: Jain 0.39, a 486 MB NIC backlog, no join ever converged.
- **DCQCN (paper RP + RED marking) is qualitatively the paper's DCQCN** and
  converges far more slowly than HPCC: after every join the aggregate drops to
  185-358 Gbps for milliseconds, and the last survivor needs 4.4 ms (3 of 5
  seeds never) to climb back to 400. Jain 0.98 / 0.74 / 0.87 at 2 / 3 / 4
  flows. RED's rate-proportional marking is now visible (faster flows get more
  CNPs), and the receiver queue stays at 142 KB p99.
- **So the check "DCQCN converges more slowly than HPCC" now passes**, on
  every measure: utilisation after joins and departures, time to reclaim
  bandwidth, fairness, and queue.
- **EQDS** reaches fair share within one 50 us bin with 3 and 4 flows, keeps
  the receiver queue at one packet, and is identical to its legacy result.
  Its 2-flow split (219/169) is still unexplained.
- **RoCE without CC** reproduces the PFC port-unfairness the DCQCN paper
  uses as motivation, identically to legacy.

## What was run

    HTSIM_BUILD_hpcc=build_hpccfix HTSIM_BUILD_eqds=build_eqds2 \
    HTSIM_BUILD_roce=build_start HTSIM_BUILD_dcqcn=build \
    POLICIES="hpcc hpccpfc eqds roce dcqcn" ./scripts/validation.sh

`validation.sh` takes a per-policy build (`HTSIM_BUILD_<cc>`; `hpccpfc` uses
HPCC's, `dcqcn` falls back to RoCE's) and a start-time mode `START_UNITS`.
When a build is given explicitly the default is `ps`: the canonical matrix is
fed unchanged, because every final binary honours picosecond starts. Without
explicit builds it keeps the old probe-and-workaround path (`START_UNITS=probe`),
which is how the legacy results were made. `results/validation/start_units.txt`
records the mode, matrix and binary per policy. The analysis alone can be
re-run with `ANALYZE_ONLY=1 ./scripts/validation.sh`.

| policy | build | binary | extra arguments |
|---|---|---|---|
| HPCC | `build_hpccfix` | htsim_hpcc, Algorithm 1 (commit 883a3be) | none (T = 13.1 us, N = 10, binary defaults) |
| HPCC, PFC 80/150 | `build_hpccfix` | same | `-pfc_thresholds 80 150` |
| DCQCN | `build` | htsim_roce, faithful RP (31a3bc8) + RED (206f9ac) + min-rate clamp (4d3e1b2) | `-dcqcn 100 -ecn_kmin 50 -ecn_kmax 400 -ecn_pmax 0.01 -pfc_thresholds 80 150` |
| EQDS | `build_eqds2` | htsim_eqds, credit and priority fixed (a4e533e) | none |
| RoCE, no CC | `build_start` | htsim_roce | `-pfc_thresholds 80 150` |

The DCQCN ECN arguments are run.sh's defaults at 400 G; `ECN_STEP=1` would
drop them and restore step marking at K = 100 packets.

The DCQCN runs were repeated on the binary with the minimum-rate clamp
(`HTSIM/OUR_CHANGES.md`, "DCQCN: minimum rate clamp"). All five seeds are
bit-identical, down to the binary sink and queue logs, so every DCQCN number
and figure below stands: no flow here is cut below one R_AI step (each join
halves a flow two or three times). The pre-clamp runs are in
`results/validation/dcqcn_preclamp/`.

**Workload**, `workloads/validation/converge_stagger.cm`. The topology is the
default 16-node, 3-tier K=4 fat tree, with all links 400 Gbps and 1 us per
hop. The base RTT from host 0 to host 8 is 13.1 us (measured with a
one-packet HPCC flow). Four flows go into host 8 from hosts 0, 4, 12 and 2.
Every sender is in a different pod or ToR from host 8, so the senders' only
shared links are in host 8's pod: the two agg-to-ToR4 links and the
ToR4-to-host-8 downlink. Max-min fair share is therefore 400/n Gbps whatever
ECMP does upstream.

| flow | path | start | size | ideal finish (max-min) |
|---|---|---|---|---|
| 1 | 0->8 | 0 | 491,666,667 B | 22 ms |
| 2 | 4->8 | 2 ms | 291,666,667 B | 20 ms |
| 3 | 12->8 | 4 ms | 191,666,667 B | 18 ms |
| 4 | 2->8 | 6 ms | 125,000,000 B | 16 ms |

The sizes are chosen so that, under ideal fair sharing, flows *leave*
last-in-first-out at 16, 18, 20 and 22 ms. Each run therefore tests both
directions: joins (n = 1, 2, 3, 4) and departures, where the survivors must
reclaim bandwidth (n = 3, 2, 1).

**Common arguments** are run.sh's for the default topology:
`-nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000 -q 500`, plus
`-end 50000 -seed {1..5}` and the logging flags below. Seeds 1-5 give five
runs per policy, 25 in all. All 25 report **zero "LOSSLESS not working"
violations**, and every logged flow start equals the matrix start
(0, 2000, 4000, 6000 us; `summary.md`, "Flow completion times").

**Start times.** The checkpoint 1 binaries misread `.cm` start times
(RoCE, DCQCN and EQDS as microseconds, HPCC ignored them), so the legacy runs
used a rescaled matrix and, for HPCC, a chain of trigger flows that started
the real flows at 0, 2.07, 4.14 and 6.21 ms (Legacy section). All final
binaries read picoseconds correctly (`HTSIM/OUR_CHANGES.md`, "Flow start
times were read in the wrong units" and "HPCC ignored the matrix start
time"), so the workarounds are gone and all policies see identical starts.

## How per-flow throughput and queue length were obtained

No new C++. The binaries already have sampling loggers:

- `-log sink -logtime 0.01` attaches `RoceSinkLoggerSampling`,
  `HPCCSinkLoggerSampling` or `EqdsSinkLoggerSampling` to every sink.
  Every 10 us they write each sink's `cumulative_ack()` (loggers.cpp:1570-1646,
  eqds_logger.cpp:14-41).
- `-log tor_downqueue` creates a `QueueLoggerFactory(LOGGER_SAMPLING)` with a
  10 us period (main_*.cpp). The fat-tree builder attaches one to *every*
  queue, so this covers all switch queues and the host NIC queues, not just
  the ToR downlinks. Each sample records the occupancy in bytes, plus the
  interval min and max (`QueueLoggerSampling::doNextEvent`, loggers.cpp:191-219).
- `-o <file>` writes HTSIM's binary logfile. The format (logfile.cpp:68-145)
  is a text preamble mapping names to ids (`: Roce_sink_0_8=494`), the line
  `# TRACE`, then 44-byte records `<f64 time_s, u32 type, u32 id, u32 ev+100*type, f64 v1, v2, v3>`.
  `idmap.txt` in the run directory maps every object id to its name, for
  example `109 LS4->DST8(0)`.

`scripts/validation_analyze.py` decodes these records directly with numpy.

**Units.** `cumulative_ack()` counts packets for RoCE and DCQCN and for EQDS
(`_expected_epsn`), but bytes for HPCC. The script converts with a per-run
unit, flow size / final cumulative ACK: 4000 B (RoCE, DCQCN), 4086 B of
payload (EQDS) and 1 B (HPCC).

**Throughput** is the difference of cumulative bytes per 50 us bin, which is
receiver goodput. **Fair share** is 400/n(t), where n(t) is the number of
flows active according to the logged start and finish times.

**Convergence time.** Measured from an event (a join or a departure) to the
start of the first 50 us bin from which the flow(s) stay within ±10 % of
400/n for 300 us, with that whole window ending before the next event.
"New flow" checks only the joining flow. "All flows" checks every active
flow. "nc" means the condition was never met before the next event. 50 us is
about 4 base RTTs, so "50 us" means "within the first measurable bin".

Note that goodput is measured against 400/n, so a policy that by design runs
below line rate (HPCC at η = 0.95 of 400 × 9000/9064, i.e. 377 Gbps; EQDS at
388 Gbps, its header and credit cost) has every fair share shifted down by
that factor: HPCC's 4-flow fair share is 94 Gbps, inside the ±10 % band
around 100.

## Results

### Figure 1: per-flow goodput vs time (seed 1)

![convergence](../results/figs/validation_convergence.png)

`results/figs/validation_convergence.{png,pdf}`, final transports, one panel
each for DCQCN, HPCC, EQDS and RoCE (the HPCC PFC 80/150 variant is in the
tables only). Dashed line: 400/n. Dotted verticals: flow starts. Per-seed time series are in
`results/validation/timeseries/`. The checkpoint 1 figure is
`results/figs/validation_convergence_legacy.{png,pdf}`.

### Figure 2: queue occupancy vs time (seed 1)

![queues](../results/figs/validation_queue.png)

`results/figs/validation_queue.{png,pdf}`. Blue: the receiver's ToR downlink
`LS4->DST8`. Orange: the sum over all switch egress queues (host NIC queues
excluded). Each panel has its own y-scale. Every egress buffer is
500 packets (2.0 MB for RoCE and DCQCN, 2.075 MB for EQDS, 4.5 MB for HPCC,
whose MTU is 9000 B). The checkpoint 1 figure is
`results/figs/validation_queue_legacy.{png,pdf}`.

### Convergence, 5 seeds

The full table is `results/validation/summary.md`; per-run rows are in
`convergence.csv`. Values are median [min-max] over seeds, with the number
of nc runs. Jain and aggregate goodput are over the second half of each
phase.

| policy | flow 2 joins (fair 200) | flow 3 joins (fair 133) | flow 4 joins (fair 100) | departure 1 (-> 3 flows) | departure 2 (-> 2) | departure 3 (-> 1) |
|---|---|---|---|---|---|---|
| **HPCC** | new 50 us; all nc 5/5 | nc 5/5 (both) | new 1,750 [1,750-1,950]; all 4,775 [2,350-5,650], nc 1/5 | 599 [544-605] | 114, nc 3/5 | 88 [76-101] |
| HPCC, PFC 80/150 | nc 5/5 (both) | new 1,550 [1,350-1,550]; all nc 5/5 | new 1,750 [1,400-1,750]; all 5,350, nc 1/5 | 94 [94-2,076] | nc 5/5 | 69 [69-110], nc 1/5 |
| **DCQCN** (RP + RED) | nc 5/5 (both) | nc 5/5 (both) | new 300 [300-6,050]; all nc 5/5 | nc 5/5 | 5,499, nc 4/5 | 4,409 [4,113-4,704], nc 3/5 |
| **EQDS** | nc 5/5 (both) | 50 [0-50] (both) | 0 (first bin, both) | 41 | nc 5/5 | 27 [27-28] |
| **RoCE**, no CC | 50 (both) | nc 5/5 (both) | nc 5/5 (both) | nc 5/5 | 16, nc 4/5 | 15 [12-48] |

| policy | Jain, n = 2 / 3 / 4 (joins) | aggregate goodput n = 1 / 2 / 3 / 4, Gbps | aggregate after departures 1 / 2 / 3 |
|---|---|---|---|
| **HPCC** | 0.971 / 0.969 / 1.000 | 383 / 378 / 378 / 379 | 378 / 378 / 382 |
| HPCC, PFC 80/150 | 0.980 / 0.985 / 0.998 | 383 / 378 / 378 / 379 | 378 / 378 / 383 |
| **DCQCN** | 0.978 / 0.735 / 0.871 | 394 / **185** / 305 / 358 | 352 / 383 / 258 |
| **EQDS** | 0.984 / 1.000 / 1.000 | 388 / 388 / 388 / 388 | 388 / 388 / 388 |
| **RoCE** | 1.000 / 0.888 / 0.728 | 394 / 394 / 394 / 394 | 394 / 394 / 394 |

Seed 1 per-flow goodput, second half of each join phase (Gbps):

| policy | 2 flows | 3 flows | 4 flows |
|---|---|---|---|
| HPCC | 156 / 222 | 105 / 116 / 158 | 91 / 95 / 97 / 96 |
| DCQCN | 134 / 106 | 60 / 42 / 106 | 67 / 56 / 72 / 151 |
| EQDS | 219 / 169 | 129 / 129 / 129 | 97 / 97 / 97 / 97 |
| RoCE | 197 / 197 | 197 / 102 / 95 | 98 / 48 / 51 / 197 |

### Queues (seed medians; "all active" window = 0.5 ms after the 4th join until the first departure)

| policy | `LS4->DST8` median | p99 | max | all switch queues, median | whole-run max at `LS4->DST8` |
|---|---|---|---|---|---|
| **HPCC** | 18.1 KB (2 pkts) | 27.2 KB | 36.3 KB | 54.4 KB | 199 KB (newcomer's first window) |
| HPCC, PFC 80/150 | 18.1 KB | 27.2 KB | 36.3 KB | 54.4 KB | 635 KB |
| **DCQCN** | 4.1 KB (1 pkt) | 142 KB | 199 KB | 16.3 KB | 841 KB (joins) |
| **EQDS** | 4.2 KB (1 pkt) | 4.2 KB | 4.2 KB | 20.8 KB | 261 KB (first window at each join) |
| **RoCE**, no CC | 914 KB (229 pkts) | 1,291 KB | 1,301 KB | 9.0 MB | 1,301 KB |

"1 pkt" is the packet being serialised. Queues count a packet until it
leaves (`_queuesize -=` at dequeue, queue_lossless_output.cpp).

### Flow completion (us, seed 1; all seeds in `summary.md`)

| policy | flow 1 | flow 2 | flow 3 | flow 4 | makespan / ideal 22.0 ms |
|---|---|---|---|---|---|
| ideal, max-min at 400 | 22,000 | 20,000 | 18,000 | 16,000 | |
| ideal at HPCC's η (377 Gbps goodput) | 23,300 | 21,200 | 19,100 | 17,000 | |
| HPCC | 23,277 | 20,912 | 17,886 | 15,351 | 1.06 |
| DCQCN | 27,336 | 29,230 | 21,503 | 13,641 | 1.33 |
| EQDS | 22,677 | 21,222 | 18,432 | 16,309 | 1.03 |
| RoCE | 20,534 | 22,365 | 21,216 | 11,092 | 1.02 |

## HPCC: legacy against fixed

| measure, seeds 1-5 | legacy (checkpoint 1 `build/`, trigger-flow starts) | fixed (Algorithm 1, ps starts) | HPCC paper |
|---|---|---|---|
| Jain, 2 / 3 / 4 flows | 0.632 / 0.474 / **0.395** | 0.971 / 0.969 / **1.000** | converges to fair |
| 4-flow split, seed 1 (Gbps) | 272 / 55 / 44 / 26 | 91 / 95 / 97 / 96 | equal |
| aggregate goodput | 397 Gbps, link saturated by flow 1's NIC backlog | 378-383 Gbps | η = 0.95 of line (377 here) |
| receiver queue median / max, all active | 100-109 KB / 190 KB | **18 KB** / 36 KB | near zero |
| sender NIC peak | **486-488 MB** (flow 1) | 9 KB (one packet) | n/a (window-limited) |
| new flow within ±10 %, joins 2 / 3 / 4 | nc / nc / nc | 50 us / nc / 1,750 us | fast to Pareto, slow to fair |
| last departure, survivor to 400 | 611 us [44-1,217], nc 1/5 | 88 us [76-101] | a few RTTs |
| finish order | flow 1 (largest) first at 11.8 ms | LIFO: 15.4, 17.9, 20.9, 23.3 ms | LIFO |

The legacy column is the checkpoint 1 measurement (Legacy section,
`results/validation/legacy/`); the legacy Jain and NIC values are the
`-hpcc_legacy` rerun in `HTSIM/OUR_CHANGES.md`, "HPCC: from a compounding
window to the paper's Algorithm 1", Validation (a), which reproduces it.

**What changed in the code** (`HTSIM/OUR_CHANGES.md`, same section, "What was
wrong" and "What changed"): the window is now computed per ACK from a
reference window Wc that is updated once per RTT, instead of compounding on
every ACK; the INT reference is refreshed on every ACK, so U is measured over
the last RTT instead of since the first ACK; W_AI = W_init (1 - η)/N with
N = 10 (3.3 KB here) instead of one MSS; the wall-clock `srand` is gone; and
the flow honours its matrix start time.

**What now matches the HPCC paper (Li et al., SIGCOMM 2019):**

- **Near-zero queues.** The receiver downlink holds 2 packets (18 KB) with 4
  flows; the whole fabric 54 KB. The sender NIC holds one packet.
- **Utilisation at η.** 378-383 Gbps goodput, which is η × 400 × 9000/9064 =
  377 Gbps plus the W_AI step that keeps U about 0.5 % above η (docs/E1.md
  §3.3 derives the same 0.955 on single flows). The 5 % headroom the paper
  designs in is visible.
- **Fast reclaim of free bandwidth.** After the last departure the survivor
  is back at target in 88 us [76-101] (about 7 RTTs), and the aggregate is
  back on target within 49-88 us after every departure (OUR_CHANGES.md,
  "Utilization convergence"). After a join, the newcomer's line-rate first
  window (about 200 KB) drains in 70-80 us.
- **Fairness with 4 flows.** Jain 1.000, all four flows within 7 % of each
  other (91-97 Gbps on seed 1).
- **Last-in-first-out finish order**, with flow 1 finishing at exactly the
  η ideal (23.3 ms).

**What does not match, and why:**

- **Fairness after a join is slow.** The newcomer reaches ±10 % of 400/n in
  1.75 ms at the 4th join and never within the 2 ms phase at the 3rd; all
  flows together take 4.8 ms at the 4th join, and never at the 2nd or 3rd.
  The paper says to expect this: with a small W_AI, HPCC converges rapidly to
  a Pareto-optimal allocation and then slowly to fairness (Sec. 3.3 and
  Appendix A). With `-hpcc_N 2` (W_AI 5x larger) every join converges in
  350 us at Jain 1.000 (OUR_CHANGES.md), at the cost of a deeper queue
  (36 KB median). We keep N = 10, HTSIM's default (the paper sizes N as the expected number of concurrent flows per link and used 100 in its evaluation); CLAUDE.md open item 7
  records the choice.
- **Jain 0.97 at 2-3 flows, newcomer ahead.** In the 2-flow phase the newcomer
  gets 222 Gbps against the incumbent's 156. A new flow starts at line rate
  and multiplicative cuts preserve ratios, so it holds a larger share until
  W_AI equalises the flows (about 1.7 ms at N = 10). A per-flow U bias of
  ~0.006 from packet arrival phases splits windows ~1.3:1 when U sits only
  ~1 % above η (OUR_CHANGES.md, "What still differs from an ideal fluid
  HPCC"). Flow 4 finishes early (15.4 against 17.0 ms) for the same reason.
- **Departure 2 converges in only 2 of 5 seeds** (114 us when it does): the
  two survivors keep the ratio their windows had, at 219 / 158 Gbps on
  seed 1, which is again the slow fairness phase.
- **PFC 80/150 changes nothing in steady state.** The fixed HPCC never fills
  a queue to the default 12/15-packet PFC thresholds except during a
  newcomer's first window, so raising them only moves the join-time peak
  (whole-run max 635 KB against 199 KB). Jain 0.980 / 0.985 / 0.998 and the
  same aggregate.

## DCQCN: paper RP and RED marking

**Mechanisms, and where they come from** (`HTSIM/OUR_CHANGES.md`, "DCQCN made
faithful to the paper", Validation (a), and "DCQCN switch marking:
probabilistic (RED) ECN", Results (a); the final runs reproduce the
"red 50/400/1 %" row of the latter exactly: Jain 0.978 / 0.735 / 0.871,
aggregate 185 / 305 / 358, receiver p99 / max 142 / 199 KB):

| mechanism | HTSIM, final | DCQCN paper (Zhu et al., SIGCOMM 2015) | match? |
|---|---|---|---|
| start rate | line rate | line rate | yes |
| rate decrease on CNP | `RT = RC; RC *= (1 - α/2); α = (1-g)α + g` | eq. (1) | yes |
| α | per flow, g = 1/256, decays every 55 us without a CNP | per RP, same rule | **yes** (legacy: one static α shared by all flows) |
| byte counter | B = 10 MB, in bytes | B = 10 MB | **yes** (legacy: counted packets, never fired) |
| R_AI / R_HAI | 0.1 % / 0.5 % of line rate (400 Mbps / 2 Gbps at 400 G) | R_AI 40 Mbps on 40 G (0.1 %); R_HAI not given | **yes** (legacy: 5 % / 10 %) |
| fast recovery, hyper increase | F = 5; hyper when both timer and byte counter exceed F | same | yes; hyper increase now fires |
| CNP generation | at most one per 50 us per flow | N = 50 us | yes |
| ECN marking | RED on the instantaneous egress queue at dequeue: Kmin 50 KB, Kmax 400 KB, Pmax 1 % | RED, 5 KB / 200 KB / 1 % at 40 G | **yes, scaled to 400 G**: Kmin = the same 1 us of queueing; Kmax kept below the 600 KB PFC pause point (legacy and `ECN_STEP=1`: step at 400 KB) |

**What the runs show.**

- **Every join collapses the aggregate.** The newcomer starts at 400 G, the
  bottleneck queue passes the 350 KB RED band in about 10 us, less than one
  50 us CNP window, so every flow gets forced marks (98-99 % of all marks) and
  two or three CNPs in consecutive windows. With α ≈ 1 each CNP halves the
  rate. The 2-flow phase averages 185 Gbps of aggregate goodput (seed 1:
  134 / 106), the 3-flow phase 305.
- **Recovery is the paper's slow recovery.** R_AI = 0.1 % of line per 55 us
  is 7.3 Gbps per ms; hyper increase needs 60 MB sent since the last CNP.
  So a cut flow climbs for milliseconds. After the last departure the
  survivor needs 4.4 ms to reach 400 Gbps and in 3 of 5 seeds does not get
  there before it finishes (aggregate 258 Gbps in that phase).
- **RED's rate-proportional marking works between transients.** Under RED a
  flow's chance of a mark in a 50 us window grows with its packet count, so
  faster flows back off more, the convergence mechanism the paper relies on.
  OUR_CHANGES.md measures it on this workload: the faster flow of a pair got
  more CNPs in 44 % of flow pairs (0 % under step), rate-CNP correlation
  0.36 (0.00 under step). It does not act during joins, because the queue
  goes straight past Kmax.
- **Fairness: real but incomplete.** 0.978 at 2 flows, 0.735 at 3 (on 3 of 5
  seeds the newcomer got one cut fewer and ran at ~205 against 41-70 Gbps),
  0.871 at 4. Each flow's α and R_T record its own history, and the only
  equalising forces are RED's marks and the slow additive increase.
- **Queue: in the RED band.** Receiver downlink 1 packet median, 142 KB p99,
  199 KB max while all 4 flows are active, against 728 / 801 KB under legacy
  step marking. Join spikes reach 841 KB.
- **Makespan 29.35 ms median (75 % of the 22.0 ms ideal).** Seed 1 finishes
  flow 2 last, at 29.2 ms; flow 4 at 13.6 ms, 2.4 ms before its max-min
  ideal. The newcomer keeps the highest rate (151 Gbps in the 4-flow phase
  on seed 1) because its R_T was captured when it was cut from line rate,
  while the incumbents' R_T were captured at lower rates
  (OUR_CHANGES.md, Validation (a), item 3).

**Comparison with the DCQCN paper.**

- PFC alone is unfair; DCQCN is meant to fix it. **Reproduced**: RoCE splits
  197/98/51/48 Gbps by ingress port (Jain 0.73 at 4 flows); DCQCN raises the
  4-flow Jain to 0.87 at a large utilisation cost.
- DCQCN keeps the queue near the marking thresholds instead of PFC-full.
  **Reproduced**: 142 KB p99 against RoCE's 1.3 MB and 9 MB of fabric
  occupancy.
- Convergence takes milliseconds, driven by the timer and byte counter.
  **Reproduced.** With the paper's R_AI (0.1 % of line per 55 us) a cut flow
  recovers over milliseconds, which on 2-30 ms flows is a large fraction of
  the run.
- Hyper increase, per-flow α and RED fairness now all run as specified.

## "DCQCN converges more slowly than HPCC": passes

| | HPCC | DCQCN |
|---|---|---|
| aggregate after a join (2 / 3 / 4 flows) | 378 / 378 / 379 Gbps | 185 / 305 / 358 Gbps |
| survivor to 400 after the last departure | 88 us | 4,409 us, nc 3/5 |
| Jain 2 / 3 / 4 | 0.971 / 0.969 / 1.000 | 0.978 / 0.735 / 0.871 |
| receiver queue p99, 4 flows | 27 KB | 142 KB |
| makespan, seed 1 | 23.3 ms (η ideal 23.3) | 29.2 ms |

HPCC reaches its utilisation target within one 50 us bin after every event
and holds it; DCQCN loses 9-53 % of the link for milliseconds after every
join. Both converge slowly to *fairness* (HPCC's W_AI, DCQCN's R_AI), which
matches both papers. Against EQDS, DCQCN is slower still: EQDS is exactly fair
within one bin with 3 and 4 flows.

## EQDS: unchanged

The final `build_eqds2` binary gives finish times identical to the legacy
`build/` binary on all 5 seeds (flows finish at 22,677 / 21,222 / 18,432 /
16,309 us on every seed; EQDS ignores `-seed` on this workload). The fixes in
this build (finished-flow credit, strict priority, stale-NACK handling,
`HTSIM/OUR_CHANGES.md`) do not touch a single-priority, loss-free 4-flow
incast.

Each receiving host has one `EqdsPullPacer`:

- **Pull timing.** One pull every 0.99 x one MTU serialisation time
  (pacer created with modifier 0.99 at `datacenter/main_eqds.cpp:550`).
- **Pull size.** `_credit_per_pull` = 8 x 512 B (`eqds.cpp:23`).
- **Pull order.** Retransmission requests first, then active senders, FIFO
  for equal priority, served sinks re-queued at the back: per-packet round
  robin across senders.
- **First window.** A new sender may send `_credit_spec = _maxwnd` unscheduled
  (`eqds.cpp:969`), with `_maxwnd` = `DEFAULT_CWND` 64 packets × 4150 B =
  265.6 KB, set by `datacenter/main_eqds.cpp:573-574`.

These explain the measurements: fair share as soon as the newcomer's first
pull arrives (≤ one 50 us bin), the receiver queue at the packet in service,
one 261-265 KB burst per join, departures re-converged in 27-41 us, and
388 Gbps aggregate = 400 × 4086/4150 × 4096/(0.99 × 4198), the header and
credit cost docs/E1.md §3.2 derives.

**The 2-flow split (219/169 Gbps, in favour of the older flow) is not
explained.** Two flows started together split 201/193, so it is
history-dependent. Our hypothesis is that the incumbent accumulates duplicate
entries in the pacer's idle/active sender list while it is alone, so it is
served more often once a second flow joins; with 3 and 4 flows the split is
exact. Not verified in the code.

## RoCE without CC: unchanged

Identical to legacy on all 5 seeds (the `build_start` binary at picosecond
starts gives the same per-phase rates as the legacy binary on the rescaled
matrix). Nothing but PFC limits the senders, so every hop's buffer fills to
the PFC threshold, pauses propagate hop by hop (9 MB of fabric occupancy),
and the share each flow gets is set by which ingress port it arrives on:
198 / 98 / 51 / 48 Gbps with 4 flows on seed 1, Jain 0.73. It converges after
a join only when the arbitration happens to be fair, as at n = 2. This is the
victim-flow behaviour the DCQCN paper uses to motivate DCQCN.

## Consequences for the project

- **HPCC numbers are now HPCC.** Use the Algorithm 1 binary
  (`build_hpccfix` or a rebuild of HEAD) with `-hpcc_T` per topology for every
  HPCC result, and state N: at N = 10 HPCC converges slowly to fairness, by
  design.
- **DCQCN numbers are the paper's DCQCN with RED at 400 G thresholds.** Its
  utilisation loss after every join is a property of the paper's R_AI on
  short, fast transfers, not a simulator bug. Any DCQCN-vs-X comparison must
  say which DCQCN (final, or `-dcqcn_legacy ECN_STEP=1`).
- **Start times are honoured by every binary.** No workaround is needed for
  matrices with nonzero starts; parse.py still checks logged against matrix
  starts.
- **RED on 100 G trees is untested here.** This validation is 400 G only.
  docs/E1.md §5.3 has the first 100 G DCQCN-with-RED data (Kmin 12 KB).

## Files

- `workloads/validation/converge_stagger.cm`: canonical matrix (starts in ps).
- `scripts/validation.sh`: per-policy builds (`HTSIM_BUILD_<cc>`), start-time
  mode (`START_UNITS=ps|us|trigger|probe`), runs the selected policies x 5
  seeds, then analyzes. `VAL_OUT` and `FIGDIR` redirect outputs.
- `scripts/validation_analyze.py`: decodes the logs and computes
  convergence, Jain's index and queue statistics. Writes:
  - `results/validation/{summary.md, convergence.csv, queues.csv}`;
  - `timeseries/*_s1_{rate,queue}.csv`;
  - `results/figs/validation_{convergence,queue}.{png,pdf}`.
- `results/validation/<cc>_s<seed>.log`: run stdout; `start_units.txt`:
  mode, matrix and binary per policy (final runs first, legacy entries
  after them).
- `results/validation/raw/`: gzipped full stdout, binary logfile and idmap
  per run (95 MB, gitignored).
- `results/validation/legacy/`: the checkpoint 1 runs, same layout
  (`summary.md`, `convergence.csv`, `queues.csv`, logs, `matrices/` with the
  rescaled and trigger-flow matrices, `timeseries/`, `raw/`).
- `results/figs/validation_{convergence,queue}_legacy.{png,pdf}`: the
  checkpoint 1 figures.

## Legacy: checkpoint 1 results (old `build/` binaries)

Everything below is the checkpoint 1 validation, kept for the record. It was
measured on `HTSIM/htsim/sim/build/datacenter/` as built 2026-11-07 20:46
(source `b4832e8`, line numbers below at that commit), with the start-time
workarounds of L.1, `-dcqcn 100` step marking, and the legacy HPCC. Its
DCQCN is the **legacy DCQCN** (shared α, packet-counting byte counter,
R_AI 5 %, step ECN), not the paper's. Its HPCC is the **legacy HPCC**
(compounding window). Data: `results/validation/legacy/`; figures:
`results/figs/validation_{convergence,queue}_legacy.{png,pdf}`. Every
divergence listed here has since been fixed (`HTSIM/OUR_CHANGES.md`), and the
results above supersede these.

**Checkpoint 1 verdict, as written then.** HTSIM's DCQCN behaved like the
DCQCN paper qualitatively but with real differences: no hyper increase, a
single α shared by every flow, step ECN marking, and an additive-increase step
50x larger relative to line rate. HTSIM's HPCC did not reproduce the HPCC
paper at all: the first flow took 270-350 Gbps and kept it, later flows got
25-55 Gbps, nothing converged, and the window overflowed ~200 us after the
flow started, dumping flow 1's whole 486 MB into its NIC queue. So the
"DCQCN converges more slowly than HPCC" check failed, because of the HPCC
implementation.

### L.1 Flow start times were not honoured by the checkpoint 1 binaries

The `.cm` parser stores `start` in picoseconds (connection_matrix.cpp:755,
"start is in picoseconds already"), but:

- `RoceSrc::connect` (roce.cpp:134 at HEAD b4832e8) does
  `timeFromUs(starttime)`, so RoCE and DCQCN read the number as microseconds.
  A 2 ms start written as 2000000000 ps becomes 2000 s.
- `EqdsSrc::connect` (eqds.cpp:432) has the same bug.
- `HPCCSrc::connect` (hpcc.cpp:143) calls `startflow()` immediately, so
  every HPCC flow starts at t = 0.

Verified with a probe: a second flow at 50 us never started in RoCE, DCQCN
or EQDS, and started at 0 in HPCC. `validation.sh` probes each binary and
writes the matrix in the units it actually honours
(`results/validation/start_units.txt`):

- RoCE, DCQCN and EQDS get the starts divided by 1e6.
- HPCC gets an HTSIM trigger chain. Three back-to-back "timer" flows run on
  the otherwise idle pair 14->15. Each is sized by a solo calibration run to
  last ~2 ms (97.4 MB), and its `send_done_trigger` starts the next real flow.

HPCC's real flows therefore start at 0, 2.07, 4.14 and 6.21 ms. Every
analysis uses the start times logged by the binary, never the nominal ones.
Uncommitted fixes for all three connect() bugs are in the working tree
(another agent's work), but they are not in `build/`. The probe means the
script stays correct after a rebuild. **Project impact:** every matrix with
nonzero start times, such as the E3/E6 matrices, is misread by every
transport in the current binaries. parse.py's start-time check already
refuses such runs.


### L.2 Convergence times, 5 seeds (legacy)

The full table, including departures and the HPCC/PFC variant, is in
`results/validation/legacy/summary.md`; per-run rows are in `legacy/convergence.csv`.
Values are median [min-max] over seeds, with the number of nc runs.

| policy | flow 2 joins (fair 200) | flow 3 joins (fair 133) | flow 4 joins (fair 100) | last departure, survivor to 400 |
|---|---|---|---|---|
| DCQCN | new: nc 5/5; all: nc 5/5 | new: 1.5 ms (4/5), nc 1/5; all: nc 5/5 | new: 4.7 ms [4.7-7.3]; all: nc 5/5 | 485 us [367-850] |
| HPCC | nc 5/5 (both) | nc 5/5 (both) | nc 5/5 (both) | 611 us [44-1217], nc 1/5 |
| EQDS | nc 5/5 (both) | 50 us [0-50] | 0 us (first bin) | 27 us |
| RoCE, no CC | 50 us (both) | nc 5/5 (both) | nc 5/5 (both) | 15 us [12-48] |

Here is what the "nc" entries mean, with seed-1 per-flow means, p5 and p95
over 50 us bins.

- **DCQCN never settles.** The flows oscillate together. With 2 flows, both
  sit at mean 147 Gbps, p5 91, p95 197, and their traces are identical. With
  4 flows the means are 79-95 Gbps (p5 59, p95 97-122). The rate swings are
  wider than the +-10 % band, and the average sits below fair share because
  the link is under-used: aggregate goodput is 293 Gbps with 2 flows and
  335 Gbps with 4, against 394 for RoCE. Jain's index is still 0.98-1.00,
  so DCQCN is fair *on average*.
- **HPCC never converges.** Flow 1 holds 350 Gbps against flow 2's 48. With
  4 flows the split is 272, 55, 44 and 26 Gbps (Jain 0.39). Aggregate is
  397 Gbps.
- **EQDS gets 2 flows slightly wrong.** The split is stable at 219/169 Gbps,
  in favour of whichever flow is older. A standalone 2-flow test reproduces
  it in either order, and two flows started together get 201/193. With 3 or
  4 flows EQDS is exact: 97 Gbps each with 4, p5 = p95.
- **RoCE without CC is unfair by port.** With 4 flows the split is 197, 98,
  51 and 48 Gbps, and which flows win depends on the seed's ECMP placement.
  Jain is 0.73-0.89 with 3-4 flows. RoCE converges after a join only when
  the arbitration happens to be fair, as at n = 2.

### L.3 Queues, legacy (seed medians; "4 flows" window = 0.5 ms after the 4th join until the first departure)

| policy | `LS4->DST8` median | p99 | max | all switch queues, median | where it sits |
|---|---|---|---|---|---|
| DCQCN | 4.1 KB (1 pkt) | 728 KB | 801 KB | 20 KB | empty, with 0.5-0.8 MB spikes every ~0.5 ms |
| HPCC | 109 KB (12 x 9 KB) | 190 KB | 190 KB | 354 KB | receiver downlink and agg/core queues; **plus 486 MB in flow 1's NIC queue** |
| EQDS | 4.2 KB (1 pkt) | 4.2 KB | 4.2 KB | 21 KB | ~one packet in service per hop; 265 KB spike at each join |
| RoCE, no CC | 914 KB (229 pkts) | 1291 KB | 1301 KB | 9.0 MB | spread upstream by PFC: `CS0->US4`, `US4->LS_4` ~1 MB each |
| HPCC, PFC 80/150 | 1079 KB | 1405 KB | 1414 KB | 4.1 MB | receiver downlink |

"1 pkt" is the packet being serialized. Queues count a packet until it
leaves (`_queuesize -=` happens at dequeue, queue_lossless_output.cpp:144).

- **DCQCN.** The sawtooth is the DCQCN signature. The queue climbs past
  K = 100 pkts = 400 KB (`-dcqcn 100`), and packets are ECN-marked on
  dequeue (queue_lossless_output.cpp:122). Each sink sends at most one CNP
  per 50 us (dcqcn.cpp:19, 214). Every sender cuts, and the queue drains to
  zero and stays there while rates recover on 55 us timers, so the link idles.
- **EQDS.** The 265 KB spike at each join is exactly the newcomer's
  unscheduled first window: `DEFAULT_CWND` 64 x 4150 B = 265.6 KB
  (main_eqds.cpp:33, 574). After that, the receiver's pull pacer meters
  arrivals to line rate and the queue holds one packet.
- **RoCE without CC.** Nothing but PFC limits the senders, so every hop's
  buffer fills to the PFC threshold. Pauses propagate hop by hop, which
  explains both the 9 MB fabric occupancy and the per-port unfairness above.
- **HPCC.** The switch queue is modest, but the backlog has moved into the
  sender NIC, which INT never sees (see below). Raising HPCC's PFC
  thresholds to 80/150 releases more of that NIC backlog into the fabric,
  and the receiver queue grows tenfold.


### L.4 Grounding in the simulator source (legacy code)

Line numbers are for commit b4832e8, matching the built binaries for these
paths. dcqcn.cpp is identical in the working tree.

#### DCQCN (legacy, `HTSIM/htsim/sim/dcqcn.cpp`)

| mechanism | HTSIM | DCQCN paper (Zhu et al., SIGCOMM 2015) | match? |
|---|---|---|---|
| start rate | `_RC = _RT = rate` (line rate), 38-40 | flows start at line rate | yes |
| rate decrease on CNP | `_RT = _RC; _RC *= (1 - alpha/2); alpha = (1-g)alpha + g`, 55-57 | same rule | yes |
| alpha decay | `alpha = (1-g)alpha` every 55 us without CNP, 131-134; g = 1/256, 23 | same rule; g = 1/256, 55 us timer | rule yes, **scope no**: `_alpha` is `static` (dcqcn.h:44, dcqcn.cpp:22), so one alpha is shared by every DCQCN flow in the simulation. Each flow's timer also decays it, so with n flows it decays n times per 55 us |
| fast recovery | `RC = (RT+RC)/2` while `max(T,BC) <= F`, F = 5, 84-86 | F = 5 rounds of `RC = (RT+RC)/2` | yes |
| additive increase | `RT += R_AI; RC = (RT+RC)/2`, 94-96; `R_AI = rate/20` = 20 Gbps at 400 G, 42 | R_AI = 40 Mbps on 40 G NICs (0.1 % of line rate) | rule yes, **step 50x larger** relative to line rate |
| hyper increase | `RT += (min(T,BC)-F) * R_HAI`, 88-91; `R_HAI = rate/10`, 43 | hyper increase once both timer and byte counter pass F | **unreachable**, see the next row |
| byte counter | `_byte_counter += _highest_sent - _old_highest_sent`, compared with `_B = 10000000`, 24, 120-125 | B = 10 MB | **broken**: `_highest_sent` counts *packets* in RoceSrc (roce.cpp:302), so BC fires after 10 M packets (40 GB). Our largest flow is 122,917 packets, so BC stays 0 and hyper increase never runs. Increase is purely timer-driven |
| rate-increase timer | 55 us (`_cc_update_period`), 20, 138-144 | 55 us | yes |
| CNP generation | at most one CNP per 50 us per flow, 19, 212-221 | N = 50 us | yes |
| ECN marking | step: mark on dequeue if queue > K = `-dcqcn` x MTU (queue_lossless_output.cpp:122, main_roce.cpp:335) | RED-like: Kmin, Kmax, Pmax (paper: 5 KB / 200 KB / 1 %) | **no**, step marking at 400 KB |

What the runs show, mechanism by mechanism:

- Recovery after the last departure takes 485 us [367-850] from ~190 to
  ~395 Gbps. That matches timer-only increase: 5 fast-recovery halvings,
  then +20 Gbps on RT every 55 us, smoothed by `RC = (RT+RC)/2`. Step
  counting gives roughly 10 steps, about 550 us.
- The flows' identical sawtooth (legacy Figure 1, `validation_convergence_legacy.png`, 2-flow phase) fits the shared
  alpha and synchronized CNPs. A per-flow alpha would let flows drift apart.
- Step marking at 400 KB (vs. RED from 5 KB in the paper) combined with the
  50 us CNP interval explains the 0.5-0.8 MB queue spikes and the
  utilization gaps.

The same file explains DCQCN's slow runs. `DCQCNSrc::doNextEvent` reschedules
the source every 55 us (147) on top of RoceSrc's own pacing event (roce.cpp:343).
Each extra wakeup creates a pacing chain that never ends, so DCQCN runs take
~3 min here against 10-20 s for the other transports.

**Comparison with the DCQCN paper.** These are the paper claims we are
confident of.

- PFC alone is unfair: flows sharing an ingress port get less, and innocent
  flows suffer ("victim flow" / parking-lot behaviour). That is the paper's
  motivation for DCQCN. **Reproduced** by RoCE without CC: shares of 197,
  98, 51 and 48 Gbps set by port, and PFC back-pressure filling 9 MB of
  upstream buffer.
- DCQCN restores fairness among competing flows and keeps the queue around
  the marking thresholds. **Reproduced qualitatively**: Jain 0.98-1.00, and
  queues bounded near K instead of PFC-full.
- Convergence is driven by the rate-increase timer and byte counter, so it
  takes milliseconds rather than RTTs. **Consistent**: a newcomer takes
  1.5-7 ms to enter the +-10 % band, and the band is never held by all flows.
- Hyper increase: **diverges**, because it never runs.

Both the DCQCN-paper behaviour HTSIM shows and its quantitative differences
(utilization 73-84 %, synchronized oscillation) trace to the four divergences
in the table: shared alpha, R_AI, byte counter, and step ECN.

#### HPCC (legacy, `HTSIM/htsim/sim/hpcc.cpp`)

Parameters match the paper's defaults:

- η = 0.95 (line 30). maxStage = 5 (31). T = 12 us base RTT (29).
- W_init = T x line rate = 600 KB (78).
- Pacing rate = W / T (80, 202).

INT is stamped by every switch egress queue at dequeue: qlen, timestamp,
txBytes and link rate (queue_lossless_output.cpp:125-141). The sender
computes U as in the paper's Algorithm 1 (`measureInFlight`, 339-382):
`u' = min(qlen, qlen_ref)/(B·T) + txRate/B` with
`txRate = (txBytes - txBytes_ref)/(ts - ts_ref)` (362, 364), then
`U = (1 - τ/T)U + (τ/T)u` (373). It then computes the window
(`computeWind`, 384-417): `W = Wc/(U/η) + W_AI` if U ≥ η or
incStage ≥ maxStage (401-402), else `W = Wc + W_AI` (410). W_AI = MSS =
9000 B (68). Against the paper, the code differs in three ways.

1. **The reference window is overwritten on every ACK.** In the paper, W is
   recomputed per ACK from a reference Wc that changes only once per RTT
   (`updateWc`). HTSIM uses `_cwnd` as both W and Wc and assigns the result
   on *every* ACK, including the `updateWc = false` path:
   `_cwnd = computeWind(measureInFlight(ack), false)` (198, also 193). The
   per-RTT update therefore compounds per ACK:
   - W_AI is added per ACK, which doubles the window each RTT while
     U < η. Flow 1 grows 609 KB -> 960 KB in its first 40 ACKs.
   - With incStage ≥ 5 and U < η, `Wc/(U/η)` multiplies per ACK.
   - Flow 1's window passes 1e14 B at 179 us and overflows to ~1.8e19 B at
     202 us (from the binary's own CWND printouts), the same in all 5 seeds.
   - With an effectively infinite window and pacing rate W/T, the source
     hands all its data to its NIC. The `SRC0->LS0` host queue holds
     **486 MB at 200 us** (`results/validation/raw/hpcc_s1.dat.gz`). After
     that, flow 1 is a PFC-paced NIC backlog that no congestion control
     touches, and it takes 270-350 Gbps until it is empty. Later flows join
     a link already at U ≈ 1 and settle at windows of 56-140 KB.
   - The 2-hop timer flows hit 64 MB windows the same way (calibration log).
2. **The INT reference `L` is never refreshed.** The paper sets `L = ack.L`
   after every ACK. HTSIM sets `_link_info` only when the hop count changes,
   i.e. at the first ACK (378). From then on:
   - txRate is the average since the flow's first ACK, not over the last RTT.
   - τ = ts − ts_first exceeds T almost at once, so the EWMA degenerates to
     `U = u` (373).
   - The queue term uses `min(qlen_now, qlen_at_first_ACK)`. For flow 1,
     which started on an empty network, that is 0 forever, so flow 1 cannot
     see queueing at all. Later flows capture whatever queue existed at
     their first ACK.
   So each flow sees a different, stale U: a built-in unfairness even
   without bug 1.
3. **`_incStage` and `_Wc` are never initialised.** `_Wc` is never used
   (hpcc.h:121-122; the constructor sets neither).

The binary also prints two lines per ACK (194, 199). Two more points about
the build:

- **The built `htsim_hpcc` is not deterministic.** The same `-seed 1` command
  run twice gave flow-1 finish times of 12,789 and 11,889 us. HEAD b4832e8
  still has `srand(time(NULL))` in the constructor (65). The fix described in
  OUR_CHANGES.md is only in the working tree and is not in `build/`, so the
  HPCC seed spread here includes wall-clock randomness. Every one of the 5
  runs shows the same qualitative failure, so the conclusion does not depend
  on it.
- **W_AI.** The paper derives W_AI from W_init·(1−η)/N for N expected
  concurrent flows. HTSIM declares `_N = 10` (32) but never uses it, and
  sets W_AI = MSS. That gives 9000 B, ~3x the 3 KB the formula would give.

**Comparison with the HPCC paper** (Li et al., SIGCOMM 2019). The paper's
abstract claims that HPCC "can quickly converge to utilize free bandwidth"
while keeping near-zero in-network queues. The design converges in a few
RTTs by using U/η from INT instead of AIMD probing, targets 95 %
utilization, and gets fairness from W_AI.

- **Not reproduced**:
  - no join converges, Jain 0.39 with 4 flows;
  - the receiver queue is 109 KB median, not near zero;
  - flow 1's 486 MB NIC backlog defeats the point of the design.
- Aggregate utilization is 397 Gbps (99 %), not the 95 % target, because
  flow 1's NIC queue keeps the link saturated whatever HPCC decides.
- After the last departure, the survivor ramps to 400 in 44 us in some runs
  (W_AI per ACK compounding) and 1.2 ms in others (stale U).

"DCQCN converges more slowly than HPCC" therefore **cannot be confirmed with
this HPCC**. Against EQDS, the other INT/credit-precise scheme here, DCQCN
does converge far more slowly: milliseconds versus one 50 us bin.


#### EQDS (legacy line numbers)

Each receiving host has one `EqdsPullPacer`.

- **Pull timing.** It sends a pull every `_pktTime` = 0.99 x one MTU
  serialization time at line rate (2179, created with modifier 0.99 at
  main_eqds.cpp:550).
- **Pull size.** Each pull grants `_credit_per_pull` = 8 x 512 B, one MTU
  (23).
- **Pull order.** `doNextEvent` (2183-2265) serves retransmission requests
  first, then active senders. It takes the front of the list via
  `mostUrgent`, which is FIFO for equal priority, and re-queues served sinks
  at the back (2215-2230). That is per-packet round robin across senders.
- **First window.** A new sender may send an unscheduled first window,
  `_credit_spec = _maxwnd` (362), with `_maxwnd` set to `DEFAULT_CWND` 64
  packets by main_eqds.cpp:574.

These four points explain the measurements:

- Fair share is reached as soon as the newcomer's first pull arrives (<= one
  50 us bin, i.e. a few RTTs).
- The receiver queue stays at the packet in service.
- The only queue excursion is one 265 KB burst per join.
- Departures re-converge in 27-41 us.

The 2-flow 57/43 split in favour of the older flow is not explained by the
pull pacer alone. Two flows started together split 201/193, so it is
history-dependent, probably credit or speculative state carried by the
incumbent. We did not chase it further. EQDS ignores `-seed` for this
workload: all 5 seeds gave identical finish times.


### L.5 Consequences drawn at checkpoint 1 (all since acted on)

- **HPCC results are an artifact.** Every HPCC result, including the
  CLAUDE.md reference number (6,797 us, 2.03x floor), runs with the per-ACK
  compounding window. The smoke workload `kv_llama70b_4k_tp8.cm` (one run,
  same binary) is a clear example:
  - all 8 flows start at t = 0 on an idle fabric;
  - every flow's window peaks between 40 MB and 1.8e19 B within ~370 us,
    against a 600 KB BDP and 168 MB flows.
  A flow that starts on an idle path blows up its window and dumps most of
  itself into its NIC queue. HPCC "results" then mostly measure NIC FIFO
  plus PFC behaviour. Fixing it is a small change:
  - compute W into a local variable and update `_cwnd`/Wc only when
    `updateWc`;
  - refresh `_link_info` from `ack._int_info` after each ACK;
  - (optionally) initialise `_incStage`.
  After the fix, this validation should be re-run.
- **DCQCN differs from the paper.** HTSIM's DCQCN has no hyper increase, a
  global alpha and step marking. Any DCQCN-vs-X comparison should say so. Its
  throughput loss (73-84 % link utilization at 2-4 flows here) partly comes
  from step marking at a high K plus synchronized cuts.
- **Matrix start times are misread.** Any matrix with nonzero start times is
  misread by all four policies in the current `build/` binaries (see "Flow
  start times" above).

