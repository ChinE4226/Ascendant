"""Metric summaries and division calculations."""

import statistics
from typing import Any

from ascendant import config


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def video_metric_value(item: dict[str, Any], stat_key: str) -> int | None:
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    return int_or_none(stat.get(stat_key))


def calculate_metric_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for _, label, stat_key in config.VIDEO_STAT_FIELDS:
        values = [
            value
            for item in items
            if stat_key is not None
            if (value := video_metric_value(item, stat_key)) is not None
        ]
        summaries.append(
            {
                "label": label,
                "count": len(values),
                "mean": statistics.mean(values) if values else None,
                "median": statistics.median(values) if values else None,
            }
        )
    return summaries


def division_field_choices() -> list[dict[str, str]]:
    choices = [
        {
            "field": field_key,
            "label": label,
            "source": "video_stat",
            "stat_key": stat_key,
        }
        for field_key, label, stat_key in config.VIDEO_STAT_FIELDS
    ]
    choices.append(
        {
            "field": "followers",
            "label": "Followers",
            "source": "creator_relation",
            "relation_key": "follower",
        }
    )
    return choices


def division_needs_creator_relation(*fields: dict[str, str]) -> bool:
    return any(field.get("source") == "creator_relation" for field in fields)


def division_field_value(
    item: dict[str, Any],
    field: dict[str, str],
    creator_relation: dict[str, Any] | None = None,
) -> int | None:
    if field.get("source") == "creator_relation":
        return int_or_none((creator_relation or {}).get(field["relation_key"]))
    return video_metric_value(item, field["stat_key"])


def ratio_for_item(
    item: dict[str, Any],
    numerator_field: dict[str, str],
    denominator_field: dict[str, str],
    creator_relation: dict[str, Any] | None = None,
) -> tuple[int | None, int | None, float | None]:
    numerator = division_field_value(item, numerator_field, creator_relation)
    denominator = division_field_value(item, denominator_field, creator_relation)
    if numerator is None or denominator in (None, 0):
        return numerator, denominator, None
    return numerator, denominator, numerator / denominator


def aggregate_division_value(
    items: list[dict[str, Any]],
    field: dict[str, str],
    creator_relation: dict[str, Any] | None = None,
) -> dict[str, int | None]:
    if field.get("source") == "creator_relation":
        value = division_field_value({}, field, creator_relation)
        selected_count = len(items)
        return {
            "value": None if value is None else value * selected_count,
            "count": selected_count if value is not None else 0,
            "missing": 0 if value is not None else selected_count,
            "base_value": value,
        }

    total = 0
    count = 0
    missing = 0
    for item in items:
        value = division_field_value(item, field, creator_relation)
        if value is None:
            missing += 1
            continue
        total += value
        count += 1

    return {"value": total, "count": count, "missing": missing}


def aggregate_division_label(field: dict[str, str], selected_count: int | None = None) -> str:
    if field.get("source") == "creator_relation":
        if selected_count is None:
            return field["label"]
        return f"{field['label']} x {selected_count} selected video(s)"
    return f"Total {field['label']}"
