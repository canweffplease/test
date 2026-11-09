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
| Congestion control | DCQCN, HPCC, EQDS, RoCE-no-CC | working, paper-faithful (legacy flags keep the old code) |
| Load balancing | ECMP, switch AR, host AR, per-packet RR | working, `STRAT` in run.sh |
| Topology | 16-node tree, 128-node fat tree 1:1 / 4:1 / 8:1, rail optimized | working; rail via 2-tier `.topo` + host relabeling |
| Transport scheduling | fair share, SRPT (pFabric), EDF (D3) | working in EQDS only, via receiver priority (`prio`) |

Baseline is fat tree + ECMP + DCQCN + FIFO, which is what production runs.
Change exactly one element per run. Rail-only (no spine) is not expressible;
see docs/TOPOLOGY_RAIL.md. RoCE and HPCC ignore `prio`.

## Metrics

Primary is **distance from theoretical floor**, meaning measured completion time
divided by size/bandwidth. 1.0 means the network added nothing. This normalizes
across transfer sizes, link speeds and topologies.

Also p50/p95/p99 TTFT, deadline miss rate, and fairness (Jain's index over TTFT
normalized by prompt length, to catch policies that starve long prompts).

A tensor-parallel transfer completes only when its **slowest shard** arrives.
Always take the max across a request's flows, never the mean.

## Layout

    HTSIM/                  simulator source, vendored and already fixed
    HTSIM/OUR_CHANGES.md    every change we made and why, with evidence
    docs/PROJECT.md         problem, motivation, scope, references
    docs/EXPERIMENTS.md     E1-E9 plan and status, reference table, metrics
    docs/E1.md ... E7.md    one write-up per experiment (no E5 yet)
    docs/VALIDATION.md      convergence vs the HPCC and DCQCN papers
    docs/TOPOLOGY_RAIL.md   rail-optimized / rail-only in HTSIM
    docs/checkpoint2_draft.md   progress report 2 draft
    TASKS.md                who does what, week by week
    workloads/              .cm traffic matrices (+ .json sidecars)
    scripts/run.sh          one run; sweep.sh: all policies x seeds
    scripts/parse.py        log -> per-flow CSV, per-request CSV, summary JSON
    scripts/gen_workload.py Azure-trace / Poisson matrices at an offered load
    scripts/e1.sh e2.sh e3.sh e4.sh e6.sh   experiment drivers (explib.sh runner)
    scripts/e7_scaleup.py   E7, analytic
    scripts/rail.sh validation.sh   TOPOLOGY_RAIL and VALIDATION drivers
    scripts/exp_report.py plot.py   tables and figures from summary JSONs
    results/                <cc>_<topo>_<load>[_<strat>][_p<n>][_s<seed>].*
    results/reference/      the reference table below

## Quick start

    ./setup.sh                                  # build + smoke test
    SEED=1 ./scripts/run.sh hpcc default load100 workloads/kv_llama70b_4k_tp8.cm
    ./scripts/sweep.sh workloads/kv_llama70b_4k_tp8.cm default ref 10   # 4 policies x 10 seeds

## Editing the simulator

HTSIM source is vendored in this repo, not cloned. Edit it directly and commit
like any other code. See HTSIM/OUR_CHANGES.md for what we already changed.

After changing anything under HTSIM/, run `./scripts/diff-upstream.sh` to
refresh `docs/htsim-changes-vs-upstream.patch`. That file is the only record
of how we differ from the original, since we removed HTSIM's own git history
when vendoring it. It is also what we would submit upstream.

To undo a change: `git log -- HTSIM/path/to/file.cpp` then
`git checkout <commit> -- HTSIM/path/to/file.cpp`. Every edit is committed,
so nothing is unrecoverable.

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
- `HTSIM_BUILD=<dir>` makes run.sh use another build dir under HTSIM/htsim/sim

### Gotchas that will cost you an afternoon

1. `-dcqcn` **takes an argument** (ECN threshold). `-dcqcn -strat ecmp_host`
   silently eats the `-strat` and you get "Unknown parameter ecmp_host".
2. Stock DCQCN **never terminates**. `_last_alpha_update` is never assigned in
   `DCQCNSrc::doNextEvent`, so the rate timer re-arms forever. **Already fixed
   in this repo.** Do not revert it. (`-dcqcn_legacy` still runs to `-end`.)
3. Default buffers are far too small for 400 Gbps. Without
   `-q 500 -pfc_thresholds 80 150` you get hundreds of thousands of
   "LOSSLESS not working" violations. **Any run with nonzero violations is
   invalid.** parse.py refuses to record one.
4. **`-linkspeed` must match the topology.** Transports take their initial
   rate, credit pacing and timeouts from it, not from the `.topo` file.
   400000 on a 100 Gbps tree gave EQDS false timeouts and 1.33x single flows.
   run.sh reads it from the `.topo` file; do not hand-roll commands.
5. **128-node trees need `-q 2000`.** At 500, load matrices give 10^4-10^5
   lossless violations on ToR uplinks. run.sh sets it for every `.topo`.
6. **`.cm` start times are picoseconds** (`start 9385959000` = 9.39 ms), read
   as float32. parse.py refuses a run whose logged starts disagree.
7. **Fidelity flags.** The defaults are paper-faithful: DCQCN with per-flow
   alpha and RED ECN marking (run.sh passes `-ecn_kmin/-ecn_kmax/-ecn_pmax`;
   `ECN_STEP=1` for step marking), HPCC Algorithm 1 (`-hpcc_T` 13.1 us on
   the 16-node tree, 16.4 on ft128 via `HPCC_T`; `-hpcc_N` default 10),
   EQDS with finished-flow credit and strict priority fixed.
   `EXTRA_ARGS=-dcqcn_legacy ECN_STEP=1` and `EXTRA_ARGS=-hpcc_legacy`
   reproduce the checkpoint 1 transports bit for bit, except where DCQCN's
   min-rate clamp acts (legacy incast moved). Always say which.

## Reference numbers

One Llama-3-70B 4K cache, TP=8 cross pod, 16-node fat tree, 400 Gbps, matched
buffers. Median slowest shard over seeds 1-10. Floor is 3,355 us. Full table,
per-seed rows and incast: `results/reference/reference_table.md`.

| policy | median | x floor | range, seeds 1-10 | 2x / 3x class |
|---|---|---|---|---|
| EQDS | 4,409 us | 1.31 | 4,159-4,787 | 1.31 / 1.32 |
| HPCC | 7,362 us | 2.19 | 7,168-10,709 | 2.19 / 3.19 |
| RoCE, no CC | 6,842 us | 2.04 | 6,831-10,240 | 2.04 / 3.05 |
| DCQCN | 13,488 us | 4.02 | 10,302-15,009 | 3.41 / 4.35 |
| HPCC, legacy* | 7,224 us | 2.15 | 6,797-10,749 | 2.14 / 3.20 |
| DCQCN, legacy, step ECN* | 8,530 us | 2.54 | 8,006-12,209 | 2.45 / 3.58 |

\* checkpoint 1 numbers, legacy transports (`-hpcc_legacy`; `-dcqcn_legacy`
`ECN_STEP=1`), now over the same ten seeds. Checkpoint 1 quoted one placement
each. Class = flows on the busiest link: seeds 1-10 gave six 2x and four 3x
placements; the ECMP model predicts 63 % / 33 % (plus 0.4 % 1x, 3 % 4x).
Incast 8 to 1, median over seeds, against the 26,844 us bound: RoCE 1.016x,
EQDS 1.030x, HPCC 1.047x, DCQCN 1.221x (min-rate clamp; 1.211x before it).

If your numbers differ wildly, something is misconfigured. Wall clock per run
on the 8-flow matrices (busy 24-core box): HPCC 1-3 s, DCQCN 2-5 s, EQDS 2-11 s,
RoCE 15-30 s. `-dcqcn_legacy` grinds until `-end`: pass `END=60000` (90-120 s);
at the default 2 s it had not finished after 7 minutes.

## Runs are deterministic per seed; the spread is ECMP placement

Same command and `-seed`, bit-identical output (HPCC's `srand(time(NULL))`
was removed). What the seed changes is ECMP hashing, i.e. which flows share a
link. On the TP=8 matrix that alone moves the slowest shard between 1x, 2x,
3x and 4x floor (`results/determinism/ecmp_collision_model.py`). The same
seed gives the same placement in every ECMP policy, so compare per seed.

**Never report one seed.** Use `./scripts/sweep.sh` (rep i = seed i), report
median and range. Any difference smaller than the seed spread is noise.

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
2048. It predates grouped-query attention, so its 819,200 B per token is 2.5x
Llama-3-70B's 327,680 B (a model five times its size) and 6.25x Llama-3-8B's
131,072 B. That lets us vary transfer size without changing anything else
about the workload.

Use **decimal GB** throughout, since link rates are decimal. Do not compute in
MiB and relabel with decimal prefixes. That mistake produced the wrong "1.28 GB"
figure that was in an earlier draft.

## Conventions

- Every result filename encodes its full configuration
- Commit results, not just code
- Buffer settings stay matched across any two policies being compared
- Never report a run with lossless violations
- State the transport version (faithful or legacy) and the seeds

## Open work

1. E5, topology x congestion control, is covered by E2 (all four policies
   on 1:1 / 4:1 / 8:1 at 30/50/70 percent load, docs/E2.md); a dedicated
   write-up of the interaction, and E9 background training traffic, remain.
2. E8 is done (docs/E8.md); a bursty-arrival replay of the code trace is not.
3. A true rail-only topology needs C++ (no spine, NVLink relay); see
   "Requirements for a true rail-only run" in docs/TOPOLOGY_RAIL.md.
4. UEC transport (`htsim_uec`) is not in the comparison.
5. RED ECN on 100 Gbps trees is untested: run.sh scales Kmin to 12 KB but
   keeps Kmax 400 KB; no fairness or queue check at those thresholds.
6. Choose and state HPCC's N (W_AI). Default 10 matches the paper for
   <= 10 flows per link; `-hpcc_N 2` converges to fairness much faster.
