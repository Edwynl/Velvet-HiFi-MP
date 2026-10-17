// A localhost-only UI fixture. No database, music files, credentials or devices.
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(process.env.VELVET_QA_ROOT || process.cwd());
export const albums = Array.from({ length: 610 }, (_, i) => ({
  id: i + 1, title: `Album ${String(i + 1).padStart(3, '0')}`, artist_name: 'QA Ensemble',
  artist_id: 1, year: 2024, genre: 'Classical', is_hires: true, is_favorite: true,
  updated_at: 'qa-v1',
}));
const tracks = id => Array.from({ length: 5 }, (_, i) => ({
  id: id * 10 + i, title: `Movement ${i + 1}`, album_id: id, album_title: albums[id - 1]?.title,
  artist_name: 'QA Ensemble', duration: 180, format: 'FLAC', sample_rate: 96000, bit_depth: 24,
}));
const json = (res, data, status = 200) => {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(data));
};
export function createFixtureServer({ root: fixtureRoot = root } = {}) {
  return http.createServer(async (req, res) => {
    const u = new URL(req.url, 'http://127.0.0.1');
    try {
      if (/^\/api\/covers\/\d+/.test(u.pathname)) {
        const n = { small: 150, medium: 250, large: 400, xlarge: 800 }[u.searchParams.get('size')] || 1200;
        const id = Number(u.pathname.split('/')[3]);
        res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'public,max-age=3600' });
        res.end(`<svg xmlns="http://www.w3.org/2000/svg" width="${n}" height="${n}" viewBox="0 0 800 800"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="hsl(${id * 29 % 360} 40% 22%)"/><stop offset="1" stop-color="#111119"/></linearGradient></defs><rect width="800" height="800" fill="url(#g)"/><circle cx="400" cy="340" r="220" fill="none" stroke="#c9a84c" stroke-width="2"/><path d="M100 340h600M400 100v480" stroke="#c9a84c" opacity=".3"/><text x="400" y="335" text-anchor="middle" fill="#ede8df" font-family="serif" font-size="72">VELVET</text><text x="400" y="415" text-anchor="middle" fill="#c9a84c" font-size="28">QA ENSEMBLE</text><text x="400" y="700" text-anchor="middle" fill="#ede8df" font-size="34">ALBUM ${id}</text></svg>`);
        return;
      }
      if (u.pathname.startsWith('/api/')) {
        const p = u.pathname.slice(4);
        if (p === '/security/status') return json(res, { enabled: false });
        if (p === '/stream-ticket') return json(res, { ticket: 'fixture-ticket', expires_in: 300 });
        if (p === '/albums/page') {
          const offset = Number(u.searchParams.get('offset') || 0);
          const limit = Number(u.searchParams.get('limit') || 60);
          const search = (u.searchParams.get('search') || '').toLowerCase();
          const list = albums.filter(a => a.title.toLowerCase().includes(search));
          return json(res, { albums: list.slice(offset, offset + limit), total: list.length,
            genres: ['Classical'], decades: ['2020s'], limit, offset });
        }
        const album = p.match(/^\/albums\/(\d+)$/);
        if (album) return json(res, { ...albums[Number(album[1]) - 1], tracks: tracks(Number(album[1])) });
        if (/\/credits$/.test(p)) return json(res, { composers: [], works: [], performers: [], conductors: [], ensembles: [] });
        if (/\/favorite$/.test(p)) return json(res, { success: true, is_favorite: true });
        if (p === '/stats') return json(res, { albums: albums.length, artists: 1, tracks: 3050, total_duration_hours: 152 });
        if (p === '/recently-added') return json(res, albums.slice(0, 12));
        if (p === '/artists/page') return json(res, { artists: [{ id: 1, name: 'QA Ensemble', album_count: 610 }], total: 1 });
        if (p === '/artists/1') return json(res, { id: 1, name: 'QA Ensemble' });
        if (p === '/artists/1/albums') return json(res, albums.slice(0, 20));
        if (p === '/search') return json(res, { tracks: tracks(1), albums: albums.slice(0, 5), artists: [] });
        if (p === '/config') return json(res, { music_dir: 'C:/QA/Music', libraries: [{ id: 1, path: 'C:/QA/Music' }], port: 9876, data_dir: 'C:/QA/Data' });
        if (p === '/remote/status') return json(res, { status: 'not_running', url: '' });
        if (p === '/dsp/profiles' || p === '/history' || p === '/devices' || p === '/zones') return json(res, []);
        if (/\/status$/.test(p)) return json(res, { running: false, enabled: false });
        return json(res, {});
      }
      const file = path.resolve(fixtureRoot, u.pathname === '/' ? 'static/index.html' : '.' + u.pathname);
      if (!file.startsWith(fixtureRoot + path.sep)) return json(res, {}, 403);
      const body = await readFile(file);
      const type = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png' }[path.extname(file)] || 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-cache' }); res.end(body);
    } catch { res.writeHead(404); res.end('Not found'); }
  });
}
if (process.argv[1]?.endsWith('fixture-server.mjs')) {
  const port = Number(process.env.VELVET_QA_PORT || 8767);
  createFixtureServer().listen(port, '127.0.0.1', () => console.log(`QA fixture http://127.0.0.1:${port}`));
}
