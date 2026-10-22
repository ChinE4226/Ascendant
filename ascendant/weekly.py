"""Descriptive averages for one weekly list; no I/O or missing-value imputation."""

import statistics

from ascendant import config
from ascendant.distributions import distribution, has_complete_metrics, metric


def summarize_weekly_items(items):
    valid, excluded, seen = [], [], set()
    duplicates = invalid = 0
    for item in items:
        identity = str(item.get("bvid") or item.get("aid") or "")
        reason = None
        if identity and identity in seen:
            duplicates += 1
            reason = "Duplicate video"
        else:
            if identity:
                seen.add(identity)
            if not identity or not has_complete_metrics(item):
                invalid += 1
                reason = "Missing identity or invalid metric data"
        if reason:
            excluded.append({"title": item.get("title") or "(no title)", "bvid": item.get("bvid"), "reason": reason})
        else:
            valid.append(item)

    summaries, engagement = [], []
    for field, label, key in config.VIDEO_STAT_FIELDS:
        values = [metric(item, key) for item in valid]
        summaries.append({"field": field, "label": label, "total": sum(values), **distribution(values)})
        if key == "view":
            continue
        pairs = [(metric(item, key), metric(item, "view")) for item in valid if metric(item, "view") > 0]
        ratios = [value / views for value, views in pairs]
        engagement.append({"field": field, "label": label, "count": len(pairs), "excluded": len(valid) - len(pairs),
                           "mean_per_video": statistics.mean(ratios) if ratios else None,
                           "median_per_video": statistics.median(ratios) if ratios else None,
                           "pooled": sum(value for value, _ in pairs) / sum(views for _, views in pairs) if pairs else None})
    creators = {str(owner.get("mid")) for item in valid
                if isinstance(owner := item.get("owner"), dict) and owner.get("mid") is not None}
    return valid, {"summaries": summaries, "engagement": engagement, "excluded": excluded,
                   "counts": {"listed": len(items), "included": len(valid), "invalid": invalid,
                              "duplicates": duplicates, "creators": len(creators)}}
