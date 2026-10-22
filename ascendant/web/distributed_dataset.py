"""Remote collection fills the same working dataset used by every local tool."""

import asyncio
import time

from ascendant import state as settings
from ascendant.distributed.coordinator import COORDINATOR
from ascendant.web.creators import selected_creator
from ascendant.web.progress import set_progress
from ascendant.web.videos import fetch_selected_video_items


async def fetch_workspace_items(payload, *, local_fetcher=None):
    local_fetcher = local_fetcher or fetch_selected_video_items
    source = payload.get('fetch_source') or 'auto'
    if source not in {'auto', 'local', 'remote', 'parallel'}:
        raise ValueError('Choose this Mac or connected fetching nodes.')
    if source == 'parallel':
        from ascendant.web.parallel import fetch_parallel_workspace
        return await fetch_parallel_workspace(payload, local_fetcher=local_fetcher)
    if source == 'local':
        return await local_fetcher(payload)
    snapshot = COORDINATOR.snapshot()
    compatible = [node for node in snapshot['nodes'] if 'selection' in node['capabilities']]
    available = [node for node in compatible if snapshot['running'] and node['status'] == 'idle'
                 and ('pacing' in node['capabilities'] or settings.REQUEST_FREQUENCY >= 4)]
    if source == 'auto' and not available:
        set_progress('Fetching on this Mac: no connected node is ready.', running=True, percent=2, count=0)
        return await local_fetcher(payload)
    if not snapshot['running']:
        raise ValueError('Start node connections in Workspace → Nodes before fetching remotely.')
    if not compatible:
        raise ValueError('Pair a fetching node with the latest synced source and restart its node app.')
    if not available and any(node['status'] == 'idle' and 'pacing' not in node['capabilities'] for node in compatible) and settings.REQUEST_FREQUENCY < 4:
        raise ValueError('Sync the latest source, restart and re-pair the node app to apply Dataset pacing.')
    if not available:
        raise ValueError('Fetching nodes are busy, paused or offline. Resume an available node, or choose This Mac.')
    # Legacy selections and larger number ranges are supported by the local
    # collector, but cannot be represented by the bounded node protocol.
    if source == 'auto':
        from ascendant.distributed.protocol import workspace_selection
        try:
            workspace_selection(payload.get('selection'))
        except ValueError:
            set_progress('Fetching on this Mac: this range needs the local collector.', running=True, percent=2, count=0)
            return await local_fetcher(payload)
    creator = selected_creator()
    if creator is None:
        raise ValueError('Choose a Creator first.')
    target = available[0]
    if source == 'auto' and len(snapshot['tasks']) >= 30:
        set_progress('Fetching on this Mac: the node collection queue is full.', running=True, percent=2, count=0)
        return await local_fetcher(payload)
    job = COORDINATOR.create_job({'kind': 'selection', 'uid': creator['uid'],
                                'selection': payload.get('selection'), 'targets': [target['id']], 'request_rate': settings.REQUEST_FREQUENCY})
    waiting_since = time.monotonic()
    task = None
    try:
        while True:
            snapshot = COORDINATOR.snapshot()
            task = next((item for item in snapshot['tasks'] if item['id'] == job['id']), None)
            node = next((item for item in snapshot['nodes'] if item['id'] == target['id']), None)
            if (source == 'auto' and task and task['state'] == 'queued' and task.get('started_at') is None
                    and (not snapshot['running'] or not node or node['status'] != 'idle')
                    and COORDINATOR.cancel_unstarted_selection(job['id'])):
                set_progress('Fetching on this Mac: the node became unavailable before collection started.',
                             running=True, percent=2, count=0)
                return await local_fetcher(payload)
            if task is None or not snapshot['running']:
                raise ValueError('Node connections stopped during collection. Reconnect and fetch again.')
            if task['state'] == 'completed':
                return COORDINATOR.working_result(job['id'])
            if task['state'] in {'failed', 'canceled'}:
                raise ValueError(f"Fetching on {target['name']} stopped. {task['error'] or 'Remote dataset collection was canceled.'}")
            if node and node['status'] == 'busy':
                waiting_since = time.monotonic()
            if time.monotonic() - waiting_since > 120:
                raise ValueError('The fetching node stopped responding. Reconnect it and fetch again.')
            set_progress(f"Fetching on {target['name']}: {node['message'] if node else 'Waiting for reconnection'}",
                         running=True, percent=task['progress'], count=task['included'])
            await asyncio.sleep(.3)
    except BaseException:
        try:
            # Keep a failed task marked failed; cancel only work still active.
            if task is None or task['state'] in {'queued', 'running'}:
                COORDINATOR.task_action(job['id'], 'cancel')
        except ValueError:
            pass
        raise
