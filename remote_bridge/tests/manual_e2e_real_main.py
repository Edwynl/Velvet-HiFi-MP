"""Manual E2E: the real Velvet main server.py upstream -> bridge -> fake DLNA renderer.

Not run by unittest discovery. From the repository root:
    venv\\Scripts\\python.exe -I remote_bridge/tests/manual_e2e_real_main.py <repo root> <venv python>
Needs ffmpeg (FFMPEG env var or PATH); uses a temporary library and port 18765.
"""
import hashlib, http.client, ipaddress, json, os, re, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

WT = Path(sys.argv[1])
sys.path.insert(0, str(WT / 'remote_bridge'))
import bridge  # noqa

PY = sys.argv[2]
FFMPEG = os.environ.get('FFMPEG', 'ffmpeg')
PORT = 18765
tmp = Path(tempfile.mkdtemp(prefix='velvet-e2e-'))
music = tmp / 'music' / '徐佳莹' / 'E2E Album'
music.mkdir(parents=True)
flac = music / '01 测试曲.flac'
subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=997:sample_rate=96000:duration=20',
                '-c:a', 'flac', '-sample_fmt', 's32', '-bits_per_raw_sample', '24',
                '-metadata', 'artist=徐佳莹', '-metadata', 'album=E2E Album', '-metadata', 'title=测试曲',
                '-metadata', 'track=1', str(flac)], check=True)
SRC = flac.read_bytes()
SHA = hashlib.sha256(SRC).hexdigest()
print('source', len(SRC), 'bytes')

env = {**os.environ, 'VELVET_DATA_DIR': str(tmp / 'data'), 'VELVET_MUSIC_DIR': str(tmp / 'music'),
       'VELVET_PORT': str(PORT), 'PYTHONIOENCODING': 'utf-8'}
log = open(tmp / 'server.log', 'wb')
srv = subprocess.Popen([PY, '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', str(PORT),
                        '--no-proxy-headers'], cwd=WT, env=env, stdout=log, stderr=subprocess.STDOUT)
results = []
def check(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))

try:
    up = f'http://127.0.0.1:{PORT}'
    for _ in range(120):
        try:
            urlopen(up + '/api/health', timeout=2).read(); break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit('server did not start; see ' + str(tmp / 'server.log'))
    urlopen(Request(up + '/api/scan', data=b'{}', method='POST', headers={'Content-Type': 'application/json'})).read()
    for _ in range(120):
        st = json.loads(urlopen(up + '/api/scan/status').read())
        if not st.get('running') and st.get('state', st).get('status', '') != 'running':
            tracks = json.loads(urlopen(up + '/api/tracks').read())
            items = tracks if isinstance(tracks, list) else tracks.get('tracks') or tracks.get('items') or []
            if items:
                break
        time.sleep(0.5)
    track = items[0]
    TID = track['id']
    print('track', TID, track.get('format'), track.get('sample_rate'), track.get('bit_depth'))

    # ── fake renderer on 127.0.0.2 ──
    state = {'uri': None, 'transport': 'NO_MEDIA_PRESENT', 'fetch': {}}
    def renderer_fetch(uri):
        p = urlparse(uri)
        def req(method, headers):
            c = http.client.HTTPConnection(p.hostname, p.port, timeout=20, source_address=('127.0.0.2', 0))
            c.request(method, p.path, headers=headers)
            r = c.getresponse(); body = r.read(); c.close()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, body
        f = state['fetch']
        f['head'] = req('HEAD', {'getcontentFeatures.dlna.org': '1'})
        f['full'] = req('GET', {'getcontentFeatures.dlna.org': '1'})
        f['range'] = req('GET', {'Range': 'bytes=1000-'})
        f['suffix'] = req('GET', {'Range': 'bytes=-500'})
        f['past'] = req('GET', {'Range': f'bytes={len(SRC) + 10}-'})
        art = re.search(r'<upnp:albumArtURI>([^<]+)</upnp:albumArtURI>', state.get('meta', ''))
        if art:
            ap = urlparse(art.group(1).replace('&amp;', '&'))
            c = http.client.HTTPConnection(ap.hostname, ap.port, timeout=20, source_address=('127.0.0.2', 0))
            c.request('GET', ap.path); r = c.getresponse(); f['art'] = (r.status, r.read()); c.close()
        state['transport'] = 'PLAYING'

    class R(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length'])).decode()
            action = self.headers['SOAPACTION'].split('#')[1].strip('"')
            out = ''
            if action == 'SetAVTransportURI':
                import html as h
                state['uri'] = h.unescape(re.search(r'<CurrentURI>(.*?)</CurrentURI>', body).group(1))
                state['meta'] = h.unescape(re.search(r'<CurrentURIMetaData>(.*?)</CurrentURIMetaData>', body, re.S).group(1))
                state['transport'] = 'STOPPED'
            elif action == 'Play':
                threading.Thread(target=renderer_fetch, args=(state['uri'],), daemon=True).start()
            elif action == 'Stop':
                state['transport'] = 'STOPPED'
            elif action == 'GetTransportInfo':
                out = f'<CurrentTransportState>{state["transport"]}</CurrentTransportState>'
            elif action == 'GetPositionInfo':
                import html as h
                out = (f'<RelTime>0:00:07</RelTime><TrackDuration>0:00:20</TrackDuration>'
                       f'<TrackURI>{h.escape(state["uri"] or "")}</TrackURI>')
            data = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                    f'<u:{action}Response xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">{out}'
                    f'</u:{action}Response></s:Body></s:Envelope>').encode()
            self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers()
            self.wfile.write(data)
    rend = ThreadingHTTPServer(('127.0.0.2', 0), R)
    threading.Thread(target=rend.serve_forever, daemon=True).start()

    cfg = bridge.BridgeConfig(up, '127.0.0.1', 0, '127.0.0.1', 0, ipaddress.ip_network('127.0.0.0/8'))
    core = bridge.Bridge(cfg)
    control = bridge.BridgeHTTPServer(('127.0.0.1', 0), 'control', core)
    media = bridge.BridgeHTTPServer(('127.0.0.1', 0), 'media', core)
    core.cfg = bridge.BridgeConfig(up, '127.0.0.1', control.server_port, '127.0.0.1', media.server_port,
                                   ipaddress.ip_network('127.0.0.0/8'))
    udn = 'uuid:fake-exn100'
    core.devices[udn] = bridge.Renderer(udn, 'Cambridge Audio EXN100', '127.0.0.2',
                                        f'http://127.0.0.2:{rend.server_port}/ctl')
    core.last_scan = time.monotonic()
    for s in (control, media):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{control.server_port}'

    def phone(path, method='GET', body=None, headers=None):
        h = {'Origin': base} if method != 'GET' else {}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data: h['Content-Type'] = 'application/json'
        try:
            with urlopen(Request(base + path, data=data, method=method, headers=h), timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except HTTPError as e:
            return e.code, dict(e.headers), e.read()

    s, h, b = phone('/')
    check('proxied original Velvet UI', s == 200 and b'VELVET' in b.upper(), s)
    s, _, b = phone('/api/security/status')
    sec = json.loads(b)
    print('security/status via bridge:', {k: sec.get(k) for k in ('auth_enabled', 'passwordless_listening', 'admin_protected')})
    s, _, b = phone('/api/search?q=' + quote('测试'))
    check('Chinese search proxied', s == 200 and '测试曲'.encode() in b, s)
    s, _, b = phone('/api/zones')
    check('zones without API key (passwordless parent)', s == 200 and b'EXN100' in b, (s, b[:200]))
    s, _, b = phone(f'/api/cast/{udn}', 'POST', {'track_id': TID, 'dsp_profile_id': None, 'request_id': 'e2e-1'})
    res = json.loads(b)
    check('cast success', s == 200 and res.get('success') and res.get('transport') == 'playing', (s, res))
    for _ in range(100):
        if state['transport'] == 'PLAYING': break
        time.sleep(0.1)
    f = state['fetch']
    hs, hh, hb = f['head']
    check('HEAD 200, length, no body', hs == 200 and int(hh['content-length']) == len(SRC) and hb == b'', (hs, hh))
    check('HEAD DLNA headers', hh.get('contentfeatures.dlna.org', '').startswith('DLNA.ORG_PN=FLAC') and
          hh.get('transfermode.dlna.org') == 'Streaming', hh)
    gs, gh, gb = f['full']
    check('GET full bytes identical (sha256)', gs == 200 and hashlib.sha256(gb).hexdigest() == SHA, (gs, len(gb)))
    check('Content-Type audio/flac', gh.get('content-type') == 'audio/flac', gh.get('content-type'))
    rs, rh, rb = f['range']
    check('Range bytes=1000- 206', rs == 206 and rb == SRC[1000:] and rh['content-range'] == f'bytes 1000-{len(SRC)-1}/{len(SRC)}', (rs, rh.get('content-range')))
    ss, sh, sb = f['suffix']
    check('suffix Range bytes=-500', ss == 206 and sb == SRC[-500:], (ss, len(sb)))
    ps, ph, pb = f['past']
    check('unsatisfiable Range -> 416 + Content-Range', ps == 416 and ph.get('content-range') == f'bytes */{len(SRC)}', (ps, ph))
    meta = state['meta']
    check('DIDL OP=01 + 96k/24bit', 'DLNA.ORG_OP=01' in meta and 'sampleFrequency="96000"' in meta and 'bitsPerSample="24"' in meta, meta[:400])
    if 'art' in f:
        print('album art fetch status', f['art'][0], len(f['art'][1]))
    s, _, b = phone(f'/api/cast/status/{udn}')
    st = json.loads(b)
    check('status track_id', s == 200 and st.get('track_id') == TID and st.get('state') == 'PLAYING', st)
    s, _, b = phone(f'/api/cast/result/{udn}/e2e-1')
    check('cast result done', json.loads(b).get('done') is True, b)
    # foreign LAN host with the ticket
    p = urlparse(state['uri'])
    c = http.client.HTTPConnection(p.hostname, p.port, timeout=10, source_address=('127.0.0.3', 0))
    c.request('GET', p.path); r = c.getresponse(); r.read()
    check('ticket refused from another LAN IP', r.status == 403, r.status)
    # browser path via bridge (ticket)
    s, _, b = phone('/api/stream-ticket', 'POST')
    tk = json.loads(b).get('ticket')
    s, _, b = phone(f'/api/stream/{TID}?ticket={quote(tk)}') if tk else (0, 0, b'')
    check('phone browser stream via bridge identical', s == 200 and hashlib.sha256(b).hexdigest() == SHA, s)
    s, _, b = phone(f'/api/cast/control/{udn}/Stop', 'POST')
    check('Stop success', json.loads(b).get('success') is True, b)
    c = http.client.HTTPConnection(p.hostname, p.port, timeout=10, source_address=('127.0.0.2', 0))
    c.request('GET', p.path); r = c.getresponse(); r.read()
    check('ticket revoked after Stop', r.status == 403, r.status)
    s, _, _ = phone('/api/zones', headers={'Host': 'attacker.example:8877'})
    check('DNS-rebinding Host refused', s == 421, s)
    s, _, _ = phone(f'/api/cast/{udn}', 'POST', {'track_id': TID}, headers={'Origin': 'http://evil.example'})
    check('cross-origin cast refused', s == 403, s)
finally:
    srv.terminate()
    try: srv.wait(10)
    except Exception: srv.kill()
print(f"\n{sum(ok for _, ok in results)}/{len(results)} checks passed; tmp={tmp}")
sys.exit(0 if all(ok for _, ok in results) else 1)
