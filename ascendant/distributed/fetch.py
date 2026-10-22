"""Node fetching without dashboard, plotting, or data files."""

import asyncio

from ascendant.api import user

from ascendant import accounts, client, videos
from ascendant.distributed.protocol import wire_video, frequency, integer
from ascendant.distributions import has_complete_metrics
from ascendant.errors import check_detail_rejection


async def fetch_unit(unit, cookie, rate, canceled, progress):
    from ascendant.fetch_context import REQUEST_DELAY, CANCELED
    payload = unit['payload']
    if payload.get('request_rate') is not None:
        rate = min(rate, frequency(payload['request_rate']) / integer(payload.get('participants', 1), 'Participating Macs', 21))
    tokens = [(variable, variable.set(value)) for variable, value in (
        (REQUEST_DELAY, 1 / rate), (CANCELED, canceled))]
    try:
        return await _fetch_unit(unit, cookie, rate, canceled, progress)
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


async def _fetch_unit(unit, cookie, rate, canceled, progress):
    credential = accounts.credential_from_cookie_header(cookie) if cookie else accounts.credential_from_environment()
    if unit['kind'] == 'selection':
        from ascendant.fetch_context import PROGRESS_CALLBACK
        from ascendant.web.videos import fetch_selected_video_items
        tokens = [(variable, variable.set(value)) for variable, value in (
            (PROGRESS_CALLBACK, lambda percent, message: progress(percent if percent is not None else 0, message)),)]
        try:
            rows, label, total, collection = await fetch_selected_video_items(
                {'action': 'list', 'selection': unit['payload']['selection']},
                creator_uid=unit['payload']['uid'], credential_override=credential, max_candidates=2500, max_pages=84)
            return {'items': [wire_video(row) for row in rows], 'selection_label': label,
                    'source_total': total, 'collection': collection}
        finally:
            for variable, token in reversed(tokens):
                variable.reset(token)
    client.configure_bilibili_client()
    seen, rows = set(), []

    def check_stop():
        if canceled.is_set():
            raise ValueError('Collection stopped because this task was canceled or its connection ended.')

    async def delay():
        await asyncio.sleep(1 / rate)
        check_stop()

    async def detail(summary, total):
        check_stop()
        identity = summary.get('bvid')
        if not identity or identity in seen:
            return
        seen.add(identity)
        item = await videos.fetch_video_detail(summary, credential)
        check_detail_rejection(item)
        rows.append(wire_video(item))
        valid = sum(has_complete_metrics(row) for row in rows)
        progress(min(95, int(95 * valid / max(total, 1))), f'{len(rows)} videos checked · {valid} valid')
        await delay()

    try:
        payload = unit['payload']
        if unit['kind'] == 'videos':
            for bvid in payload['bvids']:
                await detail({'bvid': bvid}, len(payload['bvids']))
            return {'items': rows}
        if unit['kind'] != 'creator':
            raise ValueError('This node does not support the assigned task.')
        uploader = user.User(int(payload['uid']), credential=credential)
        check_stop()
        first = await videos.fetch_creator_video_page(uploader, pn=1, ps=1, order=user.VideoOrder.PUBDATE)
        total = videos.video_total_from_response(first)
        await delay()
        page, offset = (payload['start'] - 1) // 30 + 1, (payload['start'] - 1) % 30
        valid = 0
        pages_checked = 0
        while valid < payload['count'] and (page - 1) * 30 < total and len(rows) < 2500 and pages_checked < 84:
            check_stop()
            response = await videos.fetch_creator_video_page(
                uploader, pn=page, ps=30, order=user.VideoOrder.PUBDATE,
                collected=valid, requested=payload['count'])
            pages_checked += 1
            await delay()
            summaries = response.get('list', {}).get('vlist', [])
            if not isinstance(summaries, list):
                raise ValueError('Bilibili returned an invalid creator video list.')
            if not summaries:
                break
            for summary in summaries[offset:]:
                await detail(summary, payload['count'])
                valid = sum(has_complete_metrics(row) for row in rows)
                if valid >= payload['count'] or len(rows) >= 2500:
                    break
            page, offset = page + 1, 0
        return {'items': rows, 'source_total': total}
    finally:
        await client.close_bilibili_client()
