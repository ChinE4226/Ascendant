"""Video ordering, range parsing, and filtering."""

from datetime import datetime
import re
from typing import Any

from ascendant import config
from ascendant.analysis import (
    int_or_none,
    video_metric_value,
)


def video_sort_mode(field_key: str, field_label: str, stat_key: str | None, descending: bool) -> dict[str, Any]:
    direction_label = (
        "latest to oldest"
        if field_key == "published_time" and descending
        else "oldest to latest"
        if field_key == "published_time"
        else "largest to smallest"
        if descending
        else "smallest to largest"
    )
    return {
        "field": field_key,
        "label": f"{field_label} ({direction_label})",
        "stat_key": stat_key,
        "descending": descending,
    }


def video_selection_choices() -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = [
        {
            "kind": "published_time_range",
            "label": "Published time (latest to oldest)",
            "sort_mode": video_sort_mode("published_time", "Published time", None, True),
        },
        {
            "kind": "published_time_range",
            "label": "Published time (oldest to latest)",
            "sort_mode": video_sort_mode("published_time", "Published time", None, False),
        },
    ]

    for field_key, field_label, stat_key in config.VIDEO_STAT_FIELDS:
        choices.append(
            {
                "kind": "metric_range",
                "field": field_key,
                "label": f"{field_label} (smallest to largest)",
                "range_label": field_label,
                "stat_key": stat_key,
                "sort_mode": video_sort_mode(field_key, field_label, stat_key, False),
            }
        )

    choices.append(
        {
            "kind": "published_time_position_range",
            "label": "Published-time ordered number range (latest to oldest)",
        }
    )
    return choices


def parse_datetime_input(raw: str, *, end_of_day: bool = False) -> datetime | None:
    value = raw.strip()
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and end_of_day:
        return parsed.replace(hour=23, minute=59, second=59)
    return parsed


def parse_int_range_value(raw: str) -> int | None:
    value = raw.strip().lower().replace(",", "").replace(" ", "")
    if not value:
        return None
    multiplier = 1
    if value.endswith("k"):
        multiplier = 1_000
        value = value[:-1]
    elif value.endswith("m"):
        multiplier = 1_000_000
        value = value[:-1]

    try:
        number = float(value)
    except ValueError:
        return None
    if number < 0:
        return None
    return int(number * multiplier)


def video_published_timestamp(item: dict[str, Any]) -> int | None:
    return int_or_none(item.get("pubdate") or item.get("created"))


def video_sort_value(item: dict[str, Any], sort_mode: dict[str, Any]) -> int | None:
    if sort_mode["field"] == "published_time":
        return video_published_timestamp(item)
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    return int_or_none(stat.get(sort_mode["stat_key"]))


def sort_video_items(items: list[dict[str, Any]], sort_mode: dict[str, Any]) -> list[dict[str, Any]]:
    direction = -1 if sort_mode["descending"] else 1

    def sort_key(item: dict[str, Any]) -> tuple[int, int]:
        value = video_sort_value(item, sort_mode)
        if value is None:
            return (1, 0)
        return (0, direction * value)

    return sorted(items, key=sort_key)


def filter_items_by_published_time_range(
    items: list[dict[str, Any]],
    start_timestamp: int,
    end_timestamp: int,
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if (published_time := video_published_timestamp(item)) is not None
        if start_timestamp <= published_time <= end_timestamp
    ]


def filter_items_by_metric_range(
    items: list[dict[str, Any]],
    stat_key: str,
    minimum_value: int | None,
    maximum_value: int | None,
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if (metric_value := video_metric_value(item, stat_key)) is not None
        if minimum_value is None or metric_value > minimum_value
        if maximum_value is None or metric_value < maximum_value
    ]


def ordered_by_published_time(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_video_items(
        items,
        video_sort_mode("published_time", "Published time", None, False),
    )
