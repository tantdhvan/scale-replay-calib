#!/usr/bin/env python3
"""Create publication-style Max-Cut figures from isolated experiment logs."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, LogLocator, MaxNLocator, NullLocator


RHO_VALUES = (0.01, 0.02, 0.03, 0.04, 0.05)
ORDER_SEEDS = tuple(range(45, 55))
ALGORITHMS = (
    ("Offline-Greedy", "Offline-Greedy", "#1f77b4", "o"),
    ("1P-NSMGC", "1P-NSMGC", "#d62728", "D"),
    ("2P-NSMGC", "2P-NSMGC", "#2ca02c", "s"),
    ("3P-NSMGC", "3P-NSMGC", "#9467bd", "^"),
)
ALGORITHM_ALIASES = {
    "Offline-Greedy": "Offline-Greedy",
    "Offline-Greedy-CC": "Offline-Greedy",
    "1P-NSMGC": "1P-NSMGC",
    "2P-NSMGC": "2P-NSMGC",
    "3P-NSMGC": "3P-NSMGC",
    # Accept historical logs while displaying the manuscript names.
    "SCALE": "1P-NSMGC",
    "REPLAY": "2P-NSMGC",
    "CALIB": "3P-NSMGC",
}
METRICS = (
    ("objective", "objective", r"Max-Cut value $f(S)$", False),
    ("chance_cost", "chance_cost_over_B", r"Normalized chance cost $g(S)/B$", False),
    ("queries", "queries", r"Oracle queries", True),
    ("memory", "peak_memory_mb", r"Peak memory usage (MB)", False),
    ("runtime", "runtime_ms", r"Running time (ms)", True),
)
# Keep these style values explicit so the manuscript-sized panels remain
# legible after the six-page chart is included at reduced width.
PANEL_MARKER_SIZE = 15.5
PANEL_MARKER_EDGE_WIDTH = 1.7
PANEL_LINE_WIDTH = 1.8
PANEL_XLABEL_SIZE = 25
PANEL_YLABEL_SIZE = 23
PANEL_TICK_SIZE = 19
LEGEND_MARKER_SIZE = 14.0
LEGEND_FONT_SIZE = 20.0
P0_FIELDS_LEGACY = (
    "algorithm", "budget_fraction", "B", "objective", "queries", "feasible",
    "chance_score", "chance_cost_over_B", "size", "n", "edges", "epsilon",
    "alpha", "order", "order_seed", "tangents", "active_states",
    "retained_candidate_slots",
)
P0_FIELDS_CONFIG = P0_FIELDS_LEGACY + ("delta", "gamma", "configuration")
P0_FIELDS = P0_FIELDS_CONFIG + ("runtime_ms", "peak_memory_mb")
T_CRITICAL_975 = (
    math.nan, 12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365,
    2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
)


def mean_interval(values: list[float]) -> tuple[float, float, float, float]:
    center = statistics.fmean(values)
    if len(values) <= 1:
        return center, center, center, 0.0
    deviation = statistics.stdev(values)
    df = len(values) - 1
    critical = T_CRITICAL_975[df] if df < len(T_CRITICAL_975) else 1.960
    half_width = critical * deviation / math.sqrt(len(values))
    return center, center - half_width, center + half_width, deviation


def rho_key(value: str | float) -> float:
    return round(float(value), 8)


def parse_logs(
    paths: list[Path], dataset_filter: str | None = None
) -> tuple[dict[tuple[str, float, int, str], float], dict[str, object]]:
    samples: dict[tuple[str, float, int, str], float] = {}
    graph_info: dict[str, object] = {}
    algorithm_names = set(ALGORITHM_ALIASES)
    for path in paths:
        current_dataset: str | None = None
        with path.open(encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                line = raw.strip()
                if line.startswith("START "):
                    fields = dict(item.split("=", 1) for item in line[6:].split())
                    current_dataset = fields.get("dataset")
                    continue
                if line.startswith("END "):
                    current_dataset = None
                    continue
                if (
                    not line
                    or line in {",".join(P0_FIELDS), ",".join(P0_FIELDS_CONFIG), ",".join(P0_FIELDS_LEGACY)}
                    or line.split(",", 1)[0] not in algorithm_names
                    or (dataset_filter is not None and current_dataset != dataset_filter)
                ):
                    continue
                values = next(csv.reader([line]))
                values[0] = ALGORITHM_ALIASES[values[0]]
                if len(values) == len(P0_FIELDS_LEGACY):
                    row = dict(zip(P0_FIELDS_LEGACY, values))
                    row.update({"delta": row["epsilon"], "gamma": row["epsilon"], "configuration": "legacy"})
                    row.update({"runtime_ms": "", "peak_memory_mb": ""})
                elif len(values) == len(P0_FIELDS_CONFIG):
                    row = dict(zip(P0_FIELDS_CONFIG, values))
                    row.update({"runtime_ms": "", "peak_memory_mb": ""})
                elif len(values) == len(P0_FIELDS):
                    row = dict(zip(P0_FIELDS, values))
                else:
                    raise ValueError(f"{path}:{line_number}: expected {len(P0_FIELDS_LEGACY)}, {len(P0_FIELDS_CONFIG)}, or {len(P0_FIELDS)} columns")
                if not row["runtime_ms"] or not row["peak_memory_mb"]:
                    raise ValueError(
                        f"{path}:{line_number}: peak memory/runtime fields are missing; "
                        "rerun with --separate-algorithms"
                    )
                rho = rho_key(row["budget_fraction"])
                seed = int(row["order_seed"])
                key_prefix = (row["algorithm"], rho, seed)
                metrics = {
                    "objective": float(row["objective"]),
                    "chance_cost_over_B": float(row["chance_cost_over_B"]),
                    "queries": float(row["queries"]),
                    "peak_memory_mb": float(row["peak_memory_mb"]),
                    "runtime_ms": float(row["runtime_ms"]),
                }
                for metric, value in metrics.items():
                    key = (*key_prefix, metric)
                    if key in samples:
                        raise ValueError(f"{path}:{line_number}: duplicate sample {key}")
                    samples[key] = value
                graph_info.setdefault("n", int(row["n"]))
                graph_info.setdefault("edges", int(row["edges"]))
                graph_info.setdefault("epsilon", float(row["epsilon"]))
                graph_info.setdefault("alpha", float(row["alpha"]))
                graph_info.setdefault("delta", float(row["delta"]))
                graph_info.setdefault("gamma", float(row["gamma"]))
                graph_info.setdefault("configuration", row["configuration"])
                if current_dataset is not None:
                    graph_info.setdefault("dataset", current_dataset)
    return samples, graph_info


def validate(
    samples: dict[tuple[str, float, int, str], float],
    allow_incomplete_offline: bool = False,
) -> None:
    for algorithm, *_ in ALGORITHMS:
        for rho in RHO_VALUES:
            seeds = ORDER_SEEDS
            if algorithm == "Offline-Greedy" and allow_incomplete_offline:
                seeds = tuple(
                    seed for seed in ORDER_SEEDS
                    if (algorithm, rho, seed, "objective") in samples
                )
                if not seeds:
                    raise ValueError(f"missing all Offline-Greedy samples at rho={rho}")
            for seed in seeds:
                for _, metric, *_ in METRICS:
                    key = (algorithm, rho, seed, metric)
                    if key not in samples:
                        raise ValueError(f"missing sample: {key}")


def compact_number(value: float, _position: int | None = None) -> str:
    for scale, suffix in ((1.0e9, "B"), (1.0e6, "M"), (1.0e3, "K")):
        if abs(value) >= scale:
            return f"{value / scale:.1f}".rstrip("0").rstrip(".") + suffix
    if abs(value) >= 100.0:
        return f"{value:.0f}"
    if abs(value) >= 1.0:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    if value == 0.0:
        return "0"
    if abs(value) >= 0.01:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.1e}"


def draw_metric(axis, samples, metric: str, ylabel: str, log_scale: bool,
                summary_rows: list[dict[str, str]], dataset_label: str) -> None:
    for algorithm, label, color, marker in ALGORITHMS:
        intervals = []
        for rho in RHO_VALUES:
            seeds = tuple(
                seed for seed in ORDER_SEEDS
                if (algorithm, rho, seed, metric) in samples
            )
            values = [samples[(algorithm, rho, seed, metric)] for seed in seeds]
            interval = mean_interval(values)
            intervals.append(interval)
            summary_rows.append({
                "dataset": dataset_label,
                "metric": metric,
                "algorithm": algorithm,
                "budget_fraction": f"{rho:.2f}",
                "n": str(len(seeds)),
                "mean": f"{interval[0]:.9f}",
                "sample_sd": f"{interval[3]:.9f}",
                "ci95_lower": f"{interval[1]:.9f}",
                "ci95_upper": f"{interval[2]:.9f}",
            })
        means = [entry[0] for entry in intervals]
        lowers = [entry[1] for entry in intervals]
        uppers = [entry[2] for entry in intervals]
        if log_scale:
            lowers = [max(1.0e-12, value) for value in lowers]
            uppers = [max(1.0e-12, value) for value in uppers]
        axis.plot(RHO_VALUES, means, color=color, marker=marker, markerfacecolor="none",
                  markeredgewidth=PANEL_MARKER_EDGE_WIDTH,
                  markersize=PANEL_MARKER_SIZE, linewidth=PANEL_LINE_WIDTH, label=label)
        axis.fill_between(RHO_VALUES, lowers, uppers, color=color, alpha=0.10)

    axis.set_xlabel(r"Budget fraction $\rho=B/g_D(V)$", fontsize=PANEL_XLABEL_SIZE, labelpad=7)
    axis.set_ylabel(ylabel, fontsize=PANEL_YLABEL_SIZE, labelpad=9,
                    y=0.46 if metric == "chance_cost_over_B" else 0.5)
    axis.set_xticks(RHO_VALUES)
    axis.set_xticklabels([f"{value:.2f}" for value in RHO_VALUES])
    axis.tick_params(axis="both", labelsize=PANEL_TICK_SIZE, pad=4)
    axis.grid(True, which="major", alpha=0.28)
    if log_scale:
        axis.set_yscale("log")
        axis.yaxis.set_major_locator(LogLocator(base=10.0, subs=(1.0,), numticks=7))
        axis.yaxis.set_major_formatter(FuncFormatter(compact_number))
        axis.yaxis.set_minor_locator(NullLocator())
    else:
        axis.yaxis.set_major_locator(MaxNLocator(nbins=6))
        axis.yaxis.set_major_formatter(FuncFormatter(compact_number))
    axis.margins(x=0.04, y=0.10)


def legend_figure() -> plt.Figure:
    figure = plt.figure(figsize=(11.0, 1.15))
    handles = [
        Line2D([0], [0], color=color, marker=marker, markerfacecolor="none",
               markeredgewidth=PANEL_MARKER_EDGE_WIDTH, markersize=LEGEND_MARKER_SIZE,
               linewidth=PANEL_LINE_WIDTH, label=label)
        for _, label, color, marker in ALGORITHMS
    ]
    figure.legend(handles=handles, loc="center", ncol=4, frameon=False,
                  fontsize=LEGEND_FONT_SIZE, handlelength=2.1, columnspacing=2.0)
    return figure


def metric_figure(samples, metric, summary_rows: list[dict[str, str]], dataset_label: str) -> plt.Figure:
    _metric_name, metric_column, ylabel, log_scale = metric
    figure, axis = plt.subplots(1, 1, figsize=(11.0, 7.0))
    draw_metric(axis, samples, metric_column, ylabel, log_scale, summary_rows, dataset_label)
    figure.tight_layout(rect=(0.16, 0.16, 0.99, 0.98))
    return figure


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-glob", required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--dataset-label", default="ca-GrQc")
    parser.add_argument(
        "--dataset",
        default=None,
        help="dataset block to select when a log contains multiple datasets",
    )
    parser.add_argument("--output-prefix", default="grqc")
    parser.add_argument(
        "--allow-incomplete-offline",
        action="store_true",
        help="allow fewer Offline-Greedy repetitions while requiring all streaming samples",
    )
    args = parser.parse_args()
    paths = [Path(item) for item in sorted(glob.glob(args.log_glob))]
    if not paths:
        raise SystemExit(f"no logs matched: {args.log_glob}")
    samples, graph_info = parse_logs(paths, dataset_filter=args.dataset)
    validate(samples, allow_incomplete_offline=args.allow_incomplete_offline)
    args.outdir.mkdir(parents=True, exist_ok=True)

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.size"] = 10
    summary_rows: list[dict[str, str]] = []
    pdf_path = args.outdir / f"{args.output_prefix}_maxcut.pdf"
    with PdfPages(pdf_path) as pdf:
        legend = legend_figure()
        pdf.savefig(legend, bbox_inches="tight", transparent=True)
        plt.close(legend)
        for metric in METRICS:
            figure = metric_figure(samples, metric, summary_rows, args.dataset_label)
            pdf.savefig(figure, bbox_inches="tight", transparent=True)
            plt.close(figure)

    summary_rows.sort(key=lambda row: (row["metric"], row["algorithm"], float(row["budget_fraction"])))
    summary_path = args.outdir / f"{args.output_prefix}_maxcut_plot_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["dataset", "metric", "algorithm", "budget_fraction", "n", "mean",
                  "sample_sd", "ci95_lower", "ci95_upper"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)

    manifest = {
        "application": "Unit-weight Max-Cut under Gaussian chance constraint",
        "dataset": args.dataset_label,
        "graph_nodes": graph_info.get("n"),
        "graph_edges": graph_info.get("edges"),
        "algorithms": [algorithm for algorithm, *_ in ALGORITHMS],
        "budget_fractions": list(RHO_VALUES),
        "repetitions": len(ORDER_SEEDS),
        "order_seeds": list(ORDER_SEEDS),
        "resource_seed": 42,
        "sigma_ratio": 0.5,
        "alpha": 0.05,
        "epsilon": graph_info.get("epsilon"),
        "delta": graph_info.get("delta"),
        "gamma": graph_info.get("gamma"),
        "configuration": graph_info.get("configuration"),
        "metrics": [metric for metric, *_ in METRICS],
        "ci": "two-sided 95% Student-t interval over ten independent stream orders",
        "source_logs": [str(path) for path in paths],
        "style_source": "native plotting pipeline for the independent P0 experiment package",
        "pdf_layout": "legend page followed by one page per metric",
        "legend_columns": 4,
        "legend_font_size": LEGEND_FONT_SIZE,
        "panel_font_sizes": {
            "x_label": PANEL_XLABEL_SIZE,
            "y_label": PANEL_YLABEL_SIZE,
            "ticks": PANEL_TICK_SIZE,
        },
        "panel_marker_size": PANEL_MARKER_SIZE,
        "allow_incomplete_offline": args.allow_incomplete_offline,
    }
    manifest_path = args.outdir / f"{args.output_prefix}_maxcut_plot_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Created {pdf_path}")
    print(f"Created {summary_path}")
    print(f"Created {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
