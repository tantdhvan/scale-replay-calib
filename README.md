# Streaming Max-Cut under a Gaussian Chance Constraint

This repository contains the independent experiment package for the paper
"Streaming for Non-Monotone Submodular Maximization under a Gaussian Chance
Constraint." It has no imports, binaries, paths, or runtime dependency on any
other draft repository.

The package evaluates three deterministic streaming algorithms - SCALE (one
pass), REPLAY (two passes), and CALIB (three passes) - against `Offline-Greedy`,
an offline reference heuristic. The application is unit-weight Max-Cut on an
undirected graph. Vertices arrive in a stream; the exact value oracle uses the
static graph to evaluate the cut function

```text
f(S) = number of edges with exactly one endpoint in S.
```

Each vertex has an independent Gaussian resource with mean `mu_v` and
variance `sigma_v^2`. A set is feasible when

```text
g(S) = sum(mu_v) + z_(1-alpha) * sqrt(sum(sigma_v^2)) <= B.
```

This is a vertex-stream experiment with a static value oracle, not an
edge-stream implementation.

## Repository layout

```text
.
|- README.md
|- requirements.txt
|- Makefile
|- src/
|  |- experiment.cpp       # algorithms, oracle, feasibility, measurements
|  |- prepare_maxcut.py    # normalize an undirected edge list
|  |- generate_costs.py    # attach fixed Gaussian vertex resources
|  |- run_maxcut.py        # reproducible experiment runner
|  |- summarize_maxcut.py  # raw and Student-t CSV summaries
|  `- plot_maxcut.py       # six-page publication figure builder
|- data/                   # dataset instructions; downloaded data is ignored
|- results/                # output instructions; generated outputs are ignored
`- slurm/run_maxcut.slurm  # tracked cluster batch script
```

Generated binaries, downloaded data, scratch previews, logs, and result files
are intentionally excluded by `.gitignore`.

## Requirements and build

Use Python 3.10 or newer and a C++17 compiler. Install the only Python plotting
dependency with:

```powershell
python -m pip install -r requirements.txt
```

On Linux or macOS:

```bash
make
```

On Windows with MinGW-w64:

```powershell
g++ -std=c++17 -O3 -Wall -Wextra -pedantic `
  src/experiment.cpp -lpsapi -o build/p0_maxcut.exe
```

The executable reports exact-oracle queries, objective value, feasibility,
running time, and peak process working-set memory. Peak memory is comparable
across algorithms only when they are run in separate processes.

## Data

Download `ca-GrQc` and `ca-AstroPh` from the [SNAP collaboration network
collection](https://snap.stanford.edu/data/), extract the `.txt` files, and
place them at:

```text
data/raw/ca-GrQc.txt
data/raw/ca-AstroPh.txt
```

The preparation step removes self-loops and duplicate undirected edges. The
cost-generation step adds the fixed Gaussian resource block used by the
experiments. See [`data/README.md`](data/README.md) for the dataset pages.

## Reproduce the reported experiment

The reported practical configuration is:

```text
alpha = 0.05                 resource seed = 42
sigma_v = 0.5 * mu_v        stream-order seeds = 45,...,54
epsilon = 0.12              delta = 0.30
gamma = 0.30                budget fractions = 0.01,...,0.05
```

Build the executable, then run the full grid locally from the repository root:

```powershell
python src/run_maxcut.py `
  --graph ca-GrQc=data/raw/ca-GrQc.txt `
  --graph ca-AstroPh=data/raw/ca-AstroPh.txt `
  --executable build/p0_maxcut.exe `
  --epsilon 0.12 --delta 0.30 --gamma 0.30 `
  --configuration practical-balanced-e012-d030-g030 `
  --separate-algorithms --workers 10 `
  --log results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log
```

`--separate-algorithms` measures each algorithm in its own process. Because
`Offline-Greedy` is deterministic for a fixed graph, resource instance, and
budget, the runner executes it once per budget while running the three
streaming methods over all ten stream orders.

Create raw and summary tables:

```powershell
python src/summarize_maxcut.py `
  --log results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log `
  --raw-csv results/maxcut/practical-balanced-e012-d030-g030-raw.csv `
  --summary-csv results/maxcut/practical-balanced-e012-d030-g030-summary.csv
```

Create the six-page figure PDFs (legend page plus five metric pages) for each
dataset from the combined log:

```powershell
python src/plot_maxcut.py `
  --log-glob 'results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log' `
  --dataset ca-GrQc --dataset-label ca-GrQc --output-prefix grqc `
  --outdir results/maxcut/grqc

python src/plot_maxcut.py `
  --log-glob 'results/maxcut/practical-balanced-e012-d030-g030-isolated-w10.log' `
  --dataset ca-AstroPh --dataset-label ca-AstroPh --output-prefix astroph `
  --outdir results/maxcut/astroph
```

The five metric pages are objective value, normalized chance cost, exact-oracle
queries, peak process memory, and running time. The plots show means and
two-sided 95% Student-`t` intervals over the ten stream orders. The deterministic
`Offline-Greedy` curve is a full-access location reference, not an exact
optimum and not a like-for-like streaming competitor.

## Smoke test

The bounded in-memory smoke test needs no dataset:

```powershell
build/p0_maxcut.exe --smoke
```

It should print four CSV result rows after the header and exit with status 0.

## Cluster execution

For substantive cluster runs, use `slurm/run_maxcut.slurm` through `sbatch`.
Set `P0_GRQC` and `P0_ASTROPH` to the extracted raw graph paths before
submission. The script requests resources, builds with `srun`, and writes logs
under `results/maxcut/`.

## Citation and code availability

The public repository URL will be inserted here after the repository is
created. Until then, the manuscript's data-and-code statement refers to this
package by its local experiment directory. The code is intended to reproduce
the application-level results and does not replace the formal model and proofs
in the manuscript.
