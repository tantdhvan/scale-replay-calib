#!/usr/bin/env bash
# Reproduce the two-dataset isolated experiment grid on a Slurm node.
# Before submission, export P0_GRQC and P0_ASTROPH to the extracted .txt files.
# Example:
#   P0_GRQC=/data/ca-GrQc.txt P0_ASTROPH=/data/ca-AstroPh.txt sbatch slurm/run_maxcut.slurm
#SBATCH --job-name=p0-maxcut
#SBATCH --output=results/maxcut/slurm-%j.out
#SBATCH --error=results/maxcut/slurm-%j.err
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=10
#SBATCH --mem=8G

set -euo pipefail

: "${P0_GRQC:?Set P0_GRQC to the extracted ca-GrQc.txt path}"
: "${P0_ASTROPH:?Set P0_ASTROPH to the extracted ca-AstroPh.txt path}"

cd "${SLURM_SUBMIT_DIR}"
mkdir -p build data/processed results/maxcut scratch

make

python3 -m pip install --user -r requirements.txt

python3 src/run_maxcut.py \
  --graph ca-GrQc="${P0_GRQC}" \
  --graph ca-AstroPh="${P0_ASTROPH}" \
  --executable build/p0_maxcut \
  --epsilon 0.12 --delta 0.30 --gamma 0.30 \
  --configuration practical-balanced-e012-d030-g030 \
  --separate-algorithms --workers 10 \
  --log results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log

python3 src/summarize_maxcut.py \
  --log results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log \
  --raw-csv results/maxcut/practical-balanced-e012-d030-g030-raw.csv \
  --summary-csv results/maxcut/practical-balanced-e012-d030-g030-summary.csv

python3 src/plot_maxcut.py \
  --log-glob 'results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log' \
  --dataset ca-GrQc --dataset-label ca-GrQc --output-prefix grqc \
  --outdir results/maxcut/grqc

python3 src/plot_maxcut.py \
  --log-glob 'results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log' \
  --dataset ca-AstroPh --dataset-label ca-AstroPh --output-prefix astroph \
  --outdir results/maxcut/astroph
