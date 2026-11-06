# CS 6250 project — KV cache transfer over datacenter networks

## What this project is

We evaluate how existing datacenter network mechanisms handle KV cache transfer
in disaggregated LLM inference. Production serving splits inference across a
prefill GPU pool and a decode GPU pool, which forces the KV cache across the
fabric before the first output token can be generated. For Llama-3-70B that is
320 KiB per token, so a 4K prompt produces 1.34 GB sitting on the critical path
of every request.

We do not propose a new mechanism. The contribution is a unified comparison:
existing methods were each evaluated on different simulators and workloads, so
nobody knows which helps under which conditions.

**Scope is networking only.** We vary topology, congestion control, load
balancing, and transport scheduling. We do NOT touch cache compression,
eviction, or paged memory management. Those are memory management, published at
OS venues, and out of scope for a networking course. Our professor warned
explicitly that out-of-scope work will be penalized.

## What we vary

| Layer | Options | Status |
|---|---|---|
| Congestion control | DCQCN, HPCC, EQDS, RoCE-no-CC | working |
| Load balancing | ECMP, adaptive routing | ECMP tested only |
| Topology | fat tree 1:1, fat tree 4:1, rail optimized | untested |
| Transport scheduling | FIFO, SRPT (pFabric), EDF (D3) | not wired up |

Baseline is fat tree + ECMP + DCQCN + FIFO, which is what production runs.
Change exactly one element per run.

## Metrics

Primary is **distance from theoretical floor**, meaning measured completion time
divided by size/bandwidth. 1.0 means the network added nothing. This normalizes
across transfer sizes, link speeds and topologies.

Also p50/p95/p99 TTFT, deadline miss rate, and fairness (Jain's index over TTFT
normalized by prompt length, to catch policies that starve long prompts).

A tensor-parallel transfer completes only when its **slowest shard** arrives.
Always take the max across a request's flows, never the mean.

## Layout

    HTSIM/          simulator source, vendored and already fixed
    HTSIM/OUR_CHANGES.md    what we changed and why
    docs/PROJECT.md         problem, motivation, scope, references
    docs/EXPERIMENTS.md     E1-E9, metrics, preliminary results
    TASKS.md        who does what, week by week
    workloads/      .cm traffic matrices
    scripts/        run.sh, sweep.sh, parse.py
    results/        one CSV per run, named <cc>_<topo>_<load>.csv

## Quick start

    ./setup.sh                                  # build + smoke test
    ./scripts/run.sh hpcc default load100 workloads/kv_llama70b_4k_tp8.cm
    ./scripts/sweep.sh workloads/kv_llama70b_4k_tp8.cm   # all four CC policies

## Editing the simulator

HTSIM source is vendored in this repo, not cloned. Edit it directly and commit
like any other code. There is no patch file to regenerate and no fork to keep
in sync. See HTSIM/OUR_CHANGES.md for what we already changed.

## The simulator

HTSIM is the Ultra Ethernet Consortium reference simulator. We vendored the
ETH Zurich SPCL fork, https://github.com/spcl/HTSIM, at commit ac1aea6, and
pruned the Dragonfly and SlimFly artifacts we do not use.

**Build targets are not default.** Plain `cmake --build build` gives only the
UEC binaries. Name the targets:

    cmake --build build --target htsim_roce htsim_hpcc htsim_eqds --parallel 4

- DCQCN lives inside `htsim_roce` behind `-dcqcn`, not its own binary
- `htsim_ndp` does not link, ignore it
- Upstream CI only tests Ubuntu. We added two `<cstdint>` includes for macOS;
  if you hit other portability errors, fix and note them in OUR_CHANGES.md
- `htsim_swift` builds but rejects our `-strat` args, invocation unresolved

### Three gotchas that will cost you an afternoon

1. `-dcqcn` **takes an argument** (ECN threshold). `-dcqcn -strat ecmp_host`
   silently eats the `-strat` and you get "Unknown parameter ecmp_host".
2. Stock DCQCN **never terminates**. `_last_alpha_update` is never assigned in
   `DCQCNSrc::doNextEvent`, so the rate timer re-arms forever. **Already fixed
   in this repo.** Do not revert it.
3. Default buffers are far too small for 400 Gbps. Without
   `-q 500 -pfc_thresholds 80 150` you get hundreds of thousands of
   "LOSSLESS not working" violations. **Any run with nonzero violations is
   invalid.** parse.py refuses to record one.

## Reference numbers

One Llama-3-70B 4K cache, TP=8, 16-node fat tree, 400 Gbps, matched buffers.
Median slowest shard across repetitions. Floor is 3,355 us.

| policy | median | x floor | spread over reps |
|---|---|---|---|
| EQDS | 4,165 us | 1.24 | 0% |
| HPCC | 6,797 us | 2.03 | **58%** |
| RoCE, no CC | 10,240 us | 3.05 | 0% |
| DCQCN | 12,098 us | 3.61 | 0% |

If your numbers differ wildly, something is misconfigured.

DCQCN takes ~70s per run, the others 1-25s. Budget sweeps around DCQCN.

## Runs are NOT deterministic

Identical commands give different answers. HPCC ranged 6,796 to 10,710 us
over nine repetitions. `-seed` does not fix it.

**Never report a single run.** Use `./scripts/sweep.sh`, which repeats and
reports median plus spread. Any difference smaller than a policy's own spread
is noise, not a result.

## Do not trust "total bytes" in the logs

Different transports print different things under that label. RoCE prints a
packet count, EQDS prints real bytes. `parse.py` takes flow sizes from the
traffic matrix instead, which is always correct.

## Cache size arithmetic

    bytes_per_token = 2 * layers * kv_heads * head_dim * dtype_bytes

| Model | Layers | KV heads | Head dim | Per token | 4K prompt |
|---|---|---|---|---|---|
| Llama-3-8B | 32 | 8 | 128 | 128 KiB | 0.54 GB |
| Llama-3-70B | 80 | 8 | 128 | 320 KiB | 1.34 GB |
| Llama-2-13B | 40 | 40 | 128 | 800 KiB | 3.36 GB |

Llama-2-13B replaces OPT-13B: same architecture, but 4096 context instead of
2048. It predates grouped-query attention, which is why its cache is ~6x larger
than a model five times its size. That lets us vary transfer size without
changing anything else about the workload.

Use **decimal GB** throughout, since link rates are decimal. Do not compute in
MiB and relabel with decimal prefixes. That mistake produced the wrong "1.28 GB"
figure that was in an earlier draft.

## Conventions

- Every result filename encodes its full configuration
- Commit results, not just code
- Buffer settings stay matched across any two policies being compared
- Never report a run with lossless violations

## Open work, roughly in priority order

1. Verify `.topo` files load. Nothing has run on anything but the default
   auto-generated 16-node fat tree. Try
   `-topo topologies/fat_tree_1024_4os.topo`. Highest-risk unknown.
2. Build the Azure trace to connection-matrix generator (Python, no C++).
3. Run E1, the uncontended per-tier cost table. Everything else is read
   against it.
4. Run E3, the congestion control sweep at 30/50/70/90 percent load.
5. Wire up `set_priority`, commented out in main_eqds.cpp and main_uec.cpp.
   Without it there is no scheduling axis. Timeboxed: drop it if not working
   by end of week 3.
6. Determine whether rail-optimized wiring is expressible in the .topo format.
   Look at generic_topology.cpp.
