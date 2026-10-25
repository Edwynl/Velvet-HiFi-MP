"""Manual browser acceptance: real Velvet main -> bridge -> a fake EXN100 that
plays in real time. Not run by unittest discovery.

    venv\\Scripts\\python.exe -I remote_bridge/tests/manual_browser_stack.py <repo root> <venv python> <ffmpeg>

Builds a 4-track album of 15 s FLACs (TRACK_SECONDS) in a temporary library, starts the
repository's own server.py on 18765, the bridge on 127.0.0.1:18877 (media on
18878) with its queue monitor, and the renderer on 127.0.0.2:18999. Wait for
READY, then in a browser open http://127.0.0.1:18877/ and:

  1. Cast button -> "Cambridge Audio EXN100 (test)"; open Queue Album; Play All.
  2. Phone locked / page gone: navigate the tab to http://127.0.0.2:18999/log
     and wait ~65 s. Expect one "load" per song, ~0.1 s apart, then the end.
  3. Page open throughout: Play All again and watch: the player shows each
     song in turn; the log again has exactly one load per song.
  4. Page discarded and reloaded (Safari/PWA): Play All, navigate away for
     ~35 s (two unattended advances), then open http://127.0.0.1:18877/ fresh.
     Expect the player on the EXN100 zone showing Song 3 with the album queue,
     and no extra "load" in the log; Song 4 then follows on the page too.

The renderer log is JSON: load (title, URI tail), play, pause, stop, ended.
Stop with Ctrl+C.
"""
import html, ipaddress, json, os, re, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

WT, PY, FFMPEG = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, str(WT / 'remote_bridge'))
import bridge  # noqa: E402

PORT, TRACK_SECONDS = 18765, int(os.environ.get('TRACK_SECONDS', '15'))
tmp = Path(tempfile.mkdtemp(prefix='velvet-queue-'))
album = tmp / 'music' / 'Queue Artist' / 'Queue Album'
album.mkdir(parents=True)
for n, freq in ((1, 440), (2, 660), (3, 880), (4, 990)):
    subprocess.run([FFMPEG, '-v', 'error', '-f', 'lavfi', '-i',
                    f'sine=frequency={freq}:sample_rate=44100:duration={TRACK_SECONDS}', '-c:a', 'flac',
                    '-metadata', 'artist=Queue Artist', '-metadata', 'album=Queue Album',
                    '-metadata', f'title=Song {n}', '-metadata', f'track={n}',
                    str(album / f'{n:02d} Song {n}.flac')], check=True)
env = {**os.environ, 'VELVET_DATA_DIR': str(tmp / 'data'), 'VELVET_MUSIC_DIR': str(tmp / 'music'),
       'VELVET_PORT': str(PORT), 'PYTHONIOENCODING': 'utf-8'}
server = subprocess.Popen([PY, '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', str(PORT),
                           '--no-proxy-headers'], cwd=WT, env=env,
                          stdout=open(tmp / 'server.log', 'wb'), stderr=subprocess.STDOUT)
up = f'http://127.0.0.1:{PORT}'
for _ in range(120):
    try:
        urlopen(up + '/api/health', timeout=2).read()
        break
    except Exception:
        time.sleep(0.5)
urlopen(Request(up + '/api/scan', data=b'{}', method='POST', headers={'Content-Type': 'application/json'})).read()
for _ in range(120):
    tracks = json.loads(urlopen(up + '/api/tracks').read())
    items = tracks if isinstance(tracks, list) else tracks.get('tracks') or tracks.get('items') or []
    if len(items) >= 4:
        break
    time.sleep(0.5)
print('tracks', sorted((t['id'], t['title']) for t in items), flush=True)

# ── Timed fake renderer: plays in real time, STOPPED at the end ──
log, lock = [], threading.Lock()
r = {'uri': '', 'state': 'NO_MEDIA_PRESENT', 'started': 0.0, 'offset': 0.0, 'duration': 0.0}


def position():
    if r['state'] == 'PLAYING':
        pos = r['offset'] + time.monotonic() - r['started']
        if pos >= r['duration'] > 0:
            r['state'], r['offset'] = 'STOPPED', r['duration']
            log.append({'t': round(time.time(), 1), 'event': 'ended', 'uri': r['uri'][-12:]})
            return r['duration']
        return pos
    return r['offset']


def fmt(seconds):
    s = int(seconds)
    return f'{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}'


class Renderer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        with lock:
            body = json.dumps(log, indent=1).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length'])).decode()
        action = self.headers['SOAPACTION'].split('#')[1].strip('"')
        out = ''
        with lock:
            if action == 'SetAVTransportURI':
                r['uri'] = html.unescape(re.search(r'<CurrentURI>(.*?)</CurrentURI>', body).group(1))
                meta = html.unescape(re.search(r'<CurrentURIMetaData>(.*?)</CurrentURIMetaData>', body, re.S).group(1))
                d = re.search(r'duration="(\d+):(\d+):(\d+)"', meta)
                r['duration'] = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + int(d.group(3)) if d else 0
                title = re.search(r'<dc:title>(.*?)</dc:title>', meta)
                r['state'], r['offset'] = 'STOPPED', 0.0
                log.append({'t': round(time.time(), 1), 'event': 'load', 'title': title and title.group(1),
                            'uri': r['uri'][-12:]})
            elif action == 'Play':
                position()
                if r['state'] != 'PLAYING':
                    r['state'], r['started'] = 'PLAYING', time.monotonic()
                log.append({'t': round(time.time(), 1), 'event': 'play'})
            elif action == 'Pause':
                r['offset'] = position()
                r['state'] = 'PAUSED_PLAYBACK'
                log.append({'t': round(time.time(), 1), 'event': 'pause'})
            elif action == 'Stop':
                r['state'], r['offset'] = 'STOPPED', 0.0
                log.append({'t': round(time.time(), 1), 'event': 'stop'})
            elif action == 'GetTransportInfo':
                position()
                out = f'<CurrentTransportState>{r["state"]}</CurrentTransportState>'
            elif action == 'GetPositionInfo':
                out = (f'<RelTime>{fmt(position())}</RelTime><TrackDuration>{fmt(r["duration"])}</TrackDuration>'
                       f'<TrackURI>{html.escape(r["uri"])}</TrackURI>')
        data = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                f'<u:{action}Response xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">{out}'
                f'</u:{action}Response></s:Body></s:Envelope>').encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


rend = ThreadingHTTPServer(('127.0.0.2', 18999), Renderer)
threading.Thread(target=rend.serve_forever, daemon=True).start()

cfg = bridge.BridgeConfig(up, '127.0.0.1', 18877, '127.0.0.1', 18878, ipaddress.ip_network('127.0.0.0/8'))
core = bridge.Bridge(cfg)
udn = 'uuid:fake-exn100'
core.devices[udn] = bridge.Renderer(udn, 'Cambridge Audio EXN100 (test)', '127.0.0.2', 'http://127.0.0.2:18999/ctl')
core.last_scan = time.monotonic() + 10 ** 6  # never rescan: the fake is not on SSDP
control = bridge.BridgeHTTPServer(('127.0.0.1', 18877), 'control', core)
media = bridge.BridgeHTTPServer(('127.0.0.1', 18878), 'media', core)
for s in (control, media):
    threading.Thread(target=s.serve_forever, daemon=True).start()
stop = threading.Event()
threading.Thread(target=core.run_monitor, args=(stop,), daemon=True).start()
print('READY http://127.0.0.1:18877/  log http://127.0.0.2:18999/log  tmp', tmp, flush=True)
try:
    while server.poll() is None:
        time.sleep(1)
finally:
    server.terminate()
