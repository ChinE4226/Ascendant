"""Browser video fetching, selection, and progress updates."""

from __future__ import annotations

import asyncio
from typing import Any

from ascendant.api import user, video

from ascendant import accounts as account_service, client, selection as video_selection, videos as video_service
from ascendant.distributions import has_complete_metrics
from ascendant.fetch_context import DETAIL_BATCHER
from ascendant.errors import BilibiliRequestError, check_detail_rejection, request_error_message
from ascendant.web.creators import selected_creator
from ascendant.web.progress import set_progress
from ascendant.web.serializers import (
    extract_bvid,
    field_by_name,
    int_or_none,
    parse_range_number,
    serialize_single_video,
    sort_by_published_time,
    video_metric,
    video_timestamp,
)


async def fetch_single_video(value: Any) -> dict[str, Any]:
    try:
        bvid = extract_bvid(value)
    except ValueError as exc:
        set_progress(f"Lookup failed: {exc}", running=False, percent=100, count=0)
        raise
    credential = account_service.credential_from_env()
    client.configure_bilibili_client()
    set_progress(f"Fetching {bvid}.", running=True, percent=15, count=0)
    try:
        info = await video.Video(bvid=bvid, credential=credential).get_info()
        if not isinstance(info, dict) or not info:
            raise BilibiliRequestError("Bilibili returned no video information.")
        set_progress(f"Lookup completed for {bvid}.", running=False, percent=100, count=1)
        return serialize_single_video(info)
    except Exception as exc:
        message = f"Could not fetch {bvid}. {request_error_message(exc)}"
        set_progress(message, running=False, percent=100, count=0)
        raise BilibiliRequestError(message, code=getattr(exc, 'code', None), status=getattr(exc, 'status', None)) from exc
    finally:
        await client.close_bilibili_client()


async def fetch_web_video_summaries(
    uploader: Any,
    target_count: int,
    order: Any,
    *,
    progress_label: str,
    start_percent: int,
    end_percent: int,
) -> list[dict[str, Any]]:
    page_size = 30
    page_number = 1
    fetched_items: list[dict[str, Any]] = []
    total_pages = max(1, (target_count + page_size - 1) // page_size)

    while len(fetched_items) < target_count:
        percent = start_percent + int((len(fetched_items) / max(target_count, 1)) * (end_percent - start_percent))
        set_progress(
            f"{progress_label}: page {page_number}/{total_pages}, {len(fetched_items)}/{target_count} summaries fetched.",
            percent=percent,
            count=len(fetched_items),
        )
        try:
            page_data = await video_service.fetch_creator_video_page(
                uploader, pn=page_number, ps=page_size, order=order, collected=len(fetched_items),
                requested=target_count, item_kind='video summaries')
        except Exception as exc:
            set_progress(f"Failed to fetch video summary page {page_number}: {exc}", percent=percent)
            raise

        page_items = page_data.get("list", {}).get("vlist", [])
        if not page_items:
            break

        fetched_items.extend(page_items[: target_count - len(fetched_items)])
        set_progress(
            f"{progress_label}: {len(fetched_items)}/{target_count} summaries fetched.",
            percent=start_percent + int((len(fetched_items) / max(target_count, 1)) * (end_percent - start_percent)),
            count=len(fetched_items),
        )
        page_number += 1
        await asyncio.sleep(client.request_delay_seconds())

    return fetched_items


async def enrich_web_video_items(
    items: list[dict[str, Any]],
    credential: Any,
    *,
    progress_label: str,
    start_percent: int,
    end_percent: int,
) -> list[dict[str, Any]]:
    enriched_items: list[dict[str, Any]] = []
    total = len(items)
    if total == 0:
        set_progress(f"{progress_label}: no video details to fetch.", percent=end_percent, count=0)
        return enriched_items

    batcher = DETAIL_BATCHER.get()
    if batcher is not None:
        for offset in range(0, total, 30):
            batch = await batcher(items[offset:offset + 30], credential)
            for enriched in batch:
                check_detail_rejection(enriched)
            enriched_items.extend(batch)
            set_progress(f"{progress_label}: {len(enriched_items)}/{total} details fetched across Macs.",
                         percent=start_percent + int(len(enriched_items) / total * (end_percent - start_percent)), count=len(enriched_items))
        return enriched_items

    for index, item in enumerate(items, start=1):
        set_progress(
            f"{progress_label}: fetching detail {index}/{total}.",
            percent=start_percent + int(((index - 1) / total) * (end_percent - start_percent)),
            count=index - 1,
        )
        enriched = await video_service.fetch_video_detail(item, credential)
        check_detail_rejection(enriched)
        enriched_items.append(enriched)
        set_progress(
            f"{progress_label}: fetched detail {index}/{total}.",
            percent=start_percent + int((index / total) * (end_percent - start_percent)),
            count=index,
        )
        await asyncio.sleep(client.request_delay_seconds())

    return enriched_items


async def fetch_valid_video_count(uploader, credential, start, requested, total, action_label, *, max_candidates=None, max_pages=None):
    """Scan from a raw publication position until enough unique valid rows exist."""
    page_size = 30
    page_number = (start - 1) // page_size + 1
    offset = (start - 1) % page_size
    items, seen = [], set()
    examined = skipped = duplicates = 0
    pages = 0
    while len(items) < requested and (page_number - 1) * page_size < total:
        if max_pages is not None and pages >= max_pages or max_candidates is not None and examined >= max_candidates:
            break
        pages += 1
        set_progress(f"{action_label}: {len(items)}/{requested} valid videos; fetching summary page {page_number}.",
                     percent=15 + int(73 * len(items) / requested), count=len(items))
        response = await video_service.fetch_creator_video_page(
            uploader, pn=page_number, ps=page_size, order=user.VideoOrder.PUBDATE,
            collected=len(items), requested=requested)
        await asyncio.sleep(client.request_delay_seconds())
        summaries = response.get("list", {}).get("vlist", [])
        if not summaries:
            break
        cursor = offset
        batcher = DETAIL_BATCHER.get()
        while cursor < len(summaries) and len(items) < requested:
            width = min(30 if batcher else 1, requested - len(items))
            if max_candidates is not None:
                width = min(width, max_candidates - examined)
            if width <= 0:
                break
            candidates = []
            while cursor < len(summaries) and len(candidates) < width:
                summary = summaries[cursor]
                cursor += 1
                bvid = summary.get('bvid')
                if bvid and bvid in seen:
                    duplicates += 1
                    continue
                if bvid:
                    seen.add(bvid)
                candidates.append(summary)
            if not candidates:
                continue
            if batcher:
                enriched = await batcher(candidates, credential)
            else:
                enriched = [await video_service.fetch_video_detail(candidates[0], credential)]
            for item in enriched:
                examined += 1
                check_detail_rejection(item)
                if has_complete_metrics(item):
                    items.append(item)
                else:
                    skipped += 1
                set_progress(f"{action_label}: {len(items)}/{requested} valid videos · {examined} checked · {skipped} invalid skipped.",
                             percent=15 + int(73 * len(items) / requested), count=len(items))
            if not batcher:
                await asyncio.sleep(client.request_delay_seconds())
        offset = 0
        page_number += 1
    collection = {"requested": requested, "examined": examined, "skipped_invalid": skipped,
                  "skipped_duplicates": duplicates, "shortfall": max(0, requested - len(items))}
    if len(items) < requested and ((max_pages is not None and pages >= max_pages) or (max_candidates is not None and examined >= max_candidates)):
        collection['limited'] = True
    return items, collection


def valid_collection(items):
    valid = [item for item in items if has_complete_metrics(item)]
    return valid, {"requested": None, "examined": len(items), "skipped_invalid": len(items) - len(valid),
                   "skipped_duplicates": 0, "shortfall": 0}


_LOCAL_CREDENTIAL = object()


async def fetch_selected_video_items(payload: dict[str, Any], *, creator_uid=None, credential_override=_LOCAL_CREDENTIAL,
                                     max_candidates=None, max_pages=None) -> tuple[list[dict[str, Any]], str, int | None, dict]:
    selected = {"uid": creator_uid} if creator_uid is not None else selected_creator()
    if selected is None:
        raise ValueError("No Creator is selected.")

    credential = account_service.credential_from_env() if credential_override is _LOCAL_CREDENTIAL else credential_override
    uploader = user.User(int(selected["uid"]), credential=credential)
    selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
    kind = selection.get("kind") or "latest"
    action = str(payload.get("action") or "video")
    action_label = {"list": "Listing", "analysis": "Analysis", "division": "Division", "plot": "Plotting"}.get(action, "Video action")

    client.configure_bilibili_client()
    try:
        set_progress(f"{action_label}: checking selected Creator video count.", running=True, percent=5, count=0)
        first_page = await video_service.fetch_creator_video_page(uploader, pn=1, ps=1, order=user.VideoOrder.PUBDATE)
        total = video_service.video_total_from_response(first_page)
        await asyncio.sleep(client.request_delay_seconds())
        if kind == "position":
            start = max(int(selection.get("start") or 1), 1)
            end = int(selection.get("end") or start)
            if start > end:
                raise ValueError("Start number must be smaller than or equal to end number.")
        if total <= 0:
            set_progress("No videos were found for the selected Creator.", running=False, percent=100, count=0)
            requested = end - start + 1 if kind == "position" else None
            return [], "no videos", 0, {"requested": requested, "examined": 0, "skipped_invalid": 0,
                                       "skipped_duplicates": 0, "shortfall": requested or 0}

        if kind == "position":
            items, collection = await fetch_valid_video_count(uploader, credential, start, end - start + 1, total, action_label,
                                                            max_candidates=max_candidates, max_pages=max_pages)
            set_progress(f"Selected {len(items)} video(s).", percent=90, count=len(items))
            return sort_by_published_time(items), f"{end - start + 1} valid videos starting at published-time position {start}", total, collection

        if max_candidates is not None and total > max_candidates:
            raise ValueError(f"This Creator has {total} videos. Remote time/metric selection supports up to {max_candidates}; use a published-time number range or fetch on this Mac.")
        summaries = await fetch_web_video_summaries(
            uploader,
            total,
            user.VideoOrder.PUBDATE,
            progress_label=f"{action_label}: fetching summaries for all {total} video(s)",
            start_percent=15,
            end_percent=50,
        )

        if kind == "published":
            start_raw = str(selection.get("start_time") or "").strip()
            end_raw = str(selection.get("end_time") or "").strip()
            if not start_raw or not end_raw:
                raise ValueError("Start time and end time are required.")
            start_dt = video_selection.parse_datetime_input(start_raw)
            end_dt = video_selection.parse_datetime_input(end_raw, end_of_day=True)
            if start_dt is None or end_dt is None:
                raise ValueError("Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS for time ranges.")
            start_ts = int(start_dt.timestamp())
            end_ts = int(end_dt.timestamp())
            selected_summaries = [
                item
                for item in summaries
                if (timestamp := video_timestamp(item)) is not None
                if start_ts <= timestamp <= end_ts
            ]
            items = await enrich_web_video_items(
                selected_summaries,
                credential,
                progress_label=f"{action_label}: fetching details for videos in the time range",
                start_percent=60,
                end_percent=88,
            )
            items, collection = valid_collection(items)
            set_progress(f"Selected {len(items)} video(s).", percent=90, count=len(items))
            return sort_by_published_time(items), f"published time {start_raw} to {end_raw}", total, collection

        if kind == "metric":
            field = field_by_name(str(selection.get("metric") or "views"))
            if field is None:
                raise ValueError("Invalid metric field.")
            minimum = parse_range_number(selection.get("minimum"))
            maximum = parse_range_number(selection.get("maximum"))
            if minimum is None and maximum is None:
                raise ValueError("Enter at least one metric boundary.")
            items = await enrich_web_video_items(
                summaries,
                credential,
                progress_label=f"{action_label}: fetching details before metric filtering",
                start_percent=55,
                end_percent=85,
            )
            items, collection = valid_collection(items)
            filtered = [
                item
                for item in items
                if (value := video_metric(item, field["stat_key"])) is not None
                if minimum is None or value > minimum
                if maximum is None or value < maximum
            ]
            set_progress(f"Selected {len(filtered)} video(s) after metric filtering.", percent=90, count=len(filtered))
            return sort_by_published_time(filtered), f"{field['label']} range", total, collection

        raise ValueError("Invalid video selection mode.")
    finally:
        await client.close_bilibili_client()


async def fetch_followers(*, creator_uid=None) -> int | None:
    selected = {'uid': creator_uid} if creator_uid is not None else selected_creator()
    if selected is None:
        return None
    credential = account_service.credential_from_env()
    uploader = user.User(int(selected["uid"]), credential=credential)
    client.configure_bilibili_client()
    try:
        relation = await uploader.get_relation_info()
        return int_or_none(relation.get("follower"))
    finally:
        await client.close_bilibili_client()


async def selected_creator_detail() -> dict[str, Any]:
    selected = selected_creator()
    if selected is None:
        raise ValueError("No Creator is selected.")
    credential = account_service.credential_from_env()
    uploader = user.User(int(selected["uid"]), credential=credential)
    client.configure_bilibili_client()
    try:
        info = await uploader.get_user_info()
        relation = await uploader.get_relation_info()
        first_page = await uploader.get_videos(pn=1, ps=1, order=user.VideoOrder.PUBDATE)
        return {
            "selected": selected,
            "profile": info,
            "relation": relation,
            "video_total": video_service.video_total_from_response(first_page),
        }
    finally:
        await client.close_bilibili_client()
