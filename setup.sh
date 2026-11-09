#!/usr/bin/env bash
# Build the simulator and smoke-test it. Safe to re-run.
# HTSIM source lives in this repo, so there is nothing to clone or patch.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "==> prerequisites"

# cmake is often installed somewhere that is not on PATH in a fresh shell,
# e.g. a pip --user install or the Kitware app bundle. Look in the usual
# places before giving up.
if ! command -v cmake >/dev/null 2>&1; then
  for d in /Applications/CMake.app/Contents/bin \
           "$HOME"/Library/Python/3.*/bin \
           "$HOME"/.local/bin \
           "$HOME"/.local/lib/python3.*/site-packages/cmake/data/bin \
           /opt/homebrew/bin \
           /usr/local/bin \
           /usr/bin; do
    if [ -x "$d/cmake" ]; then
      export PATH="$d:$PATH"
      echo "    found cmake in $d (not on your PATH)"
      echo "    to make that permanent:  echo 'export PATH=\"$d:\$PATH\"' >> ~/.zshrc"
      break
    fi
  done
fi

if ! command -v cmake >/dev/null 2>&1; then
  cat <<'MSG'
cmake not found. Works on any platform, no compiling:

    pip3 install --user cmake

then reopen your terminal, or add its bin directory to PATH.

  Linux  : sudo apt install cmake g++      (or dnf/pacman equivalent)
  macOS  : official universal binary from https://cmake.org/download/
           Avoid "brew install cmake" unless a bottle exists for your macOS
           version, otherwise Homebrew compiles it from source and that
           takes a very long time.
  Windows: this project needs WSL2. See README.md.
MSG
  exit 1
fi

command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
cmake --version | head -1

echo "==> building htsim_roce (DCQCN), htsim_hpcc, htsim_eqds"
cd HTSIM/htsim/sim
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build --target htsim_roce htsim_hpcc htsim_eqds --parallel 4
cd "$HERE"

echo
echo "==> smoke test: DCQCN, one Llama-3-70B 4K cache at TP=8"
echo "    expect 8 flows, 0 violations, about 4.5x floor (15,171 us at the default seed)"
echo "    paper-faithful DCQCN with probabilistic ECN marking; the 3.6x in checkpoint 1"
echo "    was the legacy transport, reproducible with EXTRA_ARGS=-dcqcn_legacy ECN_STEP=1"
./scripts/run.sh dcqcn default smoketest workloads/kv_llama70b_4k_tp8.cm
echo
echo "SETUP OK. Read TASKS.md, then run  claude  in this folder."
