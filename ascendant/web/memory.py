"""Bounded dataset estimates and process memory for the dashboard."""

import sys

from ascendant.memory import process_memory
from ascendant.web import dataset, missions, plots
from ascendant.distributed.coordinator import COORDINATOR
from ascendant.distributed.protocol import timestamp

SAMPLE_ROWS = 100


def _object_size(value, seen):
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_object_size(key, seen) + _object_size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple)):
        count = min(len(value), SAMPLE_ROWS)
        if count:
            # Evenly sample large lists, so diagnostics do not copy entire datasets.
            indices = range(count) if count == len(value) else (index * (len(value) - 1) // (count - 1) for index in range(count))
            size += round(sum(_object_size(value[index], seen) for index in indices) * len(value) / count)
    return size


def _collection(identity, entry):
    meta = entry['meta']
    return {'id': identity, 'label': meta.get('source_label') or meta.get('selection') or 'Creator dataset',
            'kind': 'mission' if meta.get('mission_id') else meta.get('source_kind', 'creator'),
            'videos': len(entry['data'][0]),
            'estimated_bytes': _object_size({'data': entry['data'], 'meta': meta}, set())}


def overview():
    server = process_memory()
    # Stored entries are replaced atomically; take references, never duplicate rows.
    current = dataset.CURRENT
    with dataset.COHORT_LOCK:
        sources = list(dataset.COHORTS.items()) + list(dataset.MISSION_COLLECTIONS.items())
    collections = ([_collection('creator', current)] if current is not None else [])
    collections += [_collection(identity, entry) for identity, entry in sources]
    with missions.LOCK:
        mission_count = len(missions.MISSIONS)
    with plots._plot_lock:
        chart_count = len(plots._pending_plots)
    with COORDINATOR.lock:
        now = COORDINATOR.clock()
        nodes = [{'id': node['id'], 'name': node['name'], 'rss_bytes': node.get('rss_bytes'),
                  'sampled_at': timestamp(node['memory_received_at']) if node.get('memory_received_at') is not None else None,
                  'stale': not COORDINATOR.server or node.get('memory_received_at') is None or now - node['memory_received_at'] > 20}
                 for node in COORDINATOR.nodes.values()]
        node_task_count = len(COORDINATOR.jobs)
    return {'server': server, 'collections': collections,
            'dataset_estimated_bytes': sum(row['estimated_bytes'] for row in collections),
            'retained_videos': sum(row['videos'] for row in collections),
            'cache_counts': {'missions': mission_count, 'unsaved_charts': chart_count, 'node_tasks': node_task_count},
            'nodes': nodes}
