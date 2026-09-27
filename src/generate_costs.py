#!/usr/bin/env python3
"""Attach independent Gaussian vertex resources to a normalized graph."""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path


def read_graph(path: Path) -> tuple[int, list[str]]:
    with path.open(encoding="utf-8") as handle:
        header = handle.readline().strip()
        n, m = map(int, header.split())
        lines = [header + "\n"]
        for index in range(m):
            line = handle.readline()
            if len(line.split()) != 2:
                raise ValueError(f"{path}: malformed edge {index}")
            lines.append(line.rstrip("\n") + "\n")
    return n, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--output-graph", type=Path, required=True)
    parser.add_argument("--output-samples", type=Path, required=True)
    parser.add_argument("--sigma-ratio", type=float, default=0.5)
    parser.add_argument("--resource-seed", type=int, default=42)
    parser.add_argument("--cost-seed", type=int, default=46)
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    if args.sigma_ratio <= 0 or args.samples <= 0:
        raise SystemExit("sigma-ratio and samples must be positive")
    n, graph_lines = read_graph(args.graph)
    rng = random.Random(args.resource_seed)
    sample_rng = random.Random(args.cost_seed)
    parameters = []
    for vertex in range(n):
        mu = 1.0 - rng.random()
        sigma = args.sigma_ratio * mu
        parameters.append((vertex, mu, sigma * sigma))

    args.output_graph.parent.mkdir(parents=True, exist_ok=True)
    args.output_samples.parent.mkdir(parents=True, exist_ok=True)
    with args.output_graph.open("w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(graph_lines)
        handle.write(f"VERTEX_GAUSSIAN_COSTS_P0_V1 {n}\n")
        for vertex, mu, variance in parameters:
            handle.write(f"{vertex} {mu:.17g} {variance:.17g}\n")
    with args.output_samples.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("sample_id", "vertex_id", "mu", "variance", "noise",
                         "sampled_cost", "resource_seed", "cost_seed", "sigma_ratio"))
        for sample in range(1, args.samples + 1):
            for vertex, mu, variance in parameters:
                sigma = variance ** 0.5
                noise = sample_rng.gauss(0.0, sigma)
                writer.writerow((sample, vertex, f"{mu:.17g}", f"{variance:.17g}",
                                 f"{noise:.17g}", f"{mu + noise:.17g}",
                                 args.resource_seed, args.cost_seed, args.sigma_ratio))
    print(f"generated {args.output_graph}: vertices={n}, resource_seed={args.resource_seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
