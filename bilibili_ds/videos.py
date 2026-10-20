"""Video response handling and detail fetching used by the website."""

from typing import Any

import httpx
from bilibili_ds.api import Credential, video
from bilibili_ds.errors import BilibiliRequestError, InvalidResponseError, integer_code, request_error_message


async def fetch_creator_video_page(uploader, *, pn, ps, order, collected=None,
                                  requested=None, item_kind='valid videos'):
    """Keep useful failure details without exposing the upstream response."""
    try:
        return await uploader.get_videos(pn=pn, ps=ps, order=order)
    except Exception as exc:
        code, status = getattr(exc, 'code', None), getattr(exc, 'status', None)
        reason = request_error_message(exc)
        count = '' if collected is None else f' {collected}{"/" + str(requested) if requested is not None else ""} {item_kind} collected before failure.'
        raise BilibiliRequestError(f'Video collection stopped on page {pn}. {reason}{count}', code=code, status=status) from exc


def video_total_from_response(videos: dict[str, Any]) -> int:
    page = videos.get("page", {})
    for key in ("count", "total"):
        try:
            return int(page[key])
        except (KeyError, TypeError, ValueError):
            continue
    return len(videos.get("list", {}).get("vlist", []))


async def fetch_video_detail(item: dict[str, Any], credential: Credential | None) -> dict[str, Any]:
    enriched = dict(item)
    bvid = enriched.get("bvid")
    if not bvid:
        enriched["stat"] = {}
        enriched["detail_error"] = "No BVID available."
        return enriched

    try:
        info = await video.Video(bvid=bvid, credential=credential).get_info()
    except Exception as exc:
        enriched["stat"] = {}
        enriched["detail_error"] = request_error_message(exc)
        enriched["detail_error_code"] = integer_code(getattr(exc, "code", None))
        enriched["detail_error_status"] = integer_code(getattr(exc, "status", None))
        enriched['detail_error_operation'] = getattr(exc, 'operation', None)
        enriched['detail_error_credentials'] = getattr(exc, 'credential_mode', None)
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            enriched['detail_error_kind'] = 'timeout'
        elif isinstance(exc, httpx.TransportError):
            enriched['detail_error_kind'] = 'network'
        elif isinstance(exc, InvalidResponseError):
            enriched['detail_error_kind'] = 'invalid_response'
        elif getattr(exc, 'kind', None) in ('timeout', 'network'):
            enriched['detail_error_kind'] = exc.kind
        return enriched

    stat = info.get("stat") if isinstance(info.get("stat"), dict) else {}
    enriched.update(
        {
            "title": info.get("title") or enriched.get("title"),
            "bvid": info.get("bvid") or enriched.get("bvid"),
            "aid": info.get("aid") or enriched.get("aid"),
            "pubdate": info.get("pubdate") or enriched.get("pubdate") or enriched.get("created"),
            "stat": stat,
        }
    )
    for key in ("owner", "tid", "tname", "duration"):
        if key in info:
            enriched[key] = info[key]
    return enriched
