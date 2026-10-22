"""Browser-facing data formats, metric fields, and calculations."""

from __future__ import annotations

from datetime import datetime
import re
import statistics
from typing import Any


VIDEO_FIELDS = [
    {"field": "views", "label": "Views", "stat_key": "view"},
    {"field": "likes", "label": "Likes", "stat_key": "like"},
    {"field": "replies", "label": "Replies", "stat_key": "reply"},
    {"field": "favorites", "label": "Favorites", "stat_key": "favorite"},
    {"field": "coins", "label": "Coins", "stat_key": "coin"},
    {"field": "shares", "label": "Shares", "stat_key": "share"},
]

def field_by_name(name: str, *, allow_followers: bool = False) -> dict[str, str] | None:
    for field in VIDEO_FIELDS:
        if name == field["field"] or name == field["stat_key"]:
            return field
    if allow_followers and name == "followers":
        return {"field": "followers", "label": "Followers", "source": "followers"}
    return None


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_range_number(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace(",", "").replace(" ", "")
    multiplier = 1
    if text.endswith("k"):
        multiplier = 1_000
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0:
        return None
    return int(number * multiplier)


def video_timestamp(item: dict[str, Any]) -> int | None:
    return int_or_none(item.get("pubdate") or item.get("created"))


def video_metric(item: dict[str, Any], stat_key: str) -> int | None:
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    return int_or_none(stat.get(stat_key))


def format_time(timestamp: int | None) -> str:
    if timestamp is None:
        return "Unknown"
    try:
        return datetime.fromtimestamp(timestamp).isoformat(sep=" ")
    except Exception:
        return str(timestamp)


def serialize_video(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title") or "(no title)",
        "bvid": item.get("bvid"),
        "aid": item.get("aid"),
        "published_time": format_time(video_timestamp(item)),
        "published_timestamp": video_timestamp(item),
        "views": video_metric(item, "view"),
        "likes": video_metric(item, "like"),
        "replies": video_metric(item, "reply"),
        "favorites": video_metric(item, "favorite"),
        "coins": video_metric(item, "coin"),
        "shares": video_metric(item, "share"),
    }


def extract_bvid(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Enter a Bilibili video link or BV ID.")

    match = re.search(r"(?i)(?<![0-9a-z])BV[0-9A-Za-z]{10}(?![0-9a-z])", text)
    if match is None:
        raise ValueError(
            "No valid BV ID was found. Paste a full bilibili.com/video/BV... link or a 12-character BV ID."
        )
    return "BV" + match.group(0)[2:]


def format_duration(value: Any) -> str:
    seconds = int_or_none(value)
    if seconds is None or seconds < 0:
        return "Unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def serialize_single_video(info: dict[str, Any]) -> dict[str, Any]:
    item = serialize_video(info)
    owner = info.get("owner") if isinstance(info.get("owner"), dict) else {}
    dimension = info.get("dimension") if isinstance(info.get("dimension"), dict) else {}
    bvid = str(item.get("bvid") or "")
    item.update(
        {
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else None,
            "description": info.get("desc") or "",
            "duration": format_duration(info.get("duration")),
            "duration_seconds": int_or_none(info.get("duration")),
            "category": info.get("tname"),
            "owner_name": owner.get("name"),
            "owner_mid": owner.get("mid"),
            "owner_face": owner.get("face"),
            "page_count": int_or_none(info.get("videos")),
            "width": int_or_none(dimension.get("width")),
            "height": int_or_none(dimension.get("height")),
        }
    )
    return item


def sort_by_published_time(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: video_timestamp(item) or 0)


def field_value(item: dict[str, Any], field: dict[str, str], followers: int | None = None) -> int | None:
    if field.get("source") == "followers":
        return followers
    return video_metric(item, field["stat_key"])


def ratio_value(
    item: dict[str, Any],
    numerator: dict[str, str],
    denominator: dict[str, str],
    followers: int | None,
) -> float | None:
    numerator_value = field_value(item, numerator, followers)
    denominator_value = field_value(item, denominator, followers)
    if numerator_value is None or denominator_value in (None, 0):
        return None
    return numerator_value / denominator_value


def analyse_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for field in VIDEO_FIELDS:
        values = [
            value
            for item in items
            if (value := video_metric(item, field["stat_key"])) is not None
        ]
        summaries.append(
            {
                "label": field["label"],
                "count": len(values),
                "mean": statistics.mean(values) if values else None,
                "median": statistics.median(values) if values else None,
            }
        )
    return summaries
