# CS 6250 — KV cache transfer over datacenter networks

Evaluating how topology, congestion control, load balancing and transport
scheduling affect KV cache transfer in disaggregated LLM inference.

## Setup

    ./setup.sh

Builds the simulator and runs a smoke test. Needs cmake (`brew install cmake`)
and Xcode command line tools. Takes a few minutes the first time.

## Run one configuration

    ./scripts/run.sh hpcc default load100 workloads/kv_llama70b_4k_tp8.cm

Writes `results/hpcc_default_load100.csv`.

## Run the congestion control sweep

    ./scripts/sweep.sh workloads/kv_llama70b_4k_tp8.cm

Runs all four policies and prints a comparison table.

## Where things are

| | |
|---|---|
| `docs/PROJECT.md` | problem, motivation, scope, key references |
| `docs/EXPERIMENTS.md` | E1 to E9, metrics, preliminary results |
| `TASKS.md` | who does what, week by week |
| `CLAUDE.md` | context Claude Code reads automatically |
| `workloads/` | traffic matrices |
| `results/` | one CSV per run |

## Working with Claude Code

    claude

CLAUDE.md is picked up automatically. Start by pointing it at the first
unchecked item in TASKS.md.
