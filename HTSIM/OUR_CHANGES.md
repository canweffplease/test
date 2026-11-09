# Our changes to HTSIM

Vendored from https://github.com/spcl/HTSIM at commit
`ac1aea6e34ccc8249cb59b4c7cdd920283c9cfdb` (2026-10-05).

Edit these files directly and commit like any other code. The original diff
against upstream is kept at `docs/htsim-changes-vs-upstream.patch` for the
record and for a future upstream pull request.

## Summary of changes

One line per change, with the section that describes it, in the order the
work was done. "Harness only" means no simulator source changed.

- DCQCN termination fix (`_last_alpha_update`, stop on `_done`): "Fixes applied"
- Debug prints commented out (dcqcn.cpp, roce.cpp, fat_tree_topology.cpp): "Fixes applied"
- macOS `<cstdint>` includes: "macOS portability"
- Topology loader fix (`set_queue_sizes(0)` is a no-op): "Topology files could not be loaded at all"
- 8:1 trees need `-q 2000` (harness only): "Oversubscribed fat trees: resolved"
- Floor computed against the topology's link speed (harness only): "Our own bug: floor computed against the wrong link speed"
- EQDS stale NACK ignored, and the sequence-table (reorder window) stall: "EQDS on oversubscribed fabrics: abort, then a silent stall"
- EQDS `set_priority` (receiver credit priority): "Transport scheduling: per-flow priority in EQDS"
- HPCC `-topo`: "HPCC on topology files"
- Deterministic seeding (HPCC's `srand(time(NULL))` removed): "Runs were not deterministic: HPCC reseeded from the wall clock"
- Start-time units in RoCE, DCQCN and EQDS: "Flow start times were read in the wrong units (RoCE, DCQCN, EQDS)"
- HPCC honours the start time, and flowId in the finish line: "HPCC ignored the matrix start time; finish line now carries the flow id"
- Load-balancing knobs, and what `ecmp_ar` / `ecmp_host_ar` do (harness only): "Load balancing axis: what `-strat ecmp_ar` / `ecmp_host_ar` actually do"
- run.sh `-linkspeed` matched to the topology (harness only): "run.sh passed -linkspeed 400000 on 100 Gbps fabrics"
- DCQCN fidelity (per-flow alpha, hyper increase, step sizes, `-dcqcn_legacy`): "DCQCN made faithful to the paper"
- HPCC fidelity (Algorithm 1, `-hpcc_legacy`): "HPCC: from a compounding window to the paper's Algorithm 1"
- EQDS finished-flow credit and strict priority: "EQDS pull pacer: finished flows kept credit, priority was soft"
- run.sh `-q 2000` on every 128-node tree (harness only): "run.sh: 2000-packet buffers on every 128-node tree"
- Probabilistic (RED) ECN marking for DCQCN, run.sh default (`ECN_STEP=1` for step): "DCQCN switch marking: probabilistic (RED) ECN"
- DCQCN minimum-rate clamp at one R_AI step: a flow cut every 50 us for a few ms reached an integer rate of 0 and the pacer divided by it. Not bit-identical: it acts whenever a cut would go below R_AI, which moved most staggered-arrival DCQCN runs (E2, E3) and a few incast/oversubscribed cells; pre-clamp results are kept under `results/*/dcqcn_preclamp/`. See "DCQCN: minimum rate clamp".

Some early sections make claims that later work disproved. They are kept
where they were written, each followed by a **Correction** line that says
what changed and which document has the evidence.

## The reference numbers quoted in this file

Several sections compare against the CLAUDE.md reference numbers for
kv_llama70b_4k_tp8.cm on the default 16-node tree: EQDS 4,165, HPCC 6,797,
RoCE 10,240 and DCQCN 12,098 us. Read them as **legacy transports at seed
13**, one ECMP placement each:

- RoCE 10,240 us is a 3-way hash collision. The median over 40 seeds is
  6,842 us (2.04x). See "Runs were not deterministic".
- DCQCN 12,098 us is the legacy reaction point (R_AI = 5 % of line rate).
  The paper-faithful default gives 14,217 us at seed 13. See "DCQCN made
  faithful to the paper".
- HPCC 6,797 us was not seed 13 but one wall-clock-seeded run (a 2x
  placement), and it measured the window-overflow bug, not HPCC. Faithful
  HPCC gives 7,168 us on that placement (seed 4). See "HPCC: from a
  compounding window to the paper's Algorithm 1".
- EQDS 4,164.7 us is unchanged by every later EQDS fix (byte-identical
  logs), but it is still a single seed.

Runs are deterministic per seed since commit 2521a60. Older text that says
runs are nondeterministic, or that HPCC varies by 58 %, is describing the
wall-clock reseeding in "Runs were not deterministic".

**Current reference** (final transports, median over seeds 1-10, which give
six 2x and four 3x placements; `results/reference/reference_table.md`, also
in CLAUDE.md and docs/EXPERIMENTS.md): EQDS 4,409 us (1.31x), RoCE 6,842 us
(2.04x), HPCC 7,362 us (2.19x), DCQCN 13,488 us (4.02x). The legacy
transports over the same seeds: HPCC 7,224 us (2.15x), DCQCN with step ECN
8,530 us (2.54x).

## Fixes applied

**`htsim/sim/dcqcn.cpp`**: DCQCN never terminated.

- `_last_alpha_update` was never assigned in `DCQCNSrc::doNextEvent`, so once
  the alpha-decay condition first became true it stayed true forever and the
  rate timer re-armed indefinitely. The run never ended and eventually died on
  memory. Fixed by assigning it, matching what the adjacent `_last_cc_update`
  branch already did.
- Added an early return when `_done` is set, so the timer stops once the flow
  completes.
  **Correction:** each timer wakeup also started an extra RoCE pacing chain
  that ran until `-end`, so runs still lasted to `-end`. Fixed only in the
  faithful DCQCN path; see "A simulator-efficiency bug fixed along the way"
  under "DCQCN made faithful to the paper".
- Commented out debug `cout` calls in hot paths. One produced 97 million lines
  in a single short run.

**`htsim/sim/roce.cpp`**: two unguarded debug prints. The identical line in
`hpcc.cpp` is already commented out upstream.

**`htsim/sim/datacenter/fat_tree_topology.cpp`**: one unguarded debug print.

## Removed to keep the repo small

Dragonfly and SlimFly topology artifacts, the `experiments/` directory, and
checked-in `results/`. None are used by this project. Pull them from upstream
if ever needed.

## macOS portability

**`htsim/sim/data_collector.h`**, **`htsim/sim/loggertypes.h`**: added
`#include <cstdint>`.

These use fixed-width integer types but were the only two headers that did not
reach `<stdint.h>` through `config.h`. GNU libstdc++ supplies it transitively,
Apple's libc++ does not, so they would likely fail to compile on macOS.
Upstream CI only runs `ubuntu-latest`, so this is untested territory for them.

Otherwise the codebase is portable: C++17, no `__linux__` guards, no
Linux-only headers, no static linking, no variable-length arrays.

## Topology files could not be loaded at all

**`htsim/sim/datacenter/fat_tree_topology.cpp`**, `set_queue_sizes()`.

Every `-topo <file>.topo` run died with `Missing queue_up for tier 0`, in
every binary that accepts the flag. The shipped topology configs were
therefore completely unusable, which blocked our entire topology axis.

Cause: the from-file constructor does

    FatTreeTopologyCfg(istream& file, mem_b queue_size, ...) {
        read_cfg(file, queue_size);                      // sets queue sizes
        initialize(0u, _no_of_nodes, 0u, 0u, 0u, 0u, ...); // wipes them
    }

`read_cfg` correctly populates `_queue_up` and `_queue_down`, then
`initialize` calls `set_queue_sizes(0)` which overwrites every tier with
zero. `check_consistency()` then rejects the config.

The confusing part when debugging: adding an explicit `queue_up` line to the
.topo file produces `Duplicate queue_up setting`, because at parse time the
value really is set. It only gets zeroed afterwards.

Fix: make `set_queue_sizes(0)` a no-op, matching how `set_tiers(0)` and
`set_linkspeeds(0)` already treat zero as "already configured". One guard
clause, no behaviour change for the programmatic path.

This is a clean upstream bug and worth reporting.

### Still broken after the fix (resolved below)

**Correction:** this was not a fabric stall. 4:1 and 8:1 were EQDS aborting
and then stalling, fixed in "EQDS on oversubscribed fabrics", and 8:1 also
needed `-q 2000` (next section). Neither a PFC deadlock nor an unroutable
fabric was involved. The original note follows.

`fat_tree_128_1os.topo` now loads and runs (8 flows, 14,433 us with EQDS).
`fat_tree_128_4os.topo` and `fat_tree_128_8os.topo` load, start all flows, but
**no flow ever completes**, even with `-end 5000000` (5 simulated seconds)
where 1:1 finishes in 14 ms. Four times the oversubscription should be
roughly four times slower, not infinitely slower, so this looks like a stall
rather than slowness.

Those two files differ from 1os only in Tier 1:

    Oversubscribed 1  ->  Oversubscribed 4
    Radix_Up 4        ->  Radix_Up 1

Next step is to find out whether oversubscribed aggregation uplinks deadlock
under PFC, or whether the radix arithmetic produces an unroutable fabric.

## Oversubscribed fat trees: resolved

After the `set_queue_sizes` fix above, 4:1 and 8:1 still appeared to hang.
They were not hanging. Two separate things were happening.

**EQDS aborts on oversubscribed fabrics.** `htsim_eqds` exited with
SIGABRT, no message, immediately after starting all flows. Fixed, see
"EQDS on oversubscribed fabrics" below; `run.sh` and `sweep.sh` no longer
exclude it.

**8:1 needs bigger buffers.** At `-q 500` it completes but reports 324,586
lossless violations, meaning PFC failed and the numbers are meaningless. At
`-q 2000` the violations go to zero and the completion time is unchanged.
`run.sh` now sets the buffer per topology.

### Working, RoCE, same traffic, 128 nodes (seed 13 only)

**Correction:** one ECMP placement (default seed 13), run at
`-linkspeed 400000`. RoCE does not depend on that flag (docs/E1.md section
5.5), but it does depend on the placement: over seeds 1-5 RoCE on
ft128_1os is 2.03x or 3.05x floor (docs/E1.md section 4.3).

| topology | slowest shard | lossless violations |
|---|---|---|
| fat_tree_128_1os | 40,924 us | 0 |
| fat_tree_128_4os | 40,924 us | 0 |
| fat_tree_128_8os | 68,284 us | 0 at -q 2000 |

**1:1 and 4:1 are identical, and that is a result about our workload, not
the fabric.** Eight concurrent flows do not generate enough cross-pod traffic
to saturate the aggregation uplinks, so 4:1 oversubscription never binds.
Only at 8:1 does the pod boundary become the bottleneck.

**Correction:** "1:1 and 4:1 are identical" was a seed-13 coincidence: at
seed 13 both trees put 3 flows on the busiest link (docs/E1.md sections 4.1,
8.2). Over seeds 1-10, legacy HPCC gives 2.85x on 1os and 3.41x on 4os
("HPCC on topology files"). 4os has 4 agg-to-core uplinks per pod instead
of 16, so hash collisions are more frequent, and 4:1 does bind. The next
paragraph's call for heavier offered load in E2 still holds, but the
current matrices do exercise 4:1, through collisions.

This means the current hand-written matrices are too light to exercise the
topology axis at all. E2 needs the real workload generator with enough
offered load to saturate pod uplinks, not a handful of flows.

## Our own bug: floor computed against the wrong link speed

`parse.py` assumed 400 Gbps. A topology file sets its own link speeds and
overrides the `-linkspeed` flag, and all three `fat_tree_128_*` configs
specify `Downlink_speed_Gbps 100`. So every 128-node result was divided by a
floor four times too small and looked four times worse than it was.

**Correction:** the file sets the fabric's link speeds, but the transports
still take their own rate, credit pacing and timeouts from `-linkspeed`.
See "run.sh passed -linkspeed 400000 on 100 Gbps fabrics" (commit 2077902,
docs/E1.md section 6.3).

| | reported | correct |
|---|---|---|
| ft128_1os | 12.20x floor | 3.05x |
| ft128_4os | 12.20x floor | 3.05x |
| ft128_8os | 20.35x floor | 5.09x |

`run.sh` now reads the tier-0 downlink speed out of the topology file and
passes it to `parse.py`, and prints which speed it used on every run.

The corrected 1:1 number, 3.05x, is identical to the 16-node no-congestion-
control result, which is a useful sanity check: the same policy on the same
traffic gets the same distance from floor regardless of link speed. That is
exactly what normalizing to the floor is supposed to buy us, and it only
worked once the floor was right.

**Correction:** the match is narrower than stated. RoCE's TP=8 result is
(flows on the busiest link) x 1.017 on every topology, and both seed-13
placements happen to put 3 flows on one link (docs/E1.md section 4.2).
Normalizing removes the link speed. It does not remove the placement.

## EQDS on oversubscribed fabrics: abort, then a silent stall

**`htsim/sim/eqds.cpp`, `eqds.h`, `datacenter/main_eqds.cpp`.** Two
separate bugs, the second hidden behind the first.

### 1. The abort: a stale NACK after a retransmission timeout

The abort that fired was the one in `EqdsSrc::processNack` (gdb backtrace:
`processNack` <- `EqdsSrc::receivePacket` <- `Pipe::doNextEvent`), "NACKed
packet not in `_tx_bitmap`". Upstream left it there on the theory that it
can only be caused by a bug elsewhere, with a commented-out `return` for "if
you discover a case where this happens for real".

It happens for real. Traced with `-debug` on ft128_4os, flow 1->65, PSN
2615: sent at 996 us, its trimmed header reached the sink at 1,391 us
(395 us in queues behind the 4:1 aggregation uplinks, against a ~90 us
RTT estimate), the RTO fired at ~1,393 us, which erases the send record and
queues the retransmission, and the NACK arrived at 1,397 us for a packet the
source had already given up on. Same thing if a cumulative ACK overtakes the
NACK. In both cases the retransmission is already handled, so the NACK is
now ignored. The new `Stale NACKs ignored: N` line at the end of the run
counts them (67 on ft128_4os, 3,117 on ft128_8os, 0 on the 1:1 trees).

**Root cause, found later:** the ~397 us from send to RTO is the 399 us
minimum RTO that EQDS derived from `-linkspeed 400000` on a 100 Gbps fabric.
See "run.sh passed -linkspeed 400000 on 100 Gbps fabrics" (commit 2077902,
docs/E1.md sections 6.3-6.4). The guard stays: it is correct whenever
queueing delay exceeds the RTO. The counts above were measured at
`-linkspeed 400000`.

Also: every remaining bare `abort()` in eqds.cpp now prints `eqds.cpp:<line>
abort: <reason>` to stderr first, so the next one is not silent.

### 2. The stall: the receiver's reorder bitmap aliased

With the abort gone, 4:1 ran to the end of the event list with only 2 of 8
flows finished, and 8:1 with none. No error, the event list just drained:
every source thought it had nothing outstanding (`_tx_bitmap`, `_rtx_queue`
and `_unsent` all empty) while every sink was still waiting on a hole
(e.g. cumulative ACK stuck at 6,482 with PSNs up to 41,088 received).

Cause: the sink tracks out-of-order arrivals in `_epsn_rx_bitmap`, a
`ModularVector` of `eqdsMaxInFlightPkts` = 4,096 entries indexed by
`psn & 4095`, but nothing stopped the source sending further than 4,096 PSNs
past the cumulative ACK. Under heavy trimming a hole survives several
retransmission rounds while new data keeps flowing; once the source is 4,096
PSNs ahead, PSN X+4096 lands in the same slot as the hole X, the sink SACKs
X without having it, the source erases X's send record, and nobody ever
retransmits it. Confirmed by adding a sink-side check and disabling the fix:
it fires within milliseconds (`PSN 5943 beyond reorder window (cum ack 1746
+ 4096)`). Upstream never sees this because on a non-blocking fabric a hole
is repaired within one RTT, far less than 4,096 packets.

Fix, in the source: track the highest cumulative ACK seen
(`_highest_cum_ack`) and do not send new data once `_highest_sent` is
within 64 PSNs of `_highest_cum_ack + 4096` (`newDataBlockedByWindow()`,
checked in `sendIfPermitted`, `timeToSend` and `sendNewPacket`). The 64 are
reserved for RTS packets, which also consume a PSN and are themselves
refused beyond the hard limit. Retransmissions are always below
`_highest_sent`, so they are never blocked. `processAck` already calls
`sendIfPermitted`, so sending resumes as soon as the cumulative ACK moves.
The sink now aborts with a message if a PSN ever arrives beyond its window,
rather than silently corrupting state.

Not fixed by enlarging the bitmap: at 65,536 entries the window never binds,
holes outlive more retransmission rounds, and 8:1 gets much worse (109.7 ms,
1.9 M retransmissions). 4,096 also acts as useful flow control.

### Validation

Same commands as `run.sh` (`-strat ecmp_host -paths 16 -q 500`, `-q 2000`
for 8:1), workloads/kv128_conv.cm on the 128-node trees and
workloads/kv_llama70b_4k_tp8.cm on the default tree. All 8 flows complete,
zero lossless violations, and three repetitions gave identical results.
Results are in `results/eqds_fix/`.

| topology | EQDS before | EQDS after | RoCE | bottleneck floor |
|---|---|---|---|---|
| default 16-node | 4,165 us | 4,164.7 us | 10,240 us | 3,355 us |
| fat_tree_128_1os | 14,433 us | 14,433.6 us | 40,924 us | 13,422 us |
| fat_tree_128_4os | SIGABRT | 32,649 us | 40,924 us | 26,844 us |
| fat_tree_128_8os | SIGABRT | 80,101 us | 68,284 us | 53,687 us |

Over `-seed 1..5` (what `sweep.sh` now runs) every run also completes with
zero violations and no abort:

| topology | median slowest, seeds 1-5 | min - max |
|---|---|---|
| default 16-node | 4,416 us | 4,159 - 4,504 |
| fat_tree_128_1os | 15,403 us | 14,966 - 15,679 |
| fat_tree_128_4os | 34,067 us | 33,490 - 36,737 |
| fat_tree_128_8os | 79,357 us | 55,406 - 81,134 |

These tables were run at `-linkspeed 400000` on the 100 Gbps trees (run.sh
before commit 2077902). Even the 1:1 TP=8 row moves: 1.075x at 400000 and
1.084x at 100000 (docs/E1.md sections 2 and 8.1). The oversubscribed rows
are to be rerun at matched speed. The default-tree RoCE 10,240 us is the
seed-13 reference ("The reference numbers quoted in this file").

Bottleneck floor here is the oversubscribed tier, not the host link: on
4:1 the eight flows share pod 0's four 100 Gbps aggregation uplinks; on
8:1 each group of four flows shares one 100 Gbps ToR uplink (Tier 0 there
has `Radix_Up 1`). The default and 1:1 numbers are unchanged, because the
window never binds when holes are repaired within an RTT.

**EQDS is slower than RoCE on 8:1, and that is EQDS, not this fix.** EQDS
pulls at the receiver's line rate and assumes the core is not the
bottleneck. At 8:1 four receivers each pull 100 Gbps through one 100 Gbps
ToR uplink, so that queue stays full (2,000 packets, ~660 us) and most
packets are trimmed: 842 k retransmissions for 328 k new packets. Queueing
pushes RTTs past the RTO, giving ~192 k timeouts, and the spurious ones
deliver ~87 k duplicate packets, about a quarter of the bottleneck's
capacity. RoCE with PFC loses nothing and wastes nothing. With EQDS's
optional sender-side CC (`-sender_cc`) the same run takes 60,926 us,
beating RoCE, with zero stale NACKs. That flag is not in `run.sh`. Adding it
would be a different policy, and would have to be re-checked against the
default-topology reference numbers.

**Correction:** this was measured at `-linkspeed 400000`, so each receiver
pulled at 400 Gbps, not the 100 Gbps stated above, and the minimum RTO
(`150 us + q x 6 x 8 / linkspeed`) was about 4x shorter than at matched
speed. Part of the trimming and of the ~192 k timeouts is that
misconfiguration, not EQDS ("run.sh passed -linkspeed 400000 on 100 Gbps
fabrics", docs/E1.md section 6.3). To be rerun.

Not changed: `rtxTimerExpired` does not subtract the timed-out packet from
`_in_flight` the way `processNack` does (upstream comment "update
flightsize?"). This only matters with `-sender_cc`, where `_in_flight`
gates the window.

## Transport scheduling: per-flow priority in EQDS

**`htsim/sim/eqds.h`, `eqds.cpp`, `datacenter/main_eqds.cpp`.**

`//eqds_snk->set_priority(crt->priority);` was commented out because the
method never existed. The line was copied from `main_ndp.cpp`, where
`NdpSink::set_priority` feeds a priority pull queue (`PrioPullQueue`).
EQDS's `EqdsPullPacer` kept three plain FIFO lists
(`_rtx_senders`, `_active_senders`, `_idle_senders`, each marked "TODO
priorities?"), so `prio` in a .cm was parsed into `connection::priority`
and then dropped.

Added:

- `EqdsSink::set_priority(int)` / `priority()`, default 2000000 (the
  parser's default). **Lower is more urgent**, same as NDP.
- `EqdsPullPacer::mostUrgent()`. When choosing which sink gets the next
  pull (credit), the pacer now takes the lowest-priority-value sink from
  `_rtx_senders`, or failing that from `_active_senders`, instead of the
  front of the list. Served sinks still go to the back, so sinks with equal
  priority are still served round robin. With no `prio` in the matrix every
  sink has the same priority and behaviour is identical to before (default
  topology still 4,165 us).
- `main_eqds.cpp` calls `set_priority(crt->priority)`.

**Correction:** measured in E4, this priority was only a tie-break. One
urgent flow got about 39 % of the receiver link, because a sink's demand
drains to 0 after each pull (docs/E4.md). It is strict since commit
a4e533e; see "EQDS pull pacer: finished flows kept credit, priority was
soft", which also replaces the semantics paragraph below.

Semantics: strict priority between flows **into the same receiving host**
(one pull pacer per host), applied to credit only. Retransmission pulls
still go before new-data pulls whatever their priority, as before. The
speculative first window each source sends before receiving any credit
ignores priority, as do idle/slow pulls. Priority is static per flow, so
`prio = size` gives shortest-flow-first, which is SRPT only when flows start
together; true SRPT would re-rank by remaining bytes. `prio = deadline` is
EDF. The value is a 32-bit `int` (`stoi`), so sizes above 2.1 GB, e.g. a
3.36 GB Llama-2-13B cache, must be scaled (KB, or microseconds for
deadlines). It has no effect on what switches do, only on which sender the
receiver credits next.

Validation: workloads/test_prio/, 8:1 incast into node 8 on the default
16-node tree at 400 Gbps, four 41.9 MB flows (even sources) and four
167.8 MB flows (odd sources), all starting at 0. Zero lossless violations.
Results are in `results/test_prio/`.

| matrix | small flows finish | large flows finish |
|---|---|---|
| incast_mixed_noprio.cm | 6,896-6,898 us | 17,271-17,272 us |
| incast_mixed_prio_size.cm (prio = size) | 3,901-3,902 us | 17,271 us |
| incast_mixed_prio_inverse.cm (large = 1, small = 2) | 17,271-17,272 us | 14,274-14,276 us |

Without priority the receiver splits credit eight ways, so a small flow
gets 50 Gbps and finishes in 6.9 ms (theory 6.7). With prio = size the
four small flows get all the credit and finish in 3.9 ms (theory 3.36), and
the large flows finish at exactly the same time as before because the
receiver link never idles. Inverting the priorities flips the completion
order, which shows it is the priority doing it, not flow size. Small flows
of equal priority still share among themselves and finish together.

`htsim_roce` and `htsim_hpcc` (`main_roce.cpp`, `main_hpcc.cpp`) never
read `crt->priority`, so `prio` has no effect there. Their
`-host_queue_type prio` is unrelated: a 3-level queue keyed on packet type
(control vs data), not per flow. Neither is implemented. `main_uec.cpp`
still has its line commented out. UEC has its own pull pacer in `uec.cpp`,
so the change there is not a one-liner.

## HPCC on topology files

**`htsim/sim/datacenter/main_hpcc.cpp`**: added `-topo <file>`.

`htsim_hpcc` had no `-topo` flag and always built the auto-generated 3-tier
fat tree, so HPCC could not run on any `.topo` file and `run.sh` refused the
combination. The flag and the construction now mirror `main_roce.cpp`
exactly: with `-topo`, `FatTreeTopologyCfg::load(file, queuesize, qt,
snd_type)` (so `-q` still sets every tier's queue size, the file sets tiers,
radices, link speeds and latencies), plus the same node-count mismatch check;
without it, the original `FatTreeTopologyCfg(3, nodes, linkspeed, ...)`. As in
RoCE, `-linkspeed` still sets the HPCC source's own rate and initial window
(BDP = 12 us x linkspeed) even when the file says the NIC is 100 Gbps.
Passing `-linkspeed 100000` instead changes HPCC results on ft128_1os by
under 0.3% over 10 seeds (HPCC re-sizes its window from INT after one RTT),
so we left `run.sh` at 400000 for all policies to keep them matched.

**Correction:** superseded. run.sh now passes the topology's own speed to
every policy (commit 2077902, "run.sh passed -linkspeed 400000 on 100 Gbps
fabrics"), and `-hpcc_T 16.4` to HPCC on ft128 (commit 883a3be). The 0.3 %
figure is for legacy HPCC, which had no window cap. Faithful HPCC caps W
at B x T, so the flag matters there.

`run.sh` no longer refuses hpcc on non-default topologies and `sweep.sh`
includes hpcc there. Default-topology output is unchanged by this edit (the
new code path is only taken with `-topo`).

### HPCC, kv128_conv.cm (8 x 167.8 MB, 100 Gbps floor 13,422 us), seeds 1-10

Legacy HPCC (the window-overflow version) at `-linkspeed 400000`, before
commits 2077902 and 883a3be. To be rerun.

| topology | median slowest | x floor | min - max | violations |
|---|---|---|---|---|
| fat_tree_128_1os | 38,232 us | 2.85 | 29,137 - 48,399 | 0 |
| fat_tree_128_4os | 45,787 us | 3.41 | 43,132 - 71,411 | 0 |
| fat_tree_128_8os | 69,376 us | 5.17 | 66,099 - 83,909 | 0 at -q 2000 |

Per-seed CSVs in `results/lb/topo/`. Note that over 10 seeds **4:1 is
worse than 1:1**, unlike the single RoCE run above that found them identical.
That single run was one ECMP placement (seed 13). The 4os file has
`Radix_Up 1` at tier 1, so each pod has 4 agg->core uplinks instead of 16:
fewer core paths means more hash collisions, which show up once you sample
more than one placement. See the next section.

## Runs were not deterministic: HPCC reseeded from the wall clock

**`htsim/sim/hpcc.cpp`**, `HPCCSrc::HPCCSrc()`: commented out
`srand(time(NULL))`.

Root cause. `rng.cpp` replaces libc `srand/rand/srandom/random` with one
global `mt19937`, which `main_*.cpp` seeds with `-seed` (default 13). The
HPCC source constructor then called `srand(time(NULL))` for every flow,
reseeding that global generator from the wall-clock second. Every random
draw after topology construction therefore depended on the time of day, and
`-seed` was overridden. The identical line in `roce.cpp` is already commented
out upstream, which is why RoCE and DCQCN looked deterministic. Fix: comment
it out the same way. One line, HPCC only.

What the randomness feeds, all in ECMP placement:

- `_pathid = random() % 256` per source: the ECMP entropy carried in every
  data packet (all 8 flows got the *same* pathid under the old code, since
  every constructor reseeded with the same second).
- `FatTreeSwitch::_hash_salt = random()` per switch (drawn before the HPCC
  sources, so this part was already seeded).
- `permute_paths()` shuffling each ToR/agg uplink list on the first lookup,
  during the run (time-seeded under the old code).
- next hop = `freeBSDHash(flow_id, pathid, salt) % n_uplinks` at each ToR and
  agg (`FatTreeSwitch::getNextHop`, case `ECMP`).

Nothing else in the default configuration is random (`-start_delta` is 0,
and the unordered maps keyed by pointers are only looked up, not iterated).

Verification (`results/determinism/`):

- seed 42 three times: bit-identical per-flow completion times, slowest
  10,749.2 us each time.
- seeds 1-100: 20 distinct outcomes from 3,415 to 14,493 us.
- The distribution is unchanged by the fix. Before (30 wall-clock-seeded
  runs) vs after (100 seeds), fraction of runs in each collision class:
  2x 63% vs 64%, 3x 30% vs 34%, 4x 7% vs 1%, 1x 0% vs 1%. The difference
  is within sampling error for n=30.

### What the spread physically is: ECMP hash collisions

The default topology is a K=4 fat tree: 4 pods of 4 hosts, 2 ToRs (2 hosts
each) and 2 aggs per pod, 4 cores, all links 400 Gbps. In
`kv_llama70b_4k_tp8.cm` flow i goes host i -> host i+8, so flows 0-3 go
pod 0 -> pod 2 and flows 4-7 go pod 1 -> pod 3; the two groups share no
link. The source ToR hashes to one of 2 aggs, the agg hashes to one of 2
cores, and the rest of the path is forced. **So each flow has 4 possible
paths, not 16**: `-paths` is ignored by `htsim_roce`/`htsim_hpcc` in
`ECMP_FIB` mode (it only gates an argument check; nothing ever calls
`setPath()`). Flows 0 and 1 share a source ToR and a destination ToR, as
do 2 and 3, so the pair must also land on different aggs.

Treating each hash as uniform and independent, a pod pair is collision free
only if its 4 flows use 4 distinct cores and the two flows sharing a ToR use
different aggs: 4!/4^4 x 2/3 = 1/16. Both pod pairs clean: 1/256. Enumerating
all 4^8 placements with a max-min-fair fluid model
(`results/determinism/ecmp_collision_model.py`), the slowest shard is:

| slowest shard | model P | HPCC, 100 seeds | RoCE, 40 seeds |
|---|---|---|---|
| 1x floor (no shared link) | 0.004 | 0.01 (3,416 us) | 0.00 |
| 2x (two flows share a link) | 0.631 | 0.64 (6,796 - 7,290 us) | 0.675 (6,831 - 6,842 us) |
| 3x (three share) | 0.334 | 0.34 (10,676 - 10,749 us) | 0.30 (10,240 us) |
| 4x (all four share) | 0.031 | 0.01 (14,493 us) | 0.025 (13,649 us) |

Model E[slowest] = 2.39x floor = 8,027 us. A collision divides the shared
link's 400 Gbps between the colliding flows for the whole transfer, since
every flow is the same size and starts at t=0. The slowest shard is
therefore set by the worst hash collision, not by congestion control.

Consequences for our numbers:

- **The "0% spread" of RoCE/DCQCN was an artifact of always using seed 13.**
  RoCE over 40 seeds has the same 2x/3x/4x spread as HPCC; its median is
  6,842 us (2.04x), not 10,240 us. Seed 13 happens to be a 3x placement.
- **The same seed gives the same placement in RoCE and HPCC** (all 40
  shared seeds fall in the same class), so seeds give paired comparisons.
  Within a placement RoCE is slightly *faster* than HPCC (3x class: 10,240
  vs 10,676-10,749 us; 2x: 6,831 vs 6,796-7,290 us). HPCC targets 95%
  utilisation and is not exactly max-min fair when one flow crosses two
  bottlenecks. The 6,797 vs 10,240 us HPCC/RoCE gap in CLAUDE.md compares
  two different ECMP placements, not two congestion controls.
  **Correction:** that HPCC was the legacy window-overflow version, which
  ran as a PFC-paced NIC backlog rather than at its 95 % target. Faithful
  HPCC sits at k / (0.95 x 0.993) per collision class ("HPCC: from a
  compounding window to the paper's Algorithm 1", smoke workload).
- `sweep.sh` now runs repetition i with `-seed i`. Repeating one seed is
  pointless now that the simulator is deterministic.

## Flow start times were read in the wrong units (RoCE, DCQCN, EQDS)

**`htsim/sim/roce.cpp`** (`RoceSrc::connect`, line 134) and
**`htsim/sim/eqds.cpp`** (`EqdsSrc::connectPort`, line 432). Fixed in
commit 2521a60.

The `.cm` parser already stores `start` in picoseconds
(connection_matrix.cpp:755, "start is in picoseconds already"). RoCE, and
therefore DCQCN, which is a RoCE source, scheduled its first event at
`timeFromUs((double)starttime)`. EQDS did the same with
`timeFromUs((uint32_t)start_time)`. Both read a picosecond value as
microseconds and multiplied it by 1e6, so a 2 ms start (2,000,000,000 ps)
became 2,000 s and a staggered flow never started before `-end`. The
`(uint32_t)` cast in EQDS also truncated any start above 4.29 ms before
rescaling it. HPCC had a third variant: it ignored `start` entirely (next
section).

Fix: pass the value through unchanged,
`eventlist().sourceIsPending(*this, starttime)`. One line in each file.

Nobody noticed because every matrix before E2/E3 started every flow at 0,
where the unit makes no difference, so those results are identical by
construction. docs/VALIDATION.md ("Flow start times are not honoured by the
built binaries") found it with a probe: a second flow at 50 us never started
in RoCE, DCQCN or EQDS, and started at 0 in HPCC. `validation.sh` still
probes each binary for the unit it honours, so it works on old and new
builds. parse.py refuses any run whose logged start times disagree with the
matrix, so an old binary cannot produce a recorded result on a staggered
matrix. The DCQCN fidelity convergence runs below feed
`converge_stagger.cm`'s picosecond starts directly to a build with this
fix.

## HPCC ignored the matrix start time; finish line now carries the flow id

**`htsim/sim/hpcc.cpp`**, `HPCCSrc::connect()` and `doNextEvent()`.

`connect()` called `startflow()` directly, so every HPCC flow started at
t=0 whatever the `start` field in the matrix said. It now does
`eventlist().sourceIsPending(*this, starttime)` (starttime is already in
picoseconds, as in `ndp.cpp`), and `doNextEvent()` calls `startflow()` on
that first wakeup (the upstream code for this was present but commented
out, with an `assert(_flow_started)` in its place). The "Flow ... finished
at" line now also prints `flowId <id>`, the matrix `id` (main_hpcc.cpp
already passes `crt->flowid` to `set_flowid`), matching EQDS, so parse.py
can match flows on matrices that reuse a src/dst pair.

Check: `run.sh hpcc default start_test workloads/e3_load30_ft16.cm` runs all
72 flows, logged starts span 9.4 - 84.7 ms and agree with the matrix (start
skew 0.000 us, flows matched by flowId), 0 lossless violations. For matrices
that start everything at 0 the results are bit-identical to before (seeds
1, 2, 3, 13, 42 on kv_llama70b_4k_tp8.cm).

## Load balancing axis: what `-strat ecmp_ar` / `ecmp_host_ar` actually do

`run.sh` now takes `STRAT` (default `ecmp_host`), `PATHS` (default 16),
`SEED` and `END` from the environment; non-default values go into the
result tag (`roce_default_load100_ar_s1`). No simulator change was needed
for this; the findings below are from reading `fat_tree_switch.cpp`
(`FatTreeSwitch::getNextHop`) and the `main_*.cpp` argument parsing.

**`ecmp_host`** (baseline, `FatTreeSwitch::ECMP`): every ToR and agg picks
`freeBSDHash(flow_id, pathid, salt) % n_uplinks`. Static per flow, set by
the seed (previous section).

**`ecmp_ar`** (`ADAPTIVE_ROUTING`, switch-local, flowlet granularity):

- Decided independently at every switch, using only that switch's own
  egress queues. No end-to-end or downstream congestion information.
- Packets under 100 B (ACKs) are ECMP hashed.
- First data packet of a flow at a switch: choose the uplink with the
  lowest `quantized_queuesize()` (4 levels: <5%, <10%, <20%, >=20% of the
  queue's max size, which is 4.5 MB at `-q 500`; refreshed at most every
  0.1 us), random tie-break. At t=0 every queue is empty, so the initial
  placement is uniformly random: **the same collision lottery as ECMP**.
- Later packets stick to that port (`PER_FLOWLET`, `_sticky_delta` = 10 us
  from `-ar_sticky_delta`). Only when a packet arrives >10 us after the
  flow's previous one at that switch, and then only on a 50% coin flip,
  is the choice re-evaluated, and the flow moves only if some port is
  strictly lower in quantized queue level.
- A flow sending at line rate has ~0.1-0.2 us between packets, so the
  flowlet only expires if the source is stopped, i.e. PFC-paused or
  window-limited. **Rerouting needs both a pause (to open a gap) and a
  standing queue (to make one port look worse).**
- `-ar_method` changes the comparator (pause state, queue, utilisation);
  default is queue size. `-out_of_order` (roce only) also switches to
  `PER_PACKET`: every data packet goes to the least-loaded port.
- `ecmp_ar` and `ecmp_rr` set `path_entropy_size = 1`, which is harmless:
  `-paths` is never used for ECMP_FIB routing in roce/hpcc.

**`ecmp_host_ar <n>`** (`ECMP_ADAPTIVE`): per packet, take the ECMP hash
choice, then with probability 50% call `replace_worst_choice()`: if the hashed
port is tied with the worst port, move the packet to a random best port.
When all ports are at the same quantized level (the normal case, since
levels are coarse) that means **half of all data packets are sprayed
uniformly at random**. The `<ar_thresh>` argument is stored in
`_ar_fraction` and never read; the 50% is hard-coded
(`random()%100 < 50`).

### Results, default 16-node topology, 400 Gbps

Floors: tp8 3,355 us per shard; incast 26,844 us (8 shards through one host
link). Seeds 1..n; n is the number of completed runs, "fail" are crashes.
`results/lb/seed_sweep.csv` (+ `seed_sweep_summary.txt`) and
`results/lb/raw_runs.csv` (the run.sh runs, 3 seeds, incl. DCQCN).

| cc | strat | tp8 median (x floor) | tp8 n / fail | incast median (x floor) |
|---|---|---|---|---|
| roce | ecmp_host | 6,842 (2.04) | 40 / 0 | 27,286 (1.02) |
| roce | ecmp_ar | **3,570 (1.06)**, max 3,681 | 12 / 0 | 29,414 (1.10) |
| roce | ecmp_ar, -out_of_order (per packet) | 3,425 (1.02) | 4 / 0 | 125,288 (4.67) |
| roce | ecmp_host_ar 50 | 124,364 - 171,739 (37-51) | 3 / 0 | 293,186 - 875,387 (11-33) |
| roce | ecmp_host_ar 50, -out_of_order | 14,090 (4.20) | 4 / 0 | 149,076 (5.55) |
| hpcc | ecmp_host | 7,138 (2.13) | 100 / 0 | 28,508 (1.06) |
| hpcc | ecmp_ar | 7,137 (2.13) | 25 / **5 crash** | 28,835 (1.07) |
| hpcc | ecmp_host_ar 50 | - | 0 / **10 crash** | - (5/5 crash) |
| dcqcn | ecmp_host | 8,221 / 11,997 / 12,209 | 3 / 0 | 34,979 / 34,901 / 35,043 (1.30) |
| dcqcn | ecmp_ar | 8,068 / 8,161 / 3,422 | 3 / 0 | 34,915 / 35,236 / 35,160 (1.31) |
| dcqcn | ecmp_host_ar 50 | >58-65 ms, 2-6 of 8 flows done when stopped | 0 / 3 | 36,045 / 36,130 / 36,238 (1.35) |

The HPCC and DCQCN rows are the legacy implementations, and the HPCC crash
counts are for the binary before commit 883a3be (see the correction below).

All completed runs have 0 lossless violations. Seeds do not pair across
strategies (AR consumes the random stream differently from ECMP), so compare
distributions, not seed by seed. DCQCN is only 3 seeds per cell, and with
n=3 it cannot be told apart from ECMP luck.

### Why: grounded in counters from an instrumented build

`results/lb/ar_reroute_trace.txt` comes from a scratch build with prints in
the flowlet branch of `getNextHop` (not committed). It counts first
placements, flowlet expiries (gap >10 us) and actual reroutes per run.

**RoCE + ecmp_ar fixes the tp8 collisions (2.04x -> 1.06x).** RoCE has no
congestion control, so a hash collision builds a standing queue until PFC
pauses the senders. The first pauses open >10 us gaps at about 42 us
(seeds 1, 2: 37-39 expiries, 12-16 reroutes), and the paused port now has a
higher queue level, so the flowlet moves to an idle core. The cost is
reordering: about 600 NACKs and 3,800-4,300 go-back-N retransmitted packets
per run (about 1% of the transfer). Seed 3 placed all flows apart at t=0, so
it had no gaps and no reroutes. With `-out_of_order` the sink accepts
reordering and per-packet AR gets to 1.02x.

**HPCC + ecmp_ar does nothing, then crashes.** HPCC keeps queues near empty
(eta = 0.95) and keeps sending, so it rarely creates both conditions. In 24
of the 25 completed runs there were 0 reroutes. The outcome is the t=0
random placement, so the distribution matches ECMP (median 7,137 vs 7,138
us). The only placements that do trigger rerouting are the bad ones
(3-flow collisions, first gap at 18.8 us instead of ~47 us). A reroute
reorders packets, the HPCC sink NACKs, and `HPCCSrc::processNack` zeroes
`_flightsize` while ACKs for the old window are still in flight. The next
such ACK trips `assert(ackno - _last_acked <= _flightsize)` in
`processAck` (hpcc.cpp:181). 5 of 30 seeds abort this way, and every
`ecmp_host_ar` run does. **HTSIM's HPCC cannot be combined with any load
balancer that reorders packets.** We did not fix this; it is a transport
bug outside the scope of these tasks.

**Correction:** fixed later in commit 883a3be (bug 8 in "HPCC: from a
compounding window to the paper's Algorithm 1"). HPCC no longer zeroes
`_flightsize` or asserts on ACKs after a NACK, and ignores stale NACKs, so
`ecmp_ar` completes 30 of 30 seeds in both legacy and faithful mode.
`ecmp_host_ar 50` still fails: faithful HPCC collapses like RoCE under
spraying, and legacy HPCC hits a different assert in its pacing path.

**Incast: AR cannot help and slightly hurts.** All eight flows end on the
same 400 Gbps host downlink, which no path choice avoids. ECMP already
reaches 1.02x (RoCE). AR's only effect is reroutes from PFC pauses
(seed 2: 5,119 expiries, 169 reroutes, 53,523 retransmitted packets),
which reorder packets and cost 8% (1.10x).

**ecmp_host_ar is effectively 50% random packet spraying.** Packets of one
flow take paths with different queue depths, so they arrive out of order on
every packet. A go-back-N sink (RoCE default, DCQCN tp8) retransmits
endlessly (DCQCN tp8: 215,000-238,000 NACKs before we stopped it). Even with
an out-of-order sink, the reorder window is exceeded
(incast `-out_of_order` per-packet AR: 1.09 M retransmitted packets).
DCQCN incast survives (1.35x) because ECN keeps upstream queues short, so
the sprayed paths have nearly equal delay and little reordering.

**Answer for 8 large synchronised flows:** with ECMP the slowest shard is
set by the worst hash collision (2x floor at best 63% of the time, 3x a
third of the time). Adaptive routing helps only when three conditions
hold: the transport lets queues build (no CC, i.e. PFC-only RoCE), senders
stall long enough for flowlets to expire, and the receiver tolerates
reordering. For RoCE all three hold, and AR removes the collision penalty
(1.06x). For HPCC none hold, so it is neutral (fatal only before commit
883a3be, see above). It is useless under incast, where the bottleneck is
the receiver's link, not the path.

## run.sh passed -linkspeed 400000 on 100 Gbps fabrics

**`scripts/run.sh`** (harness only, no simulator change). Fixed in commit
2077902. Evidence: docs/E1.md sections 5.5, 6.3, 6.4 and 8.1.

A topology file sets the fabric's link speeds, but each transport still
takes its own rate, credit pacing and timeout constants from `-linkspeed`.
run.sh passed a hard-coded `-linkspeed 400000` (run.sh:93 at the time) on
every topology, including the 128-node `.topo` files, which are 100 Gbps.
What that did on ft128 (line numbers from docs/E1.md, commit cab8d0c):

- **EQDS.** The receiver's pull pacer ran at 400 Gbps
  (`datacenter/main_eqds.cpp:550`, `:585`), issuing credit at about 4x what
  the 100 Gbps downlink can drain. The minimum RTO was
  `150 us + q x 6 x 8 / linkspeed` = 399 us (`datacenter/main_eqds.cpp:411`),
  too short for queues that drain at 100 Gbps. A single flow sent 12,757
  retransmissions on top of 41,061 new packets, and the completion time is
  exactly that inflation: (41,061 + 12,757) / 41,061 x 13,632 us =
  17,867 us predicted, 17,858 us measured, **1.33x** floor. At
  `-linkspeed 100000` the same flow sends 0 retransmissions and takes
  **1.03x** (1.032x, docs/E1.md sections 3.2 and 6.3).
- **DCQCN.** `_link`, R_C and R_T start at 400 Gbps, and legacy R_AI becomes
  20 Gbps, 20 % of the real link per 55 us (`dcqcn.cpp:36-43`). TP=8 cross
  pod improves from 3.999x to 3.641x when the flag matches (section 5.5).
- **HPCC.** W_init = B x T is 4x the real BDP, so the faithful window cap
  never binds (see "Why T = 13.1 us, and other topologies" below).
- **RoCE** is unaffected, 3.049x either way: the host queue serialises at
  the tier-0 speed whatever the flag says.

Fix: run.sh already read the tier-0 `Downlink_speed_Gbps` out of the
topology file for the floor. It now also passes it as
`-linkspeed $((GBPS*1000))` (run.sh:96). The default 16-node tree is still
400 Gbps, so no 16-node result changes.

**This was the root cause of the EQDS stale-NACK abort.** In the trace in
"EQDS on oversubscribed fabrics" the RTO fired about 397 us after the send:
that is the 399 us minimum RTO computed from the wrong link speed, and the
NACK that then aborted the run was for the packet the RTO had just given up
on. With the pre-guard binary, ft128_4os completes at matched link speed
(2.08x, docs/E1.md section 6.4). The stale-NACK guard is still correct and
is kept: a NACK can still arrive after an RTO, or be overtaken by a
cumulative ACK, whenever queueing delay exceeds the RTO, and the pre-guard
binary still aborted on ft128_8os at `-linkspeed 100000` (section 6.4). The
reorder-window fix in the same section deals with holes that survive
retransmission rounds under heavy trimming. Trimming still happens at
matched link speed (303,939 retransmissions on ft128_4os, section 6.4), so
that fix is kept too.

Controls at `-linkspeed 100000` (docs/E1.md section 8.1, x floor):

| | single, cross pod | TP=8, same pod | TP=8, cross pod |
|---|---|---|---|
| RoCE | 1.017 | 3.049 | 3.049 |
| DCQCN (legacy) | 1.017 | 3.578 | 3.641 |
| EQDS | 1.032 | 1.085 | 1.084 |

Every ft128 number in this file from before commit 2077902 was run at
`-linkspeed 400000`: the 128-node RoCE table, the HPCC topology table and
the EQDS oversubscription tables. The RoCE numbers stand. The others are to
be rerun.

## DCQCN made faithful to the paper

**Files:** `htsim/sim/dcqcn.h`, `htsim/sim/dcqcn.cpp`, `htsim/sim/datacenter/main_roce.cpp`
(flag parsing only, 15 lines after the `-dcqcn` branch). Commit 31a3bc8.
**Reference:** Zhu et al., "Congestion Control for Large-Scale RDMA Deployments",
SIGCOMM 2015. Section numbers below are the paper's. Its Figure 14 lists the deployed
parameters: timer 55 us, byte counter 10 MB, Kmax 200 KB, Kmin 5 KB, Pmax 1 %, g = 1/256.

docs/VALIDATION.md found four ways HTSIM's DCQCN differs from the paper. This change
fixes the three that live in the reaction point (RP): shared alpha, byte-counter units,
and the additive-increase step. The faithful version is now the **default**.
`-dcqcn_legacy` restores the old code path. The fourth difference, step ECN marking,
lives in the switch queue (`queue_lossless_output.cpp`), which was out of scope for this
change. It is documented below, and it is now the largest remaining divergence.

**Headline.** The faithful DCQCN is **slower** on our smoke workload, not faster:

- Slowest shard, seed 13: 14,217 us (4.24x floor), against 12,098 us (3.61x) for legacy.
- Paired over seeds 1-5, it is worse on every seed, by 16-38 %.
- On the 8:1 incast it is 6.7 % faster, at 82 % utilization against 77 %.

The reason is the paper's own parameters. With R_AI = 0.1 % of line rate, a flow that
has been cut recovers at about 7 Gbps per ms. The paper also deliberately keeps hyper
increase rare: section 5 says B = 10 MB is chosen "to avoid triggering the hyper
increase phase too fast". The old HTSIM numbers looked better because R_AI was 50x the
paper's value.

### What was wrong

Line numbers are HEAD b4832e8 (before this change) unless marked "new".

| # | mechanism | old HTSIM (file:line) | paper | status |
|---|---|---|---|---|
| 1 | alpha scope | `static double _alpha` (dcqcn.h:44, init dcqcn.cpp:22). One alpha is shared by every DCQCNSrc. Every flow's CNP raises it (dcqcn.cpp:57). Every flow's 55 us check decays it (131-134), so with n flows it decays n times per 55 us. | One alpha per RP, i.e. per flow (section 3.1, eq. 1-2) | **fixed**: `double _alpha` per instance (new dcqcn.h:70). `alpha()` (new dcqcn.h:73) returns the shared static only under `-dcqcn_legacy`. |
| 1b | other statics | `_RAI`, `_RHAI` were static (h:46) but re-assigned to the same value in every constructor (cpp:42-43). `_RC`, `_RT`, `_T`, `_BC`, `_byte_counter`, `_last_cc_update`, `_last_alpha_update` were already per instance (h:50-56). | n/a | `_RAI`, `_RHAI` are now per instance, so mixed link speeds would also work. Parameters that are global by design stay static: `_g`, `_F`, `_B`, the timer period. |
| 2 | byte counter | `_byte_counter += _highest_sent - _old_highest_sent` (cpp:120) compared with `_B = 10000000` (24, 123). `RoceSrc::_highest_sent` counts **packets** (roce.cpp:302), so BC fired every 10 M packets (40 GB) and never fired in any run. Since hyper increase needs `min(T,BC) > F`, it was unreachable. | B = 10 MB of bytes sent (Fig. 14). Hyper increase when both T and BC exceed F (Fig. 7). | **fixed**: packets are multiplied by `_mss` (new cpp:194). The counter keeps its remainder (`-= _B`). A go-back-N rewind can no longer underflow it. The faithful convergence trace shows hyper increase firing 132 times. |
| 3 | R_AI, R_HAI | `R_AI = rate/20`, `R_HAI = rate/10` (cpp:42-43): 20 and 40 Gbps per step at 400 G | R_AI = 40 Mbps on 40 G NICs (Table 2, "fixed at 40Mbps"). The paper gives no R_HAI and defers to QCN [17]. | **fixed**: `R_AI = 0.001 x line rate`, `R_HAI = 0.005 x line rate` (new cpp:61-62), i.e. 400 Mbps and 2 Gbps at 400 G. Set them with `-dcqcn_rai <Mbps>` / `-dcqcn_rhai <Mbps>`. R_HAI = 5 x R_AI is our choice, since the paper is silent. |
| 3b | R_T cap | R_T grew without bound in hyper and additive increase (cpp:89, 94). Only R_C was capped. | not stated; QCN caps the target at line rate | faithful mode caps R_T at line rate (new cpp:128, 134). |
| 4 | rate decrease | `RT=RC; RC*=(1-α/2); α=(1-g)α+g` (cpp:55-57). Timer, byte counter, T, BC and the alpha timer are reset on CNP (61-73). | eq. (1), Fig. 7 "Reset(Timer, ByteCounter, T, BC, AlphaTimer)". Initial α = 1 (footnote 1). | **matched, unchanged** |
| 4b | g | 1/256 (cpp:23) | 1/256 (Fig. 14). The task brief's g = 1/16 is the paper's *strawman*: QCN/DCTCP defaults that section 5 tunes away from ("B = 150KB, T = 1.5ms, Kmin = Kmax = 40KB, Pmax = 1, and g = 1/16"). | **matched**, kept 1/256. `-dcqcn_g` was added and 1/16 measured as a sensitivity run. |
| 4c | alpha decay | `α=(1-g)α` once 55 us pass without a CNP (cpp:131-134) | eq. (2), K = 55 us | rule matched. The scope was wrong (item 1). |
| 4d | CNP generation | DCQCNSink: send a CNP at once if none went out in the last 50 us. Otherwise count marks and send one CNP at the end of the 50 us window (cpp:212-230) | "If a marked packet arrives ... and no CNP has been sent for the flow in last N microseconds, a CNP is sent immediately. Then ... at most one CNP packet every N microseconds ... if any packet that arrives within that time window was marked" (section 3.1, N = 50 us) | **matched, unchanged** |
| 4e | fast recovery | `max(T,BC) <= F`, i.e. 5 rounds (cpp:84) | The text says "F = 5 successive iterations". Fig. 7 draws `Max(T,BC) < F`, which would be 4. | kept `<=`, which follows the text |
| 5 | ECN marking | step: mark on dequeue if `_queuesize > _K`, with `_K = -dcqcn x MTU` = 400 KB (queue_lossless_output.cpp:122, main_roce.cpp:335) | RED: 0 below Kmin = 5 KB, linear to Pmax = 1 % at Kmax = 200 KB, 1 above (eq. 5, Fig. 5) | **not changed** (switch code, out of scope). See below. |

#### A simulator-efficiency bug fixed along the way (no change to results)

Every DCQCN timer wakeup used to reschedule the *source itself*
(`sourceIsPendingRel(*this, 55us)`, cpp:76 and 147). Each wakeup ran
`RoceSrc::doNextEvent`, which schedules another pacing event (roce.cpp:343). Each wakeup
therefore started an extra pacing chain, and chains only died during a PFC pause.

- **Duplicates are no-ops.** Duplicate chains fire at the same instants, and
  `now - _time_last_sent >= _packet_spacing` (roce.cpp:335) lets only the first one send.
- **Chains never end.** After the last packet, `_highest_sent >= _flow_size`
  (roce.cpp:329) compares packets with bytes. Every chain therefore keeps firing every
  ~80 ns until `-end`.

That is why DCQCN took 70 s to many minutes. Under `run.sh`'s `-end 2000000`, legacy
smoke runs used over 13 CPU-minutes each on the loaded machine.

Faithful mode fixes both:

- The 55 us timer is a separate `DCQCNTimer` EventSource (new h:35; `armTimer`/`timerFired`,
  new cpp:77, 153). Stale timer events are ignored via `_timer_due`.
- `DCQCNSrc::doNextEvent` returns without re-arming pacing once `_done` is set.

Runs now take **2-3 s** instead of 35-55 s (with `-end 60000`) or minutes. Legacy mode
keeps the old event structure, so `-dcqcn_legacy` remains bit-for-bit the old binary.

### How to select behaviour

| flag (htsim_roce, only with `-dcqcn <K>`) | effect | default |
|---|---|---|
| *(none)* | faithful DCQCN: per-flow α, byte counter in bytes (B = 10 MB), R_AI = 0.1 %, R_HAI = 0.5 % of line rate, R_T capped, separate timer | on |
| `-dcqcn_legacy` | the original HTSIM code path: shared static α, packet-counting byte counter, R_AI = 5 %, R_HAI = 10 %, timer wakeups on the source | off |
| `-dcqcn_rai <Mbps>` / `-dcqcn_rhai <Mbps>` | override R_AI / R_HAI (faithful mode only) | 400 / 2000 at 400 G |
| `-dcqcn_g <g>` | override α gain | 0.00390625 (1/256) |
| env `DCQCN_TRACE=1` | prints `DCQCN_TRACE <us> <flow> cnp\|inc RC_Gbps RT_Gbps alpha T BC` on every CNP and rate increase | off |

`scripts/run.sh dcqcn ...` now gets the faithful version. To compare with the old
numbers, pass `-dcqcn_legacy`. At the time run.sh had no extra-args hook, so
`results/dcqcn_fidelity/tools/runleg.sh` copies its command line. Since commit 883a3be,
`EXTRA_ARGS=-dcqcn_legacy ./scripts/run.sh dcqcn ...` does the same.

**Legacy reproduces the old numbers exactly.** Smoke workload, default seed: 12,097.6 us,
the CLAUDE.md reference of 12,098. Convergence test, seeds 1-5: every flow finish time
is identical to `results/validation/summary.md`, e.g. seed 1 is 26,160 / 23,865 /
20,135 / 16,669 us.

**Correction:** since the minimum-rate clamp (commit 4d3e1b2), legacy is still
exact on the smoke workload but not on incast (seed 1: 34,979 → 34,553 us). See
"DCQCN: minimum rate clamp".

### Validation

Build `build_dcqcn` (Release; includes the roce.cpp:134 start-time fix, see "Flow start times
were read in the wrong units").
Matched buffers everywhere: `-q 500 -pfc_thresholds 80 150 -dcqcn 100`, default 16-node
400 Gbps fat tree, `-strat ecmp_host -paths 16`. **Every run has 0 "LOSSLESS not
working" violations.** In this binary, DCQCN is deterministic for a given seed: two
identical runs gave identical finish times, for both legacy and faithful.

Variants measured:

- **legacy**: `-dcqcn_legacy`.
- **faithful**: the default.
- **rai20g**: faithful with `-dcqcn_rai 20000 -dcqcn_rhai 40000`. This keeps legacy's step
  sizes but per-flow α, byte-counter units, R_T cap and the timer fix, so it separates
  the step-size effect from the rest.
- **g16**: faithful with `-dcqcn_g 0.0625`, the strawman g.

#### (a) Convergence test (`workloads/validation/converge_stagger.cm`, picosecond starts, seeds 1-5)

Same logging and analysis as docs/VALIDATION.md (`-log sink -log tor_downqueue -logtime 0.01 -o`,
then `scripts/validation_analyze.py`). Ideal makespan is 22.0 ms (1.1 GB at 400 Gbps).
Figures: `results/dcqcn_fidelity/figs/dcqcn_fidelity_{convergence,queue}.png` (seed 1).

| | legacy | faithful | rai20g | g16 |
|---|---|---|---|---|
| makespan, median (ms) | 26.16 | 31.59 | 27.60 | 29.20 |
| overall utilization (22.0 / makespan) | 84 % | 70 % | 80 % | 75 % |
| aggregate Gbps, 2nd half of 2 / 3 / 4-flow join phases | 294 / 342 / 342 | 240 / 208 / 318 | 287 / 292 / 323 | 268 / 248 / 375 |
| Jain, 2 / 3 / 4 flows | 1.000 / 0.981 / 1.000 | 0.987 / 0.869 / **0.780** | 0.984 / 0.988 / 0.981 | 0.629 / 0.553 / 0.698 |
| new flow within ±10 % of 400/n, join 2 / 3 / 4 | nc / 1.5 ms (4/5) / 4.7 ms | nc / nc / 0.3 ms | nc / nc / nc | nc / nc / nc |
| all flows within ±10 %, any join | nc | nc | nc | nc |
| last departure, survivor to 400 ±10 % | 485 us [367-850] | **nc**: survivor at 125 Gbps for 2.1 ms until it finishes | 658 us [640-693] | nc (214 Gbps) |
| receiver downlink queue, all 4 flows active: median / p99 / max (KB) | 4 / 728 / 801 | 4 / 242 / 435 | 4 / 585 / 679 | 4 / 583 / 715 |
| CNP episodes (queue above K) | every ~0.5 ms | every 2-8 ms | every ~0.5 ms | every ~3 ms |

Full tables: `results/dcqcn_fidelity/converge_comparison.md`. Per-variant analyzer
output: `converge_<variant>/summary.md`.

**Why, mechanistically** (from `DCQCN_TRACE`, `traces/faithful_converge_s1.trace.gz`):

1. **Each join halves every flow 2-3 times, in every variant.** The newcomer starts at
   line rate. With step marking at K = 400 KB, every flow's packets are marked while
   the queue is above K, and every NP sends a CNP in the same 50 us window. After the
   first cut, two flows at 200 Gbps still fill the 400 G link, so the queue does not
   drain and a second CNP follows 50 us later. A third follows because the NP sends a
   deferred CNP for marks seen in the previous window. With α ≈ 1, each cut halves:
   400 → 200 → 100 → 50 Gbps for the newcomer at 2,020-2,120 us.
2. **Legacy recovers in about 0.5 ms; faithful takes milliseconds.**
   - Legacy: fast recovery brings R_C back to R_T, then +20 Gbps per 55 us.
   - Faithful, additive increase: +0.4 Gbps per 55 us, i.e. 7.3 Gbps/ms. This is the
     paper's rate relative to line rate.
   - Faithful, hyper increase: needs BC > 5, i.e. 60 MB sent since the last CNP. That
     is about 3.8 ms at 125 Gbps. In Figure 1 the late upward-curving segments are
     hyper increase.
   - This is the paper's design, not a bug. Section 5: B = 10 MB is chosen "to avoid
     triggering the hyper increase phase too fast, which may harm convergence".
   - Effect: after a join the link runs at 52-80 % for milliseconds. After the last
     departure, the survivor needs 6+ ms to climb to 400, longer than it has left.
3. **Per-flow α decouples the flows, and nothing re-equalizes them.**
   - Legacy: every flow's cut used the same shared α at the same instant, so rates
     moved in lockstep. Jain was 1.000 by construction, not by convergence.
   - Faithful: each flow's α and R_T record its own history. In the 4-flow phase the
     newcomer has the highest rate (152 Gbps against 47-61) because its R_T was
     captured at 100 Gbps while the incumbents' were at 18-35.
   - What should equalize the flows is AIMD: a flow that sends more must get more
     CNPs. Section 5 says this comes from RED marking with a small Pmax: "the larger
     flow will get more CNPs, and hence back off faster".
   - With step marking, every flow gets exactly one CNP per 50 us window whatever its
     rate. The only equalizing force left is additive increase, at 7 Gbps/ms, so
     fairness does not converge within a 2 ms phase.
4. **g = 1/16 makes it worse.** Flow 1, alone for 2 ms, decays α to about 0.1, so its
   cuts are about 5 %. A newcomer starts at α = 1 and halves. Flow 1 holds 190-236 Gbps
   against 30-60 for the others (Jain 0.55-0.70).
5. **rai20g isolates the step size.** It recovers almost like legacy (makespan 27.6 ms
   against 26.2; survivor to 400 in 658 us). So the large slowdown of the faithful
   version comes from R_AI, not from per-flow α.

#### (b) Smoke workload (`kv_llama70b_4k_tp8.cm`, 8 x 167.8 MB, floor 3,355 us)

Faithful seeds 1-5 come from
`HTSIM_BUILD=build_dcqcn SEED=$i OUTDIR=$PWD/results/dcqcn_fidelity ./scripts/run.sh dcqcn default fidelity_r$i ...`.
The other variants use the same command line plus their flag. "default" is no `-seed`,
which is the binary's 13 and the CLAUDE.md reference. RoCE without CC (same build) is
shown for context. Slowest shard in us (x floor):

| seed | RoCE, no CC | DCQCN legacy | DCQCN faithful | rai20g | g16 |
|---|---|---|---|---|---|
| default (13) | 10,240 (3.05) | **12,098 (3.61)** | **14,217 (4.24)** | 12,803 (3.82) | 14,025 (4.18) |
| 1 | 6,842 (2.04) | 8,221 (2.45) | 11,296 (3.37) | 8,942 (2.66) | 10,535 (3.14) |
| 2 | 10,240 (3.05) | 11,997 (3.58) | 14,217 (4.24) | 12,803 (3.82) | 14,025 (4.18) |
| 3 | 10,240 (3.05) | 12,209 (3.64) | 14,217 (4.24) | 12,803 (3.82) | 14,025 (4.18) |
| 4 | 6,831 (2.04) | 8,006 (2.39) | 9,778 (2.91) | 8,698 (2.59) | 9,447 (2.82) |
| 5 | 6,842 (2.04) | 8,163 (2.43) | 11,296 (3.37) | 9,103 (2.71) | 10,535 (3.14) |
| **median s1-5** | 6,842 (2.04) | 8,221 (2.45) | **11,296 (3.37)** | 9,103 (2.71) | 10,535 (3.14) |

- **The spread across seeds is ECMP placement, not noise.** Seeds 2, 3 and 13 give the
  worse placement; RoCE also slows to 10,240 us on exactly those seeds. Compare per seed: faithful is worse than legacy on every seed
  (+16 % to +38 %), and both are worse than RoCE without CC on every seed.
- **The faithful version does not close the gap to RoCE; it widens it.** At the default
  seed the slowest shard goes from 3.61x to 4.24x floor, against RoCE's 3.05x.

Mechanism (traces `faithful_smoke_noseed`, `legacy_smoke_noseed`, `rai20g_smoke_noseed`):

- **Startup collapse.** All 8 flows start at line rate. ECMP collisions push the
  colliding queues past K within 20 us. The colliding flows get CNPs at 20, 70, 120 (and
  170) us. With α = 1 every cut halves, so they fall to 25-50 Gbps within 170 us. This
  happens in every variant.
- **Faithful recovery is too slow for a 3 ms transfer.**
  - Recovery: R_T is the pre-cut rate (50-100 Gbps). Additive increase adds
    7.3 Gbps/ms. Hyper increase needs 60 MB sent since the last CNP, a third of the
    whole 168 MB shard.
  - Result: the cut flows finish at ~137 Gbps average (9,778 us) or ~94 Gbps (14,217 us).
    The uncut flows finish at 3,422 us.
  - The transfer is about as long as one recovery, so the paper's slow recovery is
    exposed directly. The brief's expectation that hyper increase regains line rate in
    a few timer periods does not hold with B = 10 MB: at 50-130 Gbps the byte counter
    ticks only every 0.6-1.6 ms.
- **Per-flow α costs another 5-12 %**, measured as rai20g against legacy, worse on
  every seed.
  - Legacy: 8 flows each decayed the one shared α every 55 us. By 1.4 ms the shared α
    was 0.53, so later cuts removed ~26 %.
  - Per flow: each α decays only once per 55 us. At 1.4 ms it is still 0.92, so cuts
    remove ~46 %.
  - The old code therefore gained gentler cuts from uncongested flows decaying the
    congested flows' α. That is the coupling, and it is not in the paper.
- **Lockstep is caused by step marking, not by the shared α.** With per-flow α, flows
  with identical histories still have identical rates. For example, three flows sit at
  91.7465 Gbps at 1,462 us in the rai20g trace. They are marked in the same 50 us
  windows and so are cut identically. Removing the shared α removed the coupling
  *between* differently congested flows, but not the synchronization.

#### (c) Incast (`kv_incast_8to1.cm`, 8 x 167.8 MB into host 8; ideal makespan 8 x 3,355 = 26,843 us)

| variant | makespan, median s1-5 (us) | x single-flow floor | utilization (26,843 / makespan) | spread | default seed |
|---|---|---|---|---|---|
| legacy | 35,029 | 10.44 | 76.6 % | 1 % | 34,665 |
| **faithful** | **32,676** | **9.74** | **82.1 %** | 0 % | 32,697 |
| rai20g | 35,509 | 10.58 | 75.6 % | 8 % | 35,489 |
| g16 | 32,464 | 9.68 | 82.7 % | 1 % | 32,399 |

Jain over the 8 flows is 1.000 in every variant. Mechanism (traces
`faithful_incast_noseed`, `legacy_incast_noseed`):

- **Startup is identical in both.** 8 x 400 Gbps into one port fills the PFC-backed
  buffers. The receiver queue stays above K for ~420 us, so every flow takes nine
  consecutive halvings: 400 → 0.8 Gbps.
- **Legacy recovers fast, then overshoots every ~0.5 ms.** It is back in ~0.5 ms, but
  8 flows each adding 20 Gbps per 55 us overshoot 400 G quickly. The queue crosses K
  and 2-4 CNP rounds cut each flow from ~62 to ~27-37 Gbps. After t = 5 ms that is 222 CNPs per flow, and the link
  idles after each episode.
- **Faithful wastes ~7 ms at the start, then oscillates gently.** At 7.3 Gbps/ms per
  flow it needs ~7 ms to climb back to ~50 Gbps each. After that it overshoots only
  every 1.2-2.5 ms, with exactly 2 CNPs per episode (34 per flow after t = 5 ms). The cuts also get
  shallower as α decays: per flow 52 → 47 → 42 Gbps late in the run.
- **The step size accounts for all of the incast gain.** rai20g is no better than
  legacy, so per-flow α and the byte-counter fix do nothing here. With 8 symmetric
  flows, a shared α and 8 per-flow αs evolve almost identically.

### Remaining divergence: step ECN marking

**Since fixed:** see "DCQCN switch marking: probabilistic (RED) ECN" below. RED
marking is now run.sh's default; this subsection describes the step rule that
preceded it, which `ECN_STEP=1` still selects.

HTSIM marks a packet on dequeue if the queue exceeds `_K = ecn_threshold x MTU`
(queue_lossless_output.cpp:122, set at main_roce.cpp:335; `-dcqcn 100` gives 400 KB).
The paper uses RED (eq. 5): marking probability 0 below Kmin = 5 KB, rising linearly to
Pmax = 1 % at Kmax = 200 KB, and 1 above Kmax. Why it matters:

- **Synchronization.** With a step at K, every flow through the queue is marked while
  q > K, so every NP sends a CNP in the same 50 us window and every RP cuts at once. The
  aggregate halves together, the queue empties, and the link idles. That is the lockstep
  sawtooth in docs/VALIDATION.md.
- **Fairness.** Under RED with Pmax = 1 %, a flow's chance of seeing a mark in a 50 us
  window grows with its packet count, so faster flows get CNPs more often and back off
  more. Section 5 relies on exactly this to make DCQCN converge (Fig. 11(c)/(d)). Step
  marking gives every flow one CNP per window regardless of rate. With the paper's small
  R_AI, nothing else equalizes rates, hence faithful Jain 0.78.
- **Queue level.** K = 400 KB is twice the paper's Kmax. The queue therefore idles at
  ~1 packet between episodes and spikes to 0.4-0.8 MB, instead of hovering in the
  5-200 KB band.

Now that the RP follows the paper, step marking is the biggest reason HTSIM's DCQCN
differs from the paper's. Implementing RED in `LosslessOutputQueue::completeService`
needs Kmin, Kmax and Pmax plus a random draw per dequeued packet, about 15 lines. That
is the recommended next fidelity step. The paper's thresholds are for 40 G. At 400 G,
Kmin and Kmax probably need scaling, e.g. by bandwidth-delay product.

### What this means for the project

- **DCQCN numbers depend on R_AI more than on anything else.** The old reference
  (12,098 us, 3.61x) used R_AI = 5 % of line rate: an aggressive, unpublished tuning that
  happened to be closer to RoCE. The paper-faithful default gives 14,217 us (4.24x) at
  the reference seed.
- **Pick one DCQCN definition and say which.** Any DCQCN-vs-X comparison must state it:
  "DCQCN (paper parameters, step marking)" vs. `-dcqcn_legacy`. Re-run DCQCN cells
  produced with the old build.
- **The seed matters as much as the policy on the smoke matrix.** The CLAUDE.md
  "spread 0 %" for DCQCN means repeated identical runs. Across seeds 1-5, legacy spans
  8,006-12,209 us (51 %).

### Files

- `HTSIM/htsim/sim/dcqcn.h`, `dcqcn.cpp`: the change. Legacy code is preserved
  verbatim inside `if (_legacy)` / the legacy branch of `doNextEvent`.
- `HTSIM/htsim/sim/datacenter/main_roce.cpp`: `-dcqcn_legacy`, `-dcqcn_rai`, `-dcqcn_rhai`,
  `-dcqcn_g` (lines 111-125).
- `results/dcqcn_fidelity/`
  - `converge_{legacy,faithful,rai20g,g16}/`: run logs, `raw/` binary logs, analyzer
    output (`summary.md`, `convergence.csv`, `queues.csv`, `timeseries/`).
  - `converge_comparison.md`, `figs/dcqcn_fidelity_{convergence,queue}.{png,pdf}`:
    written by `compare_converge.py`.
  - `dcqcn_default_{smoke,incast8to1}_<variant>_<seed>.{log,csv,cmd}` and
    `dcqcn_default_fidelity_r<i>_s<i>.*` (run.sh faithful runs). Summary:
    `workload_summary.md`.
  - `roce_context/`: RoCE without CC, seeds 1-5, same build.
  - `traces/`: `DCQCN_TRACE` output (CNPs and rate increases) for the runs quoted above.
  - `tools/`: `runleg.sh` (run.sh's command plus extra flags), `conv.sh` (validation.sh's
    DCQCN command for one variant and seed), `agg.py` (workload_summary.md).

## DCQCN switch marking: probabilistic (RED) ECN

**Files:** `htsim/sim/queue_lossless_output.h`, `htsim/sim/queue_lossless_output.cpp`,
`htsim/sim/datacenter/main_roce.cpp` (flag parsing only).
**Reference:** Zhu et al., "Congestion Control for Large-Scale RDMA Deployments",
SIGCOMM 2015. The congestion point (CP) marks with RED on the instantaneous egress queue:
p = 0 below Kmin, rising linearly to Pmax at Kmax, and 1 above Kmax. Deployed values
for 40 G (Fig. 14): Kmin = 5 KB, Kmax = 200 KB, Pmax = 1 %.

Commit 206f9ac. This is the "next fidelity step" that "Remaining divergence: step ECN
marking" above names. It is the fourth and
last way docs/VALIDATION.md found HTSIM's DCQCN to differ from the paper. In the binary RED is
**opt-in**: without the new flags the step rule runs unchanged and every older
result is bit-identical. run.sh turns it on by default (see "Recommendation" below).

**Headline.** At 400 G, with Kmin = 50 KB, Kmax = 400 KB and Pmax = 1 %:

- **Fairness and queue: better.** On the convergence test, utilization goes from 70 % to
  75 %. The 4-flow Jain index goes from 0.780 to 0.871. The receiver queue p99 drops
  from 242 KB to 142 KB, and the max from 435 KB to 199 KB.
- **The paper's mechanism now works in HTSIM.** Under step marking, faster flows get
  *exactly* as many CNPs as slower ones: 70 of 70 flow pairs tie, correlation 0.00. Under
  RED the faster flow gets more CNPs in 44 % of pairs and fewer in 13 %, correlation 0.36.
- **Completion times: about the same.**
  - Smoke slowest shard: 11,296 → 11,557 us median (+2.3 %, inside the 39 % seed spread).
  - Incast: 32,736 → 32,560 us (−0.5 %).
  - Load 70 %: p50 13.91x → 12.24x, p99 17.52x → 15.30x (3 seeds, mixed per seed).
- **At 400 G, RED mostly does not act during transients.** 98-99 % of all marks are
  forced marks (q > Kmax). A new flow at line rate pushes the queue through the
  350 KB RED band in about 10 us, faster than one 50 us CNP window. RED only matters
  in quasi-steady state between transients. That is why the startup collapse and the
  smoke-matrix numbers barely move.

### What changed

Line numbers marked "new" are commit 206f9ac; "old" is its parent, 19d0856.

| # | what | old (file:line) | new (file:line) |
|---|---|---|---|
| 1 | marking decision | `if (_ecn_enabled && _queuesize > _K) set ECN_CE` on dequeue, for **every** packet type (queue_lossless_output.cpp:122) | `completeService` (new cpp:171-183): if RED is on, data packets only (`type()==ROCE`) go through `red_mark(_queuesize)`. Otherwise the original step line runs verbatim (new cpp:181). |
| 2 | RED curve | none | `red_mark` (new cpp:44-60): q > Kmax → mark (counted "forced"); q ≤ Kmin → no mark; otherwise mark with p = Pmax·(q−Kmin)/(Kmax−Kmin). It uses the same instantaneous `_queuesize` at dequeue (including the departing packet) and the same strict `>` as the step rule. So with Kmin = Kmax = K and Pmax = 0 it reduces exactly to step marking (verified below). |
| 3 | RNG | none | Its own `std::mt19937_64 _ecn_rng` (new h:60), seeded with `-seed` in `enable_red` (new cpp:31-39). It draws only when Kmin < q ≤ Kmax. Why not `drand()` (config.cpp:9, the global `rand()` stream of rng.cpp): runtime draws from it (RTO jitter roce.cpp:194, `random()` in fat_tree_switch.cpp) would shift with every mark decision. A step-vs-RED difference would then mix the marking effect with RNG perturbation. Runs stay deterministic per seed: a repeated seed-2 smoke run is bit-identical. |
| 4 | flags | `-dcqcn <K>` only (main_roce.cpp:106) | `-ecn_kmin <KB> -ecn_kmax <KB> -ecn_pmax <0..1>` (new main_roce.cpp:130-138). Any one of them turns RED on; the others default to 50 / 400 / 0.01 (new :74-75). KB is decimal (1000 B), as elsewhere in the project. They require `-dcqcn` (which still turns ECN on) and are validated (new :364-376). `-dcqcn K` is ignored when RED is on. |
| 5 | diagnostics | none | At exit, RED runs print `ECN_RED stats: data pkts dequeued N drawn(Kmin<q<=Kmax) D marked_prob P marked_forced(q>Kmax) F` (new cpp:24-29). With env `ECN_TRACE=1`, each mark prints `ECN_MARK <us> <queue> <flow> prob\|forced <q bytes>` (new cpp:41-42, 177-179). |

"Data packets only": step marking sets CE on ACKs, NACKs and CNPs too. Only
`DCQCNSink::receivePacket` reads the bit (dcqcn.cpp:305), so those marks were no-ops.
RED marks only `ROCE` packets, so ACKs do not consume random draws. Step mode is untouched.

### Parameter choice for 400 G

| | paper, 40 G | ours, 400 G | reasoning |
|---|---|---|---|
| Kmin | 5 KB | **50 KB** | Same drain time. 5 KB at 40 G and 50 KB at 400 G are both 1 us of queueing (12.5 packets of 4 KB). |
| Kmax | 200 KB | **400 KB** | Scaling 200 KB by 10x gives 2 MB, the whole egress buffer (`-q 500` x 4000 B), and it is above PFC XOFF. PFC pauses an ingress port at 150 packets = 600 KB (`-pfc_thresholds 80 150` are low/high in **packets**, main_roce.cpp:358-359, compared with the per-ingress byte count at queue_lossless_input.cpp:70). Kmax must sit below 600 KB so marks precede pauses, as the paper intends. 400 KB = the current step K (`-dcqcn 100` x 4000 B), so step vs RED is a clean ablation: the q > 400 KB region is marked identically. |
| Pmax | 1 % | **1 %** | Kept, but see the 400 G caveat below. Sensitivity: 0.1 % and 10 %. |
| sensitivity | | Kmax 200 KB (50/200/1 %), and the paper's literal 5/200/1 % | |

**PFC caveat.** PFC is per *ingress* port and ECN per *egress* queue. If one ingress feeds
the congested egress, the ingress count is at least the egress occupancy, so the queue
crosses Kmax = 400 KB before XOFF at 600 KB. If two or more ingresses feed it, the egress
can exceed 600 KB without any pause. ECN-before-PFC therefore holds per ingress, not in
general.

**Pmax at 400 G.** A flow's chance of a CNP depends on its marks per 50 us CNP window:
Pmax · rate · 50 us / 4000 B. A 100 Gbps flow sends 156 packets per window. At Pmax 1 %
that is up to 1.56 marks per window near Kmax. That is already near saturation: one CNP
per window whether the flow sends 156 or 300 packets. The measurements bear this out:

- **Pmax 10 % saturates.** Rate/CNP correlation 0.17, against 0.36 at 1 %.
- **Pmax 0.1 % is too rare.** Marks almost never come before q reaches Kmax (13 prob
  against 4,516 forced), so it behaves like step marking. Its convergence row matches
  step to three digits.

1 % is the only tested value in the useful regime.

### Validation that the default did not change

Build `build_ecn` (Release, htsim_roce target). Every run below has **0** "LOSSLESS not
working" violations.

| check | expected | got |
|---|---|---|
| smoke, faithful default, seed 13 (`-seed 13` and no seed) | 14,217 us | 14,217.2 us. The log is byte-identical to `results/dcqcn_fidelity/dcqcn_default_smoke_faithful_noseed.log`. |
| smoke, `-dcqcn_legacy`, default seed | 12,097.6 us | 12,097.6 us. The log is identical apart from the `-end` value. |
| convergence, faithful and legacy, seeds 1-5 | identical to results/dcqcn_fidelity | every finish time is identical. `converge_step/summary.md` == `dcqcn_fidelity/converge_faithful/summary.md`, and legacy matches likewise. |
| RED reduced to step: `-ecn_kmin 400 -ecn_kmax 400 -ecn_pmax 0`, faithful and legacy | same as step | same finish times and log, plus the two RED print lines. This confirms that ACK/CNP marks were no-ops and that the threshold semantics match. |
| `EXTRA_ARGS="-ecn_kmin 50 -ecn_kmax 400 -ecn_pmax 0.01" SEED=1 ./scripts/run.sh dcqcn ...` | = wrapper result | 11,556.8 us, same as `tools/runwl.sh` |

### Results

Faithful DCQCN RP everywhere unless the row says legacy. Matched buffers: `-q 500
-pfc_thresholds 80 150 -dcqcn 100`, default 16-node 400 G fat tree, `-strat ecmp_host
-paths 16`. The variant names below are used in the results directory:

- `step`: the current default.
- `red_<Kmin>_<Kmax>_p<Pmax%>`: e.g. `red_50_400_p1` is Kmin 50 KB, Kmax 400 KB,
  Pmax 1 % (the proposal); `p01` means 0.1 %.

The legacy rows separate the RP effect from the marking effect.

#### (a) Convergence test (`workloads/validation/converge_stagger.cm`, seeds 1-5)

Same run command and analysis as results/dcqcn_fidelity: validation.sh's command,
then `scripts/validation_analyze.py`. Medians over seeds. Ideal makespan 22.0 ms.

| variant | makespan (ms) | util. | Jain 2 / 3 / 4 flows | aggregate Gbps 2 / 3 / 4 | new flow within 10 % of 400/n, join 2 ; 3 ; 4 (us) | survivor to 400 after last departure | rcv queue p99 / max, all 4 active (KB) | marks prob / forced |
|---|---|---|---|---|---|---|---|---|
| **step** (default) | 31.59 | 70 % | 0.987 / 0.869 / 0.780 | 240 / 208 / 318 | nc ; nc ; 300 | nc 5/5 (125 Gbps) | 242 / 435 | - |
| **red 50/400/1 %** | **29.35** | **75 %** | 0.978 / 0.735 / **0.871** | 185 / 305 / 358 | nc ; nc ; 300 [300-6050] | 4,409 us, nc 3/5 | **142 / 199** | 43 / 2,630 |
| red 50/400/0.1 % | 31.08 | 71 % | 0.987 / 0.869 / 0.781 | 240 / 208 / 319 | nc ; nc ; 300 | nc 5/5 | 241 / 423 | 13 / 4,516 |
| red 50/400/10 % | 29.76 | 74 % | 0.974 / 0.735 / 0.877 | 135 / 332 / 352 | nc ; nc ; 300 | 3,698 us, nc 1/5 | 53 / 94 | 100 / 2,109 |
| red 50/200/1 % | 29.77 | 74 % | 0.987 / 0.869 / 0.831 | 240 / 209 / 342 | nc ; nc ; 300 (nc 1/5) | 3,982 us, nc 3/5 | 84 / 122 | 35 / 3,018 |
| red 5/200/1 % (paper, unscaled) | 31.20 | 71 % | 0.987 / 0.867 / 0.858 | 234 / 209 / 330 | nc ; nc ; 300 | nc 5/5 | 12 / 12 | 43 / 2,782 |
| legacy, step | 26.16 | 84 % | 1.000 / 0.981 / 1.000 | 294 / 342 / 342 | nc ; 1,500 ; 4,700 | 485 us | 728 / 801 | - |
| legacy, red 50/400/1 % | 26.25 | 84 % | 0.997 / 0.975 / 0.995 | 311 / 333 / 343 | 1,250 (4/5 nc) ; nc ; 1,500 | 638 us | 622 / 695 | 244 / 30,142 |

"nc" = no 300 us hold within ±10 % before the next event. Full per-event tables, whole-run
queue statistics and per-seed finish times: `results/ecn_marking/converge_comparison.md`.
Figures (seed 1): `results/ecn_marking/figs/ecn_{convergence,queue}.png`.

**Do faster flows get more CNPs?** This is the paper's fairness argument. The test pools
all phases with ≥ 2 flows over seeds 1-5 (70 flow pairs), skipping the first 300 us
after each join. Script: `tools/rate_vs_cnp.py`.

| variant | faster flow got more CNPs/ms | equal | fewer | corr(rate, CNP rate) |
|---|---|---|---|---|
| step | 0 % | **100 %** | 0 % | 0.00 |
| red 50/400/1 % | **44 %** | 43 % | 13 % | **0.36** |
| red 50/400/0.1 % | 11 % | 79 % | 10 % | 0.04 |
| red 50/400/10 % | 37 % | 57 % | 6 % | 0.17 |

Per phase, seed 1 (`tools/trace_stats.py traces/<v>_converge_s1.trace.gz --phases`),
as mean R_C Gbps / CNPs:

| phase | step | red 50/400/1 % |
|---|---|---|
| 2 flows, 2-4 ms | 135/3, 107/3 | 134/3, 107/3 (1,178 forced marks, 1 prob) |
| 4 flows, 6-13.6 ms | 49/5, 38/5, 55/5, **134/5** | 54/3, 43/3, 60/4, **133/6** |
| 3 flows, 13.6-21.5 ms | 109/4, 83/4, 105/4 | **149/9**, 97/7, 113/6 (all prob, 0 forced) |
| 2 flows, 21.5-27.3 ms | 210/5, 113/5 | **210/3**, 120/1 (all prob) |

Why the numbers move:

1. **Joins are unchanged.** A newcomer starts at 400 G. The bottleneck queue (mostly
   `US4->LS_4`, the aggregation-to-ToR link, plus `LS4->DST8`) passes 50 → 400 KB in
   about 10 us, less than one CNP window. Every flow therefore gets forced marks and the
   same three CNPs as under step. The rates after the 2- and 3-flow joins are identical
   to step on seed 1 (table above).
   - Other seeds differ by one CNP: a probabilistic mark at the edge of an episode adds
     or removes a deferred CNP, which with α ≈ 1 is a halving.
   - That is why the 3-flow Jain falls (0.869 → 0.735). On 3 of 5 seeds the newcomer got
     only one cut and ran at 205 Gbps against 41-70 for the incumbents. Aggregate
     throughput in that phase rises from 208 to 305-334 Gbps.
2. **Between transients, RED replaces synchronized multi-halving episodes with
   rate-proportional single cuts.**
   - Under step, the queue climbs slowly (additive increase) until it crosses 400 KB.
     Then every flow is marked in the same window and gets 2 CNPs, i.e. 2 cuts of
     about α/2 each.
   - Under RED, the queue enters the 50-400 KB band and individual packets are marked
     with p ≤ 1 %. A flow's chance of a mark grows with its packet count. In the 3-flow
     phase the 149 Gbps flow got 9 CNPs, against 6-7 for the 97 and 113 Gbps flows.
   - The queue never reaches Kmax in these phases (0 forced marks).
   - Back-to-back CNPs (a second CNP to the same flow within 51 us, i.e. a
     multi-halving episode) fall from 60 % to 46 % of all CNPs.
3. **Queue: lower and bounded.** In the all-active window, p99 falls from 242 to 142 KB
   and the max from 435 to 199 KB, so the queue is kept inside the band instead of
   spiking past 400 KB. Pmax 10 % holds it at 53/94 KB. The paper's unscaled 5/200 KB
   holds it at 12 KB, but at 71 % utilization.
4. **Throughput: up, but convergence is still slow.** Utilization goes from 70 to 75 %,
   because there are fewer synchronized double cuts that idle the link. "Within 10 %
   of fair share for 300 us" still almost never happens. RED fixes *who* gets cut, not
   how fast a cut flow recovers: R_AI = 0.1 % of line rate is 7.3 Gbps/ms
   ("DCQCN made faithful to the paper"). After the last departure, the survivor reaches 400 G in
   2 of 5 seeds (4.4 ms) instead of 0 of 5. It enters that phase faster (152-327 Gbps
   against 125), because it took fewer synchronized halvings earlier.
5. **Legacy + RED: the lockstep breaks, little else changes.** Jain at 2 and 4 flows
   goes from 1.000 to 0.997 and 0.995.
   Under legacy, Jain 1.000 was a lockstep artefact; it is now real convergence.
   - Legacy's R_AI (20 Gbps per 55 us) drives the queue through the band too fast:
     30,142 forced against 244 prob marks.
   - So it behaves like step with slightly lower peaks (p99 728 → 622 KB) and the same
     makespan (26.16 → 26.25 ms).
   - RED needs a slow-growing queue to act on; the faithful RP provides one, the
     legacy RP does not.

#### (b) Smoke workload (`kv_llama70b_4k_tp8.cm`, 8 x 167.8 MB, floor 3,355 us)

Slowest shard, us (x floor). Legacy step comes from results/dcqcn_fidelity, which uses
the same command, and the code path is bit-identical (verified above).

| seed | step | **red 50/400/1 %** | red 50/400/0.1 % | red 50/400/10 % | red 50/200/1 % | red 5/200/1 % | legacy step | legacy red 50/400/1 % |
|---|---|---|---|---|---|---|---|---|
| default (13) | 14,217 (4.24) | 15,171 (4.52) | 16,283 (4.85) | 14,888 (4.44) | 14,381 (4.29) | 14,763 (4.40) | 12,098 (3.61) | 11,896 (3.55) |
| 1 | 11,296 | 11,557 | 11,296 | 11,773 | 10,909 | 11,179 | 8,221 | 7,842 |
| 2 | 14,217 | 14,628 | 14,458 | 14,796 | 14,444 | 15,536 | 11,997 | 11,966 |
| 3 | 14,217 | 14,550 | 14,267 | 14,820 | 14,945 | 15,114 | 12,209 | 12,015 |
| 4 | 9,778 | 10,302 | 9,951 | 10,588 | 10,021 | 10,620 | 8,006 | 7,997 |
| 5 | 11,296 | 10,476 | 11,656 | 14,501 | 10,005 | 11,990 | 8,163 | 8,035 |
| **median s1-5** | **11,296 (3.37)** | **11,557 (3.44)** | 11,656 (3.47) | 14,501 (4.32) | 10,909 (3.25) | 11,990 (3.57) | **8,221 (2.45)** | **8,035 (2.39)** |
| spread s1-5 | 39 % | 37 % | 39 % | 29 % | 45 % | 41 % | 51 % | 52 % |

- **Faithful + RED is slightly slower.** Paired, it is worse on 5 of 6 seeds by 2-7 %
  and better on seed 5 by 7 %. The median moves +2.3 %, far inside the seed spread.
- **Legacy + RED is slightly faster.** It is better on all 6 seeds, by 0.1-4.6 %.

Mechanism (traces `smoke_{step,red_50_400_p1}_{noseed,s4}`, `tools/cnp_summary.py`):

- **Startup is identical.** Every forced mark falls in the first 300 us (4,386 at the
  default seed), in the ECMP-collision collapse that "DCQCN made faithful to the paper" describes.
  The cut flows fall to 25-50 Gbps in both cases.
- **Recovery differs.**
  - Step: colliding flows recover in lockstep. On seed 4, five flows sit at the same
    139.8 Gbps mean and finish within 0.1 us of each other at 9,778 us. Each later
    episode above 400 KB is a synchronized double cut, e.g. 146.8 → 104.4 → 74.1 Gbps.
  - RED: flows sharing a link get single, desynchronized CNPs whenever the queue
    sits in the band, e.g. 211 → 140 Gbps at α = 0.68 (flow 1->9, seed 4, t = 5.64 ms). They receive more CNPs (seed 4:
    41 against 30; default seed: 59 against 44), each slightly shallower (R_C kept
    0.60 against 0.57). Back-to-back CNPs fall from 60 % to 37-41 % of all CNPs.
- **Net effect on the slowest shard.** On seed 4, the mean finish of the six cut flows
  improves (9,604 against 9,778 us). But they now finish spread from 8,312 to 10,302 us,
  and the slowest shard is the max. Desynchronization turns one common finish time into
  a spread, and the TP metric reports its worst draw.
- **Why each CNP costs so much.** α never decays far in a 3-14 ms transfer: g = 1/256
  per 55 us halves α only every ~9.8 ms. Every extra CNP is therefore a 25-35 % cut.
  Recovery at 7.3 Gbps/ms takes milliseconds.
- **Legacy recovers at 20 Gbps per 55 us**, so extra cuts are cheap there, and RED's
  smaller episodes are a small net gain.

#### (c) Incast (`kv_incast_8to1.cm`, seeds 1-3; ideal makespan 26,843 us)

| variant | s1 | s2 | s3 | **median makespan** | utilization | median flow | Jain |
|---|---|---|---|---|---|---|---|
| step | 32,754 | 32,736 | 32,676 | **32,736** (9.76x) | 82.0 % | 32,544 | 1.000 |
| **red 50/400/1 %** | 32,560 | 33,693 | 32,182 | **32,560** (9.70x) | 82.4 % | 30,409 | 0.998 |
| red 50/400/0.1 % | 34,640 | 33,753 | 35,467 | 34,640 (10.32x) | 77.5 % | 31,937 | 0.998 |
| red 50/400/10 % | 32,734 | 32,777 | 34,128 | 32,777 (9.77x) | 81.9 % | 30,742 | 0.996 |
| red 50/200/1 % | 34,223 | 33,712 | 32,782 | 33,712 (10.05x) | 79.6 % | 31,270 | 0.997 |
| red 5/200/1 % | 33,809 | 33,130 | 34,529 | 33,809 (10.08x) | 79.4 % | 30,322 | 0.995 |
| legacy step | 34,979 | 34,901 | 35,043 | **34,979** (10.42x) | 76.7 % | 34,779 | 1.000 |
| legacy red 50/400/1 % | 34,307 | 34,236 | 34,432 | **34,307** (10.22x) | 78.2 % | 33,528 | 1.000 |

The step median of 5 seeds is 32,676 ("DCQCN made faithful to the paper"); seeds 1-3 give 32,736.

Mechanism (traces `incast_{step,red_50_400_p1}_s1`):

- **Startup is the same.** 8 x 400 G into one port. In RED, all 17,078 forced marks fall
  in the first millisecond.
- **After startup, RED never marks above Kmax again.** All 314 marks after 1 ms are
  probabilistic, so the queue stays below 400 KB.
- **Step overshoots in synchronized episodes.** After 5 ms it has 17 episodes above
  400 KB, each giving all 8 flows 2 CNPs (59 % back-to-back).
- **RED cuts one flow at a time.** It gives slightly more CNPs (48 against 44 per flow)
  but only 32 % back-to-back.
- **Makespan is unchanged.** The aggregate is set by eight flows each oscillating
  around 50 Gbps, and RED changes the phase of the cuts, not the mean rate.
- **Individual finishes spread.** Median flow 30.4 against 32.5 ms, Jain 0.998, because
  the flows are no longer in lockstep.

#### (d) Staggered load, 70 % (`e3_load70_ft16.cm`, `END=600000`, 21 TP-8 requests)

Brief asked for seed 1; seeds 2-3 added because one seed proved misleading (see s2).
Per-request x floor from parse.py:

| variant | s1 p50 / p99 | s2 p50 / p99 | s3 p50 / p99 | **median p50 / p99** | marks prob / forced (s1) |
|---|---|---|---|---|---|
| step | 13.92 / 17.52 | 11.30 / 17.57 | 13.91 / 15.25 | **13.91 / 17.52** | - |
| **red 50/400/1 %** | 12.35 / 15.30 | 12.24 / 17.59 | 11.46 / 12.66 | **12.24 / 15.30** | 5,303 / 237,469 |
| red 50/400/0.1 % | 13.87 / 18.48 | 12.48 / 17.57 | 14.12 / 15.81 | 13.87 / 17.57 | 603 / 324,288 |
| red 50/400/10 % | 12.16 / 14.43 | | | (s1 only) | 12,490 / 197,065 |
| red 50/200/1 % | 14.48 / 17.80 | | | (s1 only) | 4,658 / 295,731 |

- **The direction is mixed.** RED at 1 % is better on seeds 1 and 3 by 11-18 %, but
  worse on p50 on seed 2 (+8 %). The 3-seed median improves p50 by 12 % and p99 by 13 %.
- **Load 70 % has no quiet periods.** Each new request adds 8 flows at line rate, so
  98 % of marks are forced and the transients dominate.
- **RED acts in the gaps between arrivals.** There it cuts single flows earlier and
  shallower, which leaves less standing queue for the next arrival to build on. CNP
  totals are similar (s1: 7,969 against 8,324), with fewer back-to-back (about 77 %
  against 92 %; approximate, see the next point).
- Per-flow trace attribution is not possible here: repeated `Roce_<src>_<dst>` names
  collide across requests.
- Pmax 0.1 % tracks step again.

### Recommendation

**Yes. Make RED with Kmin 50 KB, Kmax 400 KB, Pmax 1 % the project's default DCQCN.**

- **Fidelity.** It is what the paper deploys and what production RoCE switches
  implement (WRED/ECN on egress). The step rule is an HTSIM simplification. With the
  paper's RP now in place, the step rule removes the one mechanism the paper relies on
  for fairness: rate-proportional CNPs.
- **It fixes measurable artefacts.** Lockstep finish times, synchronized double cuts
  that idle links, and queue spikes to 0.4-0.8 MB.
- **It costs at most noise on the headline numbers.** Smoke +2.3 % median, inside a 37-39 %
  seed spread. Incast −0.5 %. Load 70 % −12 % p50.
- 400 KB = the old K, so the change is a clean, explainable ablation.

**Adopted in commit 206f9ac.** run.sh's dcqcn line now passes
`-dcqcn 100 -ecn_kmin $((GBPS/8)) -ecn_kmax 400 -ecn_pmax 0.01`: Kmin is one
microsecond of queueing at the topology's link rate (50 KB at 400 G, 12 KB at
100 G), Kmax 400 KB, Pmax 1 %. `ECN_STEP=1` restores step marking;
`ECN_KMIN` / `ECN_KMAX` / `ECN_PMAX` override the values. `-dcqcn 100` still
turns ECN on; K is unused when RED is on.

What changed when it was adopted:

- **The DCQCN reference number moves.** Seed 13: 14,217 → 15,171 us (4.24x → 4.52x).
  Median over seeds 1-5: 11,296 → 11,557 us (3.37x → 3.44x). Re-run DCQCN cells, and
  state "DCQCN (paper RP + RED 50/400 KB/1 %)".
- **Runs are seed-dependent in a new way.** They are still deterministic per seed, but
  the faithful-step convergence results were nearly seed-invariant and RED's are not.
  Use ≥ 5 seeds and paired comparisons. This was already the rule.
- **The 128-node trees are untested.** ft128_* topologies have 100 G host links and
  `-q 2000`. run.sh scales Kmin to 12 KB there but keeps Kmax = 400 KB, as the step K
  does. Neither that choice nor a BDP-scaled Kmax (100 KB at 100 G) has been measured
  for fairness or queue behaviour. `-pfc_thresholds 80 150` (600 KB) still sits above
  Kmax.

Not recommended:

- **Pmax 10 %.** Lowest queue, but saturates CNPs. Smoke worse, 14,501 median.
- **Pmax 0.1 %.** Indistinguishable from step.
- **The paper's literal 5/200 KB.** At 400 G it marks at 1 us of queue and costs
  utilization: 71 % on convergence, 11,990 smoke median.
- **50/200 KB.** A plausible alternative, but it is mixed: best smoke median (10,909),
  but worse incast (+3 %) and load 70 % p50 (+4 %, s1).

### Files

- `HTSIM/htsim/sim/queue_lossless_output.h`, `.cpp`: RED marking, RNG, stats, `ECN_TRACE`.
  CRLF line endings kept.
- `HTSIM/htsim/sim/datacenter/main_roce.cpp`: `-ecn_kmin`, `-ecn_kmax`, `-ecn_pmax`
  (lines 74-75, 130-138, 364-376).
- `results/ecn_marking/`
  - `converge_<variant>/`: run logs, `raw/` binary logs (gitignored), analyzer output
    (`summary.md`, `convergence.csv`, `queues.csv`, `timeseries/`).
    `converge_comparison.md` and `figs/ecn_{convergence,queue}.{png,pdf}` come from
    `compare_converge.py`. `converge_runs.txt` is the command per run.
  - `runs/`: smoke / incast / load70 / verification runs (`<tag>.{cmd,csv,log}`,
    `_requests.csv`, `_summary.json`). `workload_summary.md` (from `tools/agg.py`) has
    every per-seed row.
  - `traces/`: `DCQCN_TRACE=1 ECN_TRACE=1` output for the runs quoted above.
  - `tools/`:
    - `runwl.sh`: run.sh's dcqcn command plus extra flags.
    - `conv.sh`: validation.sh's DCQCN command per variant and seed.
    - `variants.sh`: variant → flags.
    - `batch.sh`: at most N parallel runs.
    - `trace_wl.sh`: traced workload run.
    - `trace_stats.py`: per-phase CNPs, marks and R_C.
    - `rate_vs_cnp.py`: the fairness-mechanism test.
    - `cnp_summary.py`: CNP counts, cut depth and back-to-back share.
    - `agg.py`: builds `workload_summary.md`.

## HPCC: from a compounding window to the paper's Algorithm 1

**`htsim/sim/hpcc.cpp`, `hpcc.h`, `datacenter/main_hpcc.cpp` (flags only).**
Commit 883a3be. Line numbers marked "HEAD" are commit 2521a60, before this
change. Unmarked line numbers are after this change.

docs/VALIDATION.md showed that HTSIM's HPCC never converges. The first flow
keeps 270-350 Gbps until it finishes, and the window overflows to 1.8e19 B
about 200 us after the flow starts. This change re-implements the sender
side as Algorithm 1 of Li et al., "HPCC: High Precision Congestion
Control", SIGCOMM 2019, checked against the paper text and
draft-miao-tsv-hpcc-01. The old behaviour is kept behind `-hpcc_legacy` for
comparison.

**Result in one paragraph.** Seeds 1-5 on the convergence workload, matched
buffers (`-q 500`), zero lossless violations everywhere.
- Jain with 4 flows goes from 0.395 to 1.000.
- The receiver ToR queue goes from a 100 KB median to 18 KB (2 packets).
- The sender NIC backlog goes from 488 MB to one packet.
- Aggregate goodput settles at 379 Gbps. That is HPCC's own target,
  η x 400 x payload fraction = 377, so the 5 % headroom the paper designs
  in is now visible.
- After a join, the link stays at target and the newcomer's line-rate burst
  (~200 KB) drains in 70-80 us (~6 RTTs).
- After a departure, the survivors reclaim the bandwidth in 50-90 us. That
  is the paper's "fast convergence", and it now reproduces.

Convergence to *fairness* is slow: 1.7 ms to several ms, and some phases
never get inside +-10 % of 400/n. That is also what the paper says to
expect from a small W_AI ("rapid convergence to a Pareto optimal rate
allocation, followed by a slower convergence to fairness", Sec. 3.3 and
Appendix A). With W_AI 5x larger (`-hpcc_N 2`), every join converges in
350 us at Jain 1.000.

### `-hpcc_legacy` is bit-identical to the old binary

`-hpcc_legacy` restores the old window update, the old T = 12 us and the
old W_AI = MSS. On kv_llama70b_4k_tp8.cm, seeds 1, 2, 3, 4 and 42 give
per-flow finish times identical to `results/determinism/hpcc_tp8_seeds1-100.csv`.
That means `_incStage` was in practice zero-initialised before (bug 3 below
never changed a result). The new default is also deterministic: seed 42 run
twice is identical.

### What was wrong

| # | HEAD location | what the code did | paper (Alg. 1 / Sec. 3) |
|---|---|---|---|
| 1 | hpcc.cpp:200, 205, 408, 416 | `_cwnd` served as both W and Wc, and `_cwnd = computeWind(...)` ran on **every** ACK, including the `updateWc = false` path. So the once-per-RTT update compounded per ACK. W_AI was added per ACK, and `Wc/(U/η)` multiplied per ACK whenever incStage >= 5 | W is recomputed per ACK from a reference Wc. Wc and incStage change only when `ack.seq > lastUpdateSeq`, i.e. once per RTT |
| 2 | hpcc.cpp:381-385 | The INT reference `_link_info` was set only when the hop count changed, i.e. at the first ACK. txRate was then an average since the flow began. The queue term was `min(qlen_now, qlen_at_first_ACK)`, which stays 0 forever for a flow that started on an idle path | `L = ack.L` on every ACK (Alg. 1 line 27). txRate is measured between consecutive ACKs |
| 2b | hpcc.cpp:364, 377-379 | `tau/_T` divided two `simtime_picosec` integers, so the weight was 0 unless tau >= T. With a fresh reference (tau ~ 0.2 us), U would **never** move. Bug 2 hid this, because the stale reference made tau >= T, which turned the EWMA into `U = u` | `U = (1 - τ/T)U + (τ/T)u`, with real-valued τ/T |
| 3 | hpcc.h:121-122 | `_incStage` and `_Wc` were never initialised | incStage starts at 0 |
| 4 | hpcc.cpp:209 | No cap on W. Pacing at W/T therefore exceeded line rate, and the excess queued in the sender NIC, which INT never sees | W_init = B_NIC·T is "line rate" (Sec. 3.1). The reference ns-3 code clamps the rate to [minRate, lineRate] |
| 5 | queue_lossless_output.cpp:130 vs :144 | INT `qlen` is stamped **before** the departing packet is subtracted, so it always includes that packet | Fig. 5: when P1 is dequeued, the reported qLen counts the 4 packets left behind |
| 6 | hpcc.cpp:29 | T = 12 us: propagation only (6 links x 1 us x 2) | T "slightly greater than the maximum RTT" (Sec. 5.1, 13 us in their simulations) |
| 7 | hpcc.cpp:32, 72 | W_AI = MSS = 9000 B, and `_N` declared but unused | W_AI = W_init(1-η)/N, with N the expected number of concurrent flows per link (Sec. 3.3) |
| 8 | hpcc.cpp:173, 184 | A NACK cut the window to 1 MSS and zeroed `_flightsize` while ACKs of the abandoned window were still in flight. The next such ACK fired `assert(ackno - _last_acked <= _flightsize)`. This is the adaptive-routing crash in "Load balancing axis" (cited there as hpcc.cpp:181, which is 184 at HEAD) | Go-back-N only, no window cut |
| 9 | hpcc.cpp:165 | The NACK restart condition used `_highest_sent > _flow_size`, but `doNextEvent` stops at `>=` | (robustness) |

Units were otherwise right: qlen is in bytes, txRate in bit/s from
picoseconds, B in bit/s from INT, and T in picoseconds converted to
seconds. η = 0.95 and maxStage = 5 were already the paper's values.

### What changed

**Faithful path (the default)**, `HPCCSrc::processAck`, hpcc.cpp:274-300.

- **First ACK.** Store its INT as the reference and set
  `lastUpdateSeq = snd_nxt`. The window does not change. The reference
  ns-3 code does the same in its first RTT.
- **Every later ACK:**
  - `U = measureInflightPaper(ack)` (hpcc.cpp:534-577);
  - `W = computeWindPaper(U, ack.seq > lastUpdateSeq)` (hpcc.cpp:583-601);
  - on an update ACK, also `lastUpdateSeq = _highest_sent` (snd_nxt,
    hpcc.cpp:289);
  - finally `setWindow(W)`, which sets the window and the pacing
    R = W/T (hpcc.cpp:603-610).
- **Wc is its own variable** (`_Wc_d`), so the per-ACK W never feeds back
  into it (bug 1).
- **`measureInflightPaper`:**
  - per hop, `u' = max(0, min(q, q_ref) - own pkt)·8/(B·T) + txRate/B`,
    with `txRate = Δtx·8/Δts` (bit/s);
  - τ is the Δts of the max-u hop, and `U = (1-τ/T)U + (τ/T)u` in double
    (bug 2b);
  - then `L = ack.L` (hpcc.cpp:571, bug 2);
  - a hop is skipped if its switch ID changed (a reroute) or if ts/txBytes
    did not advance, instead of producing a negative or garbage txRate.
- **Own-packet subtraction** (hpcc.cpp:551-560, used at 561) corrects bug 5 without
  touching the switch.
- **Window clamp:** W is kept in [MSS, W_init] *before* it is stored as Wc
  (hpcc.cpp:587, 594), as ns-3 clamps the rate before storing it (bug 4).
  Since W <= B·T, pacing W/T <= line rate.
- **Constructor** (hpcc.cpp:97-121):
  - `_incStage = 0` and `_Wc = _cwnd` (bug 3);
  - W_init = B·T, W_max = W_init, W_min = MSS;
  - W_AI = W_init(1-η)/N with the existing `_N = 10`, giving 3,275 B at
    400 Gbps and T = 13.1 us.
- **`_T` default is 13.1 us** (hpcc.cpp:29-37), the measured base RTT host
  0 -> host 8 on the default topology: 12 us of propagation plus
  6 x 0.18 us serialization of a 9064 B packet. See "Why T = 13.1" below.

**Both paths: loss recovery** (bug 8). This is a transport fix, not a CC
change, so it applies under `-hpcc_legacy` too.

- `processNack` ignores a NACK whose ackno is behind the cumulative ACK.
  It also ignores a NACK whose triggering data packet (`nack.ts()`, the
  data send time the sink echoes) was sent before our last go-back-N
  (hpcc.cpp:203). That go-back already resends everything such a NACK could
  ask for. Before, every NACK of a reordering burst rewound again.
- `processAck` no longer asserts. It does
  `_flightsize -= min(newly, _flightsize)` and moves `_highest_sent` up to a
  cumulative ACK beyond it, so data the sink already has is not resent
  (hpcc.cpp:242-245).
- The faithful path no longer cuts W to 1 MSS on a NACK (hpcc.cpp:223-228).
  Legacy still does.
- The restart check uses `>=` (hpcc.cpp:215, bug 9).

**Logging.** The per-ACK `CWND1/CWND2` prints are gone; they were tens of
MB of stdout per run.

- Each flow now prints one line at finish:
  `HPCC_STATS <name> maxW .. minW .. Wc_updates .. nacks .. stale_nacks ..`.
- `-hpcc_trace` prints one `HPCCW` line per Wc update. It carries the time,
  W, U and incStage, plus the time-weighted split of u into its queue term
  and txRate term and the max hop.

**Flags** (`main_hpcc.cpp`):

| flag | meaning |
|---|---|
| `-hpcc_legacy` | old window update (also T = 12 us and W_AI = MSS unless overridden) |
| `-hpcc_T <us>` | base RTT T; default 13.1 |
| `-hpcc_eta <x>` | η; default 0.95 |
| `-hpcc_maxstage <n>` | maxStage; default 5 |
| `-hpcc_N <n>` | N in W_AI = W_init(1-η)/N; default 10 |
| `-hpcc_wai <bytes>` | set W_AI directly |
| `-hpcc_trace` | per-update trace lines |

`run.sh` passes none of them, so `run.sh hpcc ...` now runs the faithful
version.

#### Why each change moves the numbers

- **Per-RTT gating of Wc (bug 1)** is what lets the MIMD term track U
  instead of running away.
  - With Wc fixed for an RTT, `W = Wc·η/U` is a single correction per RTT
    toward the target.
  - Applied on every ACK (~66 per RTT at this BDP), the same correction is
    raised to the 66th power. When U < η and incStage >= 5, even a 1 %
    shortfall becomes 1.01^66 ≈ 2x per RTT. That is how flow 1 reached
    1.8e19 B and dumped 488 MB into its NIC.
  - Now W never exceeds W_init (maxW = 655,000 B in every run), and the
    NIC queue holds at most the packet in service.
- **Refreshing L every ACK, plus a real-valued τ/T (bugs 2, 2b),** makes U
  measure the *current* link: txRate over the last inter-packet interval,
  and the queue as it is now.
  - When flow k joins, every incumbent's U jumps above η within one RTT,
    and all of them cut proportionally. That is why the join burst drains
    in 70-80 us.
  - Before, flow 1's queue term was frozen at 0 (its first ACK saw an empty
    queue), so flow 1 never backed off. Later flows' references froze
    whatever they first saw, giving each flow a different, stale U.
- **The window cap (bug 4)** keeps the rate at or below line rate. Without
  it, anything W/T asks for above B piles up in the NIC, invisible to INT
  and released only by PFC.
- **Own-packet qlen (bug 5)** removes a phantom 1-packet queue,
  9064·8/(B·T) = 1.4 % of U. With it, every flow over-estimated U and the
  link settled at ~93 % instead of η = 95 %. Aggregate goodput rose from
  370-374 to 378-379 Gbps when this was fixed (same seeds, T = 13.1).
- **T = 13.1 us (bug 6).** W_init = B·T is the largest window. If T is
  shorter than the real base RTT, a lone flow is window-limited to
  B·T/RTT = 12/13.1 = 92 % of line rate even when the link is idle. At
  T = 12, a solo flow gets 359 Gbps; at T = 13.1 it gets 383 (η plus a
  slightly positive AI term).
- **W_AI (bug 7)** is the fairness knob. At equilibrium, a flow's reference
  window satisfies `Wc = W_AI / (1 - η/U)`. W_AI therefore sets how fast
  flows equalise, and how much a small per-flow bias in U costs (see the
  next section).

#### What still differs from an ideal fluid HPCC (not bugs in this code)

- **Small biases in U have large effects.** Two flows on the same
  bottleneck can measure U differing by ~0.006. In the 2-flow phase
  (`-hpcc_trace`), flow 1 averaged U = 0.962 with a queue term of 0.0127,
  and flow 2 averaged 0.956 with a queue term of 0.0061. Their txRate terms
  were identical (0.950).
  - The difference is the queue each flow sees at its own dequeues. In a
    deterministic simulator, packet arrival phases are systematic, and one
    9 KB packet is 1.4 % of B·T.
  - With W_AI = 3.3 KB, U sits only ~1 % above η, so `1 - η/U` is
    ~0.01. A 0.006 bias then splits the windows ~1.3:1 (Jain 0.97 at
    n = 2-3).
  - With `-hpcc_N 2` (W_AI = 16 KB) U sits further above η, the same bias
    barely matters, and Jain is 1.000.
- **Multi-bottleneck flows lose.** HPCC reacts to the max-U hop. A flow
  crossing two shared links sees the max of two noisy U's, which is biased
  upward, so it gets a smaller window than its max-min share. This is the
  2.19x sub-class in the smoke workload below. The paper itself (Sec. 3.1)
  says that with multiple bottlenecks HPCC converges to a Pareto-optimal
  allocation rather than to max-min.
- **Newcomer advantage.** A new flow starts at W_init (line rate), and the
  multiplicative cut preserves ratios. The newcomer therefore holds a
  larger share until W_AI equalises the flows: ~1.7 ms at N = 10, 350 us
  at N = 2. This is why flow 4 finishes before its ideal time.

### Validation

All runs use `-nodes 16 -strat ecmp_host -paths 16 -linkspeed 400000
-q 500` (run.sh's arguments; hpcc takes no `-pfc_thresholds`), on build dir
`build_hpccfix`. Every run reports **0 "LOSSLESS not working"
violations**. Everything is reproducible with
`HTSIM_BUILD=build_hpccfix ./scripts/hpcc_fidelity.sh`.

#### a. Convergence, workloads/validation/converge_stagger.cm, seeds 1-5

The picosecond-start matrix is fed directly; the binary honours start times
(HEAD hpcc.cpp:145-151), so trigger flows are no longer needed. Logging is
validation.sh's (`-log sink -log tor_downqueue -logtime 0.01 -o`).
Analysis is `scripts/hpcc_fidelity_analyze.py`, a wrapper around the
unchanged `validation_analyze.py` that adds NIC-queue and
utilization-convergence tables.

The variants are: legacy; paper (the default, T = 13.1 us, N = 10); paper
with T = 12; and paper with N = 2. Values are median [min-max] over
seeds; "nc" means not converged before the next event. Seeds 1 and 2 give
the same ECMP placement here.

**Time for the new flow, and for all flows, to be within 10 % of 400/n**
(held 300 us, 50 us bins):

| variant | flow 2 joins (200) | flow 3 joins (133) | flow 4 joins (100) |
|---|---|---|---|
| legacy | nc 5/5; all nc 5/5 | nc 5/5; all nc 5/5 | nc 5/5; all nc 5/5 |
| **paper (default)** | 50 us; all nc 5/5 | nc 5/5; all nc 5/5 | 1,750 [1,750-1,950] us; all 4,775 [2,350-5,650], nc 1/5 |
| paper, T = 12 us | 50 us; all 150 us | 1,200 [1,150-1,300]; all nc 5/5 | 1,850 [1,400-2,650]; all 2,850, nc 3/5 |
| paper, N = 2 | 50 us; all 100 us | 350 us; all 350 us | 350 [350-400]; all 350 [350-400] |

| variant | departure 1 -> 3 flows | departure 2 -> 2 flows | departure 3 -> 1 flow |
|---|---|---|---|
| legacy | nc 5/5 | nc 5/5 | 27 [27-38], nc 1/5 |
| **paper (default)** | 599 [544-605] | 114, nc 3/5 | 88 [76-101] |
| paper, T = 12 us | 1,697, nc 4/5 | 167 [89-2,286] | nc 5/5 (solo flow capped at 359 Gbps) |
| paper, N = 2 | 10 [3-19] | 86 [66-88] | 66 [63-89] |

**Fairness, utilization and queues.** Jain is over the second half of each
phase. "Aggregate" is goodput, whose target is η x 400 x 9000/9064 =
377 Gbps. Queues are for the "all 4 active" window (0.5 ms after the 4th
join until the first departure).

| variant | Jain n=2 / 3 / 4 | aggregate n=1 / 2 / 3 / 4 (Gbps) | LS4->DST8 median / p99 / max (KB) | all switch queues, median | sender NIC peak (flow 1) |
|---|---|---|---|---|---|
| legacy | 0.632 / 0.474 / **0.395** | 397 / 397 / 397 / 397 | **99.7** / 181 / 190 | 344 KB | **488 MB** (all seeds) |
| **paper (default)** | 0.971 / 0.969 / **1.000** | 383 / 378 / 378 / 379 | **18.1** / 27.2 / 36.3 | 54 KB | **9 KB** (1 packet) |
| paper, T = 12 us | 0.998 / 0.992 / 0.998 | 359 / 375 / 376 / 378 | 18.1 / 36.3 / 45.3 | 54 KB | 9 KB |
| paper, N = 2 | 1.000 / 1.000 / 1.000 | 386 / 390 / 396 / 396 | 36.3 / 72.5 / 81.6 | 73 KB | 9 KB |

Legacy flows 2-4 also park 18 KB to 70 MB in their NICs. The legacy flow 1 NIC
holds ~104 MB at the median while all four flows are active. Every paper
variant peaks at one packet in every NIC.

**Utilization convergence**, the paper's "fast convergence" claim: the time
until aggregate goodput stays within 10 % of 377 Gbps for 300 us, plus the
receiver-queue peak after the event and its drain time to <= 2 packets.

| variant | after joins: aggregate / queue peak / drained | after departures 1, 2, 3: aggregate back on target |
|---|---|---|
| legacy | 0 us (flow 1's NIC backlog keeps the link full) / 190-254 KB / 60-7,830 us | 881, 2,316 (nc 4/5), 27 (nc 1/5) us |
| **paper (default)** | 0 us / 190-199 KB / **70-80 us** | **49, 64, 88 us** |
| paper, N = 2 | 0 us / 190-208 KB / 70-100 us | 10, 38, 66 us |

The ~200 KB join peak is the newcomer's first window at line rate meeting a
link that is already at η. It is gone in about 6 RTTs.

**Flow completion (seed 1).** The ideal at η = 0.95 (with 9000/9064
payload) is 17.0 / 19.1 / 21.2 / 23.3 ms, leaving last-in-first-out.

- **Paper:** flows 4, 3, 2, 1 finish at 15.4-15.7, 17.8-18.2, 20.1-20.9 and
  23.3 ms, in the right order. Flow 1 finishes at exactly the η ideal;
  flow 4 is early because of the newcomer advantage.
- **Legacy:** flow 1, the largest, finishes *first* at 11.8 ms. The
  others finish at 21.6-24.4 ms.

`results/hpcc_fidelity/converge/summary.md` has every table;
`results/hpcc_fidelity/figs/validation_{convergence,queue}.png` has the
seed-1 time series for all four variants.

#### b. Smoke workload, kv_llama70b_4k_tp8.cm

Seeds 1-10 were run via `HTSIM_BUILD=build_hpccfix SEED=$i ./scripts/run.sh
hpcc default fidelity ...`. Legacy numbers are the identical runs in
`results/determinism/hpcc_tp8_seeds1-100.csv`. The 4 KB floor is
3,355.4 us.

| seed | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | median |
|---|---|---|---|---|---|---|---|---|---|---|---|
| legacy (us) | 7,224 | 10,749 | 10,676 | 6,797 | 7,137 | 10,749 | 7,224 | 7,224 | 10,749 | 7,137 | 7,224 (2.15x) |
| **paper** (us) | 7,352 | 10,709 | 10,698 | 7,168 | 7,362 | 10,709 | 7,362 | 7,352 | 10,709 | 7,362 | 7,362 (2.19x) |

Seeds 1-100 for the paper version are in
`results/hpcc_fidelity/smoke/hpcc_paper_tp8_seeds1-100.csv`. Every seed
lands in the same collision class as legacy (paired 100/100), so CC does
not change which flows collide.

| class | n | fluid max-min | fluid at η (k / (0.95 x 0.993)) | legacy, median [range] | **paper**, median [range] |
|---|---|---|---|---|---|
| 1x | 1 | 1.00 | 1.06 | 1.018 | 1.049 |
| 2x | 64 | 2.00 | 2.12 | 2.026 [2.026-2.173] | 2.136 [2.132-2.218] |
| 3x | 34 | 3.00 | 3.18 | 3.192 [3.182-3.204] | 3.191 [3.188-3.198] |
| 4x | 1 | 4.00 | 4.24 | 4.319 | **4.245** |
| all | 100 | E = 2.39 | | median 2.127, mean 2.467 | median 2.194, mean 2.521 |

**Why the classes moved.** The collision classes remain, as expected,
because colliding flows still share a link. Within a class, though, the
paper version is not closer to the max-min fluid value (2.00, 3.00). It
sits on the *η-adjusted* fluid value, because faithful HPCC deliberately
keeps 5 % of every bottleneck free.

- **Single-bottleneck placements land exactly on k/(0.95·0.993):**
  - 2x: 2.13-2.14, 35 seeds;
  - 3x: 3.19;
  - 4x: 4.245, against a model value of 4.244.
- **Legacy beat the fluid value for pure 2-flow collisions (2.026).** Its
  windows overflowed and both flows ran as NIC backlogs paced only by PFC,
  i.e. at 100 % of the link, like RoCE without CC (RoCE's 2x class is
  2.04).
- **The 2x class now splits into two sub-classes:**
  - 2.13-2.14 (35 seeds): two flows on one link;
  - 2.19-2.22 (29 seeds): a parking lot, where one flow crosses two shared
    links. That flow sees max(U_A, U_B) and is squeezed below its max-min
    share until its competitors finish (seed 1: competitors done at
    4.6-4.8 ms, it finishes at 7.35 ms).
  Legacy's spread within the 2x class was 7 % (2.03-2.17); now it is 4 %
  (2.13-2.22).
- **The CLAUDE.md reference number 6,797 us (2.03x) was this legacy
  artifact.** With faithful HPCC, the same placement (seed 4) gives
  7,168 us (2.14x).

#### c. Incast, kv_incast_8to1.cm, seeds 1-3

Eight 167.8 MB flows go into host 8. The bottleneck floor for the whole
transfer is 26,844 us. parse.py's per-flow "x floor" (8.4x) uses a single
shard's floor, so the table below re-normalises to 26,844 us.

| variant | seed 1 | seed 2 | seed 3 | last / 26,844 | first-to-last finish spread | max W |
|---|---|---|---|---|---|---|
| legacy | 28,556 | 28,369 | 28,739 us | 1.057-1.071 | 35-39 % (first flow done at 17.6-18.4 ms) | 0.9-3.3 MB |
| **paper** | 28,109 | 28,081 | 28,096 us | **1.046-1.047** | **2.4-3.2 %** | 655 KB (= W_init) |

Under incast, U >= η from the first RTT, so even legacy never blew up its
window, but it was still unfair: windows compounded per ACK from per-flow
stale references. The faithful version shares the link within 3 % and
finishes ~1.5 % sooner. The 1.046x is below the 1.06x one would expect at
η. The reason is that 8 flows x W_AI = 26 KB of additive increase per RTT
(4 % of B·T) holds U slightly above η, so the link runs a little above
95 %. Sec. 3.3 expects this: when the number of flows approaches N, HPCC
stays stable but trades a small standing queue for the extra utilization.

#### d. Staggered-start sanity, e3_load30_ft16.cm

`HTSIM_BUILD=build_hpccfix SEED=1 END=500000 ./scripts/run.sh hpcc default
e3test workloads/e3_load30_ft16.cm`: 72 flows, 9/9 requests, 0 violations,
start skew 0.000 us, matched by flowId. parse.py prints per-request
**p50 3.22x, p99 5.39x** (completion p50 10,815 us, p99 18,071 us).

Seeds 1-3 for each variant, run directly with run.sh's arguments plus the
flag, then parse.py. Values are request x floor:

| variant | seed 1 p50 / p99 | seed 2 p50 / p99 | seed 3 p50 / p99 |
|---|---|---|---|
| legacy | 3.19 / 4.68 | 3.63 / 4.48 | 3.66 / 4.26 |
| **paper (default, N = 10)** | 3.22 / 5.39 | 3.72 / 4.57 | 3.86 / 5.16 |
| paper, N = 2 | 3.19 / 4.05 | 3.05 / 4.03 | 3.05 / 4.36 |

**This matters for E3.** With overlapping requests, the faithful default
has a *worse* tail than legacy. Newer requests start at line rate and hold
an outsized share for milliseconds, and parking-lot shards are squeezed.
Legacy's NIC dumping instead behaved like a FIFO that favoured older
requests. A larger W_AI fixes both effects and beats legacy on every seed.
W_AI (via N) is a real HPCC parameter. Which N the project uses is a
methodological choice to make and report, not a bug. The paper's rule is N
= the expected maximum number of concurrent flows per link. That rule
suggests N ~ 8 here (8 shards). The paper's own evaluation used N ~ 100,
i.e. W_AI = 80 B at 100 Gbps (Sec. 5.1).

#### e. Reordering (bug 8), kv_llama70b_4k_tp8.cm

| strat | variant | runs | crashes | flows done | notes |
|---|---|---|---|---|---|
| `ecmp_ar` | legacy, old binary ("Load balancing axis") | 30 | **5** | - | assert at HEAD hpcc.cpp:184 |
| `ecmp_ar` | legacy, this build | 30 | 0 | 30/30 | 6 go-back-Ns; 1,349 stale NACKs ignored; median 7,137 us |
| `ecmp_ar` | paper | 30 | 0 | 30/30 | 0 NACKs (no reroutes); median 7,353 us |
| `ecmp_host_ar 50` | legacy | 5 | **5** | - | `assert(next_send > now)` at hpcc.cpp:431, i.e. the legacy window blew up and spacing reached 0. Not a loss-recovery issue, left as is |
| `ecmp_host_ar 50` | paper | 5 | 0 | 0/8 by 100 ms; seed 1 completes in 127-181 ms (38-54x) | 50 % per-packet spraying + go-back-N, the same collapse as RoCE (124-172 ms, "Load balancing axis") |

### Why T = 13.1 us, and other topologies

- The paper defines T as the base RTT used in `inflight = rate x T`
  (Sec. 3.1). Its evaluation sets T "slightly greater than the maximum
  RTT" (Sec. 5.1).
- HTSIM's 12 us is the propagation-only RTT. With 9 KB packets at
  400 Gbps, store-and-forward adds 1.1 us over 6 hops. The paper's 1 KB
  packets at 100 Gbps would add only 0.5 us.
- 13.1 us is the default topology's measured base RTT (docs/VALIDATION.md).
- **On the 128-node `.topo` files** (100 Gbps, 1 us links), the base RTT is
  ~16.4 us (12 + 6 x 0.725). There, the old run.sh's `-linkspeed 400000`
  made W_init 4x the real BDP, so the cap never bound. A faithful run should pass
  `-hpcc_T 16.4 -linkspeed 100000`. run.sh now does both: `-linkspeed` from
  the topology since commit 2077902, and `-hpcc_T 16.4` on `ft128_*` since
  this commit (run.sh:99-106, override with `HPCC_T`).

### Follow-ups

- **run.sh `-hpcc_T` on ft128**: done (previous section).
- **docs/VALIDATION.md, CLAUDE.md and EXPERIMENTS.md** quote HPCC numbers
  from the legacy implementation: 6,797 us (2.03x), the 58 % spread, and
  the "nc" convergence. They need updating to the tables above after the
  rerun (see "The reference numbers quoted in this file").
- **Re-run validation.sh** with `HTSIM_BUILD=build_hpccfix`.
  `probe_units` will now report `ps` for hpcc, so the trigger matrix is no
  longer used.
- **Decide N** (W_AI) for the experiments and state it. The default 10 is
  HTSIM's constant and matches the paper's rule for <= 10 flows per link.
- **Rebuild `build/`.** It still holds the legacy binary. `build_hpccfix` had to
  be built from a scratch symlink tree with HEAD copies of dcqcn.cpp,
  dcqcn.h and main_roce.cpp, because another agent was editing them.

### Files

- `htsim/sim/hpcc.cpp`, `hpcc.h`, `datacenter/main_hpcc.cpp`: the change.
- `scripts/hpcc_fidelity.sh`: runs a-d.
- `scripts/hpcc_fidelity_analyze.py`: convergence analysis, wrapping
  `validation_analyze.py` (unchanged).
- `results/hpcc_fidelity/converge/`:
  - `summary.md`, `convergence.csv`, `queues.csv`, `timeseries/`;
  - `<variant>_s<seed>.log`;
  - `raw/`, which has the binary log and idmap for seed 1 only (the
    seeds 2-5 `.dat.gz` were deleted for size; rerun `hpcc_fidelity.sh
    converge`, which is deterministic), plus `*.wtrace.gz` `-hpcc_trace`
    window traces for all runs.
- `results/hpcc_fidelity/figs/validation_{convergence,queue}.{png,pdf}`.
- `results/hpcc_fidelity/smoke/`: run.sh outputs for seeds 1-10, and
  `hpcc_paper_tp8_seeds1-100.csv`.
- `results/hpcc_fidelity/incast/`, `results/hpcc_fidelity/e3/`: logs and
  parse.py CSVs.

## EQDS pull pacer: finished flows kept credit, priority was soft

Commit a4e533e. Follows on from "Transport scheduling: per-flow priority in
EQDS". Line numbers are in the edited files unless marked "upstream", which
here means eqds.cpp just before this change
(`git show a4e533e~1:HTSIM/htsim/sim/eqds.cpp`).

**`htsim/sim/eqds.cpp`, `eqds.h`.** `main_eqds.cpp` is unchanged, and so is
the command line: no new flag. Two problems, both found in E4
(`docs/E4.md`, "Why: how EQDS orders credit"):

1. A finished flow kept receiving credit ("zombie" sink), so the next flow
   into the same receiver ran at half rate.
2. Receiver priority was soft. One urgent flow got about 39 % of the
   receiver link instead of all of it.

Binary: `HTSIM/htsim/sim/build_eqds2` (`cmake --build build_eqds2 --target
htsim_eqds`). Results: `results/e4_fixed/` (diag in
`results/e4_fixed/diag/`), `results/test_prio_fixed/`.

### 1. Finished flows kept receiving credit

#### What was wrong

`docs/E4.md` measured it. A 7.4 MB flow into host 8 takes 174 us alone
(1.17x floor). Started 70 us after a 16.2 MB flow into the same host
finished, it took 357 us (2.41x). After a 167.8 MB flow it took 357 us even
1.5 ms later. The `-debug` pull-pacer trace showed the finished flow
receiving 3,635 slow pulls (14.9 MB of credit, 92 % of its own size) after
it completed, 2,514 of them while the next flow was running.

#### Root cause

`EqdsPullPacer` keeps three lists: `_rtx_senders`, `_active_senders`, and
`_idle_senders` (speculative "slow" pulls, served only when the other two
are empty).

- When a pull drains an active sink's demand (`backlog() == 0`), the sink
  is `push_back`ed onto `_idle_senders` with no check that it is already
  there (upstream `eqds.cpp:2230`). `isIdle()` (upstream `2298`) exists but
  is never called. `requestPull()` (upstream `2306`) moves the sink back to
  `_active_senders` without removing its idle entry. A sink's demand is
  refilled one packet at a time and drained by every pull, so it gains
  about one idle entry per packet: 3,843 for the 16.2 MB flow.
- Nothing removes a sink when its flow completes. Each stale entry is
  eventually popped and served one slow pull (upstream `2235-2257`). The
  one-window cap at upstream `2251` only stops the re-push, not the pull,
  and the source discards the credit because it has nothing to send. The
  idle list is reached whenever no active sink has demand, which with one
  live flow is every few slots, so the next flow lost about half its
  receiver's pulls.

`_active_senders` does not have the problem. `requestPull()` aborts on a
duplicate, and every push into it is guarded by `_in_pull`. Traced with
`-debug`, the finished flow received 0 active and 0 retransmission pulls
after completing, in both `gap70` and `u1b3`. All 3,635 wasted pulls were
idle-list pulls.

#### Fix

The sink now tracks its flow's lifecycle (`EqdsSink::updateFlowState`,
`eqds.cpp:2052`, called at the end of `receivePacket`, `2045`):

- First packet of any kind: `_started`, `EqdsPullPacer::sinkStarted`
  (`2426`), which adds the sink to a new `_live_sinks` list (`eqds.h:554`).
- `_received_bytes >= flowsize()`, i.e. every payload byte has arrived:
  `_finished`, `EqdsPullPacer::sinkFinished` (`2446`). This removes every
  entry for the sink from `_rtx_senders`, `_active_senders`,
  `_idle_senders` (`std::list::remove`, so all stale copies at once) and
  `_live_sinks`, and clears its flags.
- A finished sink never asks for new-data credit again:
  `handlePullTarget` (`1739`), `processRts` (`1951`), and the
  retransmission path's move back to the active list (`2265`).

The receiver learns the flow size by reading the source's `flowsize()`.
The sink already does that for its `_received_bytes` assert. A real
receiver learns the message length from the first packet's header.

**Not changed: the duplicate entries while a flow is live.** The obvious
"proper" fix also skips the push at `2304` when the sink is already idle,
and removes the idle entry in `requestPull`. I built and measured that
variant. It changes the behaviour of every lone flow, because the
duplicates turn out to be load-bearing. `_maxwnd` (64 x 4,150 B =
265.6 KB) is below the 12 us cross-pod BDP (600 KB at 400 Gbps). With a
single idle entry the slow-credit cap of one window applies as written, and
a lone flow cannot keep the pipe full:

| test | upstream | dedup variant (not kept) | this fix |
|---|---|---|---|
| `alone` (7.4 MB, 1 flow into host 8) | 174.1 us (1.17x) | 188.0 us (1.27x) | 174.1 us (1.17x) |
| `gap70` flow 1 (16.2 MB, alone) | 355.5 us | 372.8 us | 355.5 us |
| kv_llama70b_4k_tp8 (8 x 1-to-1), slowest | 4,164.7 us | 4,217.3 us | **4,164.7 us** |

So the duplicates stay while the flow is live, with a comment at `2295`
saying why. They were a bug only because they outlived the flow. Without
them EQDS would need a slow-credit cap of at least one BDP. That is a
protocol parameter change, outside the scope of a bug fix.

#### Validation (`scripts/e4.sh diag`, `results/e4_fixed/diag/diag_summary.txt` vs `results/e4/diag/`)

| test (7.4 MB flow into host 8) | before | after |
|---|---|---|
| alone | 174.1 us | 174.1 us |
| 70 us after a 16.2 MB flow finished (`gap70`) | 357.0 us (2.41x) | **174.1 us** (1.17x) |
| 200 us after (`gap200`) | 268.1 us | **174.1 us** |
| 1,521 us after a 167.8 MB flow finished (`big_gap1521`) | 357.0 us | **174.1 us** |
| `gap300`, `gap1145`, `gap70_otherdst` | 174.1 us | 174.1 us |

`-debug` trace of `gap70` (`gap70_debug_counts.txt`): slow pulls to flow 1
after it finished went from **3,635 (14.9 MB) to 0**. Pulls to flow 2 went
from 1,775 active + 1,141 idle to 1,752 + 172. It no longer needs the idle
list to make up for pulls lost to the zombie.

### 2. Receiver priority was soft

#### What was wrong

`EqdsPullPacer::mostUrgent` ("Transport scheduling: per-flow priority in EQDS") picks
the most urgent sink, but only among sinks in `_active_senders`, i.e. with
`backlog() = pull_target - latest_pull > 0`. The source caps its
outstanding demand at one window (`computePullTarget`, upstream
`eqds.cpp:1054-1069`). Its pull target advances one packet per packet it
sends, so in steady state the urgent sink's backlog is refilled by one
packet on each arrival and drained to 0 by the next pull. It then drops out
of the active list until its next packet lands, and the pull goes to a less
urgent sink. Priority only broke ties. One urgent 41.9 MB flow against
three 167.8 MB flows finished at 2,148 us (39 % of the link, floor 839 us).
With `-cwnd 400` it finished at 1,172 us (72 %).

#### Fix: preempt on uncredited bytes, not on one-window demand

A sink's remaining demand is now its **uncredited bytes**:
`EqdsSink::uncredited()` (`2070`). That is the flow's wire bytes (payload
plus one 64 B header per 4,096 B, as `EqdsSrc::startFlow` computes
`_backlog`), minus the speculative first window `_maxwnd` that the source
sends without credit, minus every pull issued so far. This is the
"remaining size" a receiver learns from the message length. Before the
normal order runs, `doNextEvent` (`2223`) asks `preemptingSink()` (`2351`)
for the most urgent live sink with `uncredited() > 0`, and serves it
instead (`2269`) if it is

- strictly more urgent than the sink the original order would serve
  (`mostUrgent(_active_senders)`, else the idle-list front), or,
- when no sink is queued at all, strictly more urgent than some other live
  sink. Otherwise the pacer would idle while a high-priority flow still has
  bytes to send, but momentarily 0 pull-target demand.

Ties among the most urgent sinks rotate through `_live_sinks`, so equal
priorities still get round robin. Retransmission pulls still go first,
before any priority, as before. When the urgent flow's uncredited bytes reach 0,
its tail (the last window or so, e.g. after retransmissions or unspent
speculative credit) is pulled by the normal pull-target mechanism, as is
any flow smaller than one window. `sinkStarted` also wakes the pacer when
a new flow's priority differs from a live flow's, so preemption cannot wait
for an unrelated packet.

**No-prio behaviour is identical, so no `-strict_prio` flag.** With every
priority equal (no `prio` token, or E4's "fifo" with all 2,000,000), no sink
is ever *strictly* more urgent, so `preemptingSink()` always returns NULL.
`sinkStarted` schedules no extra event, and the code path is the upstream
one plus fix 1. A flow alone on its receiver is never preempted either.
Logs are byte-identical to the pre-change binary (`diff` of the full
`.log`, 0 lines):

| check | before | after |
|---|---|---|
| kv_llama70b_4k_tp8.cm, default seed 13, slowest shard | 4,164.7 us | **4,164.7 us** (all 8 flows identical, `Rtx: 4067` identical) |
| kv_incast_8to1.cm, slowest shard | 27,645.3 us | **27,645.3 us** (all 8 identical) |
| test_prio incast_mixed_noprio.cm | 6,897.6 / 17,271.6 us | identical |
| diag `u1b3_noprio`, `alone`, `gap300`, `gap1145`, `gap70_otherdst` | | identical |

#### Validation

workloads/test_prio/ (`results/test_prio_fixed/` vs `results/test_prio/`):
8:1 incast into host 8, four 41.9 MB flows and four 167.8 MB flows, all at
t = 0. Zero lossless violations.

| matrix | before: small flows | after: small flows | before: large | after: large |
|---|---|---|---|---|
| noprio | 6,896-6,898 us | 6,896-6,898 us | 17,271-17,272 us | 17,271-17,272 us |
| prio = size | 3,901-3,902 us | **3,433-3,463 us** | 17,271 us | 17,276 us |
| prio inverse (large urgent) | 17,271-17,272 us | 17,278 us | 14,274-14,276 us | **13,787-13,836 us** |

The four small flows have equal priority, so they share. Together they
need 4 x 41.9 MB / 400 Gbps = 3,355 us, so 3,463 us is 1.03x. Their
completion is now limited by the link, not by the credit loop. The four
large urgent flows (671 MB, floor 13,422 us) finish at 1.03x. The large
flows in the prio = size case finish 5 us later than before: the receiver
link stays busy, but the hand-over costs a few pulls.

Micro-tests (`results/e4_fixed/diag/diag_summary.txt`), all into host 8 at
t = 0, 41.9 MB = 839 us floor:

| test | before | after | strict-priority ideal |
|---|---|---|---|
| 1 urgent + 3 large (`u1b3`) | 2,148 us | **884 us** | 839 us + RTT |
| 2 urgent + 2 large (`u2b2`) | 2,149 / 2,150 us | **1,733 / 1,744 us** | 1,678 us |
| 4 flows prio 1/2/3/4 (`ladder4`) | 2,147 / 2,150 / 3,068 / 4,018 us | **884 / 1,751 / 2,617 / 3,484 us** | 839 / 1,678 / 2,517 / 3,355 |
| `u1b3` at -cwnd 64/128/200/400 | 2,148 / 2,039 / 1,873 / 1,172 us | 884 / 897 / 903 / 903 us | |

The window size no longer matters. In `ladder4` each flow now starts after
the previous one, at the cost of about 1.05x per step (RTT plus headers).
Before, prio 1 and 2 finished together.

### Semantics and limits (replaces the semantics paragraph in "Transport scheduling: per-flow priority in EQDS")

- Priority is now strict between flows into the same receiving host, at
  the granularity of credit. The most urgent flow with uncredited bytes
  gets every new-data pull until its whole remaining size has been
  credited. Still static per flow: `prio = size` is SJF, not
  remaining-size SRPT.
- Still not prioritized: retransmission pulls (served first), each new
  flow's speculative first window (`_maxwnd` = 265.6 KB, sent without
  credit), and the slow pulls of the idle list. These are only reached when
  no strictly more urgent flow has uncredited bytes.
- Switches remain FIFO. Nothing changes for contention in the core.
- Cost: `preemptingSink` scans the live flows of one receiver per pull,
  O(live flows). E4 wall time per run did not change measurably.

### Effect on E4 (`docs/E4.md`, "After the credit and priority fixes")

45 runs, same matrices and seeds, `results/e4_fixed/`. Medians over seeds,
x = completion / floor, at 50 / 70 / 90 % load:

| | before | after | fluid model |
|---|---|---|---|
| SRPT p50 | 2.43 / 2.59 / 2.85 | 1.10 / 1.13 / 1.18 | SJF 1.00 / 1.00 / 1.00 |
| fair share p50 | 2.57 / 4.05 / 6.85 | 1.83 / 3.26 / 6.46 | PS 1.76 / 2.69 / 5.78 |
| SRPT Jain | 0.93 / 0.72 / 0.57 | 0.82 / 0.47 / 0.31 | SJF 0.82 / 0.54 / 0.30 |
| receiver busy efficiency (medians) | 0.74-0.94 | 0.96-0.97 | ~0.985 (headers) |

At 70 % load, the share of requests in the old 2.3-2.6x "wall" band fell
from 47 % to 2.7 % (SRPT).

## run.sh: 2000-packet buffers on every 128-node tree

**`scripts/run.sh`** (harness only, no simulator change). Commit 19d0856.

Before this commit only `ft128_8os` used `-q 2000` (see "Oversubscribed fat
trees: resolved"); `ft128_1os` and `ft128_4os` used `-q 500`. That was enough
for the 8-flow matrices, but under the E2/E3 load matrices RoCE logged
10^4-10^5 "LOSSLESS not working" violations at `-q 500`, all on ToR uplinks.

Why 500 is too small there: PFC is per ingress port, with XOFF at 150
packets (`-pfc_thresholds 80 150`), and a 4-host ToR on these trees has 8
ingress ports. Each can hold about 155 packets for one egress queue before
its pause takes effect (150 plus what is in flight when XOFF is sent), so
up to ~1,240 packets can converge on one uplink queue. That exceeds 500 and
fits in 2000.

Fix: every `ft128_*` tree now gets `-q 2000`, as other `.topo` files
already did. The 16-node default tree keeps `-q 500`. Buffers are therefore
matched across policies on a given topology, which is the convention, but
not between the 16-node and 128-node trees. The violating runs were never
recorded (parse.py refuses any run with violations); the 8-flow ft128
results at `-q 500` had zero violations and stand.

## DCQCN: minimum rate clamp

**`htsim/sim/dcqcn.cpp`**, `DCQCNSrc::processCNP`.

E8 (`results/e8/`, Llama-2-13B at 50% load, seed 2) aborted at the
`roce.cpp:341` assertion 121.9 ms into the run. The DCQCN trace showed one
flow receiving a CNP every 50 us for 2.4 ms. Each CNP multiplies `_RC` by
`(1 - alpha/2)` with alpha near 1, so the integer rate fell to 0, and
`RoceSrc::update_spacing` (`roce.h:48`) then divided by it.

Real DCQCN NICs have a minimum rate. The paper does not give one; we clamp
`_RC` to one `R_AI` step (0.1% of line rate, 400 Mbps at 400 Gbps), which
is where additive increase would restart from anyway. The failing cell now
completes: 15 of 15 requests, 0 violations, p50 6.82x.

**Correction: the clamp is not bit-identical for other runs.** The commit
message and the first version of this section said it acts only when the
rate would reach zero. It acts whenever a cut would take `_RC` below one
`R_AI` step, i.e. after about 10 consecutive halvings from line rate
(400 / 2^10 = 0.39 Gbps < 0.4). The line sits in the shared `processCNP`, so
`-dcqcn_legacy` gets it too, at legacy's own `R_AI` of 5 % of line rate
(20 Gbps at 400 G).

Why it matters beyond the crash: without the clamp a deeply cut flow went
silent. `RoceSrc::doNextEvent` schedules the next send at
`_time_last_sent + _packet_spacing` from the current rate (`roce.cpp:340-343`),
so a later rate increase does not take effect until that send fires; only a
PFC resume wakes the source early (`roce.cpp:229`). In staggered-arrival
runs this silenced flows for up to 24 ms, and one 4:1 E2 run never finished.

Every DCQCN cell was rerun on the clamped binary (main `build`, 2026-11-07
23:30). Pre-clamp outputs are kept under `results/<exp>/dcqcn_preclamp/`;
`results/reference/dcqcn_clamp_diff.md` has the per-group diff.

| group | runs | changed | what moved |
|---|---|---|---|
| reference TP=8, seeds 1-10 | 10 | 0 | none (seed 13 still 15,171.1 us) |
| reference TP=8 legacy, step ECN | 10 | 0 | none |
| reference incast 8 to 1 | 10 | 2 | s1 32,560 → 32,805 us, s7 31,855 → 33,116 us; median 1.211x → 1.221x of the bound |
| E1 table, K sweep, legacy row | 121 | 2 | ft128_8os TP=8 cross pod s2 6.73 → 6.89x, s4 6.87 → 6.65x; cell median unchanged |
| rail (`scripts/rail.sh`) | 75 | 7 | 16:1 conventional, all 5 seeds (median 8.621 → 8.727x); 1:1 conventional incast s4, s5 (median 5.693 → 5.701x) |
| validation convergence test | 5 | 0 | bit-identical, including the binary sink/queue logs |
| E3 (staggered load) | 24 | 18 | rerun by the E2/E3/E6 sweep, docs/E3.md |
| E2 (load x oversubscription) | 51 | 37 | same; one 4:1 run that never finished now completes |
| E6 (incast K) | 12 | 1 | same |

So: simultaneous-start 8-flow runs are unchanged except where many flows
converge on one oversubscribed link (8:1 / 16:1) or one receiver (incast);
staggered-arrival runs mostly changed. Not rerun: `results/dcqcn_fidelity/`
and `results/ecn_marking/` (historical comparisons above) and E8's cells other
than the crash cell. Their incast rows are affected: legacy step incast seed 1
went from 34,979.3 to 34,552.5 us when repeated on the clamped binary (the
legacy TP=8 smoke runs, reference seeds 1-10 and E1 seeds 13 and 1-5, are
unchanged; legacy convergence was not rerun), so `-dcqcn_legacy` no longer
reproduces checkpoint 1 bit for bit on incast.

