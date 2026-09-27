#!/usr/bin/env python3
"""Normalize a raw undirected edge list for the independent P0 package."""

from __future__ import annotations

import argparse
from pathlib import Path


def normalize(source: Path, output: Path) -> tuple[int, int, int]:
    vertex_map: dict[int, int] = {}
    edges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    self_loops = 0

    with source.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 2:
                raise ValueError(f"{source}:{line_number}: expected two integer IDs")
            u_raw, v_raw = map(int, fields)
            for vertex in (u_raw, v_raw):
                if vertex not in vertex_map:
                    vertex_map[vertex] = len(vertex_map)
            if u_raw == v_raw:
                self_loops += 1
                continue
            u, v = vertex_map[u_raw], vertex_map[v_raw]
            edge = (min(u, v), max(u, v))
            if edge not in seen:
                seen.add(edge)
                edges.append(edge)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{len(vertex_map)} {len(edges)}\n")
        for u, v in edges:
            handle.write(f"{u} {v}\n")
    return len(vertex_map), len(edges), self_loops


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        n, m, loops = normalize(args.source, args.output)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(f"normalized {args.source}: vertices={n}, edges={m}, removed_self_loops={loops}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
