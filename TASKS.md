# Tasks

Unassigned on purpose. Claim something by putting your name next to it when
you start, and check it off when it lands. Items already checked off carry a
pointer to the doc or script holding the evidence. Simulator changes are in
`HTSIM/OUR_CHANGES.md`.

Pull, run `./setup.sh`, and every result below reproduces with the
`scripts/e*.sh` drivers.

## Done

### Experiments

- [x] E1, uncontended per tier cost table. `docs/E1.md`, `scripts/e1.sh`
- [x] E2, topology sweep across 1:1, 4:1 and 8:1 crossed with load, plus a
      PFC victim test. `docs/E2.md`, `scripts/e2.sh`. This absorbs what was
      planned as a separate E5
- [x] E3, congestion control from 30 to 90 percent load on ft16 and
      ft128_1os. `docs/E3.md`, `scripts/e3.sh`
- [x] E4, transport scheduling, SRPT and EDF at three slacks against fair
      share, via EQDS receiver priority. `docs/E4.md`, `scripts/e4.sh`
- [x] E6, incast across policies. `docs/E6.md`, `scripts/e6.sh`
- [x] E7, scale up domain sweep, analytic plus trace driven Monte Carlo
      rather than packet simulation. `docs/E7.md`, `scripts/e7_scaleup.py`
- [x] Validation against the published HPCC and DCQCN figures.
      `docs/VALIDATION.md`, `scripts/validation.sh`
- [x] Rail optimised topology, expressed as `.topo` files plus host
      relabelling, no C++ needed. `docs/TOPOLOGY_RAIL.md`, `scripts/rail.sh`
- [x] Plotting and reporting. `scripts/plot.py`, `scripts/exp_report.py`

### Infrastructure

- [x] Setup runs on macOS and Linux
- [x] Workload generator with an offered load knob measured against a
      computed bottleneck. `scripts/gen_workload.py`, `workloads/README.md`
- [x] Azure LLM inference traces parsed, files in `data/azure/`
- [x] Prefill pool queue and prefill cost model. `scripts/prefill.py`,
      with `scripts/trace.py` and `scripts/models.py`. Standalone, not yet
      wired into the generator
- [x] Deterministic runs per seed. HPCC had been reseeding from the wall
      clock
- [x] Adaptive routing axis confirmed. `-strat ecmp_ar` runs, is switch
      local and flowlet sticky, so it only reroutes after a PFC pause

### Simulator bugs found and fixed

One line each. Details under the named section in `HTSIM/OUR_CHANGES.md`.

- [x] DCQCN never terminated, `_last_alpha_update` never assigned
- [x] Debug prints in hot paths, 97 million lines in one run
- [x] Missing `<cstdint>` on macOS
- [x] Every `.topo` file unloadable, `set_queue_sizes(0)` zeroed the queues
- [x] EQDS aborted on a stale NACK after a retransmission timeout
- [x] EQDS stalled silently, reorder table aliasing
- [x] EQDS `set_priority` did not exist, and finished flows kept taking
      credit
- [x] HPCC had no `-topo`, ignored start times, reseeded from the clock, and
      compounded its window per ACK until it overflowed
- [x] Start times read as microseconds instead of picoseconds in RoCE, DCQCN
      and EQDS
- [x] DCQCN shared one alpha across flows, had no hyper increase, and used
      step marking rather than RED
- [x] Ours rather than HTSIM's, the floor computed against the wrong link
      speed, `-linkspeed 400000` on 100 Gbps fabrics, and buffers too small
      for 8:1 and for loaded four host ToRs

### Document corrections

All applied in `docs/checkpoint2_draft.md`.

- [x] Stale future tense in methodology 4.1
- [x] OPT-13B replaced with Llama-2-13B
- [x] 1.28 GB corrected to 1.34 GB throughout
- [x] Typos, "file file", "flow are generated", "cacher sizes"
- [x] Deleted the note claiming HTSIM lacks DCQCN. It ships it, inside
      `htsim_roce` behind `-dcqcn`
- [x] Added htsim provenance, the UEC reference simulator and the ETH Zurich
      SPCL fork
- [x] Added the rail optimised paper to related work

## Blocking the report

- [ ] Fill the `[[TODO]]` and `[[RESULT]]` placeholders in `docs/E2.md`,
      `docs/E3.md`, `docs/E6.md` and `docs/checkpoint2_draft.md`. The DCQCN
      rows of E2, E3 on ft128 and E6 are not in `results/` yet
- [ ] E8, context length distribution, whether the rankings hold across
      transfer sizes. Driver is `scripts/e8.sh`, matrices are in
      `workloads/e8/`, and `docs/E8.md` is not written
- [ ] Finish and submit Progress Report 2 from `docs/checkpoint2_draft.md`

## Next

- [ ] Wire the prefill pool into the generator as an `--arrival
      trace-prefill` mode, so a flow starts when its cache exists rather
      than when its request arrives. Keep it off by default, since every
      existing result assumes arrival time starts. `scripts/prefill.py`
- [ ] E9, background training traffic, behaviour under multi-tenant
      contention
- [ ] Choose and state HPCC's N, the W_AI parameter. The default is 10
- [ ] DCQCN RED thresholds on 100 Gbps fabrics. Kmin 12 KB and Kmax 400 KB
      are in use and the BDP scaled choice is untested
- [ ] Open an upstream issue or pull request on spcl/HTSIM for the clean
      upstream bugs. The patch is `docs/htsim-changes-vs-upstream.patch`

## Known gaps, decide whether to close or document

- [ ] Scheduling works in EQDS only. `prio` has no effect in RoCE or HPCC,
      so E4 is EQDS only
- [ ] UEC transport as a fifth policy. Its `set_priority` is still commented
      out and its pull pacer is separate
- [ ] True rail only topology, no spine, with an NVLink relay. Needs C++.
      See `docs/TOPOLOGY_RAIL.md`
- [ ] Decode side occupancy is not modelled. A decode instance holds a
      request for its whole generation, so contention on the decode pool is
      understated

## Deadlines

- [ ] Presentation, December 1
- [ ] Final paper, December 8

## Minimum for Progress Report 2

**Met.** See `docs/checkpoint2_draft.md`.
