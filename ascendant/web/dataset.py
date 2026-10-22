"""Creator dataset and bounded sampling collections retained only in RAM."""

from copy import deepcopy
from collections import OrderedDict
from datetime import datetime, timezone
import json
from threading import Lock
from uuid import uuid4

from ascendant import accounts
from ascendant.web.creators import selected_creator

LOCK = Lock()
CURRENT = None
COHORT_LIMIT = 4
COHORT_ROW_LIMIT = 2500
COHORTS = OrderedDict()
MISSION_COLLECTIONS = OrderedDict()
COHORT_LOCK = Lock()


def retain_mission_collection(identity, items, metadata):
    if len(items) > COHORT_ROW_LIMIT:
        raise ValueError('A mission can retain at most 2,500 videos. Use a smaller collection range.')
    meta = {**deepcopy(metadata), 'collection_id': identity, 'count': len(items)}
    with COHORT_LOCK:
        MISSION_COLLECTIONS[identity] = {'data': (deepcopy(items), meta['selection'], len(items)), 'meta': meta}
    return deepcopy(meta)


def remove_mission_collection(identity):
    with COHORT_LOCK:
        MISSION_COLLECTIONS.pop(identity, None)
    from ascendant.web.plots import discard_collection_plots
    discard_collection_plots(identity)


def release_collection(identity, *, expected_collected_at):
    """Discard one RAM source; snapshots and independent mission copies survive."""
    global CURRENT
    if not isinstance(identity, str) or not identity:
        raise ValueError('Choose a loaded collection to release.')
    if not isinstance(expected_collected_at, str) or not expected_collected_at:
        raise ValueError('Refresh the loaded collection before releasing it.')
    if not LOCK.acquire(blocking=False):
        raise ValueError('Another video operation is running. Try again when it finishes.')
    try:
        if identity == 'creator':
            metadata = creator_metadata()
            if metadata is None:
                raise ValueError('No dataset is loaded for this creator and account.')
            if metadata['collected_at'] != expected_collected_at:
                raise ValueError('The dataset changed. Refresh before releasing it.')
            CURRENT = None
        else:
            with COHORT_LOCK:
                if identity in MISSION_COLLECTIONS:
                    raise ValueError('Remove this mission in Tasks to release its data and results.')
                entry = COHORTS.get(identity)
                if entry is None:
                    raise ValueError('This collection is no longer loaded.')
                if entry['meta']['collected_at'] != expected_collected_at:
                    raise ValueError('The collection changed. Refresh before releasing it.')
                del COHORTS[identity]
        from ascendant.web.plots import discard_collection_plots
        discard_collection_plots(identity)
        return {'released': identity}
    finally:
        LOCK.release()


def describe_collection(items, *, kind, label, started_at, collected_at, collection, scope):
    return {'source_kind': kind, 'source_label': label,
            'started_at': started_at, 'collected_at': collected_at, 'count': len(items),
            'selection': label, 'uid': None, 'collection': deepcopy(collection), 'scope': deepcopy(scope)}


def retain_collection(items, *, kind, label, started_at, collected_at, collection, scope, report=None):
    """Retain trusted collected rows, never accept uploaded metrics or write files."""
    if kind not in {'weekly', 'random'} or len(items) > COHORT_ROW_LIMIT:
        raise ValueError('This collection exceeds the in-memory analysis limit.')
    identity = uuid4().hex
    meta = {'collection_id': identity, **describe_collection(items, kind=kind, label=label,
            started_at=started_at, collected_at=collected_at, collection=collection, scope=scope)}
    from ascendant.web.serializers import sort_by_published_time
    with COHORT_LOCK:
        COHORTS[identity] = {'data': (deepcopy(sort_by_published_time(items)), label, len(items)), 'meta': meta,
                             'report': deepcopy({key: value for key, value in (report or {}).items()
                                                 if key not in {'videos', 'dataset'}})}
        while len(COHORTS) > COHORT_LIMIT:
            COHORTS.popitem(last=False)
    return deepcopy(meta)


def collection_entries():
    with COHORT_LOCK:
        return [deepcopy(entry['meta']) for entry in reversed(list(MISSION_COLLECTIONS.values()))] + [deepcopy(entry['meta']) for entry in reversed(list(COHORTS.values()))]


def creator_metadata():
    current = CURRENT
    if current and current.get('key', ())[:2] == context_key({})[:2]:
        return deepcopy(current['meta'])
    return None


def snapshot_collection(identity):
    """Copy exactly the requested RAM collection. Never fetch or write files."""
    if identity == 'creator':
        current = CURRENT
        if not current or current['key'][:2] != context_key({})[:2]:
            raise ValueError('No creator dataset is loaded for this creator and account. Fetch it on Data first.')
        metadata = deepcopy(current['meta'])
        metadata['scope'] = {'uid': metadata.get('uid'), 'selection': json.loads(current['key'][2])}
        return deepcopy(current['data'][0]), metadata
    with COHORT_LOCK:
        entry = (MISSION_COLLECTIONS.get(identity) or COHORTS.get(identity)) if isinstance(identity, str) else None
        if entry is None:
            raise ValueError('This collection has expired, was removed or the server restarted. Collect it again on Data, Sampling or Tasks.')
        return deepcopy(entry['data'][0]), deepcopy(entry['meta'])


def workspace_data():
    """Rebuild page data from RAM only, without fetching or exposing raw API fields."""
    from ascendant.web.serializers import serialize_video
    current = CURRENT  # A fetch atomically replaces this reference only on success.
    creator = None
    if current and current['key'][:2] == context_key({})[:2]:
        items, label, total = current['data']
        creator = {'dataset': {**deepcopy(current['meta']), 'reused': True},
                   'selection_controls': json.loads(current['key'][2]),
                   'selection': label, 'total_videos': total,
                   'videos': [serialize_video(item) for item in items]}
    reports = {}
    with COHORT_LOCK:
        for entry in reversed(list(COHORTS.values())):
            kind = entry['meta']['source_kind']
            if kind in reports or not entry.get('report'):
                continue
            reports[kind] = {**deepcopy(entry['report']), 'dataset': deepcopy(entry['meta']),
                'videos': [{**serialize_video(item), 'creator': (item.get('owner') or {}).get('name', 'Unknown')
                           if isinstance(item.get('owner'), dict) else 'Unknown'} for item in entry['data'][0]]}
    return {'creator': creator, 'reports': reports}


def context_key(payload):
    selected = selected_creator()
    return (str((selected or {}).get("uid")), accounts.active_account_id(),
            json.dumps(payload.get("selection") or {}, sort_keys=True))


async def acquire(payload, fetch):
    global CURRENT
    identity = payload.get('collection_id')
    if identity is not None and identity != '':
        if payload.get('refresh'):
            raise ValueError('Recollect this source on Sampling; Analysis only reuses its collected rows.')
        with COHORT_LOCK:
            entry = (MISSION_COLLECTIONS.get(identity) or COHORTS.get(identity)) if isinstance(identity, str) else None
            if entry is None:
                raise ValueError('This collection has expired, was removed or the server restarted. Collect it again on Data, Sampling or Tasks.')
            return deepcopy(entry['data']), {**deepcopy(entry['meta']), 'reused': True}
    key = context_key(payload)
    if not payload.get("refresh") and CURRENT is not None and CURRENT["key"] == key:
        return deepcopy(CURRENT["data"]), {**CURRENT["meta"], "reused": True}
    if payload.get("reuse_only") and not payload.get("refresh"):
        raise ValueError("Click Fetch / Refresh to load this Creator and selection into memory first.")
    started = datetime.now(timezone.utc).isoformat()
    items, label, total, collection = await fetch(payload)
    data = (items, label, total)
    if context_key(payload) != key:
        raise ValueError("Creator or account changed during collection. Fetch again.")
    meta = {"started_at": started, "collected_at": datetime.now(timezone.utc).isoformat(),
            "count": len(data[0]), "selection": data[1], "uid": key[0], "collection": collection,
            "source_kind": "creator", "source_label": f"Creator {(selected_creator() or {}).get('name') or key[0]}"}
    CURRENT = {"key": key, "data": deepcopy(data), "meta": meta}
    return data, {**meta, "reused": False}
