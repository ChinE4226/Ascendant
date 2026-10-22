"""A fetching Mac: outgoing connections, one active task, and RAM-only results."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import errno
import ipaddress
from functools import partial
from http.client import HTTPConnection, HTTPSConnection
from threading import Event, RLock, Thread
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from urllib.parse import urlparse

from ascendant.errors import public_error_message
from ascendant.distributed.fetch import fetch_unit
from ascendant.distributed.protocol import VERSION, CAPABILITIES, MAX_BODY, ProtocolError, coordinator_url, frequency
from ascendant.distributed.network import thunderbolt_address
from ascendant.memory import process_memory


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class CableHTTPHandler(HTTPHandler):
    def __init__(self, source):
        super().__init__()
        self.source = source

    def http_open(self, request):
        return self.do_open(partial(HTTPConnection, source_address=self.source), request)


class CableHTTPSHandler(HTTPSHandler):
    def __init__(self, source):
        super().__init__()
        self.source = source

    def https_open(self, request):
        return self.do_open(partial(HTTPSConnection, source_address=self.source), request,
                            context=self._context)


def request_json(base, path, data=None, token=''):
    body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if data is not None else None
    if body is not None and len(body) > MAX_BODY:
        raise ValueError('The result is too large for the node connection.')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(base + path, data=body, headers=headers, method='POST' if body is not None else 'GET')
    # Bind control traffic to the cable's local IP. This also avoids ambiguous
    # 169.254/16 routes when Wi-Fi or USB Ethernet has a self-assigned address.
    host = urlparse(base).hostname
    try:
        same_mac = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        same_mac = False
    try:
        source = None if same_mac else (thunderbolt_address(), 0)
    except ValueError as error:
        raise ProtocolError(str(error), 503) from None
    # Never use proxies, redirects or automatic Wi-Fi fallback for node traffic.
    opener = build_opener(ProxyHandler({}), NoRedirect(), CableHTTPHandler(source), CableHTTPSHandler(source))
    try:
        with opener.open(request, timeout=8) as response:
            result = json.loads(response.read(MAX_BODY + 1))
    except HTTPError as error:
        try:
            message = json.loads(error.read(4096)).get('error')
        except (ValueError, AttributeError):
            message = None
        finally:
            error.close()
        raise ProtocolError(message or f'Connection service returned HTTP {error.code}.', error.code) from None
    except URLError as error:
        reason = error.reason
        if getattr(reason, 'errno', None) in {errno.ECONNREFUSED, 10061}:
            message = f'Connection refused at {base}. Start node connections on the main Mac, then copy its actual connection address and port.'
        elif isinstance(reason, socket.gaierror):
            message = f'Cannot resolve {base}. Use the numeric Thunderbolt Bridge IP address shown in the main Mac Nodes page.'
        else:
            message = f'Cannot reach {base}: {reason}. Check the Thunderbolt cable/Bridge addresses and that the main service is running. Node traffic does not fall back to Wi-Fi.'
        raise ProtocolError(message, 503) from None
    if not isinstance(result, dict):
        raise ValueError('The connection service returned invalid JSON data.')
    return result


def request_health(base):
    try:
        result = request_json(base, '/node/health')
    except ProtocolError as error:
        if error.status == 404:
            raise ProtocolError('This address is not the node connection service. Copy the connection address from Workspace → Nodes, including its actual port.', 400) from None
        raise
    if result.get('protocol') != VERSION or result.get('service') != 'Bilibili fetching coordinator':
        raise ProtocolError('This address is not a compatible main Mac node connection service.', 400)
    if 'selection' not in result.get('capabilities', []):
        raise ProtocolError('Sync the latest source and restart the main app before pairing this node.', 409)
    return {'ok': True, 'url': base, 'protocol': VERSION, 'message': 'Main Mac connection service is reachable. Paste a fresh pairing code and connect.'}


class Worker:
    def __init__(self, fetcher=fetch_unit, transport=request_json, interval=2, health_checker=request_health):
        self.lock = RLock()
        self.stop_event, self.cancel_event = Event(), Event()
        self.fetcher, self.transport, self.interval = fetcher, transport, interval
        self.health_checker = health_checker
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='node-fetch')
        self.thread = None
        self.url = self.token = self.node_id = ''
        self.name = socket.gethostname()
        self.cookie = ''
        self.rate = 1.0
        self.enabled = True
        self.coordinator_enabled = True
        self.connection = 'disconnected'
        self.message = 'Enter the main Mac connection URL and a pairing code.'
        self.active = self.future = self.pending = None
        self.progress = 0
        self.completed = 0

    def start(self):
        self.thread = Thread(target=self.run, daemon=True, name='node-connection')
        self.thread.start()

    def connect(self, data):
        url = coordinator_url(data.get('url'))
        name = str(data.get('name') or self.name).strip()
        if not name or len(name) > 80:
            raise ValueError('Node name must contain 1–80 characters.')
        rate = frequency(data.get('rate', 1))
        code = str(data.get('code') or '').strip()
        if not code or len(code) > 100:
            raise ValueError('Paste a current pairing code from the main Mac.')
        cookie = str(data.get('cookie') or '').strip()
        if len(cookie) > 16384:
            raise ValueError('The local cookie is too long.')
        self.health_checker(url)
        with self.lock:
            if self.active or self.token:
                raise ValueError('Disconnect the current connection before pairing again.')
            # Pair while holding the lock: the polling loop cannot see partial credentials.
            paired = self.transport(url, '/node/pair', {'protocol': VERSION, 'capabilities': list(CAPABILITIES), 'name': name, 'code': code})
            if paired.get('protocol') != VERSION or not paired.get('node_id') or not paired.get('token'):
                raise ValueError('The connection service returned an invalid pairing response.')
            self.url, self.node_id, self.token = url, paired['node_id'], paired['token']
            self.name, self.rate, self.cookie = name, rate, cookie
            self.enabled, self.connection, self.message = True, 'connected', 'Paired. Waiting for a task.'
            return self.snapshot()

    def control(self, action):
        with self.lock:
            if action == 'disconnect':
                if self.active:
                    raise ValueError('Pause and let the current task finish before disconnecting.')
                self.url = self.token = self.node_id = self.cookie = ''
                self.connection, self.message = 'disconnected', 'Disconnected.'
            elif action in {'pause', 'resume'}:
                if not self.token:
                    raise ValueError('Connect to the main Mac first.')
                self.enabled = action == 'resume'
                self.message = 'Ready for tasks.' if self.enabled else 'Paused. Any current task will finish.'
            else:
                raise ValueError('Unknown node control.')
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return {'protocol': VERSION, 'name': self.name, 'url': self.url, 'node_id': self.node_id,
                    'connection': self.connection, 'enabled': self.enabled, 'rate': self.rate,
                    'coordinator_enabled': self.coordinator_enabled,
                    'message': self.message, 'progress': self.progress, 'completed': self.completed,
                    'active': {'label': self.active['label'], 'id': self.active['id']} if self.active else None,
                    'has_local_credential': bool(self.cookie), 'memory': process_memory()}

    def update_progress(self, value, message):
        with self.lock:
            self.progress, self.message = value, message

    def tick(self):
        with self.lock:
            if not self.token:
                return
            base, token = self.url, self.token
            active = self.active
            heartbeat = {'ready': self.enabled, 'message': self.message, 'progress': self.progress}
            if active:
                heartbeat.update(unit_id=active['id'], lease=active['lease'])
        heartbeat['memory'] = process_memory()
        try:
            heartbeat_result = self.transport(base, '/node/heartbeat', heartbeat, token)
            with self.lock:
                if self.token != token:
                    return
                self.connection = 'connected'
                self.coordinator_enabled = heartbeat_result.get('enabled', True)
                if heartbeat_result.get('resume_requested'):
                    self.enabled = True
                if not self.coordinator_enabled and self.active is None:
                    self.message = 'Paused by the main Mac. Resume this node there to accept more work.'
                if self.future and self.future.done() and self.pending is None:
                    try:
                        self.pending = ('/node/complete', self.future.result())
                    except Exception as exc:
                        self.pending = ('/node/fail', {'error': public_error_message(exc)})
                        self.enabled = False
                if self.pending:
                    path, payload = self.pending
                    payload = {**payload, 'unit_id': self.active['id'], 'lease': self.active['lease']}
                else:
                    path = None
            if path:
                self.transport(base, path, payload, token)
                with self.lock:
                    self.completed += int(path == '/node/complete')
                    self.message = 'Results delivered. Waiting for a task.' if path == '/node/complete' else payload['error']
                    self.active = self.future = self.pending = None
                    self.progress = 0
                return
            with self.lock:
                ready = self.enabled and self.active is None and heartbeat_result.get('enabled', True)
            if ready:
                response = self.transport(base, '/node/claim', {}, token)
                unit = response.get('unit')
                if unit:
                    with self.lock:
                        if self.token != token:
                            return
                        if unit.get('kind') not in CAPABILITIES or not isinstance(unit.get('payload'), dict):
                            raise ValueError('The main Mac sent an unsupported task.')
                        self.active = unit
                        self.cancel_event = Event()
                        cookie, rate, canceled = self.cookie, self.rate, self.cancel_event
                        self.message, self.progress = 'Starting ' + unit['label'], 0
                        self.future = self.executor.submit(lambda: asyncio.run(self.fetcher(unit, cookie, rate, canceled, self.update_progress)))
        except ProtocolError as exc:
            with self.lock:
                self.message = public_error_message(exc)
                if exc.status == 409 and self.active:
                    self.cancel_event.set()
                    # Drain the canceled fetch before claiming another unit.
                    self.pending = None
                    self.active['abandoned'] = True
                    if self.future is None or self.future.done():
                        self.active = self.future = None
                elif exc.status in {400, 401, 403, 404, 415}:
                    self.enabled = False
                    self.connection = 'pairing required' if exc.status == 401 else 'error'
                    self.cancel_event.set()
                    if self.active:
                        self.active['abandoned'] = True
                    if self.future is None or self.future.done():
                        self.active = self.future = self.pending = None
                    if exc.status == 401:
                        self.token = self.node_id = ''
        except (OSError, ValueError) as exc:
            with self.lock:
                self.connection = 'reconnecting'
                self.message = f'Connection interrupted; pending results stay in memory. {exc}'

    def run(self):
        while not self.stop_event.is_set():
            with self.lock:
                if self.active and self.active.get('abandoned') and (self.future is None or self.future.done()):
                    self.active = self.future = self.pending = None
            self.tick()
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        self.cancel_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        self.executor.shutdown(wait=True, cancel_futures=True)
