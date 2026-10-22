"""Connection addresses from local interfaces, without probing the internet."""

import ipaddress
import re
import socket
import subprocess
import sys
import time
from threading import Lock

_CACHE_LOCK = Lock()
_INTERFACE_CACHE = None


def _command_output(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _mac_interfaces():
    hardware = _command_output(['/usr/sbin/networksetup', '-listallhardwareports'])
    ports = dict((device, name) for name, device in re.findall(r'Hardware Port: ([^\n]+)\nDevice: ([^\n]+)', hardware))
    output = _command_output(['/sbin/ifconfig'])
    interfaces = []
    for block in re.split(r'(?=^[\w.]+:\s)', output, flags=re.MULTILINE):
        match = re.match(r'([\w.]+):', block)
        if not match or re.search(r'^\s+status: inactive\s*$', block, re.MULTILINE):
            continue
        interface = match[1]
        name = ports.get(interface, interface)
        if name == 'Thunderbolt Bridge':
            kind, label = 'thunderbolt', 'Thunderbolt Bridge'
        elif interface.startswith('bridge'):
            kind, label = 'direct', 'Direct bridge'
        elif name == 'Wi-Fi':
            kind, label = 'wifi', 'Wi-Fi'
        elif name.startswith('Ethernet'):
            kind, label = 'ethernet', name
        else:
            kind, label = 'network', name
        for address in re.findall(r'^\s+inet\s+(\d+\.\d+\.\d+\.\d+)\b', block, re.MULTILINE):
            ip = ipaddress.ip_address(address)
            # Self-assigned addresses are useful on an active direct cable, but
            # should not be advertised as a usable unconnected Wi-Fi address.
            if ip.is_link_local and kind not in {'thunderbolt', 'direct', 'ethernet'}:
                continue
            interfaces.append({'address': address, 'interface': interface, 'kind': kind, 'label': label})
    return interfaces


def _interfaces(*, fresh):
    global _INTERFACE_CACHE
    with _CACHE_LOCK:
        now = time.monotonic()
        if not fresh and _INTERFACE_CACHE and _INTERFACE_CACHE[0] == sys.platform and now - _INTERFACE_CACHE[1] < 3:
            return _INTERFACE_CACHE[2]
        interfaces = _mac_interfaces() if sys.platform == 'darwin' else []
        _INTERFACE_CACHE = (sys.platform, now, interfaces)
        return interfaces


def connection_addresses(port, host='0.0.0.0', *, fresh=True):
    """Label direct bridge addresses first; never change routing or network settings."""
    if host in {'127.0.0.1', 'localhost'}:
        return [{'url': f'http://127.0.0.1:{port}', 'interface': 'lo0', 'kind': 'local', 'label': 'This Mac'}]
    interfaces = list(_interfaces(fresh=fresh))
    seen = {entry['address'] for entry in interfaces}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in seen and not ipaddress.ip_address(address).is_link_local:
                interfaces.append({'address': address, 'interface': '', 'kind': 'network', 'label': 'Network'})
                seen.add(address)
    except OSError:
        pass
    entries = {}
    for entry in interfaces:
        ip = ipaddress.ip_address(entry['address'])
        if ip.is_loopback or ip.is_unspecified or host != '0.0.0.0' and str(ip) != host:
            continue
        entries.setdefault(str(ip), {'url': f'http://{ip}:{port}', **{key: entry[key] for key in ('interface', 'kind', 'label')}})
    priority = {'thunderbolt': 0, 'direct': 1}
    result = sorted(entries.values(), key=lambda entry: (priority.get(entry['kind'], 2), entry['url']))
    if host == '0.0.0.0':
        result.extend([{'url': f'http://{socket.gethostname()}:{port}', 'interface': '', 'kind': 'hostname', 'label': 'Hostname · route may vary'},
                       {'url': f'http://127.0.0.1:{port}', 'interface': 'lo0', 'kind': 'local', 'label': 'This Mac'}])
    return result


def connection_urls(port, host='0.0.0.0'):
    return [entry['url'] for entry in connection_addresses(port, host)]


def thunderbolt_address():
    """Require an active macOS Thunderbolt Bridge instead of falling back to Wi-Fi."""
    for entry in connection_addresses(0, fresh=False):
        if entry['kind'] == 'thunderbolt':
            return entry['url'].split('://', 1)[1].rsplit(':', 1)[0]
    raise ValueError('No active Thunderbolt Bridge IPv4 address. Connect the Thunderbolt cable and check System Settings → Network → Thunderbolt Bridge.')
