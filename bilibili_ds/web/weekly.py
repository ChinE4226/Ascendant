"""Collect one weekly-popular cohort without replacing the creator dataset."""

import asyncio
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlparse

from bilibili_ds.api import hot

from bilibili_ds.errors import public_error_message
from bilibili_ds import accounts, client, videos
from bilibili_ds.distributions import has_complete_metrics
from bilibili_ds.weekly import summarize_weekly_items
from bilibili_ds.web import dataset
from bilibili_ds.web.progress import set_progress
from bilibili_ds.web.serializers import format_time, serialize_video
from bilibili_ds.web.videos import check_detail_rejection


def parse_weekly_source(value):
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9]+", text):
        number = int(text)
    else:
        parsed = urlparse(text)
        numbers = parse_qs(parsed.query).get("num", [])
        if (parsed.scheme not in {"http", "https"} or parsed.netloc not in {"www.bilibili.com", "bilibili.com"}
                or parsed.path.rstrip("/") != "/v/popular/weekly" or len(numbers) != 1
                or not re.fullmatch(r"[0-9]+", numbers[0])):
            raise ValueError("Enter a Bilibili weekly page URL with num=393, or a positive issue number.")
        number = int(numbers[0])
    if number <= 0:
        raise ValueError("The issue number must be positive.")
    return number


async def fetch_weekly_analysis(payload, *, snapshot_only=False):
    if not dataset.LOCK.acquire(blocking=False):
        raise ValueError("Another video operation is running. Try again when it finishes.")
    configured = False
    try:
        number = parse_weekly_source(payload.get("source"))
        started = datetime.now(timezone.utc).isoformat()
        set_progress(f"Fetching weekly popular issue {number}.", running=True, percent=5, count=0)
        credential = accounts.credential_from_env()
        client.configure_bilibili_client()
        configured = True
        response = await hot.get_weekly_hot_videos(number, credential=credential)
        await asyncio.sleep(client.request_delay_seconds())
        rows = response.get("list") if isinstance(response, dict) else None
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise ValueError("Bilibili returned an invalid weekly list. Try fetching again later.")
        info = response.get("config") if isinstance(response.get("config"), dict) else {}
        if info.get("number") is not None and str(info["number"]) != str(number):
            raise ValueError("Bilibili returned a different issue. Try fetching again later.")
        enriched, seen = [], set()
        for index, row in enumerate(rows, start=1):
            identity = str(row.get("bvid") or row.get("aid") or "")
            if identity not in seen and not has_complete_metrics(row):
                row = await videos.fetch_video_detail(row, credential)
                await asyncio.sleep(client.request_delay_seconds())
                check_detail_rejection(row)
            if identity:
                seen.add(identity)
            enriched.append(row)
            set_progress(f"Weekly issue {number}: checked {index}/{len(rows)} videos.",
                         percent=50 + int(40 * index / max(len(rows), 1)), count=index)
        valid, summary = summarize_weekly_items(enriched)
        result = {**summary, "issue": {"number": number, "name": info.get("name") or f"Issue {number}",
                  "subject": info.get("subject") or "", "start": format_time(info.get("stime")),
                  "end": format_time(info.get("etime")),
                  "url": f"https://www.bilibili.com/v/popular/weekly?num={number}"},
                  "started_at": started, "collected_at": datetime.now(timezone.utc).isoformat(),
                  "videos": [{**serialize_video(item),
                      "creator": (item.get("owner") or {}).get("name", "Unknown") if isinstance(item.get("owner"), dict) else "Unknown"}
                      for item in valid]}
        configured = False
        await client.close_bilibili_client()
        metadata = dataset.describe_collection(valid, kind='weekly', label=f"Weekly popular · {result['issue']['name']}",
            started_at=started, collected_at=result['collected_at'], scope=result['issue'],
            collection={'requested': len(rows), 'examined': len(rows), 'skipped_invalid': summary['counts']['invalid'],
                        'skipped_duplicates': summary['counts']['duplicates'], 'shortfall': len(rows) - len(valid)})
        if not snapshot_only:
            result['dataset'] = dataset.retain_collection(valid, kind='weekly', label=metadata['source_label'],
                started_at=started, collected_at=result['collected_at'], scope=result['issue'], collection=metadata['collection'], report=result)
        set_progress(f"Weekly analysis completed. {len(valid)} valid videos; {summary['counts']['invalid']} invalid and {summary['counts']['duplicates']} duplicate entries skipped.",
                     running=False, percent=100, count=len(valid))
        return (valid, metadata) if snapshot_only else result
    except Exception as exc:
        set_progress(f"Weekly analysis failed: {public_error_message(exc)}", running=False)
        raise
    finally:
        if configured:
            await client.close_bilibili_client()
        dataset.LOCK.release()
