# Tasks

Check things off here. Owner column is a suggestion, swap as needed.

## Now, this week

- [ ] Everyone clones this repo and runs `./setup.sh` successfully — all five
- [x] ~~Verify `.topo` files load.~~ They did not. Upstream bug found and
      fixed, see HTSIM/OUR_CHANGES.md. 1:1 now works.
- [x] ~~Oversubscribed topologies do not complete.~~ They do. EQDS aborts on
      them (separate bug), and 8:1 needs `-q 2000`. RoCE and DCQCN work on
      all three. `run.sh` now takes a real topology argument.
- [ ] **The workload is too light to exercise topology.** 1:1 and 4:1 give
      identical times because eight flows never saturate the pod uplinks.
      E2 needs the generator producing enough offered load to make
      oversubscription bind. **This is now the critical path.** — Vasili
- [ ] Investigate why `htsim_eqds` aborts on oversubscribed fabrics. Five
      bare `abort()` calls in eqds.cpp, one is being hit. Low priority,
      three of four policies work without it. — Chris
- [ ] Run E1, the uncontended per-tier cost table, using hand written
      matrices. Do not wait for the generator. — Quincy
- [ ] Download and parse the Azure LLM Inference trace — Vasili
- [ ] Find why `set_priority` is commented out in `main_eqds.cpp` and
      `main_uec.cpp`, and whether uncommenting works — Chris

## Week 2

- [ ] Generator emits valid `.cm` plus a deadline metadata file — Vasili
- [ ] Add an offered-load knob to the generator — Vasili
- [ ] Run E3, congestion control at 30/50/70/90 percent load, 16 runs — Quincy
- [ ] Build the plotting script: load on x, distance from floor on y, one
      line per policy. **This figure is Progress Report 2.** — Quincy
- [ ] Reproduce one figure from the HPCC paper — Michael
- [ ] Confirm `-strat ecmp_ar` runs, which is the adaptive routing axis — Matt

## Week 3

- [ ] Run E2, the topology sweep — Quincy
- [ ] SRPT via generator-assigned priority, if priority works — Chris
- [ ] **Hard stop on scheduling.** If priority is not working, write it up as
      future work and move to helping with runs — Chris
- [ ] Reproduce one figure from the DCQCN paper — Michael
- [ ] Draft the results section — Michael

## Week 4

- [ ] Run E5, topology crossed with congestion control — Quincy
- [ ] Finish and submit Progress Report 2

## Document corrections, any time

- [ ] Fix stale future tense in methodology 4.1, the build is verified now
- [ ] Replace OPT-13B with Llama-2-13B, same architecture, 4096 context
- [ ] Correct 1.28 GB to 1.34 GB throughout
- [ ] Fix typos: "file file", "flow are generated", "cacher sizes"
- [ ] **Delete the Checkpoint 2 note saying HTSIM lacks DCQCN.** It ships it,
      inside `htsim_roce` behind the `-dcqcn` flag.
- [ ] Add htsim provenance: UEC reference simulator, ETH Zurich SPCL fork
- [ ] Add the rail-optimized paper to related work

## Stretch

- [ ] E6 incast across congestion control policies
- [ ] E7 scale-up domain sweep at 8 and 72
- [ ] Open an upstream issue or PR on HTSIM for the DCQCN fix

## Minimum for Progress Report 2

Setup done, topology files verified, E1 table, one E3 plot with analysis, one
validation number, clean document. Everything else is upside.
