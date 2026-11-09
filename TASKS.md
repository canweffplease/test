# Tasks

Unassigned on purpose. Claim something by putting your name next to it when
you start, and check it off when it lands.

## Done

- [x] Everyone can clone and run `./setup.sh` on macOS and Linux
- [x] `.topo` files load. They did not, upstream bug, see
      `HTSIM/OUR_CHANGES.md`
- [x] All three oversubscription levels run. EQDS aborts on the
      oversubscribed ones, which is a separate bug, and 8:1 needs `-q 2000`
- [x] Workload generator, M1 through M7, in `scripts/`
- [x] Azure traces parsed, 19,366 conversation and 8,819 code requests, with
      the prompt length figure
- [x] Offered load knob, calibrated at the decode side and on the window
      actually emitted
- [x] Five HTSIM bugs found and fixed, all written up in
      `HTSIM/OUR_CHANGES.md`
- [x] Results are now per request rather than per flow, with deadline
      attainment and shard straggle

## Ready to run

These need no new code. They need machine time and someone to read the
output.

- [ ] E3, congestion control comparison, RoCE and DCQCN and EQDS on the same
      generated workload, every run to completion. The preliminary read is
      in the Findings section below and needs confirming
- [ ] Buffer sweep, `-q` at 250, 500, 1000, 2000, 4000 on one policy and one
      workload. Report MB per port, not htsim's packet count. Find where
      each policy stops needing more
- [ ] Load ceiling per policy. Raise load until the lossless invariant
      breaks. This replaces the fixed 0.3, 0.5, 0.7, 0.9 sweep, which
      assumed a range we now know is too wide for some policies
- [ ] E2, topology sweep, 1:1 against 4:1 against 8:1, now that the workload
      can actually saturate the uplinks
- [ ] Conventional against rail numbering on the same workload. The earlier
      hand written pair disagreed with the literature and that file predates
      `placement.py`, so regenerate and recheck
- [ ] E1, the uncontended per tier cost table, using the hand written
      matrices
- [ ] Plotting script. Load on x, distance from floor on y, one line per
      policy. This figure is the centrepiece of the next progress report
- [ ] Run the conv and code traces side by side, which is E8 nearly free

## Needs code or investigation

- [ ] Decide whether `parse.py` should report lossless violations as a
      metric rather than refusing the run. With no congestion control they
      are the result, not a misconfiguration
- [ ] Reproduce one published figure from the HPCC paper
- [ ] Reproduce one published figure from the DCQCN paper
- [ ] Confirm `-strat ecmp_ar` runs, which is the adaptive routing axis
- [ ] Decode side occupancy is not modelled in `prefill.py`. A decode
      instance holds a request for its whole generation. Decide whether to
      add it or defend the omission
- [ ] `set_priority` is commented out in `main_eqds.cpp` and
      `main_uec.cpp`. Find out whether uncommenting works. If it does,
      SRPT becomes reachable. Timebox it and write it up as future work
      otherwise
- [ ] `htsim_eqds` aborts on oversubscribed fabrics. Five bare `abort()`
      calls in `eqds.cpp` and one is being hit. Low priority, three of four
      policies work without it
- [ ] `htsim_hpcc` refuses a `-topo` file, so HPCC only runs on the default
      16 node topology. Find out whether that is fixable

## Findings to confirm or retract

- [ ] **The earlier claim that DCQCN performs worse than no congestion
      control is wrong** and has to come out of the document. It was
      measured on eight flows with no contention. On a realistic workload
      DCQCN is far better. Needs completed runs before the replacement claim
      goes in
- [ ] Preliminary ordering at load 0.3, from truncated runs, is EQDS with
      zero violations, then DCQCN, then rail numbered RoCE, then plain RoCE
      by a wide margin. Confirm with runs that finish
- [ ] Llama-3-70B cannot load this fabric, topping out near 7 percent, while
      Llama-2-13B reaches about 91 percent. Confirm, then state it as the
      reason the 13B is the primary model
- [ ] Buffer size is not the fix for the lossless failures. Going from 2 MB
      to 8 MB per port made it slightly worse. Confirm on completed runs

## Document corrections

- [ ] Fix stale future tense in methodology 4.1, the build is verified now
- [ ] Replace OPT-13B with Llama-2-13B
- [ ] Correct 1.28 GB to 1.34 GB throughout, and relabel the cache table,
      which uses binary units under decimal headings
- [ ] Mark the 32K prompt column as hypothetical. The longest prompt in
      either trace is 14,050 tokens
- [ ] Add the mean cache size under the real traces to the model table.
      946 MB for the 13B on conversation, 378 MB for the 70B
- [ ] Fix typos "file file", "flow are generated", "cacher sizes"
- [ ] **Delete the Checkpoint 2 note saying HTSIM lacks DCQCN.** It ships
      it, inside `htsim_roce` behind the `-dcqcn` flag
- [ ] Add htsim provenance, the UEC reference simulator and the ETH Zurich
      SPCL fork
- [ ] Cite the Azure dataset properly. It is CC-BY and the required citation
      is the Splitwise paper
- [ ] Correct the collection date to 16 November 2023. The dataset page says
      the 11th and the files say otherwise
- [ ] State the capacity definition explicitly, measured at the decode side,
      since every load number is relative to it
- [ ] Add the rail optimised paper to related work

## Repository

- [ ] Add all five of us as collaborators
- [ ] Rename `master` to `main`
- [ ] Open an upstream issue or pull request on spcl/HTSIM for the two bugs
      that are clearly upstream problems, the DCQCN timer and the flow start
      time unit conversion

## Stretch

- [ ] E6, incast across congestion control policies
- [ ] E7, scale up domain sweep at 8 and 72
- [ ] Llama-3-8B as a third point between the 13B and the 70B

## Minimum for the next progress report

Setup done, topology files verified, the E1 table, one E3 plot with
analysis, one validation number, and a clean document. Everything else is
upside.
