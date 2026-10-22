"""Validate filters and sample uniformly from a bounded, eligible video pool."""

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import random
import secrets
from zoneinfo import ZoneInfo

from ascendant.distributions import has_complete_metrics, metric
from ascendant.web.serializers import field_by_name, video_timestamp
from ascendant.weekly import summarize_weekly_items


def parse_sample_options(payload):
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword or len(keyword) > 100:
        raise ValueError("Enter a search keyword of 1–100 characters.")

    def integer(key, default):
        value = str(payload.get(key, default)).strip()
        if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 500:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be a whole number from 1 to 500.")
        return int(value)

    size, pool_size = integer("sample_size", 100), integer("pool_size", 200)
    if size > pool_size:
        raise ValueError("Candidate limit must be at least the sample size.")
    field = field_by_name(str(payload.get("metric") or "views"))
    if field is None:
        raise ValueError("Choose a valid metric filter.")

    def boundary(key):
        value = str(payload.get(key) if payload.get(key) is not None else "").strip().lower().replace(",", "")
        if not value:
            return None
        factor = 1000 if value.endswith("k") else 1000000 if value.endswith("m") else 1
        try:
            number = Decimal(value[:-1] if factor != 1 else value) * factor
        except InvalidOperation:
            raise ValueError("Metric boundaries must be nonnegative numbers; k and m suffixes are supported.") from None
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError("Metric boundaries must be nonnegative whole numbers.")
        return int(number)

    minimum, maximum = boundary("minimum"), boundary("maximum")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Minimum must be less than or equal to maximum.")
    dates = {}
    for key in ("published_start", "published_end"):
        value = str(payload.get(key) or "").strip()
        try:
            dates[key] = datetime.strptime(value, "%Y-%m-%d").date().isoformat() if value else None
        except ValueError:
            raise ValueError("Publication dates must use YYYY-MM-DD.") from None
    if dates["published_start"] and dates["published_end"] and dates["published_start"] > dates["published_end"]:
        raise ValueError("Publication start must be before or equal to the end.")
    order = str(payload.get("order") or "pubdate")
    if order not in {"pubdate", "totalrank", "click"}:
        raise ValueError("Choose a valid candidate order.")
    seed = str(payload.get("seed") or "").strip() or str(secrets.randbits(64))
    if len(seed) > 128:
        raise ValueError("Random seed must be at most 128 characters.")
    category = str(payload.get("category_id") or "").strip()
    if category and (not category.isascii() or not category.isdigit() or not 1 <= int(category) <= 10000):
        raise ValueError("Category ID must be a positive whole number, or blank for all categories.")
    return {"keyword": keyword, "sample_size": size, "pool_size": pool_size, "metric": field["field"],
            "category_id": int(category) if category else None,
            "minimum": minimum, "maximum": maximum, "order": order, "seed": seed, **dates}


def sample_candidates(items, options):
    """Filter refreshed records first, then draw without replacement from all eligible rows."""
    def timestamp(date, end=False):
        if not date:
            return None
        day = datetime.strptime(date, "%Y-%m-%d").date() + timedelta(days=int(end))
        return int(datetime.combine(day, time.min, ZoneInfo("Asia/Shanghai")).timestamp())

    start, end = timestamp(options["published_start"]), timestamp(options["published_end"], True)
    key = field_by_name(options["metric"])["stat_key"]
    eligible, excluded, seen = [], [], set()
    counts = {"candidates": len(items), "invalid": 0, "duplicates": 0, "filtered_out": 0}
    for item in items:
        identity = str(item.get("bvid") or item.get("aid") or "")
        reason = None
        if identity and identity in seen:
            counts["duplicates"] += 1
            reason = "Duplicate video"
        else:
            if identity:
                seen.add(identity)
            if not identity or not has_complete_metrics(item):
                counts["invalid"] += 1
                reason = "Missing identity or invalid metric data"
            else:
                published = video_timestamp(item)
                value = metric(item, key)
                if ((start is not None or end is not None) and published is None
                        or start is not None and published < start
                        or end is not None and published >= end
                        or options["minimum"] is not None and value < options["minimum"]
                        or options["maximum"] is not None and value > options["maximum"]):
                    counts["filtered_out"] += 1
                    reason = "Outside the publication or metric filter"
        if reason:
            excluded.append({"title": item.get("title") or "(no title)", "bvid": item.get("bvid"), "reason": reason})
        else:
            eligible.append(item)
    sampled = random.Random(options["seed"]).sample(eligible, min(options["sample_size"], len(eligible)))
    _, report = summarize_weekly_items(sampled)
    report["excluded"] = excluded
    report["sampling"] = {**options, **counts, "eligible": len(eligible), "sampled": len(sampled),
                          "shortfall": max(0, options["sample_size"] - len(sampled))}
    return sampled, report
