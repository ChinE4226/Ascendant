"""One coordinator, leased work units, and a separate authenticated node listener."""

from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import secrets
import socket
from threading import RLock, Thread
import time

from ascendant.distributed.protocol import (VERSION, CAPABILITIES, MAX_BODY, ProtocolError,
    integer, frequency, json_request, timestamp, video_ids, wire_video, workspace_selection)
from ascendant.distributed.network import connection_addresses, thunderbolt_address
from ascendant.distributions import has_complete_metrics
from ascendant.weekly import summarize_weekly_items


class Coordinator:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = RLock()
        self.nodes, self.jobs = {}, {}
        self.server = None
        self.thread = None
        self.pair_code = ""
        self.pair_expires = 0
        self.urls = []
        self.lease_seconds = 60

    def start(self, port=8010, host=None):
        with self.lock:
            if self.server is not None:
                raise ValueError("Node connections are already running.")
            if host is None:
                host = thunderbolt_address()
            self.server = NodeServer((host, port), NodeHandler)
            self.server.coordinator = self
            self.thread = Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            port = self.server.server_port
            self.urls = [entry['url'] for entry in connection_addresses(port, host)]
            self.new_pairing()
            return self.snapshot()

    def stop(self):
        with self.lock:
            server, thread = self.server, self.thread
            self.server = self.thread = None
            self.pair_code, self.pair_expires = "", 0
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def new_pairing(self):
        with self.lock:
            if self.server is None:
                raise ValueError("Start node connections before creating a pairing code.")
            self.pair_code = secrets.token_urlsafe(24)
            self.pair_expires = self.clock() + 300
            return {"code": self.pair_code, "expires_at": timestamp(self.pair_expires)}

    def _expire(self):
        now = self.clock()
        for job in self.jobs.values():
            for unit in job['units']:
                if unit['state'] == 'running' and unit['expires'] <= now:
                    self._release(unit)
                    if unit['attempts'] >= 3:
                        self._fail_job(job, unit, 'The node disconnected on three attempts.')

    def _fail_job(self, job, unit, error):
        unit.update(state='failed', error=error)
        node = self.nodes.get(unit.get('node_id'))
        if node:
            node.update(unit_id=None, ready=False, message=error)
        for other in job['units']:
            if other is not unit and other['state'] in {'queued', 'running'}:
                self._release(other)
                other['state'] = 'canceled'
        job['finished_at'] = timestamp(self.clock())

    def _release(self, unit):
        node = self.nodes.get(unit.get('node_id'))
        if node and node.get('unit_id') == unit['id']:
            node['unit_id'] = None
        unit.update(state='queued', node_id=None, lease=None, expires=0, progress=0)

    def pair(self, data):
        with self.lock:
            if data.get('protocol') != VERSION:
                raise ProtocolError("The node and main app use different protocol versions.", 409)
            code = str(data.get('code') or '')
            if not self.pair_code or self.clock() >= self.pair_expires or not secrets.compare_digest(code.encode(), self.pair_code.encode()):
                raise ProtocolError("Pairing code expired or was already used. Create a new code on the main Mac.", 401)
            if len(self.nodes) >= 20:
                raise ValueError("At most 20 nodes can be paired. Remove an unused node first.")
            name = str(data.get('name') or '').strip()
            if not name or len(name) > 80:
                raise ValueError("Node name must contain 1–80 characters.")
            capabilities = data.get('capabilities')
            if not isinstance(capabilities, list) or not capabilities or any(c not in CAPABILITIES for c in capabilities):
                raise ValueError("The node has no supported fetching capabilities.")
            token, node_id = secrets.token_urlsafe(32), secrets.token_hex(12)
            self.nodes[node_id] = {'id': node_id, 'name': name, 'token_hash': hashlib.sha256(token.encode()).hexdigest(),
                'capabilities': capabilities, 'enabled': True, 'ready': True, 'last_seen': self.clock(),
                'unit_id': None, 'message': 'Paired.', 'progress': 0}
            self.pair_code, self.pair_expires = '', 0
            return {'protocol': VERSION, 'node_id': node_id, 'token': token, 'name': name}

    def dispatch(self, path, token, data):
        with self.lock:
            self._expire()
            digest = hashlib.sha256(str(token).encode()).hexdigest()
            node = next((n for n in self.nodes.values() if secrets.compare_digest(n['token_hash'], digest)), None)
            if node is None:
                raise ProtocolError("Node pairing is no longer valid. Pair again on the main Mac.", 401)
            node['last_seen'] = self.clock()
            if path == '/node/heartbeat':
                node.update(ready=data.get('ready') is True, message=str(data.get('message') or '')[:300])
                memory = data.get('memory')
                rss = memory.get('rss_bytes') if isinstance(memory, dict) else None
                valid_rss = type(rss) is int and 0 <= rss <= 2**63 - 1
                node.update(rss_bytes=rss if valid_rss else None,
                            memory_received_at=self.clock() if valid_rss else None)
                if data.get('unit_id'):
                    _, unit = self._lease(node, data, allow_done=True)
                    if unit['state'] == 'running':
                        unit['expires'] = self.clock() + self.lease_seconds
                        progress = data.get('progress', 0)
                        unit['progress'] = min(100, max(0, progress)) if isinstance(progress, (int, float)) and math.isfinite(progress) else 0
                    node['progress'] = unit['progress']
                resume = node.pop('resume_requested', False)
                return {'ok': True, 'enabled': node['enabled'], 'resume_requested': resume}
            if path == '/node/claim':
                if not node['enabled'] or not node['ready'] or node['unit_id']:
                    return {'unit': None}
                for job in self.jobs.values():
                    if job.get('canceled') or any(u['state'] == 'failed' for u in job['units']):
                        continue
                    if job['targets'] and node['id'] not in job['targets']:
                        continue
                    if job['kind'] not in node['capabilities']:
                        continue
                    for unit in job['units']:
                        if unit['state'] == 'queued':
                            unit.update(state='running', node_id=node['id'], lease=secrets.token_urlsafe(24),
                                        node_name=node['name'],
                                        expires=self.clock() + self.lease_seconds, attempts=unit['attempts'] + 1)
                            node.update(unit_id=unit['id'], progress=0)
                            job['started_at'] = job['started_at'] or timestamp(self.clock())
                            return {'unit': {'id': unit['id'], 'job_id': job['id'], 'label': job['label'],
                                    'kind': job['kind'], 'lease': unit['lease'], 'payload': deepcopy(unit['payload'])}}
                return {'unit': None}
            if path in {'/node/complete', '/node/fail'}:
                job, unit = self._lease(node, data, allow_done=True)
                if unit['state'] == 'done':
                    return {'ok': True, 'duplicate': True}
                if path == '/node/fail':
                    self._fail_job(job, unit, str(data.get('error') or 'Node collection failed.')[:500])
                else:
                    rows = data.get('items')
                    if not isinstance(rows, list) or len(rows) > 2500:
                        raise ValueError("A node result must contain at most 2,500 examined videos.")
                    rows = [wire_video(row) for row in rows]
                    identities = [row['bvid'] for row in rows]
                    if len(set(identities)) != len(identities):
                        raise ValueError("The node result contains repeated video identities.")
                    if job['kind'] == 'videos' and set(identities) != set(unit['payload']['bvids']):
                        raise ValueError("The node result does not match its assigned video IDs.")
                    if job['kind'] == 'creator':
                        if any(row['owner']['mid'] != unit['payload']['uid'] for row in rows if has_complete_metrics(row)):
                            raise ValueError("The node returned videos from a different creator.")
                        if sum(has_complete_metrics(row) for row in rows) > unit['payload']['count']:
                            raise ValueError("The node returned more valid videos than requested.")
                    if job['kind'] == 'selection':
                        if any(not has_complete_metrics(row) or row['owner']['mid'] != unit['payload']['uid'] for row in rows):
                            raise ValueError('The node returned invalid metrics or another Creator for this dataset.')
                        if job['requested'] and len(rows) > job['requested']:
                            raise ValueError('The node returned more valid videos than requested.')
                        collection = data.get('collection')
                        if not isinstance(collection, dict):
                            raise ValueError('The node returned no collection summary.')
                        clean = {}
                        for key in ('examined', 'skipped_invalid', 'skipped_duplicates', 'shortfall'):
                            number = collection.get(key, 0)
                            if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 100000:
                                raise ValueError('The node returned an invalid collection summary.')
                            clean[key] = number
                        clean.update(requested=job['requested'] or None, limited=collection.get('limited') is True)
                        clean['shortfall'] = max(0, job['requested'] - len(rows))
                        total = data.get('source_total')
                        if isinstance(total, bool) or not isinstance(total, int) or total < len(rows):
                            raise ValueError('The node returned an invalid Creator video count.')
                        unit.update(collection=clean, selection_label=str(data.get('selection_label') or 'Remote selection')[:500])
                    unit.update(state='done', items=rows, progress=100, source_total=data.get('source_total'))
                    node.update(unit_id=None, progress=100, message='Results delivered.')
                if all(u['state'] in {'done', 'failed', 'canceled'} for u in job['units']):
                    job['finished_at'] = timestamp(self.clock())
                return {'ok': True}
            raise ProtocolError("Unknown node endpoint.", 404)

    def _lease(self, node, data, allow_done=False):
        unit_id = data.get('unit_id')
        for job in self.jobs.values():
            for unit in job['units']:
                if unit['id'] == unit_id:
                    if (unit.get('node_id') == node['id'] and unit.get('lease')
                            and secrets.compare_digest(str(data.get('lease') or '').encode(), unit['lease'].encode())
                            and (unit['state'] == 'running' or allow_done and unit['state'] == 'done')
                            and not job.get('canceled')):
                        return job, unit
                    raise ProtocolError("This task lease expired or was canceled. Discard its results.", 409)
        raise ProtocolError("Task lease was not found.", 409)

    def create_job(self, data):
        kind = data.get('kind') or 'videos'
        if kind == 'videos':
            ids = video_ids(data.get('videos'))
            size = integer(data.get('batch_size', 20), 'Detail batch size', 20)
            payloads = [{'bvids': ids[i:i + size]} for i in range(0, len(ids), size)]
            requested, label = len(ids), f"Fetch {len(ids)} video(s)"
        elif kind == 'creator':
            uid = str(integer(data.get('uid'), 'Creator UID', 10**15))
            count = integer(data.get('count', 100), 'Valid video count')
            start = integer(data.get('start', 1), 'Start position', 1000000)
            payloads = [{'uid': uid, 'count': count, 'start': start}]
            requested, label = count, f"Creator {uid} · {count} valid videos"
        elif kind == 'selection':
            uid = str(integer(data.get('uid'), 'Creator UID', 10**15))
            selection = workspace_selection(data.get('selection'))
            requested = selection['end'] - selection['start'] + 1 if selection['kind'] == 'position' else 0
            payloads = [{'uid': uid, 'selection': selection}]
            label = f"Dataset · Creator {uid} · {selection['kind']}"
        else:
            raise ValueError("Choose video links or creator collection.")
        if data.get('request_rate') is not None:
            rate = frequency(data['request_rate'])
            participants = integer(data.get('participants', 1), 'Participating Macs', 21)
            for payload in payloads:
                payload.update(request_rate=rate, participants=participants)
        targets = data.get('targets') or []
        if not isinstance(targets, list) or any(not isinstance(t, str) for t in targets):
            raise ValueError("Select valid target nodes.")
        with self.lock:
            self._expire()
            if len(self.jobs) >= 30:
                raise ValueError("Thirty tasks are already in memory. Clear completed tasks before adding another.")
            if any(t not in self.nodes for t in targets):
                raise ValueError("A selected node was removed. Refresh the node list.")
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = {'id': job_id, 'kind': kind, 'label': label, 'targets': list(dict.fromkeys(targets)),
                'requested': requested, 'created_at': timestamp(self.clock()), 'started_at': None, 'finished_at': None,
                'units': [{'id': secrets.token_hex(12), 'state': 'queued', 'attempts': 0, 'payload': p,
                           'progress': 0, 'items': []} for p in payloads]}
            return self._job_summary(self.jobs[job_id])

    def append_video_units(self, job_id, data):
        """Add the next detail wave to one bounded collection; retain leases for delivery retries."""
        ids = video_ids(data.get('videos'))
        size = integer(data.get('batch_size', 20), 'Detail batch size', 20)
        rate = frequency(data['request_rate'])
        participants = integer(data.get('participants', 1), 'Participating Macs', 21)
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['kind'] != 'videos' or self._job_summary(job)['state'] != 'completed':
                raise ValueError('The previous parallel detail wave has not completed.')
            assigned = {bvid for unit in job['units'] for bvid in unit['payload']['bvids']}
            if assigned.intersection(ids) or len(assigned) + len(ids) > 2500:
                raise ValueError('Parallel collection must contain at most 2,500 unique remote candidates.')
            targets = data.get('targets')
            if not isinstance(targets, list) or any(target not in self.nodes for target in targets):
                raise ValueError('A parallel fetching node was removed.')
            job['targets'] = list(dict.fromkeys(targets))
            job['requested'] += len(ids)
            job['finished_at'] = None
            job['label'] = f"Parallel dataset details · {job['requested']} remote candidates"
            job['units'].extend({'id': secrets.token_hex(12), 'state': 'queued', 'attempts': 0,
                                'payload': {'bvids': ids[i:i + size], 'request_rate': rate, 'participants': participants},
                                'progress': 0, 'items': []} for i in range(0, len(ids), size))
            return self._job_summary(job)

    def raw_video_result(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['kind'] != 'videos' or self._job_summary(job)['state'] != 'completed':
                raise ValueError('The parallel detail wave has not completed.')
            return deepcopy([item for unit in job['units'] for item in unit['items']])

    def cancel_unstarted_selection(self, job_id):
        """Cancel only work never claimed, atomically with node assignment."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['kind'] != 'selection' or job['started_at'] is not None or job.get('canceled'):
                return False
            self.task_action(job_id, 'cancel')
            return True

    def node_action(self, node_id, action):
        with self.lock:
            node = self.nodes.get(node_id)
            if node is None:
                raise ValueError("Node was not found.")
            if action in {'pause', 'resume'}:
                node['enabled'] = action == 'resume'
                node['resume_requested'] = action == 'resume'
            elif action == 'remove':
                for job in self.jobs.values():
                    # Keep explicit targets: removing the only target must not redirect the task.
                    for unit in job['units']:
                        if unit.get('node_id') == node_id and unit['state'] == 'running':
                            self._release(unit)
                del self.nodes[node_id]
            else:
                raise ValueError("Unknown node action.")

    def task_action(self, job_id, action):
        with self.lock:
            self._expire()
            if action == 'clear':
                self.jobs = {key: job for key, job in self.jobs.items()
                             if self._job_summary(job)['state'] in {'queued', 'running'}}
                return
            job = self.jobs.get(job_id)
            if job is None:
                raise ValueError("Task was not found.")
            if action != 'cancel':
                raise ValueError("Unknown task action.")
            job['canceled'] = True
            job['finished_at'] = timestamp(self.clock())
            for unit in job['units']:
                if unit['state'] in {'running', 'queued'}:
                    self._release(unit)
                    unit['state'] = 'canceled'

    def _job_summary(self, job):
        states = [unit['state'] for unit in job['units']]
        state = ('canceled' if job.get('canceled') else 'failed' if 'failed' in states
                 else 'completed' if all(s == 'done' for s in states) else 'running' if 'running' in states else 'queued')
        items = [item for unit in job['units'] for item in unit['items']]
        valid = sum(has_complete_metrics(item) for item in items)
        return {key: deepcopy(job[key]) for key in ('id', 'kind', 'label', 'requested', 'created_at', 'started_at', 'finished_at')} | {
            'state': state, 'units': len(states), 'done': states.count('done'), 'included': valid,
            'invalid': len(items) - valid, 'shortfall': max(0, job['requested'] - valid),
            'error': next((u.get('error') for u in job['units'] if u.get('error')), ''),
            'progress': round(sum(u['progress'] for u in job['units']) / len(states)), 'targets': job['targets']}

    def result(self, job_id):
        with self.lock:
            self._expire()
            job = self.jobs.get(job_id)
            if job is None:
                raise ValueError("Task was not found.")
            items = [deepcopy(item) for unit in job['units'] for item in unit['items']]
            valid, report = summarize_weekly_items(items)
            from ascendant.web.serializers import serialize_video
            return {**report, 'task': self._job_summary(job), 'started_at': job['started_at'] or job['created_at'],
                    'collected_at': job['finished_at'] or timestamp(self.clock()),
                    'videos': [{**serialize_video(row), 'creator': row['owner']['name']} for row in valid],
                    'nodes': sorted({u.get('node_name', 'Unknown node') for u in job['units'] if u['items']})}

    def working_result(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['kind'] != 'selection' or self._job_summary(job)['state'] != 'completed':
                raise ValueError('The remote dataset is not completed.')
            unit = job['units'][0]
            collection = {**deepcopy(unit['collection']), 'node_name': unit.get('node_name', 'Fetching node')}
            return deepcopy(unit['items']), unit['selection_label'], unit['source_total'], collection

    def snapshot(self):
        # Refresh hot-plugged cable addresses without restarting the listener or
        # losing RAM pairings/tasks. Interface discovery uses a short RAM cache.
        server = self.server
        addresses = connection_addresses(server.server_port, getattr(server, 'server_address', ('0.0.0.0',))[0], fresh=False) if server else []
        with self.lock:
            self._expire()
            now = self.clock()
            port = self.server.server_port if self.server else None
            nodes = []
            for node in self.nodes.values():
                status = ('offline' if not self.server or now - node['last_seen'] > 20 else 'busy' if node['unit_id']
                          else 'paused' if not node['enabled'] or not node['ready'] else 'idle')
                nodes.append({key: deepcopy(node[key]) for key in ('id', 'name', 'enabled', 'ready', 'capabilities', 'unit_id', 'message', 'progress')} |
                             {'status': status, 'last_seen': timestamp(node['last_seen']),
                              'rss_bytes': node.get('rss_bytes'),
                              'memory_received_at': timestamp(node['memory_received_at']) if node.get('memory_received_at') is not None else None})
            self.urls = [entry['url'] for entry in addresses] if self.server is server else []
            return {'protocol': VERSION, 'running': bool(self.server), 'port': port, 'urls': self.urls if port else [],
                'addresses': addresses if port and self.server is server else [],
                'pairing': {'code': self.pair_code, 'expires_at': timestamp(self.pair_expires)} if self.pair_code and now < self.pair_expires else None,
                'nodes': nodes, 'tasks': [self._job_summary(job) for job in reversed(list(self.jobs.values()))]}


class NodeServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


class NodeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/node/health':
            self.send_json({'protocol': VERSION, 'service': 'Bilibili fetching coordinator', 'capabilities': list(CAPABILITIES)})
        else:
            self.send_json({'error': 'Not found.'}, 404)

    def do_POST(self):
        try:
            if self.path not in {'/node/pair', '/node/heartbeat', '/node/claim', '/node/complete', '/node/fail'}:
                raise ProtocolError('Not found.', 404)
            json_request(self)
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= MAX_BODY:
                raise ValueError('Node request body is too large or empty.')
            self.connection.settimeout(10)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Use a JSON object for node requests.')
            if self.path == '/node/pair':
                result = self.server.coordinator.pair(data)
            else:
                authorization = self.headers.get('Authorization', '')
                token = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
                result = self.server.coordinator.dispatch(self.path, token, data)
            self.send_json(result)
        except (ValueError, OSError) as exc:
            self.send_json({'error': str(exc)}, getattr(exc, 'status', 400))


COORDINATOR = Coordinator()
