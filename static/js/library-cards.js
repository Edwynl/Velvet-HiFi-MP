/* VELVET library-cards: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
function makeAlbumCard(album) {
  const div = document.createElement('div');
  div.className = 'card';
  const isFav = album.is_favorite;
  // Use dynamic thumbnail size based on screen width
  const thumbSize = getThumbSize();
  // Use album.updated_at for cache busting only when cover actually changed
  const albumTs = album.updated_at ? `&t=${album.updated_at}` : '';
  const thumbUrl = `/api/covers/${album.id}/thumb?size=${thumbSize}${albumTs}`;
  const fullUrl = `/api/covers/${album.id}${albumTs}`;
  div.innerHTML = `
    <div class="card-art">
      <img src="${thumbUrl}" alt="" onerror="handleImgError(this, '${fullUrl}')" onload="handleImgLoad(this)"
           loading="lazy" decoding="async">
      <div class="card-art-placeholder"></div>
      <div class="card-play-btn">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
      </div>
      ${isFav ? '<div class="card-fav-btn" style="position:absolute;top:6px;right:6px;background:rgba(0,0,0,0.6);border-radius:50%;width:24px;height:24px;display:flex;align-items:center;justify-content:center;color:#ff6b6b;font-size:14px;pointer-events:none">♥</div>' : ''}
    </div>
    <div class="card-title-row" style="display:flex;align-items:center;justify-content:space-between;gap:6px;min-width:0">
      <div class="card-title" title="${escHtml(album.title)}" style="flex:1">${escHtml(album.title)}</div>
      ${album.is_hires ? `<img src="/static/logo-hires.png" class="hires-badge" alt="Hi-Res">` : ''}
    </div>
    <div class="card-sub">${escHtml(album.artist_name || '')}${album.year ? ' · ' + album.year : ''}</div>
  `;
  div.addEventListener('click', () => {
    state.prevView = state.view;
    navigate('album', { id: album.id });
  });
  div.querySelector('.card-play-btn').addEventListener('click', async e => {
    e.stopPropagation();
    const data = await api(`/albums/${album.id}`);
    if (data.tracks?.length) playTrack(data.tracks[0], data.tracks, 0);
  });
  return div;
}

function makeArtistCard(artist) {
  const div = document.createElement('div');
  div.className = 'card artist-card';
  const initial = artist.name.charAt(0).toUpperCase();
  div.innerHTML = `
    <div class="card-art">
      <img src="/api/artists/${artist.id}/image" alt="" onerror="handleImgError(this)" onload="handleImgLoad(this)" loading="lazy">
      <div class="card-art-placeholder artist-placeholder">
        <span class="artist-initial">${initial}</span>
      </div>
    </div>
    <div class="card-title" style="text-align:center">${escHtml(artist.name)}</div>
    <div class="card-sub" style="text-align:center">${artist.album_count || 0} albums</div>
  `;
  div.addEventListener('click', () => navigate('artist', { id: artist.id }));
  return div;
}

function makeComposerCard(composer) {
  const name = composer.composer || composer.name || 'Unknown Composer';
  const div = document.createElement('div');
  div.className = 'card artist-card';
  div.innerHTML = `
    <div class="card-art composer-placeholder">${escHtml(name.charAt(0).toUpperCase())}</div>
    <div class="card-title" style="text-align:center">${escHtml(name)}</div>
    <div class="card-sub" style="text-align:center">${composer.work_count || 0} works · ${composer.track_count || 0} tracks</div>
  `;
  div.addEventListener('click', () => navigate('composer', { id: name }));
  return div;
}

function makeWorkCard(work, opts = {}) {
  const title = work.work_title || 'Untitled Work';
  const composer = work.composer || 'Unknown Composer';
  const div = document.createElement('div');
  div.className = 'credits-card';
  div.style.cursor = 'pointer';
  div.innerHTML = `
    <div class="credits-title">${escHtml(composer)}</div>
    <div class="zone-title">${escHtml(title)}</div>
    <div class="zone-sub">${work.track_count || 0} tracks · ${work.album_count || 0} albums</div>
    <div class="zone-meta">${work.first_year || 'Unknown year'}${work.latest_year && work.latest_year !== work.first_year ? ` → ${work.latest_year}` : ''}</div>
  `;
  const params = { id: makeWorkId(composer, title) };
  if (opts.role && opts.credit) {
    params.role = opts.role;
    params.credit = opts.credit;
  }
  div.addEventListener('click', () => navigate('work', params));
  return div;
}

function makeCreditCard(item, role, label) {
  const name = item.name || `Unknown ${label}`;
  const div = document.createElement('div');
  div.className = 'credits-card';
  div.style.cursor = 'pointer';
  div.innerHTML = `
    <div class="credits-title">${escHtml(label)}</div>
    <div class="zone-title">${escHtml(name)}</div>
    <div class="zone-sub">${item.track_count || 0} tracks</div>
  `;
  div.addEventListener('click', () => navigate('credit', { id: makeCreditId(role, name) }));
  return div;
}

function makeTrackRow(track, num, showAlbum = false) {
  const el = document.createElement('div');
  el.className = 'track-item';
  el.dataset.trackId = track.id;
  el.dataset.num = num;

  const sr = track.sample_rate ? fmtSR(track.sample_rate) : '';
  const bd = track.bit_depth ? `${track.bit_depth}b` : '';
  const quality = [sr, bd].filter(Boolean).join(' · ');
  const metaBits = [];
  if (showAlbum && (track.artist_name || track.artist)) metaBits.push(escHtml(track.artist_name || track.artist || ''));
  if (showAlbum && track.album_title) metaBits.push(escHtml(track.album_title));
  if (track.composer) metaBits.push(`Composer: ${escHtml(track.composer)}`);
  if (track.work_title) metaBits.push(`Work: ${escHtml(track.work_title)}`);
  if (track.performer) metaBits.push(`Performer: ${escHtml(track.performer)}`);
  if (track.conductor) metaBits.push(`Conductor: ${escHtml(track.conductor)}`);
  if (track.ensemble) metaBits.push(`Ensemble: ${escHtml(track.ensemble)}`);

  el.innerHTML = `
    <div class="track-num">${num}</div>
    <div class="track-name">
      ${escHtml(track.title)}
      ${metaBits.length ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">${metaBits.join(' · ')}</div>` : ''}
    </div>
    <div class="track-format">${escHtml(track.format || '')}</div>
    <div class="track-sr">${quality}</div>
    <div class="track-dur">${fmt(track.duration)}</div>
    <button class="track-add-btn" title="Add to playlist">＋</button>
  `;
  const addBtn = el.querySelector('.track-add-btn');
  addBtn?.addEventListener('click', e => {
    e.stopPropagation();
    showPlaylistPicker(track);
  });
  return el;
}

// ─── Search ───────────────────────────────────────────────────────────────────

let searchTimer;
let searchOutsideHandler = null;
