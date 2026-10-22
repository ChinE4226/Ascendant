"""Longitudinal analysis of one video's observations; no cohort statistics or I/O."""

from collections import deque
from datetime import datetime

METRICS = ('views', 'likes', 'coins', 'favorites', 'replies', 'shares', 'danmaku')


def analyse_observations(rows, metric='views', *, point_limit=None):
    if metric not in METRICS:
        raise ValueError('Choose a video metric for tracking analysis.')
    if point_limit is not None and (isinstance(point_limit, bool) or not isinstance(point_limit, int) or point_limit < 1):
        raise ValueError('Tracking point limit must be a positive integer.')
    points, previous, baseline, previous_rate = deque(maxlen=point_limit), None, None, None
    corrections = missing = observations = 0
    for row in rows:
        timestamp = datetime.fromisoformat(row['collected_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError('Tracking observation times must include a timezone.')
        if previous and timestamp < previous['time']:
            raise ValueError('Tracking observations must be ordered by collection time.')
        if baseline is None:
            baseline = {'time': timestamp, 'collected_at': row['collected_at'], 'value': row.get(metric)}
        observations += 1
        value = row.get(metric)
        missing += value is None
        seconds = (timestamp - previous['time']).total_seconds() if previous else None
        delta = value - previous['value'] if previous and value is not None and previous['value'] is not None else None
        rate = delta * 3600 / seconds if delta is not None and seconds > 0 else None
        correction = delta is not None and delta < 0
        corrections += correction
        comparable_rate = None if correction else rate
        published = row.get('published_at')
        points.append({'id': row['id'], 'collected_at': row['collected_at'], 'value': value,
            'elapsed_hours': (timestamp - baseline['time']).total_seconds() / 3600,
            'video_age_hours': (timestamp.timestamp() - published) / 3600 if published is not None else None,
            'interval_hours': seconds / 3600 if seconds is not None else None,
            'delta': delta, 'rate_per_hour': rate,
            'growth_percent': 100 * delta / previous['value'] if delta is not None and previous['value'] > 0 else None,
            'rate_change_per_hour': comparable_rate - previous_rate if comparable_rate is not None and previous_rate is not None else None,
            'counter_decreased': correction, 'source': row['source']})
        previous, previous_rate = {'time': timestamp, 'value': value}, comparable_rate
    latest = points[-1] if points else None
    net = latest['value'] - baseline['value'] if latest and latest['value'] is not None and baseline['value'] is not None else None
    hours = latest['elapsed_hours'] if latest else None
    return {'metric': metric, 'points': list(points), 'summary': {
        'observations': observations, 'baseline_at': baseline['collected_at'] if baseline else None,
        'latest_at': latest['collected_at'] if latest else None,
        'baseline_value': baseline['value'] if baseline else None, 'latest_value': latest['value'] if latest else None,
        'net_change': net, 'elapsed_hours': hours,
        'average_per_hour': net / hours if net is not None and hours > 0 else None,
        'latest_per_hour': latest['rate_per_hour'] if latest else None,
        'counter_decreases': corrections, 'missing_observations': missing}}
