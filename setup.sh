#!/usr/bin/env bash
# One-command setup. Safe to re-run. Works from a fresh clone of this repo.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PINNED=ac1aea6e34ccc8249cb59b4c7cdd920283c9cfdb   # HTSIM commit we validated against

echo "==> prerequisites"
command -v cmake >/dev/null || { echo "cmake missing -> brew install cmake"; exit 1; }
command -v git   >/dev/null || { echo "git missing -> xcode-select --install"; exit 1; }
command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
cmake --version | head -1

if [ ! -d HTSIM ]; then
  echo "==> cloning HTSIM"
  git clone https://github.com/spcl/HTSIM.git
  (cd HTSIM && git checkout -q "$PINNED" 2>/dev/null \
     || echo "    WARNING: could not pin to $PINNED, using default branch")
fi

echo "==> applying DCQCN fixes"
cd HTSIM
if git diff --quiet 2>/dev/null; then
  git apply ../htsim-dcqcn-fix.patch && echo "    applied"
else
  echo "    already patched, skipping"
fi
cd "$HERE"

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
echo "SETUP OK."
echo "Next: read TASKS.md, then run  claude  in this folder."
