# Our changes to HTSIM

Vendored from https://github.com/spcl/HTSIM at commit
`ac1aea6e34ccc8249cb59b4c7cdd920283c9cfdb` (2026-10-05).

Edit these files directly and commit like any other code. The original diff
against upstream is kept at `docs/htsim-changes-vs-upstream.patch` for the
record and for a future upstream pull request.

## Fixes applied

**`htsim/sim/dcqcn.cpp`** — DCQCN never terminated.

- `_last_alpha_update` was never assigned in `DCQCNSrc::doNextEvent`, so once
  the alpha-decay condition first became true it stayed true forever and the
  rate timer re-armed indefinitely. The run never ended and eventually died on
  memory. Fixed by assigning it, matching what the adjacent `_last_cc_update`
  branch already did.
- Added an early return when `_done` is set, so the timer stops once the flow
  completes.
- Commented out debug `cout` calls in hot paths. One produced 97 million lines
  in a single short run.

**`htsim/sim/roce.cpp`** — two unguarded debug prints. The identical line in
`hpcc.cpp` is already commented out upstream.

**`htsim/sim/datacenter/fat_tree_topology.cpp`** — one unguarded debug print.

## Removed to keep the repo small

Dragonfly and SlimFly topology artifacts, the `experiments/` directory, and
checked-in `results/`. None are used by this project. Pull them from upstream
if ever needed.

## macOS portability

**`htsim/sim/data_collector.h`**, **`htsim/sim/loggertypes.h`** — added
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

### Still broken after the fix

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

## Oversubscribed fat trees: resolved, mostly

After the `set_queue_sizes` fix above, 4:1 and 8:1 still appeared to hang.
They were not hanging. Two separate things were happening.

**EQDS aborts on oversubscribed fabrics (still unresolved).** `htsim_eqds`
exits with SIGABRT, no message, immediately after starting all flows. There
are five bare `abort()` calls in `eqds.cpp`; one of them is being hit. RoCE
and DCQCN run the same configuration fine, so this is an EQDS bug, not a
topology problem. `run.sh` refuses the combination rather than producing a
silent failure.

**8:1 needs bigger buffers.** At `-q 500` it completes but reports 324,586
lossless violations, meaning PFC failed and the numbers are meaningless. At
`-q 2000` the violations go to zero and the completion time is unchanged.
`run.sh` now sets the buffer per topology.

### Working, RoCE, same traffic, 128 nodes

| topology | slowest shard | lossless violations |
|---|---|---|
| fat_tree_128_1os | 40,924 us | 0 |
| fat_tree_128_4os | 40,924 us | 0 |
| fat_tree_128_8os | 68,284 us | 0 at -q 2000 |

**1:1 and 4:1 are identical, and that is a result about our workload, not
the fabric.** Eight concurrent flows do not generate enough cross-pod traffic
to saturate the aggregation uplinks, so 4:1 oversubscription never binds.
Only at 8:1 does the pod boundary become the bottleneck.

This means the current hand-written matrices are too light to exercise the
topology axis at all. E2 needs the real workload generator with enough
offered load to saturate pod uplinks, not a handful of flows.

## Our own bug: floor computed against the wrong link speed

`parse.py` assumed 400 Gbps. A topology file sets its own link speeds and
overrides the `-linkspeed` flag, and all three `fat_tree_128_*` configs
specify `Downlink_speed_Gbps 100`. So every 128-node result was divided by a
floor four times too small and looked four times worse than it was.

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

---

## Flow start times were multiplied by a million

`roce.cpp` and `eqds.cpp`, found while testing the first generated workload.

A connection matrix line carries a start time in picoseconds. The parser in
`connection_matrix.cpp` says so in a comment on the line that reads it,
`c->start = start; // start is in picoseconds already`. Both transports then
passed that value through `timeFromUs()` on the way to the event list, which
multiplies by 1e6 a second time.

```cpp
// roce.cpp, before
eventlist().sourceIsPending(*this, timeFromUs((double)starttime));
// after
eventlist().sourceIsPending(*this, starttime);
```

`eqds.cpp` had the same conversion plus a `(uint32_t)` cast, which also
truncates any start time past 4.29 ms.

A flow asking to start 1 ms in was scheduled 1000 seconds in, far outside any
simulated window, so it never sent a packet. The run completed in about a
second and reported zero finished flows and `New: 0 Rtx: 0`.

This stayed invisible for the whole project because every hand written
workload used `start 0`, and zero times a million is still zero. It surfaced
the moment the generator emitted real arrival times.

Confirmed by taking `kv128_conv.cm`, changing nothing but `start 0` to
`start 1000000000` (1 ms), and rerunning.

| | last shard |
|---|---|
| start 0 | 40924.2 us |
| start 1 ms | 41924.2 us |

Exactly 1000 us later, which is the delay that was asked for.
