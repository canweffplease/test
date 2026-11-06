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
