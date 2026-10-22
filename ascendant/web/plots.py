"""Browser plot rendering and saved plot listings."""

from __future__ import annotations

from datetime import datetime
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import re
import math
import threading
from typing import Any
from uuid import uuid4

from ascendant import config, plotting
from ascendant.indicators import moving_average, exponential_average, rolling_median, relative_performance, performance_index, unusual_scores
from ascendant.plotting import mdates


# Unsaved snapshots are bounded and never touch disk. The lock also serializes
# matplotlib exports, which are not safe to run on concurrent request threads.
PENDING_PLOT_LIMIT = 20
_pending_plots: OrderedDict[str, dict[str, Any]] = OrderedDict()
_plot_lock = threading.Lock()


def prepare_plot(
    selected: dict[str, str], selection_label: str, plot_label: str,
    y_label: str, points: list[dict[str, Any]],
    *, collection_id: str | None = None,
) -> str | None:
    if not points:
        return None
    plot_id = uuid4().hex
    snapshot = deepcopy({"selected": selected, "collection_id": collection_id, "selection_label": selection_label,
                         "plot_label": plot_label, "y_label": y_label, "points": points})
    with _plot_lock:
        _pending_plots[plot_id] = snapshot
        while len(_pending_plots) > PENDING_PLOT_LIMIT:
            _pending_plots.popitem(last=False)
    return plot_id


def discard_collection_plots(identity):
    """Drop derived RAM exports for a released collection; saved PNGs stay on disk."""
    with _plot_lock:
        for plot_id in [key for key, entry in _pending_plots.items() if entry.get('collection_id') == identity]:
            del _pending_plots[plot_id]


def normalize_ma_periods(periods: Any) -> tuple[int, ...]:
    if periods is None:
        return ()
    if not isinstance(periods, (list, tuple)) or any(type(period) is not int or period not in (5, 10, 20) for period in periods):
        raise ValueError("Choose moving-average windows of 5, 10, or 20 videos.")
    return tuple(sorted(set(periods)))


INDICATORS = {
    "ema10": ("EMA10", 10, exponential_average, "#3669ae", "-"),
    "ema20": ("EMA20", 20, exponential_average, "#447b8b", "--"),
    "median5": ("Median5", 5, rolling_median, "#a65a37", "--"),
    "median10": ("Median10", 10, rolling_median, "#6b607d", ":"),
}


def normalize_indicators(indicators: Any) -> tuple[str, ...]:
    if indicators is None:
        return ()
    if not isinstance(indicators, (list, tuple)) or any(not isinstance(item, str) or item not in (*INDICATORS, "relative20") for item in indicators):
        raise ValueError("Choose EMA10, EMA20, Median5, Median10, or Relative20.")
    return tuple(sorted(set(indicators)))


def validate_display(value_mode, show_anomalies, chart_style):
    if value_mode not in ("raw", "log", "index"):
        raise ValueError("Choose raw values, log scale, or performance index.")
    if type(show_anomalies) is not bool:
        raise ValueError("Unusual-value markers must be on or off.")
    if chart_style not in ("line", "bar"):
        raise ValueError("Choose a line or bar chart.")


def save_prepared_plot(plot_id: Any, axis_mode: str = "time", ma_periods: Any = None, indicators: Any = None,
                       value_mode: str = "raw", show_anomalies: bool = False, chart_style: str = "line") -> dict[str, str]:
    if axis_mode not in ("time", "number"):
        raise ValueError("Choose a published-time or video-number axis.")
    periods = normalize_ma_periods(ma_periods)
    chosen = normalize_indicators(indicators)
    validate_display(value_mode, show_anomalies, chart_style)
    cache_key = (axis_mode, periods, chosen, value_mode, show_anomalies, chart_style)
    with _plot_lock:
        snapshot = _pending_plots.get(plot_id) if isinstance(plot_id, str) else None
        if snapshot is None:
            raise ValueError("This plot has expired. Generate it again before saving.")
        files = snapshot.setdefault("files", {})
        name = files.get(cache_key)
        if not name or not (config.PLOTS_DIR / name).is_file():
            name = save_web_plot_png(**{key: value for key, value in snapshot.items() if key not in {"files", "collection_id"}}, axis_mode=axis_mode, ma_periods=periods, indicators=chosen,
                                     value_mode=value_mode, show_anomalies=show_anomalies, chart_style=chart_style)
            files[cache_key] = name
        return {"name": name, "url": f"/plots/{name}"}


def plot_entries() -> list[dict[str, Any]]:
    if not config.PLOTS_DIR.exists():
        return []

    entries = []
    for path in sorted(config.PLOTS_DIR.glob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        entries.append(
            {
                "name": path.name,
                "url": f"/plots/{path.name}",
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
            }
        )
    return entries


def plot_file_path(selected: dict[str, str], plot_label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_creator_name = re.sub(r"[^\w.-]+", "_", selected.get("name", ""), flags=re.UNICODE).strip("_")[:40]
    creator_part = f"{safe_creator_name}_{selected['uid']}" if safe_creator_name else selected["uid"]
    safe_label = re.sub(r"[^\w.-]+", "_", plot_label, flags=re.UNICODE).strip("_")[:80] or "plot"
    return config.PLOTS_DIR / f"{creator_part}_{timestamp}_{safe_label}.png"


def save_web_plot_png(
    selected: dict[str, str],
    selection_label: str,
    plot_label: str,
    y_label: str,
    points: list[dict[str, Any]],
    axis_mode: str = "time",
    ma_periods: Any = None,
    indicators: Any = None,
    value_mode: str = "raw",
    show_anomalies: bool = False,
    chart_style: str = "line",
) -> str | None:
    if not points:
        return None

    validate_display(value_mode, show_anomalies, chart_style)
    periods = normalize_ma_periods(ma_periods)
    chosen = normalize_indicators(indicators)
    points = [point for point in points if type(point["value"]) in (int, float) and math.isfinite(point["value"]) and point["value"] >= 0]
    if not points:
        return None
    # Match the browser: order by publication date when every label is dated.
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(point["label"])) for point in points):
        points = sorted(points, key=lambda point: str(point["label"]))
    config.PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = plot_file_path(selected, plot_label)
    parsed_dates = [
        datetime.strptime(str(point["label"]), "%Y-%m-%d %H:%M:%S")
        for point in points
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(point["label"]))
    ]
    use_time = axis_mode == "time" and len(parsed_dates) == len(points)
    x_values = parsed_dates if use_time else list(range(1, len(points) + 1))
    raw_values = [float(point["value"]) for point in points]
    indexed = performance_index(raw_values)
    transformed = lambda value: None if value is None else math.log1p(value) / math.log(10) if value_mode == "log" else value
    trend_values = indexed["values"] if value_mode == "index" else raw_values
    y_values = [transformed(value) for value in trend_values]
    display_label = "Performance index" if value_mode == "index" else "log10(value + 1)" if value_mode == "log" else y_label
    dated = len(parsed_dates) == len(points)
    scores = unusual_scores(raw_values) if dated else [None] * len(points)

    relative_axis = None
    if "relative20" in chosen:
        figure, (axis, relative_axis) = plotting.plt.subplots(2, 1, figsize=(13, 10), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    else:
        figure, axis = plotting.plt.subplots(figsize=(13, 7))
    if chart_style == "bar":
        axis.bar(x_values, y_values, color="#596b88", label=display_label)
    else:
        axis.plot(x_values, y_values, marker="o", linewidth=2.0, markersize=4.5, color="#596b88", label=display_label)
    if value_mode == "index":
        axis.axhline(100, color="#8b94a3", linestyle="--", linewidth=1)
    if show_anomalies:
        flagged = [index for index, score in enumerate(scores) if score is not None and abs(score) > 3.5]
        if flagged:
            axis.scatter([x_values[index] for index in flagged], [y_values[index] for index in flagged],
                         marker="D", color="#b47732", edgecolors="white", zorder=5, label="Potential outlier (|score| > 3.5)")
    ma_styles = {5: ("#b47732", "-"), 10: ("#8363a5", "--"), 20: ("#b55d70", ":")}
    for period in periods:
        if len(y_values) >= period:
            color, style = ma_styles[period]
            axis.plot(x_values, [transformed(value) for value in moving_average(trend_values, period)], color=color, linestyle=style, linewidth=2, label=f"MA{period}")
    for indicator in chosen:
        if indicator in INDICATORS:
            label, period, compute, color, style = INDICATORS[indicator]
            if len(y_values) >= period:
                axis.plot(x_values, [transformed(value) for value in compute(trend_values, period)], color=color, linestyle=style, linewidth=2, label=label)
    if relative_axis is not None:
        ratios = relative_performance(raw_values)
        relative_axis.plot(x_values, ratios, color="#53758c", linewidth=2, label="Relative20")
        relative_axis.axhline(1, color="#8b94a3", linestyle="--", linewidth=1)
        relative_axis.set_ylabel("Relative20 (×)")
        plotting.configure_y_axis(relative_axis, [1] + [value for value in ratios if value is not None])
        relative_axis.grid(True, linewidth=0.5, alpha=0.5)
        if not any(value is not None for value in ratios):
            relative_axis.text(0.5, 0.5, "Needs 20 previous videos with a positive mean", ha="center", transform=relative_axis.transAxes)
    has_legend = bool(periods or chosen or value_mode != "raw" or show_anomalies and any(score is not None and abs(score) > 3.5 for score in scores))
    if has_legend:
        handles, labels = axis.get_legend_handles_labels()
        if relative_axis is not None:
            handles += [relative_axis.lines[0]]
            labels += ["Relative20"]
        figure.legend(handles, labels, loc="lower center", ncol=3, frameon=False)
    axis.set_title(f"{selected['name']} (UID {selected['uid']}) - {plot_label}")
    (relative_axis if relative_axis is not None else axis).set_xlabel("Published time" if use_time else "Video number (oldest → newest)" if parsed_dates else "Video number")
    axis.set_ylabel(display_label)
    scale_values = list(y_values)
    for line in axis.lines:
        scale_values.extend(value for value in line.get_ydata() if value is not None and math.isfinite(value))
    plotting.configure_y_axis(axis, scale_values + ([100] if value_mode == "index" else []))
    if value_mode == "index" and chart_style == "line":
        low, upper, step = plotting.index_y_axis(scale_values)
        axis.set_ylim(low, upper)
        axis.yaxis.set_major_locator(plotting.ticker.MultipleLocator(step))
    elif value_mode == "index" and min(scale_values) < 0:
        upper, step = plotting.nice_y_axis(scale_values + [100])
        low = min(scale_values)
        axis.set_ylim(math.floor((low - abs(low) * .08) / step) * step, upper)
    axis.margins(x=0.03)
    axis.grid(True, linewidth=0.5, alpha=0.5)
    if x_values and isinstance(x_values[0], datetime):
        axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))
    else:
        step = max(1, len(points) // 8)
        ticks = x_values[::step]
        axis.set_xticks(ticks)
    context = selection_label
    if value_mode == "index":
        context += f" · Index 100 = dataset median {indexed['baseline']:g} · +25 per doubling (with +1 offset)"
    elif value_mode == "log":
        context += " · log10(value + 1)"
    # Keep context and line notation outside the plotted area.
    figure.text(0.5, 0.98, context, ha="center", va="top", fontsize=8, wrap=True)
    if use_time:
        figure.autofmt_xdate()
    figure.tight_layout(rect=(0, 0.03 + 0.035 * math.ceil((len(periods) + len(chosen) + 1) / 3) if has_legend else 0, 1, .95))
    try:
        figure.savefig(output_path, dpi=160)
    finally:
        plotting.plt.close(figure)
    return output_path.name
