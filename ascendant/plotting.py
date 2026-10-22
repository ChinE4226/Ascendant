"""Headless plot preparation and PNG rendering."""

from datetime import datetime
import math
import os
from pathlib import Path
import re
from typing import Any

from ascendant import config
from ascendant.selection import (
    ordered_by_published_time,
    video_published_timestamp,
)

# Matplotlib's cache paths and headless backend must be set before pyplot loads.
os.environ.setdefault("MPLCONFIGDIR", str(config.RUNTIME_DIR / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(config.RUNTIME_DIR / "cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import dates as mdates
from matplotlib import ticker


def nice_y_axis(values: list[float]) -> tuple[float, float]:
    """Zero-based upper bound and a round 1/2/5 tick interval."""
    peak = max((value for value in values if math.isfinite(value) and value >= 0), default=0)
    target = peak * 1.08 if peak else 1.0
    raw_step = target / 5
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(factor * magnitude for factor in (1, 2, 5, 10) if factor * magnitude >= raw_step)
    return math.ceil(target / step) * step, step


def configure_y_axis(axis: Any, values: list[float]) -> None:
    upper, step = nice_y_axis(values)
    axis.set_ylim(0, upper)
    axis.yaxis.set_major_locator(ticker.MultipleLocator(step))
    axis.yaxis.set_major_formatter(ticker.FuncFormatter(
        lambda value, position: f"{value:,.12f}".rstrip("0").rstrip(".")
    ))


def index_y_axis(values: list[float]) -> tuple[float, float, float]:
    """Fit index lines to their values, keeping the median baseline visible."""
    finite = [100.0] + [value for value in values if math.isfinite(value)]
    low, high = min(finite), max(finite)
    if low == high:
        low, high = low - 1, high + 1
    padding = (high - low) * .1
    raw_step = (high - low + 2 * padding) / 5
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(factor * magnitude for factor in (1, 2, 5, 10) if factor * magnitude >= raw_step)
    return math.floor((low - padding) / step) * step, math.ceil((high + padding) / step) * step, step


def plot_file_path(selected_creator: dict[str, str], plot_label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_creator_name = re.sub(r"[^\w.-]+", "_", selected_creator.get("name", ""), flags=re.UNICODE).strip("_")[:40]
    creator_part = f"{safe_creator_name}_{selected_creator['uid']}" if safe_creator_name else selected_creator["uid"]
    safe_label = re.sub(r"[^\w.-]+", "_", plot_label, flags=re.UNICODE).strip("_")[:80] or "plot"
    return config.PLOTS_DIR / f"{creator_part}_{timestamp}_{safe_label}.png"


def build_plot_points(
    items: list[dict[str, Any]],
    value_getter: Any,
) -> tuple[list[datetime], list[float], int]:
    x_values: list[datetime] = []
    y_values: list[float] = []
    skipped = 0

    for item in ordered_by_published_time(items):
        published_time = video_published_timestamp(item)
        value = value_getter(item)
        if published_time is None or value is None:
            skipped += 1
            continue
        x_values.append(datetime.fromtimestamp(published_time))
        y_values.append(float(value))

    return x_values, y_values, skipped


def save_line_plot(
    *,
    selected_creator: dict[str, str],
    selection_label: str,
    plot_label: str,
    y_label: str,
    x_values: list[datetime],
    y_values: list[float],
) -> Path:
    config.PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = plot_file_path(selected_creator, plot_label)

    figure, axis = plt.subplots(figsize=(13, 7))
    axis.plot(x_values, y_values, marker="o", linewidth=2.0, markersize=4.5)
    axis.set_title(f"{selected_creator['name']} - {plot_label}")
    axis.set_xlabel("Published time")
    axis.set_ylabel(y_label)
    configure_y_axis(axis, y_values)
    axis.margins(x=0.03)
    axis.grid(True, linewidth=0.5, alpha=0.5)
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))
    axis.text(
        0.01,
        0.99,
        selection_label,
        transform=axis.transAxes,
        va="top",
        ha="left",
        fontsize=8,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.75, "edgecolor": "#cccccc"},
    )
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(output_path, dpi=160)
    plt.close(figure)
    return output_path
