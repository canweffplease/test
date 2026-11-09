# CS 6250 — KV cache transfer over datacenter networks

Evaluating how topology, congestion control, load balancing and transport
scheduling affect KV cache transfer in disaggregated LLM inference.

## Setup

    git checkout michael/work && ./setup.sh

Builds the simulator into `HTSIM/htsim/sim/build/` and runs a smoke test.
The first time takes a few minutes. HTSIM source is in this repo, so there is
nothing to clone.

### Platforms

**Linux.** Works. This is what HTSIM's own CI tests. You need `cmake` and a
C++17 compiler, e.g. `sudo apt install cmake g++`.

**macOS.** Works, verified on arm64. You need cmake, but do not install it
with Homebrew unless a bottle exists for your macOS version, or it will
compile from source for half an hour. Use `pip3 install --user cmake` or the
official binary from cmake.org.

**Windows.** Not natively. HTSIM's build uses GCC/Clang compiler flags
(`-Wall`, `-fPIE`, `-O3`) that MSVC rejects, and all our scripts are bash.
Use **WSL2** with Ubuntu, then follow the Linux instructions inside it:

    wsl --install -d Ubuntu          # in PowerShell, once
    # then inside Ubuntu:
    sudo apt update && sudo apt install -y cmake g++ git python3 python3-matplotlib python3-numpy
    git clone <repo> && cd 6250-kv-cache-networking && ./setup.sh

Keep the repo inside the WSL filesystem (`~/`) rather than `/mnt/c/`, since
builds on the Windows mount are very slow. The plotting scripts need
matplotlib and numpy.

## Run one configuration

    ./scripts/run.sh <cc> <topo> <label> <matrix.cm>
    SEED=1 ./scripts/run.sh hpcc default load100 workloads/kv_llama70b_4k_tp8.cm
    SEED=1 END=200000 ./scripts/run.sh eqds rail_128_1os load100 workloads/kv128_rail.cm

- `<cc>` is `dcqcn`, `hpcc`, `eqds` or `roce` (PFC only, no CC).
- `<topo>` is `default` (the 16-node auto-generated tree, 400 G). You can
  also give the name of any file in `HTSIM/htsim/sim/datacenter/topologies/`
  without `.topo`, e.g. `fat_tree_128_1os`, `fat_tree_128_4os`,
  `fat_tree_128_8os` or `rail_128_1os`. The aliases `ft128_1os/4os/8os`
  also set HPCC's base RTT for those trees.
- run.sh reads the link speed from the `.topo` file, passes the matching
  `-linkspeed`, and sets `-q 2000` on every file topology. Do not hand-roll
  simulator commands.

Environment knobs:

| knob | default | effect |
|---|---|---|
| `SEED` | binary default 13 | ECMP hash salts, i.e. which flows collide. Runs are deterministic per seed |
| `END` | 2000000 | simulated end time in us. Smaller is faster for short matrices |
| `STRAT` | `ecmp_host` | load balancing: `ecmp_ar`, `"ecmp_host_ar 50"`, `ecmp_rr` |
| `HTSIM_BUILD` | `build` | build directory under `HTSIM/htsim/sim` |
| `EXTRA_ARGS` | none | extra simulator flags, e.g. `-dcqcn_legacy` or `-hpcc_legacy` |

Also `PATHS`, `OUTDIR`, `HPCC_T`, and `ECN_STEP=1` (DCQCN step marking
instead of RED); see the header of `scripts/run.sh`. A run writes
`results/<cc>_<topo>_<label>[_<strat>][_p<paths>][_s<seed>]` with the
suffixes `.log`, `.csv` (per flow), `_requests.csv` and `_summary.json`.
parse.py refuses a run that has lossless violations, unfinished flows or
misread start times.

## Run all policies over seeds

    ./scripts/sweep.sh workloads/kv_llama70b_4k_tp8.cm default ref 10

This runs all four policies with seeds 1-10 and prints median and spread.
Never report one seed: the spread across seeds is ECMP placement.

## Reproduce the experiments

Every driver is resumable and skips runs whose summary already exists. Most
take `-j N` for parallel runs and `--dry-run`, plus `POLICIES="roce eqds"`
to pick policies. Some default to the per-fix build directories we used
during development (`build_start`, `build_hpccfix`, `build_eqds2`). After
`./setup.sh`, pass `HTSIM_BUILD=build`, which holds every fix. Figures go to
`results/figs/`.

| driver | what | results |
|---|---|---|
| `scripts/e1.sh` | E1, the uncontended per-tier cost table, plus DCQCN threshold sweep, seeds and traces | `results/e1/` |
| `scripts/e2.sh [reps]` | E2, the topology sweep (1:1, 4:1, 8:1 x load), plus a PFC victim test | `results/e2/`, `figs/e2_topology*` |
| `scripts/e3.sh ft16\|ft128_1os [reps]` | E3, congestion control at 30-90 % load | `results/e3/<topo>/`, `figs/e3_*` |
| `scripts/e4.sh [gen\|run\|plot\|diag]` | E4, fair share, SRPT and EDF via EQDS priority | `results/e4/` (the post-fix rerun is `results/e4_fixed/`), `figs/e4_*` |
| `scripts/e6.sh [reps]` | E6, 2/4/8-to-1 incast against TP=8 | `results/e6/`, `figs/e6_incast*` |
| `scripts/e7_scaleup.py` | E7, the scale-up domain size model (analytic plus Monte Carlo, no simulator) | `results/e7/`, `figs/e7_*` |
| `scripts/e8.sh [reps]` | E8, whether rankings hold across size distributions (4K, Azure conv/code, Llama-2-13B) | `results/e8/`, `figs/e8_*` |
| `scripts/rail.sh` | rail-optimized vs fat tree, matched-index traffic | `results/rail/summary.csv` |
| `scripts/validation.sh` | convergence and queue traces vs the HPCC and DCQCN papers | `results/validation/`, `figs/validation_*` |
| `scripts/hpcc_fidelity.sh` | legacy HPCC vs the paper's Algorithm 1 | `results/hpcc_fidelity/` |

Each experiment's write-up names the exact command and binaries it used.

## What we found so far

With a few large flows, ECMP hash collisions decide TP=8 completion time
more than congestion control does, because a request finishes with its
slowest shard and that shard is the one sharing a link. EQDS is the only policy
that consistently lands near the floor, and E1 shows that is mostly
packet spraying, not its credit loop. DCQCN, legacy or paper-faithful, is
slower than PFC-only RoCE wherever we have measured both. Under incast,
every policy run so far is pinned at the receiver downlink bound, and
policies differ only in fairness and finish order. On the 4:1 tree,
oversubscription binds every request even at light load: one TP=8
instance needs twice its pod's core uplink, and the transport's own
inefficiency multiplies on top of that. With strict receiver priority,
SRPT brings the median request to about 1.1x floor, at the cost of the
longest prompts, while EDF degenerates toward arrival order once the
receiver is backlogged. Scale-up domain size alone removes little traffic; placement
decides (E7). Tables and caveats are in `docs/EXPERIMENTS.md`
and the per-experiment docs.

## Where things are

| | |
|---|---|
| `docs/PROJECT.md` | problem, motivation, scope, key references |
| `docs/EXPERIMENTS.md` | E1 to E9 plan, status, reference table |
| `docs/E1.md` ... `docs/E8.md` (E8 in progress; no E5, folded into E2) | one write-up per experiment, mechanism included |
| `docs/VALIDATION.md` | convergence vs the HPCC and DCQCN papers |
| `docs/TOPOLOGY_RAIL.md` | rail-optimized (expressible) and rail-only (not) in HTSIM |
| `docs/checkpoint2_draft.md` | Progress Report 2 draft |
| `HTSIM/OUR_CHANGES.md` | every simulator change, bug and fix, with evidence |
| `docs/htsim-changes-vs-upstream.patch` | full diff against upstream (`scripts/diff-upstream.sh`) |
| `TASKS.md` | what is done, what remains |
| `CLAUDE.md` | context Claude Code reads automatically |
| `workloads/` | traffic matrices and sidecars, see `workloads/README.md` |
| `data/azure/` | Azure LLM inference traces (2023) |
| `scripts/run.sh`, `sweep.sh` | one run; all policies over seeds |
| `scripts/gen_workload.py` | matrix generator: offered load, Poisson or trace arrivals, deadlines, `prio` |
| `scripts/parse.py` | log to per-flow CSV, per-request CSV and summary JSON |
| `scripts/plot.py` | load vs floor and CDF figures |
| `scripts/exp_report.py` | E2/E3/E6 tables and figures, with iso and ideal bounds |
| `scripts/explib.sh` | shared job runner for e2/e3/e6 (resume, END doubling) |
| `results/` | per-run outputs and `figs/` |

## Working with Claude Code

    claude

CLAUDE.md is picked up automatically. Start by pointing it at the first
unchecked item under "Remaining work" in TASKS.md.
