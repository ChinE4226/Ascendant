"""Collect bounded keyword search candidates without replacing the creator dataset."""

import asyncio
from datetime import datetime, timedelta, timezone
import html
import re
from zoneinfo import ZoneInfo

from bilibili_ds.api import search

from bilibili_ds.errors import public_error_message
from bilibili_ds import accounts, client, videos
from bilibili_ds.sampling import parse_sample_options, sample_candidates
from bilibili_ds.web import dataset
from bilibili_ds.web.progress import set_progress
from bilibili_ds.web.serializers import serialize_video
from bilibili_ds.web.videos import check_detail_rejection


async def fetch_random_sample(payload, *, snapshot_only=False):
    options = parse_sample_options(payload)
    if not dataset.LOCK.acquire(blocking=False):
        raise ValueError("Another video operation is running. Try again when it finishes.")
    configured = False
    try:
        started = datetime.now(timezone.utc).isoformat()
        set_progress("Searching for sample candidates.", running=True, percent=0, count=0)
        credential = accounts.credential_from_env()
        client.configure_bilibili_client()
        configured = True
        rows, seen = [], set()
        checked = pages = 0
        time_start = time_end = None
        if options["published_start"] or options["published_end"]:
            time_start = options["published_start"] or "1970-01-01"
            end = datetime.strptime(options["published_end"], "%Y-%m-%d").date() if options["published_end"] else datetime.now(ZoneInfo("Asia/Shanghai")).date()
            time_end = (end + timedelta(days=1)).isoformat()
        # Page requests are bounded even if the server repeats candidates.
        for page in range(1, (options["pool_size"] + 19) // 20 + 1):
            response = await search.search_by_type(keyword=options["keyword"], search_type=search.SearchObjectType.VIDEO,
                order_type=search.OrderVideo(options["order"]), page=page, page_size=20,
                time_start=time_start, time_end=time_end, video_zone_type=options["category_id"], credential=credential)
            pages += 1
            await asyncio.sleep(client.request_delay_seconds())
            candidates = response.get("result") if isinstance(response, dict) else None
            if not isinstance(candidates, list) or any(not isinstance(item, dict) for item in candidates):
                raise ValueError("Bilibili returned invalid search results. Try collecting again later.")
            if not candidates:
                break
            for summary in candidates[:options["pool_size"] - len(rows)]:
                item = dict(summary)
                item["title"] = html.unescape(re.sub(r"<[^>]*>", "", str(item.get("title") or "")))
                identity = str(item.get("bvid") or item.get("aid") or "")
                if identity and identity not in seen:
                    seen.add(identity)
                    item = await videos.fetch_video_detail(item, credential)
                    checked += 1
                    await asyncio.sleep(client.request_delay_seconds())
                    check_detail_rejection(item)
                rows.append(item)
                set_progress(f"Checking candidate pool: {len(rows)}/{options['pool_size']} entries; {checked} details fetched.",
                             percent=int(95 * len(rows) / options["pool_size"]), count=len(rows))
            if len(rows) >= options["pool_size"]:
                break
        sampled, report = sample_candidates(rows, options)
        report["sampling"].update({"checked": checked, "pages": pages})
        result = {**report, "started_at": started, "collected_at": datetime.now(timezone.utc).isoformat(),
                  "videos": [{**serialize_video(item), "creator": (item.get("owner") or {}).get("name", "Unknown")
                              if isinstance(item.get("owner"), dict) else "Unknown"} for item in sampled]}
        configured = False
        await client.close_bilibili_client()
        s = report['sampling']
        metadata = dataset.describe_collection(sampled, kind='random', label=f"Random sample · {options['keyword']}",
            started_at=started, collected_at=result['collected_at'], scope=s,
            collection={'requested': s['sample_size'], 'examined': s['candidates'], 'details_checked': checked,
                        'skipped_invalid': s['invalid'], 'skipped_duplicates': s['duplicates'],
                        'collection_filtered': s['filtered_out'], 'eligible': s['eligible'], 'shortfall': s['shortfall']})
        if not snapshot_only:
            result['dataset'] = dataset.retain_collection(sampled, kind='random', label=metadata['source_label'],
                started_at=started, collected_at=result['collected_at'], scope=s, collection=metadata['collection'], report=result)
        set_progress(f"Sample completed: {len(sampled)}/{options['sample_size']} videos from {report['sampling']['eligible']} eligible candidates.",
                     running=False, percent=100, count=len(sampled))
        return (sampled, metadata) if snapshot_only else result
    except Exception as exc:
        set_progress(f"Sampling failed: {public_error_message(exc)}", running=False)
        raise
    finally:
        try:
            if configured:
                await client.close_bilibili_client()
        finally:
            dataset.LOCK.release()
