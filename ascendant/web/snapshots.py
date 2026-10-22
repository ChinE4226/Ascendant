"""Explicit snapshots of normal creator datasets and samples."""

from datetime import datetime, timezone

from ascendant import tracking
from ascendant.web import dataset
from ascendant.web.progress import set_progress


class SnapshotBusy(ValueError):
    pass


async def capture_collection(payload):
    """Explicit loaded/fresh collection snapshots; never save unrelated RAM rows."""
    mode = payload.get('mode')
    if mode not in ('loaded', 'fresh'):
        raise ValueError('Choose Save fetched data or Fetch new data.')
    identity = payload.get('collection_id')
    if mode == 'loaded':
        if not dataset.LOCK.acquire(blocking=False):
            raise SnapshotBusy('Another video operation is running. Try again when it finishes.')
        try:
            items, metadata = dataset.snapshot_collection(identity)
            expected = payload.get('expected_collected_at')
            if expected and expected != metadata.get('collected_at'):
                raise ValueError('The loaded collection changed. Review the current data before saving it.')
        finally:
            dataset.LOCK.release()
    elif identity == 'creator':
        if not dataset.LOCK.acquire(blocking=False):
            raise SnapshotBusy('Another video operation is running. Try again when it finishes.')
        try:
            from ascendant.web.distributed_dataset import fetch_workspace_items
            creator = dataset.selected_creator()
            if creator is None:
                raise ValueError('Choose a Creator first.')
            key = dataset.context_key({})[:2]
            started = datetime.now(timezone.utc).isoformat()
            set_progress('Fetching a new creator collection for a snapshot.', running=True, percent=0)
            items, label, total, collection = await fetch_workspace_items(payload)
            if dataset.context_key({})[:2] != key:
                raise ValueError('Creator or account changed during collection. Try again.')
            metadata = {'source_kind': 'creator', 'source_label': f"Creator {creator.get('name') or creator['uid']}",
                'selection': label, 'uid': str(creator['uid']), 'started_at': started,
                'collected_at': datetime.now(timezone.utc).isoformat(), 'count': len(items),
                'collection': collection, 'scope': {'uid': str(creator['uid']), 'selection': payload.get('selection') or {}}}
        except Exception:
            set_progress('Snapshot collection failed. No collection was saved.', running=False)
            raise
        else:
            set_progress('Snapshot collection fetched.', running=False, percent=100)
        finally:
            dataset.LOCK.release()
    else:
        # Only reuse the selected source's recipe. Its old video rows are not saved.
        _, previous = dataset.snapshot_collection(identity)
        if previous['source_kind'] == 'weekly':
            from ascendant.web.weekly import fetch_weekly_analysis
            items, metadata = await fetch_weekly_analysis({'source': previous['scope']['number']}, snapshot_only=True)
        elif previous['source_kind'] == 'random':
            from ascendant.web.sampling import fetch_random_sample
            items, metadata = await fetch_random_sample(previous['scope'], snapshot_only=True)
        elif previous['source_kind'] == 'creator':
            from ascendant.web.creators import CREATOR_OVERRIDE
            token = CREATOR_OVERRIDE.set({'uid': previous['uid'], 'name': previous.get('creator_name') or previous['source_label']})
            try:
                return await capture_collection({**payload, 'collection_id': 'creator',
                    'selection': previous['scope']['selection'], 'fetch_source': previous['scope'].get('fetch_source', 'auto')})
            finally:
                CREATOR_OVERRIDE.reset(token)
        else:
            raise ValueError('Choose a creator dataset or a sampling collection.')
    batch = tracking.save_collection(items, metadata, mode=mode)
    return {'batch': batch}
