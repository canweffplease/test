#!/usr/bin/env bash
#SBATCH --job-name=spritz-repro-full
#SBATCH --output=spritz-repro-%j.out
#SBATCH --error=spritz-repro-%j.err
#SBATCH --time=48:00:00
#SBATCH --cpus-per-task=16
#SBATCH --mem=16G

set -euo pipefail
cd "$(dirname "$0")"

export REPRO_PARALLEL="${SLURM_CPUS_PER_TASK:-16}"
bash reproduce.sh full
