"""Pure descriptive analysis of one observed dataset; no I/O or imputation."""

import math
import statistics
from collections import Counter

from ascendant import config


def metric(item, key):
    raw = item.get("stat", {})
    value = raw.get(key) if isinstance(raw, dict) else None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else None


def has_complete_metrics(item):
    return all(metric(item, key) is not None for _, _, key in config.VIDEO_STAT_FIELDS)


def percentile(ordered, fraction):
    """Linear interpolation at (n - 1) * fraction, including singleton samples."""
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values):
    values = sorted(values)
    q1, median, q3 = (percentile(values, p) for p in (.25, .5, .75))
    result = {"count": len(values), "mean": statistics.mean(values) if values else None,
              "median": median, "q1": q1, "q3": q3, "p90": percentile(values, .9),
              "min": values[0] if values else None, "max": values[-1] if values else None,
              "iqr": q3 - q1 if values else None, "bins": [], "lower_fence": None,
              "upper_fence": None, "whisker_low": None, "whisker_high": None}
    if not values:
        return result
    # Four observations are a minimal descriptive sample, not a significance test.
    if len(values) >= 4:
        low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        result.update(lower_fence=low, upper_fence=high)
        inside = [v for v in values if low <= v <= high]
        result.update(whisker_low=min(inside), whisker_high=max(inside))
    else:
        result.update(whisker_low=values[0], whisker_high=values[-1])
    count = min(20, math.ceil(math.sqrt(len(values)))) if values[0] != values[-1] else 1
    width = (values[-1] - values[0]) / count
    bins = [{"low": values[0] + width * i, "high": values[0] + width * (i + 1), "count": 0} for i in range(count)]
    for value in values:
        index = min(count - 1, int((value - values[0]) / width)) if width else 0
        bins[index]["count"] += 1
    result["bins"] = bins
    return result


def analyse_dataset(items, *, low_view_threshold=100):
    identities = [str(item.get("bvid") or item.get("aid")) for item in items if item.get("bvid") or item.get("aid")]
    counts = Counter(identities)
    summaries, outliers = [], []
    for field, label, key in config.VIDEO_STAT_FIELDS:
        values = [v for item in items if (v := metric(item, key)) is not None]
        summary = {"field": field, "label": label, "missing": len(items) - len(values), **distribution(values)}
        summaries.append(summary)
        if summary["lower_fence"] is not None:
            for item in items:
                value = metric(item, key)
                if value is not None and (value < summary["lower_fence"] or value > summary["upper_fence"]):
                    outliers.append({"title": item.get("title") or "(no title)", "bvid": item.get("bvid"),
                                     "metric": label, "value": value,
                                     "reason": f"Outside 1.5 × IQR fences [{summary['lower_fence']:g}, {summary['upper_fence']:g}]"})
    engagement, rows = [], []
    for item in items:
        views = metric(item, "view")
        row = {"title": item.get("title") or "(no title)", "bvid": item.get("bvid"), "views": views,
               "low_views": views is not None and views < low_view_threshold}
        for field, key in (("likes", "like"), ("favorites", "favorite"), ("coins", "coin"), ("shares", "share")):
            value = metric(item, key)
            row[field] = value / views if value is not None and views is not None and views > 0 else None
        rows.append(row)
    for field, key in (("likes", "like"), ("favorites", "favorite"), ("coins", "coin"), ("shares", "share")):
        pairs = [(metric(item, key), metric(item, "view")) for item in items]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None and b > 0]
        engagement.append({"field": field, "count": len(pairs), "excluded": len(items) - len(pairs),
                           "pooled": sum(a for a, b in pairs) / sum(b for a, b in pairs) if pairs else None,
                           "median": statistics.median(a / b for a, b in pairs) if pairs else None})
    return {"summaries": summaries, "outliers": outliers, "engagement": engagement, "engagement_rows": rows,
            "quality": {"rows": len(items), "duplicate_rows": sum(n - 1 for n in counts.values()),
                        "missing_identity": len(items) - len(identities)}, "low_view_threshold": low_view_threshold}
