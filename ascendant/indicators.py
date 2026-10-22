"""Trailing indicators over valid plotted values, without file or network access."""
from statistics import median
import math


def _validate_period(period: int) -> None:
    if type(period) is not int or period < 1:
        raise ValueError("Use a positive integer window.")


def moving_average(values: list[float], period: int) -> list[float | None]:
    _validate_period(period)
    result = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= period:
            total -= values[index - period]
        result.append(None if index + 1 < period else total / period)
    return result


def exponential_average(values: list[float], period: int) -> list[float | None]:
    _validate_period(period)
    if len(values) < period:
        return [None] * len(values)
    previous = sum(values[:period]) / period
    result = [None] * (period - 1) + [previous]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        previous = alpha * value + (1 - alpha) * previous
        result.append(previous)
    return result


def rolling_median(values: list[float], period: int) -> list[float | None]:
    _validate_period(period)
    return [None if index < period - 1 else median(values[index + 1 - period:index + 1])
            for index in range(len(values))]


def relative_performance(values: list[float], period: int = 20) -> list[float | None]:
    baseline = moving_average(values, period)
    result = []
    for index, value in enumerate(values):
        ratio = value / baseline[index - 1] if index >= period and baseline[index - 1] > 0 else None
        result.append(ratio if ratio is not None and math.isfinite(ratio) else None)
    return result


def performance_index(values: list[float]) -> dict:
    """Median-centred log index over a fixed cohort, including valid zeros."""
    if not values:
        return {"baseline": None, "values": []}
    # Average halves to avoid overflowing on an even-sized, very large cohort.
    ordered = sorted(values)
    middle = len(ordered) // 2
    baseline = ordered[middle] if len(ordered) % 2 else ordered[middle - 1] / 2 + ordered[middle] / 2
    center = math.log1p(baseline)
    return {"baseline": baseline,
            "values": [100 + 25 * (math.log1p(value) - center) / math.log(2) for value in values]}


def unusual_scores(values: list[float], period: int = 20) -> list[float | None]:
    """Modified z-scores in log space against prior observations only."""
    _validate_period(period)
    logs = [math.log1p(value) for value in values]
    result = []
    for index, value in enumerate(logs):
        if index < period:
            result.append(None)
            continue
        history = logs[index - period:index]
        center = median(history)
        mad = median([abs(previous - center) for previous in history])
        score = 0.6745 * (value - center) / mad if mad > 0 else None
        result.append(score if score is not None and math.isfinite(score) else None)
    return result
