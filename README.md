# CS 6250 — KV cache transfer over datacenter networks

Evaluating how topology, congestion control, load balancing and transport
scheduling affect KV cache transfer in disaggregated LLM inference.

## Setup

    ./setup.sh

Builds the simulator and runs a smoke test. Takes a few minutes the first
time. HTSIM source is in this repo, so there is nothing to clone.

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
    sudo apt update && sudo apt install -y cmake g++ git python3
    git clone <repo> && cd 6250-project && ./setup.sh

Keep the repo inside the WSL filesystem (`~/`) rather than `/mnt/c/`, since
builds on the Windows mount are very slow.

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
| `HTSIM/OUR_CHANGES.md` | what we changed in the simulator and why |
| `docs/EXPERIMENTS.md` | E1 to E9, metrics, preliminary results |
| `TASKS.md` | who does what, week by week |
| `CLAUDE.md` | context Claude Code reads automatically |
| `workloads/` | traffic matrices |
| `results/` | one CSV per run |

## Working with Claude Code

    claude

CLAUDE.md is picked up automatically. Start by pointing it at the first
unchecked item in TASKS.md.
