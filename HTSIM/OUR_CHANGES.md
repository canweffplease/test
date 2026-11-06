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
