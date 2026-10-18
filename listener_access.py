"""Passwordless listening only for verified tailnet peers and direct localhost."""
import ipaddress
import json
import subprocess
import threading
import time

_lock = threading.Lock()
_snapshot = ({}, 0)


def _tailnet_snapshot():
    global _snapshot
    with _lock:
        if time.monotonic() < _snapshot[1]:
            return _snapshot[0]
        result = {}
        try:
            status = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=3)
            data = json.loads(status.stdout)
            if status.returncode == 0 and data.get('BackendState') == 'Running':
                nodes = [data.get('Self') or {}, *(data.get('Peer') or {}).values()]
                result = {'ips': {ip for node in nodes for ip in node.get('TailscaleIPs', [])},
                          'dns': (data.get('Self', {}).get('DNSName') or '').rstrip('.').lower()}
                serve = subprocess.run(['tailscale', 'serve', 'status', '--json'], capture_output=True, text=True, timeout=3)
                config = json.loads(serve.stdout or '{}')
                result['web'] = config.get('Web') or {}
                result['funnel'] = any((config.get('AllowFunnel') or {}).values())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        _snapshot = (result, time.monotonic() + 30)
        return result


def _loopback(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return ip == 'localhost'


def is_passwordless_listener(request, client_ip):
    peer = request.client.host if request.client else ''
    host = (request.url.hostname or '').rstrip('.').lower()
    forwarded = any(request.headers.get(name) for name in (
        'x-forwarded-for', 'forwarded', 'x-real-ip', 'x-forwarded-host', 'tailscale-user-login'))
    # Refuse a rewritten/spoofed ASGI peer. The supported server launcher
    # disables Uvicorn's proxy rewriting so the actual socket peer is retained.
    if forwarded and not _loopback(peer):
        return False
    if _loopback(peer) and _loopback(host) and _loopback(client_ip):
        return True
    try:
        ip = ipaddress.ip_address(peer)
        candidate = (ip.version == 4 and ip in ipaddress.ip_network('100.64.0.0/10')) or (
            ip.version == 6 and ip in ipaddress.ip_network('fd7a:115c:a1e0::/48'))
    except ValueError:
        candidate = False
    if not candidate and not (_loopback(peer) and host.endswith('.ts.net')):
        return False
    snapshot = _tailnet_snapshot()
    if candidate:
        return peer in snapshot.get('ips', set())
    # A local Serve proxy must match the actual tailnet hostname and a known
    # peer. Forwarded headers from arbitrary proxies never grant this access.
    if host != snapshot.get('dns') or snapshot.get('funnel', True):
        return False
    forwarded = request.headers.get('x-forwarded-for', '').split(',')
    if len(forwarded) != 1 or forwarded[0].strip() not in snapshot.get('ips', set()):
        return False
    for address, site in snapshot.get('web', {}).items():
        if address.split(':')[0].rstrip('.').lower() != host:
            continue
        for handler in site.get('Handlers', {}).values():
            proxy = handler.get('Proxy', '').rstrip('/')
            if proxy in {f'http://127.0.0.1:{request.url.port or 8765}',
                         'http://127.0.0.1:8765', 'http://localhost:8765'}:
                return True
    return False


def request_origin(request, passwordless=None):
    if passwordless is None:
        passwordless = is_passwordless_listener(request, 'proxied:check')
    peer = request.client.host if request.client else ''
    if (passwordless and _loopback(peer) and (request.url.hostname or '').endswith('.ts.net')
            and request.headers.get('x-forwarded-proto') == 'https'):
        return 'https://' + request.headers['host']
    return str(request.base_url).rstrip('/')
