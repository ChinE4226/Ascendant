"""Parallel detail collection into the existing RAM-only creator dataset."""

import asyncio
import math
import time
from threading import Event

from ascendant import client, state as settings, videos
from ascendant.distributed.coordinator import COORDINATOR
from ascendant.distributed.protocol import workspace_selection
from ascendant.distributions import has_complete_metrics
from ascendant.fetch_context import DETAIL_BATCHER, REQUEST_DELAY, CANCELED
from ascendant.web.creators import selected_creator
from ascendant.web.progress import set_progress
from ascendant.web.videos import check_detail_rejection


async def fetch_parallel_workspace(payload, *, local_fetcher):
    workspace_selection(payload.get('selection'))
    initial = COORDINATOR.snapshot()
    nodes = [node for node in initial['nodes'] if initial['running'] and node['status'] == 'idle'
             and {'videos', 'pacing'}.issubset(node['capabilities'])]
    if not nodes:
        raise ValueError('Parallel fetching needs a ready node with the latest source. Restart and re-pair its node app after syncing.')
    creator = selected_creator()
    if creator is None:
        raise ValueError('Choose a Creator first.')
    rate = settings.REQUEST_FREQUENCY
    job_id = None
    used_nodes = set()

    async def batch(summaries, credential):
        nonlocal job_id
        identities = [(index, item) for index, item in enumerate(summaries) if item.get('bvid')]
        participants = min(len(nodes) + 1, len(identities))
        remote_indices = {index for position, (index, _) in enumerate(identities)
                          if participants > 1 and position % participants}
        local_items = [(index, item) for index, item in enumerate(summaries) if index not in remote_indices]
        remote_items = [(index, item) for index, item in enumerate(summaries) if index in remote_indices]
        targets = nodes[:max(0, participants - 1)]
        cancel = Event()
        result = {}

        async def local():
            delay = REQUEST_DELAY.set(max(1, participants) / rate)
            canceled = CANCELED.set(cancel)
            try:
                for index, summary in local_items:
                    if cancel.is_set():
                        raise ValueError('Parallel collection stopped.')
                    item = await videos.fetch_video_detail(summary, credential)
                    check_detail_rejection(item)
                    result[index] = item
                    await asyncio.sleep(client.request_delay_seconds())
            finally:
                CANCELED.reset(canceled)
                REQUEST_DELAY.reset(delay)

        async def remote():
            waiting = time.monotonic()
            while True:
                snapshot = COORDINATOR.snapshot()
                task = next((task for task in snapshot['tasks'] if task['id'] == job_id), None)
                if not snapshot['running'] or task is None:
                    raise ValueError('Node connections stopped during parallel collection.')
                if task['state'] in {'failed', 'canceled'}:
                    names = ', '.join(node['name'] for node in targets)
                    raise ValueError(f"Parallel fetching on {names} stopped. {task['error'] or 'Collection canceled.'}")
                if task['state'] == 'completed':
                    received = {item['bvid']: item for item in COORDINATOR.raw_video_result(job_id)}
                    for index, summary in remote_items:
                        item = received.get(summary['bvid'])
                        if item is None:
                            raise ValueError('A parallel node returned an incomplete detail batch.')
                        if has_complete_metrics(item) and str(item.get('owner', {}).get('mid')) != str(creator['uid']):
                            raise ValueError('A parallel node returned another Creator’s video.')
                        result[index] = {**summary, **item}
                    return
                if task['state'] == 'running':
                    waiting = time.monotonic()
                if time.monotonic() - waiting > 120:
                    raise ValueError('The parallel fetching nodes stopped responding.')
                await asyncio.sleep(.1)

        if remote_items:
            used_nodes.update(node['name'] for node in targets)
            current = COORDINATOR.snapshot()
            for target in targets:
                node = next((node for node in current['nodes'] if node['id'] == target['id']), None)
                if node is None or node['status'] in {'paused', 'offline'}:
                    raise ValueError(f"Parallel fetching stopped: {target['name']} is unavailable.")
            data = {'kind': 'videos', 'videos': ' '.join(item['bvid'] for _, item in remote_items),
                    'batch_size': min(20, math.ceil(len(remote_items) / len(targets))),
                    'targets': [node['id'] for node in targets], 'request_rate': rate,
                    'participants': participants}
            if job_id is None:
                job_id = COORDINATOR.create_job(data)['id']
            else:
                COORDINATOR.append_video_units(job_id, data)
            set_progress(f"Parallel details: this Mac + {', '.join(node['name'] for node in targets)}.", running=True)
        tasks = [asyncio.create_task(local())]
        if remote_items:
            tasks.append(asyncio.create_task(remote()))
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            cancel.set()
            if job_id is not None:
                snapshot = COORDINATOR.snapshot()
                task = next((task for task in snapshot['tasks'] if task['id'] == job_id), None)
                if task and task['state'] in {'queued', 'running'}:
                    COORDINATOR.task_action(job_id, 'cancel')
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return [result[index] for index in range(len(summaries))]

    token = DETAIL_BATCHER.set(batch)
    try:
        rows, label, total, collection = await local_fetcher(payload, max_candidates=2500, max_pages=84)
        names = sorted(used_nodes)
        provenance = 'This Mac' + (' + ' + ', '.join(names) if names else '')
        return rows, label, total, {**collection, 'node_name': provenance, 'parallel': bool(names)}
    finally:
        DETAIL_BATCHER.reset(token)
