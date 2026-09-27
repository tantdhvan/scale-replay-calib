#!/usr/bin/env python3
"""Run the self-contained P0 Max-Cut experiment grid."""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import random
import subprocess
import sys
from pathlib import Path
from statistics import NormalDist


ROOT = Path(__file__).resolve().parents[1]


def parse_graph_spec(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError("graph must have NAME=PATH form")
    name, raw_path = spec.split("=", 1)
    if not name or not raw_path:
        raise argparse.ArgumentTypeError("graph must have NAME=PATH form")
    return name, Path(raw_path).expanduser().resolve()


def read_graph_size(path: Path) -> tuple[int, int]:
    with path.open(encoding="utf-8") as handle:
        first = handle.readline().split()
    if len(first) != 2:
        raise ValueError(f"invalid compact graph header in {path}")
    return int(first[0]), int(first[1])


def prepare_dataset(
    name: str,
    raw_path: Path,
    prepared_dir: Path,
    sample_dir: Path,
    resource_seed: int,
    cost_seed: int,
    sigma_ratio: float,
    python_exe: str,
) -> Path:
    prepared_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    compact = prepared_dir / f"{name}.graph"
    annotated = prepared_dir / f"{name}_gaussian_fixed.graph"
    sample = sample_dir / f"{name}_gaussian_samples.tsv"
    prepare_script = ROOT / "src" / "prepare_maxcut.py"
    cost_script = ROOT / "src" / "generate_costs.py"
    if not compact.exists():
        subprocess.run(
            [python_exe, str(prepare_script), "--source", str(raw_path), "--output", str(compact)],
            check=True,
        )
    if not annotated.exists():
        subprocess.run(
            [
                python_exe,
                str(cost_script),
                "--graph",
                str(compact),
                "--output-graph",
                str(annotated),
                "--output-samples",
                str(sample),
                "--resource-seed",
                str(resource_seed),
                "--cost-seed",
                str(cost_seed),
                "--sigma-ratio",
                str(sigma_ratio),
            ],
            check=True,
        )
    return annotated


def gaussian_sum(path: Path, alpha: float) -> tuple[int, int, float]:
    n, m = read_graph_size(path)
    with path.open(encoding="utf-8") as handle:
        next(handle)
        for _ in range(m):
            next(handle)
        marker = next(handle).split()
        if marker[:1] != ["VERTEX_GAUSSIAN_COSTS_P0_V1"]:
            raise ValueError(f"missing P0 resource block in {path}")
        mus = []
        variances = []
        for _ in range(n):
            _, mu, variance = next(handle).split()
            mus.append(float(mu))
            variances.append(float(variance))
    z = NormalDist().inv_cdf(1.0 - alpha)
    return n, m, sum(mus) + z * math.sqrt(sum(variances))


def completed_block_keys(path: Path) -> set[tuple[str, int, float]]:
    """Return complete dataset/repetition/budget blocks already in a log."""
    completed: set[tuple[str, int, float]] = set()
    active: tuple[str, int, float] | None = None
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("START "):
                fields = dict(item.split("=", 1) for item in line[6:].split())
                active = (fields["dataset"], int(fields["repetition"]), round(float(fields["rho"]), 12))
            elif line.startswith("END ") and active is not None:
                completed.add(active)
                active = None
    return completed


def run_block(
    executable: Path,
    graph_path: Path,
    dataset: str,
    repetition: int,
    order_seed: int,
    fraction: float,
    capacity: float,
    alpha: float,
    epsilon: float,
    delta: float,
    gamma: float,
    configuration: str,
    modes: tuple[str, ...],
) -> str:
    budget = fraction * capacity
    base_command = [
        str(executable),
        "--graph",
        str(graph_path),
        "--budget",
        f"{budget:.17g}",
        "--budget-fraction",
        f"{fraction:.17g}",
        "--alpha",
        f"{alpha:.17g}",
        "--epsilon",
        f"{epsilon:.17g}",
        "--delta",
        f"{delta:.17g}",
        "--gamma",
        f"{gamma:.17g}",
        "--configuration",
        configuration,
        "--order",
        "random",
        "--order-seed",
        str(order_seed),
    ]
    output = [
        f"START dataset={dataset} repetition={repetition} rho={fraction:.17g} "
        f"B={budget:.17g} order_seed={order_seed}\n"
    ]
    # Offline-Greedy is deterministic for a fixed graph, resource instance,
    # and budget.  In isolated mode it is therefore run only for the first
    # repetition; the streaming methods still use every requested order.
    effective_modes = tuple(
        mode for mode in modes
        if mode != "offline" or repetition == 0 or "all" in modes
    )
    for mode in effective_modes:
        completed = subprocess.run(
            [*base_command, "--mode", mode],
            capture_output=True,
            text=True,
            check=True,
        )
        output.append(completed.stdout)
        if completed.stderr:
            output.append("STDERR\n")
            output.append(completed.stderr)
    output.append(f"END dataset={dataset} repetition={repetition} rho={fraction:.17g}\n")
    return "".join(output)


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", action="append", required=True, help="NAME=raw edge-list path")
    parser.add_argument("--executable", default=str(ROOT / "build" / "p0_maxcut.exe"))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--prepared-dir", default=str(ROOT / "data" / "processed"))
    parser.add_argument("--sample-dir", default=str(ROOT / "scratch"))
    parser.add_argument("--log", default=str(ROOT / "results" / "maxcut" / "run.log"))
    parser.add_argument("--budget-fractions", default="0.01,0.02,0.03,0.04,0.05")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--epsilon", type=float, default=0.053)
    parser.add_argument("--delta", type=float, default=0.053)
    parser.add_argument("--gamma", type=float, default=0.053)
    parser.add_argument("--configuration", default="theory")
    parser.add_argument("--sigma-ratio", type=float, default=0.5)
    parser.add_argument("--resource-seed", type=int, default=42)
    parser.add_argument("--cost-seed", type=int, default=46)
    parser.add_argument("--order-seed-start", type=int, default=45)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument(
        "--modes",
        default=None,
        help="comma-separated modes: scale,replay,calib,offline; defaults to all or isolated all",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="append completed blocks to the existing log instead of replacing it",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="with --append, skip dataset/repetition/budget blocks already terminated by END",
    )
    parser.add_argument(
        "--separate-algorithms",
        action="store_true",
        help="run algorithms in separate processes; Offline-Greedy is run once per budget",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of independent dataset/order/budget blocks to run concurrently",
    )
    args = parser.parse_args()

    executable = Path(args.executable).expanduser().resolve()
    if not executable.exists():
        raise SystemExit(f"missing executable: {executable}; run the build first")
    fractions = [float(item) for item in args.budget_fractions.split(",") if item.strip()]
    if not fractions or any(item <= 0 for item in fractions):
        raise SystemExit("budget fractions must be positive")
    if args.repetitions < 1:
        raise SystemExit("repetitions must be positive")
    if args.workers < 1:
        raise SystemExit("workers must be positive")

    allowed_modes = {"scale", "replay", "calib", "offline", "all"}
    if args.modes is None:
        modes = ("scale", "replay", "calib", "offline") if args.separate_algorithms else ("all",)
    else:
        modes = tuple(item.strip() for item in args.modes.split(",") if item.strip())
        if not modes or any(item not in allowed_modes for item in modes):
            raise SystemExit("--modes must contain only scale,replay,calib,offline, or all")
        if "all" in modes and len(modes) != 1:
            raise SystemExit("--modes=all cannot be combined with other modes")

    graphs = [parse_graph_spec(spec) for spec in args.graph]
    prepared_dir = Path(args.prepared_dir).expanduser().resolve()
    sample_dir = Path(args.sample_dir).expanduser().resolve()
    log_path = Path(args.log).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    annotated = {}
    capacities = {}
    for name, raw_path in graphs:
        if not raw_path.exists():
            raise SystemExit(f"missing raw graph: {raw_path}")
        annotated[name] = prepare_dataset(
            name,
            raw_path,
            prepared_dir,
            sample_dir,
            args.resource_seed,
            args.cost_seed,
            args.sigma_ratio,
            args.python,
        )
        n, m, capacity = gaussian_sum(annotated[name], args.alpha)
        capacities[name] = (n, m, capacity)
        print(f"{name}: n={n} m={m} gV={capacity:.12g}")

    jobs = []
    for name, _ in graphs:
        graph_path = annotated[name]
        _, _, capacity = capacities[name]
        for repetition in range(args.repetitions):
            order_seed = args.order_seed_start + repetition
            for fraction in fractions:
                jobs.append((name, graph_path, repetition, order_seed, fraction, capacity))

    if args.resume:
        if not args.append:
            raise SystemExit("--resume requires --append")
        if log_path.exists():
            known = completed_block_keys(log_path)
            before = len(jobs)
            jobs = [
                job for job in jobs
                if (job[0], job[2], round(job[4], 12)) not in known
            ]
            print(f"resume: skipped {before - len(jobs)} complete blocks; {len(jobs)} remain")

    if not jobs:
        print("no experiment blocks remain")
        return 0

    log_mode = "a" if args.append else "w"
    with log_path.open(log_mode, encoding="utf-8", newline="") as log:
        if args.workers == 1:
            completed_blocks = (
                run_block(
                    executable,
                    graph_path,
                    name,
                    repetition,
                    order_seed,
                    fraction,
                    capacity,
                    args.alpha,
                    args.epsilon,
                    args.delta,
                    args.gamma,
                    args.configuration,
                    modes,
                )
                for name, graph_path, repetition, order_seed, fraction, capacity in jobs
            )
            for block in completed_blocks:
                log.write(block)
                log.flush()
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [
                    executor.submit(
                        run_block,
                        executable,
                        graph_path,
                        name,
                        repetition,
                        order_seed,
                        fraction,
                        capacity,
                        args.alpha,
                        args.epsilon,
                        args.delta,
                        args.gamma,
                        args.configuration,
                        modes,
                    )
                    for name, graph_path, repetition, order_seed, fraction, capacity in jobs
                ]
                for index, future in enumerate(as_completed(futures), start=1):
                    log.write(future.result())
                    log.flush()
                    if index % max(1, args.workers) == 0 or index == len(futures):
                        print(f"completed blocks: {index}/{len(futures)}")
    print(f"wrote {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
