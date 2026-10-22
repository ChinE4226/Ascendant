"""Browser list, analysis, division, and plotting operations."""

from __future__ import annotations

from typing import Any

from ascendant.errors import public_error_message
from ascendant.distributions import analyse_dataset, metric
from ascendant.web import dataset
from ascendant.web.creators import selected_creator
from ascendant.web.plots import prepare_plot
from ascendant.web.progress import set_progress
from ascendant.web.serializers import (
    field_by_name,
    field_value,
    ratio_value,
    serialize_video,
)
from ascendant.web.videos import fetch_followers, fetch_selected_video_items
from ascendant.web.distributed_dataset import fetch_workspace_items


async def execute_video_action(payload: dict[str, Any]) -> dict[str, Any]:
    if not dataset.LOCK.acquire(blocking=False):
        raise ValueError("Another video operation is running. Try again when it finishes.")
    try:
        return await _execute_video_action(payload)
    finally:
        dataset.LOCK.release()


async def _execute_video_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "")
    set_progress(f"Starting {action or 'video'} action.", running=True, percent=1, count=0)
    try:
        if action not in {"list", "analysis", "division", "plot"}:
            raise ValueError("Invalid action.")
        async def fetcher(request):
            return await fetch_workspace_items(request, local_fetcher=fetch_selected_video_items)
        (items, selection_label, total), metadata = await dataset.acquire(payload, fetcher)
        local = payload.get("local_filter") or {}
        lower, upper = local.get("minimum_views"), local.get("maximum_views")
        def boundary(value):
            if value in (None, ""):
                return None
            try:
                parsed = int(str(value))
            except (TypeError, ValueError):
                raise ValueError("Local view boundaries must be nonnegative whole numbers.") from None
            if parsed < 0:
                raise ValueError("Local view boundaries must be nonnegative whole numbers.")
            return parsed
        lower, upper = boundary(lower), boundary(upper)
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("Minimum views must not exceed maximum views.")
        source_count = len(items)
        if lower is not None or upper is not None:
            items = [item for item in items if (value := metric(item, "view")) is not None
                     and (lower is None or value >= lower) and (upper is None or value <= upper)]
            selection_label += f"; local views {lower if lower is not None else 0}–{upper if upper is not None else 'unlimited'}"
        metadata = {**metadata, "filter_counts": {
            "fetched": source_count, "included": len(items), "excluded": source_count - len(items),
            "view_range": source_count - len(items),
        }}
        serialized = [serialize_video(item) for item in items]

        if action == "list":
            set_progress(f"List completed. Selected {len(items)} video(s).", running=False, percent=100, count=len(items))
            return {
                "action": action,
                "dataset": metadata,
                "selection": selection_label,
                "total_videos": total,
                "count": len(items),
                "videos": serialized,
            }

        if action == "analysis":
            set_progress("Calculating distributions and engagement.", percent=95, count=len(items))
            result = {
                "action": action,
                "dataset": metadata,
                "selection": selection_label,
                "count": len(items),
                **analyse_dataset(items),
            }
            set_progress(f"Analysis completed. Selected {len(items)} video(s).", running=False, percent=100, count=len(items))
            return result

        if action in {"division", "plot"}:
            numerator = field_by_name(str(payload.get("numerator") or "likes"), allow_followers=True)
            denominator = field_by_name(str(payload.get("denominator") or "views"), allow_followers=True)
            if numerator is None or denominator is None:
                raise ValueError("Invalid numerator or denominator.")
            needs_followers = (action == 'division' or payload.get('plot_mode') == 'quotient') and "followers" in {numerator["field"], denominator["field"]}
            if needs_followers and metadata.get('source_kind') in {'weekly', 'random'}:
                raise ValueError('Follower ratios require a creator dataset. Choose video metrics for this collection.')
            if needs_followers:
                set_progress("Fetching selected Creator follower count.", percent=92, count=len(items))
                followers = await fetch_followers(creator_uid=metadata['uid']) if metadata.get('collection_id') else await fetch_followers()
            else:
                followers = None

            if action == "division":
                mode = str(payload.get("mode") or "single")
                set_progress("Calculating division result.", percent=96, count=len(items))
                if mode == "aggregate":
                    numerator_values = [field_value(item, numerator, followers) for item in items]
                    denominator_values = [field_value(item, denominator, followers) for item in items]
                    pairs = [(a, b) for a, b in zip(numerator_values, denominator_values) if a is not None and b is not None and a >= 0 and b > 0]
                    numerator_total = sum(a for a, b in pairs)
                    denominator_total = sum(b for a, b in pairs)
                    ratio = None if denominator_total == 0 else numerator_total / denominator_total
                    result = {
                        "action": action,
                        "dataset": metadata,
                        "mode": mode,
                        "selection": selection_label,
                        "count": len(items),
                        "eligible_count": len(pairs),
                        "excluded_count": len(items) - len(pairs),
                        "numerator_total": numerator_total,
                        "denominator_total": denominator_total,
                        "ratio": ratio,
                    }
                    set_progress(f"Division completed. Selected {len(items)} video(s).", running=False, percent=100, count=len(items))
                    return result
                rows = []
                for item, video_data in zip(items, serialized):
                    numerator_value = field_value(item, numerator, followers)
                    denominator_value = field_value(item, denominator, followers)
                    ratio = ratio_value(item, numerator, denominator, followers)
                    rows.append(
                        {
                            "title": video_data["title"],
                            "published_time": video_data["published_time"],
                            "numerator": numerator_value,
                            "denominator": denominator_value,
                            "ratio": ratio,
                        }
                    )
                result = {
                    "action": action,
                    "dataset": metadata,
                    "mode": mode,
                    "selection": selection_label,
                    "count": len(items),
                    "rows": rows,
                }
                set_progress(f"Division completed. Selected {len(items)} video(s).", running=False, percent=100, count=len(items))
                return result

            plot_mode = str(payload.get("plot_mode") or "field")
            points = []
            set_progress("Preparing plot points.", percent=96, count=len(items))
            if plot_mode == "field":
                field = field_by_name(str(payload.get("field") or "views"))
                if field is None:
                    raise ValueError("Invalid plot field.")
                plot_label = field["label"]
                y_label = field["label"]
                for item, video_data in zip(items, serialized):
                    value = field_value(item, field, None)
                    if value is None:
                        continue
                    points.append(
                        {
                            "title": video_data["title"],
                            "label": video_data["published_time"],
                            "value": value,
                        }
                    )
            else:
                plot_label = f"{numerator['label']} divided by {denominator['label']}"
                y_label = f"{numerator['label']} / {denominator['label']}"
                for item, video_data in zip(items, serialized):
                    value = ratio_value(item, numerator, denominator, followers)
                    if value is None:
                        continue
                    points.append(
                        {
                            "title": video_data["title"],
                            "label": video_data["published_time"],
                            "value": value,
                        }
                    )
            cohort = metadata.get('source_kind') in {'weekly', 'random'}
            selected = None if cohort else ({'uid': metadata['uid'], 'name': metadata.get('creator_name') or metadata['source_label']}
                                           if metadata.get('collection_id') else selected_creator())
            plot_source = {'name': metadata['source_label'], 'uid': metadata['source_kind']} if cohort else selected
            plot_id = prepare_plot(plot_source, selection_label, plot_label, y_label, points,
                                   collection_id=metadata.get('collection_id') or 'creator') if plot_source else None
            set_progress(f"Plot completed. Selected {len(items)} video(s), plotted {len(points)} point(s).", running=False, percent=100, count=len(items))
            return {
                "action": action,
                "dataset": metadata,
                "selection": selection_label,
                "count": len(items),
                "points": points,
                "plot_id": plot_id,
                "selected_creator": selected,
                "plot_label": plot_label,
                "y_label": y_label,
            }

        raise ValueError("Invalid action.")
    except Exception as exc:
        set_progress(f"Failed: {public_error_message(exc)}", running=False)
        raise
