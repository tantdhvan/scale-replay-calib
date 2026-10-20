#!/usr/bin/env python3
"""Aggregate P0 Max-Cut logs and emit CSV summaries."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev


FIELDS = [
    "algorithm",
    "budget_fraction",
    "B",
    "objective",
    "queries",
    "feasible",
    "chance_score",
    "chance_cost_over_B",
    "size",
    "n",
    "edges",
    "epsilon",
    "alpha",
    "order",
    "order_seed",
    "tangents",
    "active_states",
    "retained_candidate_slots",
]
ALGORITHM_ALIASES = {
    "1P-NSMGC": "1P-NSMGC",
    "2P-NSMGC": "2P-NSMGC",
    "3P-NSMGC": "3P-NSMGC",
    # Accept logs from earlier experiment runs and emit the current names.
    "SCALE": "1P-NSMGC",
    "REPLAY": "2P-NSMGC",
    "CALIB": "3P-NSMGC",
    "Offline-Greedy": "Offline-Greedy",
    "Offline-Greedy-CC": "Offline-Greedy",
}


def t_critical_95(df: int) -> float:
    # Adequate for the default ten repetitions; exact values avoid a SciPy dependency.
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}
    return table.get(df, 2.0 if df >= 120 else 1.96 if df >= 30 else 2.101)


def parse_log(path: Path) -> list[dict[str, str]]:
    rows = []
    dataset = None
    repetition = None
    rho = None
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("START "):
                fields = dict(item.split("=", 1) for item in line[6:].split())
                dataset = fields["dataset"]
                repetition = int(fields["repetition"])
                rho = float(fields["rho"])
                continue
            if not line or line.startswith("END ") or line == "algorithm":
                continue
            if line.split(",", 1)[0] not in ALGORITHM_ALIASES:
                continue
            values = next(csv.reader([line]))
            if len(values) != len(FIELDS):
                raise ValueError(f"unexpected result width ({len(values)}) in {path}: {line}")
            row = dict(zip(FIELDS, values))
            row["algorithm"] = ALGORITHM_ALIASES[row["algorithm"]]
            row["dataset"] = dataset or "unknown"
            row["repetition"] = str(repetition if repetition is not None else -1)
            row["budget_fraction"] = str(rho if rho is not None else row["budget_fraction"])
            rows.append(row)
    if not rows:
        raise ValueError(f"no result rows found in {path}")
    return rows


def summarize(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped = defaultdict(list)
    for row in rows:
        key = (row["dataset"], row["algorithm"], row["budget_fraction"])
        grouped[key].append(row)
    output = []
    for (dataset, algorithm, rho), group in sorted(grouped.items()):
        record = {"dataset": dataset, "algorithm": algorithm, "budget_fraction": rho, "repetitions": str(len(group))}
        for metric in ("objective", "queries", "chance_cost_over_B", "retained_candidate_slots"):
            values = [float(row[metric]) for row in group]
            avg = mean(values)
            half = 0.0 if len(values) < 2 else t_critical_95(len(values) - 1) * stdev(values) / math.sqrt(len(values))
            record[f"{metric}_mean"] = f"{avg:.12g}"
            record[f"{metric}_ci95_halfwidth"] = f"{half:.12g}"
        record["feasible_rate"] = f"{mean(float(row['feasible']) for row in group):.12g}"
        output.append(record)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--summary-csv", required=True)
    parser.add_argument("--raw-csv", required=True)
    args = parser.parse_args()
    rows = parse_log(Path(args.log).resolve())
    summary = summarize(rows)
    raw_path = Path(args.raw_csv).resolve()
    summary_path = Path(args.summary_csv).resolve()
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["dataset", "repetition"] + FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = list(summary[0])
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)
    print(f"raw rows: {len(rows)}")
    print(f"summary rows: {len(summary)}")
    print(f"wrote {raw_path}")
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
