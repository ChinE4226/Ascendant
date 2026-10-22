"""Server-owned mission queue and results, retained in RAM until removed/restarted."""

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock, Thread
from uuid import uuid4
from urllib.parse import parse_qs

from ascendant.errors import public_error_message
from ascendant import accounts, tracking
from ascendant.distributed.protocol import workspace_selection, local_operator, json_request
from ascendant.fetch_context import PROGRESS_CALLBACK
from ascendant.web import actions, creators, dataset, distributed_dataset, plots
from ascendant.web.http import read_json_body
from ascendant.web.serializers import field_by_name, serialize_video

MAX_MISSIONS = 30
MISSIONS = OrderedDict()
LOCK = Lock()
RUNNING = False
STOP_REQUESTED = False
WORKER = None
STEP_LABELS = {'fetch': 'Fetch', 'snapshot': 'Save snapshot', 'analysis': 'Analyze', 'plot': 'Plot', 'division': 'Calculate ratio'}


def normalize(payload):
    source = str(payload.get('source') or 'fetch')
    if source not in ('fetch', 'loaded'):
        raise ValueError('Choose a new creator fetch or a loaded collection.')
    steps = payload.get('steps')
    if not isinstance(steps, list) or (not steps and source == 'loaded') or any(not isinstance(step, str) or step not in STEP_LABELS or step == 'fetch' for step in steps) or len(set(steps)) != len(steps):
        raise ValueError('Choose one or more unique processing steps.')
    config = {'name': str(payload.get('name') or '').strip()[:100], 'source': source,
              'steps': steps[:], 'account_id': accounts.active_account_id()}
    if source == 'fetch':
        uid = str(payload.get('creator_uid') or '').strip()
        if not uid.isascii() or not uid.isdigit() or not 1 <= int(uid) <= 2**63 - 1:
            raise ValueError('Enter a valid creator UID.')
        creator = next((row for row in creators.load_web_creators() if row['uid'] == uid), {'uid': uid, 'name': f'Creator {uid}'})
        config.update(creator=creator, selection=workspace_selection(payload.get('selection')),
                      fetch_source=payload.get('fetch_source') or 'auto')
        if config['fetch_source'] not in ('auto', 'local', 'remote', 'parallel'):
            raise ValueError('Choose a valid fetching source.')
    else:
        config['source_collection_id'] = payload.get('source_collection_id')
        dataset.snapshot_collection(config['source_collection_id'])  # Validate without fetching.
    filters = payload.get('local_filter') or {}
    if not isinstance(filters, dict):
        raise ValueError('Local filters must contain view boundaries.')
    bounds = {}
    for key in ('minimum_views', 'maximum_views'):
        value = filters.get(key)
        if value in (None, ''):
            bounds[key] = None
        elif str(value).isascii() and str(value).isdigit():
            bounds[key] = int(value)
        else:
            raise ValueError('Local view boundaries must be nonnegative whole numbers.')
    if all(value is not None for value in bounds.values()) and bounds['minimum_views'] > bounds['maximum_views']:
        raise ValueError('Minimum views must not exceed maximum views.')
    config['local_filter'] = bounds
    for key, default in (('numerator', 'likes'), ('denominator', 'views'), ('field', 'views')):
        value = payload.get(key) or default
        if field_by_name(value, allow_followers=key != 'field') is None:
            raise ValueError('Choose valid chart and ratio metrics.')
        config[key] = value
    for key, default, allowed in (('mode', 'aggregate', ('aggregate', 'single')), ('plot_mode', 'field', ('field', 'quotient')), ('plot_axis', 'time', ('time', 'number'))):
        config[key] = payload.get(key) or default
        if config[key] not in allowed:
            raise ValueError('Choose valid ratio and chart settings.')
    return config


def _public(row):
    return deepcopy({key: value for key, value in row.items() if key != 'results'})


def overview():
    with LOCK:
        result = {'running': RUNNING, 'stop_requested': STOP_REQUESTED, 'limit': MAX_MISSIONS,
                  'missions': [_public(row) for row in MISSIONS.values()]}
    return {**result, 'collections': dataset.collection_entries(), 'creator_dataset': dataset.creator_metadata()}


def add(payload):
    config = normalize(payload)
    loaded = dataset.snapshot_collection(config['source_collection_id']) if config['source'] == 'loaded' else None
    with LOCK:
        if RUNNING:
            raise ValueError('Wait for the queue to stop before editing missions.')
        if len(MISSIONS) >= MAX_MISSIONS:
            raise ValueError(f'The queue can retain {MAX_MISSIONS} missions. Remove a mission before adding more.')
        identity = uuid4().hex
        label = config.get('creator', {}).get('name') or loaded[1]['source_label']
        config['name'] = config['name'] or label
        row = {'id': identity, 'config': config, 'state': 'queued', 'completed_steps': [],
               'current_step': None, 'progress': 0, 'message': 'Queued', 'results': {},
               'collection_id': f'mission-{identity}', 'dataset': None}
        if loaded:
            items, meta = loaded
            row['dataset'] = dataset.retain_mission_collection(row['collection_id'], items, {**meta, 'mission_id': identity,
                'source_label': f"{config['name']} · {meta['source_label']}"})
        MISSIONS[identity] = row
        return _public(row)


def change(identity, command):
    with LOCK:
        if RUNNING:
            raise ValueError('Wait for the queue to stop before editing missions.')
        row = MISSIONS.get(identity)
        if row is None:
            raise ValueError('This mission is unavailable.')
        if command == 'remove':
            del MISSIONS[identity]
            dataset.remove_mission_collection(row['collection_id'])
        elif command in ('up', 'down'):
            keys = list(MISSIONS)
            index = keys.index(identity)
            target = index + (-1 if command == 'up' else 1)
            if 0 <= target < len(keys):
                keys[index], keys[target] = keys[target], keys[index]
                rows = [(key, MISSIONS[key]) for key in keys]
                MISSIONS.clear()
                MISSIONS.update(rows)
        else:
            raise ValueError('Choose move up, move down or remove.')


def detail(identity):
    with LOCK:
        row = MISSIONS.get(identity)
        if row is None:
            raise ValueError('This mission is unavailable.')
        result = deepcopy(row)
    if result['dataset'] is not None:
        items, _ = dataset.snapshot_collection(result['collection_id'])
        result['videos'] = [serialize_video(item) for item in items]
    chart = result['results'].get('plot')
    if chart:
        source = chart.get('selected_creator') or {'name': chart['dataset']['source_label'], 'uid': chart['dataset']['source_kind']}
        chart['plot_id'] = plots.prepare_plot(source, chart['selection'], chart['plot_label'], chart['y_label'], chart['points'],
                                             collection_id=result['collection_id'])
    return result


def _update(identity, **values):
    with LOCK:
        MISSIONS[identity].update(values)


async def execute_step(identity, step):
    with LOCK:
        row = deepcopy(MISSIONS[identity])
    config = row['config']
    if accounts.active_account_id() != config['account_id']:
        raise ValueError('The active account changed. Restore the original account before resuming this mission.')
    if not dataset.LOCK.acquire(blocking=False):
        raise ValueError('Another video operation is running. Resume the queue when it finishes.')
    creator_token = creators.CREATOR_OVERRIDE.set(config.get('creator'))
    progress_token = PROGRESS_CALLBACK.set(lambda percent, message: _update(identity, progress=percent or 0, message=message))
    try:
        if step == 'fetch':
            started = datetime.now(timezone.utc).isoformat()
            items, label, total, collection = await distributed_dataset.fetch_workspace_items({
                'action': 'list', 'selection': config['selection'], 'fetch_source': config['fetch_source']})
            creator = config['creator']
            meta = {'source_kind': 'creator', 'source_label': f"{config['name']} · {creator['name']}",
                    'creator_name': creator['name'], 'uid': creator['uid'], 'selection': label,
                    'started_at': started, 'collected_at': datetime.now(timezone.utc).isoformat(),
                    'collection': collection, 'mission_id': identity,
                    'scope': {'uid': creator['uid'], 'selection': config['selection'], 'fetch_source': config['fetch_source']}}
            meta = dataset.retain_mission_collection(row['collection_id'], items, meta)
            _update(identity, dataset=meta)
            return {'count': len(items), 'total_videos': total, 'dataset': meta}
        if step == 'snapshot':
            items, meta = dataset.snapshot_collection(row['collection_id'])
            return {'batch': tracking.save_collection(items, meta, mode='loaded')}
        payload = {key: config[key] for key in ('local_filter', 'mode', 'plot_mode', 'field', 'numerator', 'denominator')}
        return await actions._execute_video_action({'action': step, 'collection_id': row['collection_id'], 'reuse_only': True, **payload})
    finally:
        PROGRESS_CALLBACK.reset(progress_token)
        creators.CREATOR_OVERRIDE.reset(creator_token)
        dataset.LOCK.release()


def _run(identities):
    global RUNNING
    try:
        for identity in identities:
            with LOCK:
                row = MISSIONS[identity]
                steps = (['fetch'] if row['config']['source'] == 'fetch' else []) + row['config']['steps']
            for step in steps:
                with LOCK:
                    row = MISSIONS[identity]
                    if step in row['completed_steps']:
                        continue
                    if STOP_REQUESTED:
                        row.update(state='paused', message='Paused; completed steps and data are retained.', current_step=None)
                        return
                    row.update(state='running', current_step=step, message=f'{STEP_LABELS[step]}…', progress=0)
                try:
                    result = asyncio.run(execute_step(identity, step))
                except Exception as exc:
                    _update(identity, state='failed', message=public_error_message(exc), current_step=step)
                    return
                with LOCK:
                    row['results'][step] = result
                    row['completed_steps'].append(step)
            _update(identity, state='completed', progress=100, message='Completed', current_step=None)
    finally:
        with LOCK:
            RUNNING = False


def start():
    global RUNNING, STOP_REQUESTED, WORKER
    with LOCK:
        if RUNNING:
            raise ValueError('The mission queue is already running.')
        identities = [row['id'] for row in MISSIONS.values() if row['state'] != 'completed']
        if not identities:
            raise ValueError('Add a mission or resume an unfinished mission first.')
        RUNNING, STOP_REQUESTED = True, False
        WORKER = Thread(target=_run, args=(identities,), daemon=True, name='dataset-missions')
        WORKER.start()


def stop():
    global STOP_REQUESTED
    with LOCK:
        STOP_REQUESTED = True


def mission_route(handler, parsed, method):
    if not parsed.path.startswith('/api/missions'):
        return False
    try:
        local_operator(handler)
        if method == 'GET' and parsed.path == '/api/missions':
            result = overview()
        elif method == 'GET' and parsed.path == '/api/missions/result':
            result = detail(parse_qs(parsed.query).get('id', [''])[0])
        elif method == 'POST':
            json_request(handler)
            payload = read_json_body(handler)
            if parsed.path == '/api/missions/add':
                add(payload)
            elif parsed.path == '/api/missions/action':
                change(payload.get('id'), payload.get('action'))
            elif parsed.path == '/api/missions/run':
                start()
            elif parsed.path == '/api/missions/stop':
                stop()
            else:
                handler.send_error_json(404, 'Not found.')
                return True
            result = overview()
        else:
            handler.send_error_json(404, 'Not found.')
            return True
        handler.send_json(result)
    except (ValueError, OSError) as exc:
        handler.send_error_json(getattr(exc, 'status', 400), public_error_message(exc))
    return True
