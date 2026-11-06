#!/usr/bin/env bash
# Build the simulator and smoke-test it. Safe to re-run.
# HTSIM source lives in this repo, so there is nothing to clone or patch.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "==> prerequisites"
command -v cmake   >/dev/null || { echo "cmake missing -> brew install cmake"; exit 1; }
command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
cmake --version | head -1

echo "==> building htsim_roce (DCQCN), htsim_hpcc, htsim_eqds"
cd HTSIM/htsim/sim
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build --target htsim_roce htsim_hpcc htsim_eqds --parallel 4
cd "$HERE"

echo
echo "==> smoke test: DCQCN, one Llama-3-70B 4K cache at TP=8"
echo "    expect 8 flows, 0 violations, about 3.6x floor"
./scripts/run.sh dcqcn default smoketest workloads/kv_llama70b_4k_tp8.cm
echo
echo "SETUP OK. Read TASKS.md, then run  claude  in this folder."
