/* VELVET library-views: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
// Home history is device-local and bounded; the server resolves IDs against the
// current library so deleted albums never linger on the shelf.
const HOME_ACTIVITY_KEY = 'velvet:home-activity:v1';
function readHomeActivity() {
  try {
    const saved = JSON.parse(localStorage.getItem(HOME_ACTIVITY_KEY) || '{}');
    const valid = item => Number.isSafeInteger(item?.id) && item.id > 0;
    return {
      albums: Array.isArray(saved.albums) ? saved.albums.filter(valid).slice(0, 12) : [],
      artists: Array.isArray(saved.artists) ? saved.artists.filter(valid).slice(0, 64) : [],
    };
  } catch { return { albums: [], artists: [] }; }
}

function recordHomeVisit(type, item) {
  const id = Number(item?.id);
  if (!Number.isSafeInteger(id) || id <= 0) return;
  const activity = readHomeActivity();
  const at = Date.now();
  if (type === 'album') {
    activity.albums = [{ id, at }, ...activity.albums.filter(a => a.id !== id)].slice(0, 12);
  }
  const artistId = Number(type === 'artist' ? id : item.artist_id);
  if (Number.isSafeInteger(artistId) && artistId > 0) {
    const previous = activity.artists.find(a => a.id === artistId);
    activity.artists = [{ id: artistId, count: Math.min(1000000, (Number(previous?.count) || 0) + 1), at },
      ...activity.artists.filter(a => a.id !== artistId)]
      .sort((a, b) => b.count - a.count || b.at - a.at).slice(0, 64);
  }
  try { localStorage.setItem(HOME_ACTIVITY_KEY, JSON.stringify(activity)); } catch { /* Private/full storage still allows browsing. */ }
}

function homeGreeting(date = new Date()) {
  const hour = date.getHours();
  const period = hour >= 5 && hour < 12 ? 'Morning' : hour >= 12 && hour < 18 ? 'Afternoon' : 'Evening';
  return `Good ${period}, Edwyn`;
}

function refreshHomeGreeting() {
  const heading = document.getElementById('home-greeting');
  if (heading && state.view === 'home') heading.textContent = homeGreeting();
}

// Register once, rather than adding a timer or lifecycle listeners per visit.
window.addEventListener('pageshow', refreshHomeGreeting);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshHomeGreeting();
});
setInterval(() => { if (!document.hidden) refreshHomeGreeting(); }, 60000);

function makeHomeAlbumCard(album) {
  const card = makeAlbumCard(album);
  card.classList.add('home-album-card');
  // Keep the cover unobstructed; playback is available in the album detail.
  const title = card.querySelector('.card-title');
  const link = document.createElement('a');
  link.href = '#album/' + encodeURIComponent(album.id);
  link.textContent = title.textContent;
  link.title = album.title;
  link.addEventListener('click', event => { event.preventDefault(); });
  title.replaceChildren(link);
  card.querySelector('.card-play-btn')?.remove();
  // Reflect an already loaded cover through a background layer. This avoids
  // WebKit's box-reflect/3D clipping and never initiates a second cover fetch.
  const image = card.querySelector('.card-art img');
  const reflection = document.createElement('div');
  reflection.className = 'home-cover-reflection';
  reflection.setAttribute('aria-hidden', 'true');
  const syncReflection = () => {
    reflection.style.setProperty('--reflection-image', `url(${JSON.stringify(image.currentSrc || image.src)})`);
  };
  image.addEventListener('load', syncReflection);
  if (image.complete && image.naturalWidth) syncReflection();
  card.appendChild(reflection);
  if (album.recommendation_reason) {
    const reason = document.createElement('div');
    reason.className = 'home-card-reason';
    reason.textContent = album.recommendation_reason;
    reason.title = album.recommendation_reason;
    card.appendChild(reason);
  }
  return card;
}

function attachHomeCoverflow(rail) {
  const original = [...rail.querySelectorAll('.home-album-card')];
  if (!original.length) return () => {};
  // Put two records to the left of the newest one, without duplicate images.
  const lead = Math.min(2, original.length - 1);
  const cards = lead ? [...original.slice(-lead), ...original.slice(0, -lead)] : original;
  // Snap to stationary slots, not transformed covers. Otherwise the changing
  // 3D bounding boxes can make native scroll-snap settle off center.
  const slots = cards.map(card => {
    const slot = document.createElement('div');
    slot.className = 'home-flow-slot';
    slot.appendChild(card);
    return slot;
  });
  rail.replaceChildren(...slots);
  rail.classList.add('home-coverflow');
  rail.tabIndex = 0;
  rail.setAttribute('role', 'region');
  rail.setAttribute('aria-roledescription', 'carousel');
  rail.setAttribute('aria-labelledby', rail.id + '-title');
  const controls = document.createElement('div');
  controls.className = 'home-flow-controls';
  const status = document.createElement('span');
  status.className = 'home-flow-position';
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  controls.append(status);
  rail.after(controls);
  let selected = cards.indexOf(original[0]);
  let requested = null;
  let centers = [];
  let viewportWidth = 0;
  let spacing = 0;
  let frame = 0;
  let coverTimer = 0;
  let disposed = false;
  const cinematic = rail.id === 'recent-grid';
  let drag = null;
  let suppressClickUntil = 0;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const sharpenCenter = () => {
    const image = cards[selected].querySelector('.card-art img');
    if (disposed || !image || image.dataset.homeCoverSharp) return;
    const url = new URL(image.src, location.href);
    if (!url.pathname.endsWith('/thumb')) return;
    url.searchParams.set('size', 'large');
    image.dataset.homeCoverSharp = 'true';
    image.src = url.pathname + url.search;
  };
  const paint = (position) => {
    if (Math.abs(position - Math.round(position)) < .001) position = Math.round(position);
    const closest = Math.max(0, Math.min(cards.length - 1, Math.round(position)));
    cards.forEach((card, index) => {
      const distance = index - position;
      const depth = Math.min(Math.abs(distance), 4);
      const turn = Math.min(depth, 1);
      const easedTurn = turn * turn * (3 - 2 * turn);
      const spotlight = Math.exp(-1.4 * distance * distance);
      const caption = Math.max(0, 1 - Math.abs(distance));
      card.style.setProperty('--flow-angle', `${-Math.sign(distance) * easedTurn * (cinematic ? 32 : 56)}deg`);
      card.style.setProperty('--flow-depth', `${cinematic ? -42 * (1 - spotlight) - depth * 12 : -depth * 68}px`);
      card.style.setProperty('--flow-lift', `${cinematic ? -6 * spotlight : 0}px`);
      card.style.setProperty('--flow-scale', String(cinematic ? .88 + .12 * spotlight : 1 - depth * .065));
      card.style.setProperty('--flow-alpha', String(cinematic ? Math.max(0, 1 - Math.pow(depth / 4, 2)) : Math.max(0, 1 - depth / 4)));
      card.style.setProperty('--flow-caption', String(cinematic ? caption * caption * (3 - 2 * caption) : Math.max(0, 1 - Math.abs(distance) * 2)));
      card.style.zIndex = String(50 - Math.round(depth * 10));
      slots[index].style.zIndex = card.style.zIndex;
      card.classList.toggle('is-current', index === closest);
      if (index === closest) card.setAttribute('aria-current', 'true');
      else card.removeAttribute('aria-current');
    });
    if (selected !== closest) {
      clearTimeout(coverTimer);
      coverTimer = setTimeout(sharpenCenter, 150);
    }
    selected = closest;
    const logical = original.indexOf(cards[closest]) + 1;
    const label = `${String(logical).padStart(2, '0')} / ${String(cards.length).padStart(2, '0')}`;
    if (status.textContent !== label) status.textContent = label;
  };
  const measure = () => {
    // Read geometry once on resize, never interleave layout reads and writes
    // in the scroll loop (especially important with 24 covers on a phone).
    viewportWidth = rail.clientWidth;
    centers = slots.map(slot => slot.offsetLeft + slot.offsetWidth / 2);
    spacing = cards.length > 1 ? (centers.at(-1) - centers[0]) / (cards.length - 1) : 0;
  };
  const syncScroll = () => {
    frame = 0;
    if (disposed || !rail.isConnected) return;
    if (spacing > 0) {
      const position = (rail.scrollLeft + viewportWidth / 2 - centers[0]) / spacing;
      paint(position);
      if (requested !== null && Math.abs(position - requested) < .02) requested = null;
    }
  };
  const onScroll = () => {
    if (!frame) frame = requestAnimationFrame(syncScroll);
  };
  const select = (index, smooth = true) => {
    if (disposed) return;
    const wrapped = (index + cards.length) % cards.length;
    const left = centers[wrapped] - viewportWidth / 2;
    const behavior = smooth && !reducedMotion && index === wrapped ? 'smooth' : 'auto';
    requested = behavior === 'smooth' && spacing > 0 ? wrapped : null;
    if (typeof rail.scrollTo === 'function') rail.scrollTo({ left, behavior });
    else rail.scrollLeft = left;
    // During smooth scrolling the scroll position is the only animation clock.
    // Painting the target here used to jump forward, then snap back a frame.
    if (requested === null) paint(wrapped);
  };
  const intendedIndex = () => requested ?? selected;
  const onKey = event => {
    const target = { ArrowLeft: intendedIndex() - 1, ArrowRight: intendedIndex() + 1,
      Home: cards.indexOf(original[0]), End: cards.indexOf(original.at(-1)) }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    select(target);
  };
  const onClick = event => {
    if (performance.now() < suppressClickUntil) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    const card = event.target.closest('.home-album-card');
    const index = cards.indexOf(card);
    if (index < 0 || index === selected) return;
    // A side-cover tap brings it into the spotlight; the next opens the album.
    event.preventDefault();
    event.stopPropagation();
    select(index);
  };
  const onGesture = () => { requested = null; };
  // Keep native touch/trackpad momentum. Mouse dragging needs its own velocity
  // projection, and a drag must never fall through to the album click handler.
  const onPointerDown = event => {
    onGesture();
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    drag = { id: event.pointerId, x: event.clientX, left: rail.scrollLeft,
      lastX: event.clientX, time: event.timeStamp, velocity: 0, moved: false };
  };
  const onPointerMove = event => {
    if (!drag || event.pointerId !== drag.id) return;
    const delta = event.clientX - drag.x;
    if (!drag.moved && Math.abs(delta) < 6) return;
    if (!drag.moved) {
      drag.moved = true;
      rail.classList.add('is-dragging');
      rail.setPointerCapture?.(event.pointerId);
    }
    event.preventDefault();
    const elapsed = Math.max(1, event.timeStamp - drag.time);
    drag.velocity = .35 * drag.velocity + .65 * (drag.lastX - event.clientX) / elapsed;
    drag.lastX = event.clientX;
    drag.time = event.timeStamp;
    rail.scrollLeft = drag.left - delta;
    onScroll();
  };
  const onPointerUp = event => {
    if (!drag || event.pointerId !== drag.id) return;
    const ended = drag;
    drag = null;
    rail.classList.remove('is-dragging');
    if (rail.hasPointerCapture?.(event.pointerId)) rail.releasePointerCapture(event.pointerId);
    if (!ended.moved) return;
    suppressClickUntil = performance.now() + 400;
    const velocity = event.type === 'pointercancel' || event.timeStamp - ended.time > 100 ? 0 : ended.velocity;
    const coast = reducedMotion ? 0 : Math.max(-spacing * 6, Math.min(spacing * 6, velocity * 380));
    if (spacing > 0) {
      const target = Math.round((rail.scrollLeft + coast + viewportWidth / 2 - centers[0]) / spacing);
      select(Math.max(0, Math.min(cards.length - 1, target)));
    }
  };
  const preventImageDrag = event => event.preventDefault();
  rail.addEventListener('scroll', onScroll, { passive: true });
  rail.addEventListener('keydown', onKey);
  rail.addEventListener('click', onClick, true);
  rail.addEventListener('pointerdown', onPointerDown);
  rail.addEventListener('pointermove', onPointerMove);
  rail.addEventListener('pointerup', onPointerUp);
  rail.addEventListener('pointercancel', onPointerUp);
  rail.addEventListener('lostpointercapture', onPointerUp);
  rail.addEventListener('dragstart', preventImageDrag);
  rail.addEventListener('wheel', onGesture, { passive: true });
  const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    const target = intendedIndex();
    measure();
    select(target, false);
  }) : null;
  resize?.observe(rail);
  measure();
  select(selected, false);
  sharpenCenter();
  return () => {
    disposed = true;
    if (frame) cancelAnimationFrame(frame);
    clearTimeout(coverTimer);
    resize?.disconnect();
    rail.removeEventListener('scroll', onScroll);
    rail.removeEventListener('keydown', onKey);
    rail.removeEventListener('click', onClick, true);
    rail.removeEventListener('pointerdown', onPointerDown);
    rail.removeEventListener('pointermove', onPointerMove);
    rail.removeEventListener('pointerup', onPointerUp);
    rail.removeEventListener('pointercancel', onPointerUp);
    rail.removeEventListener('lostpointercapture', onPointerUp);
    rail.removeEventListener('dragstart', preventImageDrag);
    rail.removeEventListener('wheel', onGesture);
  };
}

async function renderHome(vc) {
  const cleanups = [];
  vc._disposeHome = () => cleanups.splice(0).forEach(dispose => dispose());
  const activity = readHomeActivity();
  const shelves = [
    ['recent-grid', '01', 'New Release', '', 'albums'],
    ['home-opened-grid', '02', 'Recently opened', 'Pick up where your browsing left off.', 'albums'],
    ['home-artists-grid', '03', 'Your Artists', 'The voices you keep coming back to.', 'artists'],
    ['home-playlists-grid', '04', 'Your Playlists', '', 'playlists'],
    ['home-recommendations-grid', '05', 'Made for you', 'Mostly Chinese & Asian records, with a little global pop. All in your library.', 'discover'],
  ];
  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('home')}
      <div class="search-wrap">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input id="search-input" type="text" aria-label="Search your library" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
    </div>
    <div class="home-hero home-listening-room">
      <div class="home-hero-copy">
        <div class="discover-kicker">Your listening room</div>
        <h1 class="home-greeting" id="home-greeting">${homeGreeting()}</h1>
        <div class="home-sub" id="home-library-summary">A record for this moment.</div>
      </div>
    </div>
    <div id="home-scan-banner"></div>
    ${shelves.map(([id, number, title, subtitle, action]) => `
      <section class="home-shelf" aria-labelledby="${id}-title">
        <div class="discover-section-head home-shelf-head">
          <div class="home-shelf-heading">
            <span class="home-shelf-number" aria-hidden="true">${number}</span>
            <div class="discover-section-copy"><h2 class="discover-section-title" id="${id}-title">${title}</h2>${subtitle ? `<p class="discover-section-sub">${subtitle}</p>` : ''}</div>
          </div>
          <a class="home-shelf-link" href="#${action}" data-home-nav="${action}" aria-label="Open ${action}">Explore</a>
        </div>
        <div class="grid home-shelf-grid ${id === 'home-artists-grid' ? 'home-artist-shelf' : id === 'home-playlists-grid' ? 'home-playlist-shelf' : ''}" id="${id}" aria-busy="true">
          <div class="home-shelf-placeholder">Finding your next record…</div>
        </div>
      </section>`).join('')}
  `;
  setupSearch();
  vc.querySelectorAll('[data-home-nav]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    navigate(link.dataset.homeNav);
  }));
  const root = vc.querySelector('.home-listening-room');
  const active = () => root.isConnected && state.view === 'home';
  const fill = (id, items, factory, empty) => {
    if (!active()) return;
    const grid = vc.querySelector('#' + id);
    grid.replaceChildren();
    const list = Array.isArray(items) ? items : [];
    if (list.length) {
      const fragment = document.createDocumentFragment();
      list.forEach(item => fragment.appendChild(factory(item)));
      grid.appendChild(fragment);
      if (id !== 'home-artists-grid' && id !== 'home-playlists-grid') cleanups.push(attachHomeCoverflow(grid));
    } else {
      const message = document.createElement('p');
      message.className = 'home-shelf-empty';
      message.textContent = empty;
      grid.appendChild(message);
    }
    grid.setAttribute('aria-busy', 'false');
  };
  const params = new URLSearchParams({
    album_ids: activity.albums.map(a => a.id).join(','),
    artist_ids: activity.artists.slice(0, 24).map(a => a.id).join(','),
  });
  // Independent shelves: recent albums paint without waiting for personalization.
  await Promise.allSettled([
    (async () => {
      let playlists;
      try { playlists = await api('/playlists'); } catch { /* Other shelves remain available. */ }
      fill('home-playlists-grid', Array.isArray(playlists) ? playlists.slice(0, 6) : [], makePlaylistCard,
        Array.isArray(playlists) ? 'Your playlists will appear here. Open Explore to create one.'
          : 'Playlists are unavailable. Open Explore to try again.');
    })(),
    (async () => {
      const stats = await api('/stats');
      if (!stats || !active()) return;
      const values = { artists: stats.artists || 0, albums: stats.albums || 0, tracks: stats.tracks || 0 };
      for (const [name, value] of Object.entries(values)) {
        if (UI['stat-' + name]) UI['stat-' + name].textContent = value.toLocaleString();
      }
      if (UI['stat-duration']) UI['stat-duration'].textContent = (stats.total_duration_hours || 0) + 'h';
      vc.querySelector('#home-library-summary').textContent = `${values.albums.toLocaleString()} albums · ${values.artists.toLocaleString()} artists · yours to rediscover`;
      if (!values.tracks) {
        vc.querySelector('#home-scan-banner').innerHTML = `<div class="scan-banner"><div class="scan-banner-title">Your listening room starts here.</div><div class="scan-banner-sub">Add a music folder in Settings, then scan your library.</div><button class="btn btn-gold" onclick="navigate('settings')">Go to Settings</button></div>`;
      }
    })(),
    (async () => {
      let recent;
      try { recent = await api('/recently-added?limit=24'); } catch { /* Keep the other shelves available. */ }
      fill('recent-grid', Array.isArray(recent) ? recent.slice(0, 24) : [], makeHomeAlbumCard,
        recent ? 'No albums yet. Add your first music folder in Settings.' : 'Recent albums are unavailable. Open Albums to try again.');
    })(),
    (async () => {
      let data;
      try { data = await api('/home?' + params); } catch { /* An older server can still show recent albums. */ }
      data = data || {};
      fill('home-opened-grid', (data.recently_opened || []).slice(0, 12), makeHomeAlbumCard,
        'Open an album and it will appear here. Recent visits are remembered on this device.');
      fill('home-artists-grid', (data.frequent_artists || []).slice(0, 8), artist => {
        const card = makeArtistCard(artist);
        const title = card.querySelector('.card-title');
        const link = document.createElement('a');
        link.href = '#artist/' + encodeURIComponent(artist.id);
        link.textContent = artist.name;
        link.addEventListener('click', event => event.preventDefault());
        title.replaceChildren(link);
        return card;
      }, 'Visit an artist or play a few albums to build your regular rotation.');
      const seen = new Set();
      const recommendations = [...(data.for_you || []).slice(0, 10), ...(data.global_picks || []).slice(0, 2)]
        .filter(album => !seen.has(album.id) && seen.add(album.id));
      fill('home-recommendations-grid', recommendations, makeHomeAlbumCard,
        'Keep exploring your library. Recommendations grow with your listening and visits.');
    })(),
  ]);
}

async function renderDiscover(vc) {
  const discover = await api('/discover?limit=12');
  const sections = discover || {};

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('discover')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search and jump anywhere…">
      </div>
    </div>

    <div class="discover-hero">
      <div class="discover-hero-copy">
        <div class="discover-kicker">Discover</div>
        <div class="section-title">Rediscover the best parts of your library.</div>
        <div class="discover-sub">Focus on favorites, high-resolution releases, overlooked albums, heavy-rotation artists, and the tracks you touched most recently.</div>
      </div>
      <div class="discover-actions">
        <button class="btn btn-gold" id="discover-favorites-btn">Browse Favorites</button>
        <button class="btn btn-ghost" id="discover-history-btn">Recent History</button>
      </div>
    </div>

    <div id="discover-sections"></div>
  `;

  setupSearch();
  UI['discover-favorites-btn']?.addEventListener('click', () => navigate('albums'));
  UI['discover-history-btn']?.addEventListener('click', () => navigate('history'));

  const container = UI['discover-sections'];
  const config = [
    {
      key: 'favorites',
      title: 'Favorite Albums',
      subtitle: 'Pinned records worth starting from again.',
      type: 'albums',
      action: 'albums'
    },
    {
      key: 'hi_res',
      title: 'Hi-Res Spotlight',
      subtitle: 'Albums with stronger bit depth or sample rate.',
      type: 'albums',
      action: 'albums'
    },
    {
      key: 'rediscover',
      title: 'Rediscover',
      subtitle: 'Played before, but quiet for a while.',
      type: 'albums',
      action: 'albums'
    },
    {
      key: 'trending_artists',
      title: 'Heavy Rotation Artists',
      subtitle: 'Artists showing up most in recent listening.',
      type: 'artists',
      action: 'artists'
    },
    {
      key: 'recent_tracks',
      title: 'Recent Tracks',
      subtitle: 'Jump back into the latest listening trail.',
      type: 'tracks',
      action: 'history'
    }
  ];

  config.forEach(section => {
    const items = sections[section.key] || [];
    if (!items.length) return;

    const wrap = document.createElement('section');
    wrap.className = 'discover-section';
    wrap.innerHTML = `
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">${section.title}</div>
          <div class="discover-section-sub">${section.subtitle}</div>
        </div>
        <button class="btn btn-ghost discover-inline-link" data-nav="${section.action}">Open</button>
      </div>
      <div class="${section.type === 'tracks' ? 'discover-track-list' : 'grid'}" id="discover-${section.key}"></div>
    `;
    container.appendChild(wrap);

    wrap.querySelector('[data-nav]')?.addEventListener('click', () => navigate(section.action));
    const target = wrap.querySelector(`#discover-${section.key}`);
    const fragment = document.createDocumentFragment();

    if (section.type === 'albums') {
      items.forEach(item => fragment.appendChild(makeAlbumCard(item)));
    } else if (section.type === 'artists') {
      items.forEach(item => fragment.appendChild(makeArtistCard(item)));
    } else {
      items.slice(0, 10).forEach((item, index) => {
        const row = makeTrackRow(item, index + 1, true);
        row.addEventListener('click', () => playTrack(item, items, index));
        fragment.appendChild(row);
      });
    }

    target.appendChild(fragment);
  });

  if (!container.children.length) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-title">Discover needs listening data</div>
        <div class="empty-state-sub">Play a few albums, mark favorites, and this page will start shaping itself.</div>
      </div>
    `;
  }
}

// ── Albums ────────────────────────────────────────────────────────────────────

const ALBUMS_PER_PAGE = 60;  // Bounded remote page; avoid 15k records per Tailscale request.
const ARTISTS_PER_PAGE = 60;  // Same bounded page budget as albums.

// #main outlives every view, so each re-render replaces its view's previous
// scroll handler instead of stacking another one. Listen on the document too:
// iPhone portrait uses native page scrolling, and rotation can switch owners.
const viewScrollHandlers = {};
function setViewScrollHandler(view, mainEl, handler) {
  if (viewScrollHandlers[view]) {
    mainEl.removeEventListener('scroll', viewScrollHandlers[view]);
    window.removeEventListener('scroll', viewScrollHandlers[view]);
  }
  viewScrollHandlers[view] = handler;
  mainEl.addEventListener('scroll', handler, { passive: true });
  window.addEventListener('scroll', handler, { passive: true });
}

/* The Albums shelf opens on a focused view rather than the whole catalog.
   Genre and decade values must come from the server's own vocabulary
   (library_paging.ALBUM_GENRE_GROUPS and the decade list), so each default is
   only honoured when that response actually offers it — a backend vocabulary
   change degrades to "no filter" instead of silently requesting a value that
   matches nothing. */
const DEFAULT_ALBUM_SORT = 'year';
const DEFAULT_ALBUM_GENRE = 'Asian Pop';
const DEFAULT_ALBUM_DECADE = '2020s';

const offeredAlbumFilter = (values, wanted) =>
  (Array.isArray(values) && values.includes(wanted)) ? wanted : '';

async function renderAlbums(vc) {
  const isMobile = isCompactLayout();
  // Server filters and returns only one page; the device never receives 15k albums.
  // The opening request already carries the default focus, so entering the view
  // still costs exactly one round trip. The same response returns the
  // genre/decade vocabulary, which is what confirms those defaults below.
  const firstPage = await api('/albums/page?limit=' + ALBUMS_PER_PAGE + '&offset=0'
    + '&sort=' + DEFAULT_ALBUM_SORT
    + '&genre=' + encodeURIComponent(DEFAULT_ALBUM_GENRE)
    + '&decade=' + encodeURIComponent(DEFAULT_ALBUM_DECADE));
  let allAlbums = firstPage?.albums || [];
  let totalAlbums = Number(firstPage?.total || 0);
  const genres = firstPage?.genres || [];
  const decades = firstPage?.decades || [];
  let pageRequestSerial = 0;
  let renderedPage = 1;
  let showFavoritesOnly = false;
  let currentSort = DEFAULT_ALBUM_SORT;
  let currentPage = 1;
  let currentGenre = offeredAlbumFilter(genres, DEFAULT_ALBUM_GENRE);
  let currentDecade = offeredAlbumFilter(decades, DEFAULT_ALBUM_DECADE);
  // A default the server did not offer was asked for blindly. Drop it and refetch
  // once instead of leaving an empty grid under a filter nothing can match.
  const defaultsRejected = (!!DEFAULT_ALBUM_GENRE && !currentGenre)
    || (!!DEFAULT_ALBUM_DECADE && !currentDecade);
  let allTracks = [];

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('albums')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
    </div>
    <div class="album-toolbar" role="group" aria-label="Album filters and sorting">
      <div class="album-toolbar-actions">
        <button class="btn btn-ghost" id="favorites-filter-btn" aria-pressed="false">♡ Favorites</button>
        <select class="album-select" id="album-sort-select" aria-label="Sort albums">
          <option value="title">A–Z</option>
          <option value="year">Year</option>
          <option value="artist">Artist</option>
          <option value="added">Added</option>
        </select>
        <button class="btn btn-ghost" id="clear-focus-btn" hidden>Clear filters</button>
      </div>
      <div class="album-toolbar-filters">
        <select class="album-select" id="album-genre-select" aria-label="Genre">
          <option value="">All genres</option>
          ${genres.map(g => `<option value="${escHtml(g)}">${escHtml(g)}</option>`).join('')}
        </select>
        <select class="album-select" id="album-decade-select" aria-label="Decade">
          <option value="">All decades</option>
          ${decades.map(d => `<option value="${escHtml(d)}">${escHtml(d)}</option>`).join('')}
        </select>
      </div>
    </div>
    <div class="section-header" id="albums-header" title="Click to scroll to top">
      <div class="section-title">Albums</div>
      <div class="section-sub" id="albums-count">${totalAlbums} albums</div>
    </div>
    <div class="grid" id="albums-grid"></div>
    <div class="back-to-top" id="back-to-top-btn" style="display:none">↑</div>
    <div class="pagination" id="albums-pagination">
      <button class="btn btn-ghost" id="prev-page-btn" disabled>← Prev</button>
      <span class="page-info" id="page-info">Page 1 / 1</span>
      <button class="btn btn-ghost" id="next-page-btn">Next →</button>
    </div>
  `;

  const grid = UI['albums-grid'];
  const countEl = UI['albums-count'];
  const headerEl = UI['albums-header'];
  const backToTopBtn = UI['back-to-top-btn'];
  const mainEl = UI['main'];
  const sectionTitle = headerEl.querySelector('.section-title');
  const pagination = UI['albums-pagination'];
  const favBtn = UI['favorites-filter-btn'];
  const sortSelect = UI['album-sort-select'];
  const genreSelect = UI['album-genre-select'];
  const decadeSelect = UI['album-decade-select'];
  const clearFocusBtn = UI['clear-focus-btn'];
  const prevBtn = UI['prev-page-btn'];
  const nextBtn = UI['next-page-btn'];
  const pageInfo = UI['page-info'];

  // Keep the three selects showing exactly what is being requested.
  sortSelect.value = currentSort;
  genreSelect.value = currentGenre;
  decadeSelect.value = currentDecade;

  // Scroll to top on header click
  headerEl.addEventListener('click', (e) => {
    if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
    getScrollRoot().scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Show/hide back-to-top button based on scroll position
  setViewScrollHandler('albums', mainEl, () => {
    backToTopBtn.style.display = getScrollRoot().scrollTop > 300 ? 'flex' : 'none';
  });

  // Back to top button click
  backToTopBtn.addEventListener('click', () => {
    getScrollRoot().scrollTo({ top: 0, behavior: 'smooth' });
  });

  function getFilteredData() { return allAlbums; }
  function getPagedData() { return allAlbums; }

  function updatePagination() {
    const totalPages = Math.max(1, Math.ceil(totalAlbums / ALBUMS_PER_PAGE));
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
    pageInfo.textContent = `Page ${currentPage} / ${totalPages}`;
    countEl.textContent = showFavoritesOnly
      ? `${totalAlbums} favorite albums · ${totalPages} pages`
      : `${totalAlbums} albums · ${totalPages} pages`;
    clearFocusBtn.hidden = !(currentGenre || currentDecade);
  }

  async function loadPage({ reset = false } = {}) {
    if (reset) currentPage = 1;
    const serial = ++pageRequestSerial;
    const params = new URLSearchParams({
      limit: String(ALBUMS_PER_PAGE),
      offset: String((currentPage - 1) * ALBUMS_PER_PAGE),
      sort: currentSort,
      genre: currentGenre,
      decade: currentDecade,
      favorites_only: String(showFavoritesOnly),
    });
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    try {
      const page = await api('/albums/page?' + params.toString());
      if (serial !== pageRequestSerial || state.view !== 'albums') return;
      totalAlbums = Number(page.total || 0);
      allAlbums = page.albums || [];
      renderedPage = currentPage;
      renderGrid();
    } catch (err) {
      if (serial === pageRequestSerial) {
        currentPage = renderedPage;
        renderGrid();
        showToast('Unable to load albums. Try again.');
      }
    }
  }

  // Render albums grid
  function renderGrid() {
    grid._velvetCardObserver?.disconnect();
    grid.classList.add('grid');
    grid.classList.remove('tracks-list');
    grid.style.cssText = '';

    const data = getPagedData();
    grid.innerHTML = '';
    if (!data.length) {
      const msg = showFavoritesOnly ? 'No favorite albums yet.' : 'No albums found.';
      grid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">${msg}</div>`;
      updatePagination();
      return;
    }

    // Attach only the visible batch; pagination caps all retained records.
    renderCardsOnDemand(grid, data, makeAlbumCard);

    updatePagination();
  }

  renderGrid();

  // Only a rejected default needs a second round trip; the normal path is done.
  if (defaultsRejected) loadPage({ reset: true });

  // Every topbar search is now global: Artists -> Albums -> Tracks.
  setupSearch({ autoFocus: false });

  // Pagination buttons (disabled in search mode)
  prevBtn.addEventListener('click', () => {
    if (currentPage <= 1) return;
    currentPage--;
    scrollToTop();
    loadPage();
  });

  nextBtn.addEventListener('click', () => {
    const totalPages = Math.max(1, Math.ceil(totalAlbums / ALBUMS_PER_PAGE));
    if (currentPage < totalPages) {
      currentPage++;
      scrollToTop();
      loadPage();
    }
  });

  // Favorites filter
  favBtn.addEventListener('click', () => {
    showFavoritesOnly = !showFavoritesOnly;
    favBtn.setAttribute('aria-pressed', String(showFavoritesOnly));
    favBtn.textContent = showFavoritesOnly ? '♥ Favorites' : '♡ Favorites';
    loadPage({ reset: true });
  });

  sortSelect.addEventListener('change', () => {
    currentSort = sortSelect.value;
    loadPage({ reset: true });
  });

  genreSelect.addEventListener('change', () => {
    currentGenre = genreSelect.value;
    loadPage({ reset: true });
  });

  decadeSelect.addEventListener('change', () => {
    currentDecade = decadeSelect.value;
    loadPage({ reset: true });
  });

  clearFocusBtn.addEventListener('click', () => {
    currentGenre = '';
    currentDecade = '';
    genreSelect.value = '';
    decadeSelect.value = '';
    loadPage({ reset: true });
  });
}

// ── Artists ───────────────────────────────────────────────────────────────────

async function renderArtists(vc) {
  let currentSort = 'popular';
  let currentRegion = '';
  const first = await api('/artists/page?sort=popular&limit=' + ARTISTS_PER_PAGE + '&offset=0');
  let allArtists = first.artists || [];
  let totalArtists = Number(first.total || 0);
  // main's region facets (a353624); searchQuery stays for loadPage's rollback.
  let regionFacets = first.regions || [];
  let allTotal = Number(first.all_total ?? first.total ?? 0);
  let searchQuery = '';
  let currentPage = 1;
  let renderedPage = 1;
  let serial = 0;

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('artists')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
    </div>
    <div class="section-header" id="artists-header" title="Click to scroll to top">
      <div class="section-title">Artists</div>
      <div class="section-sub" id="artists-count">${totalArtists} artists</div>
    </div>
    <div class="artist-region-filters" id="artist-region-filters" role="group" aria-label="Filter artists by region"></div>
    <div class="grid artist-grid" id="artists-grid" aria-live="polite"></div>
    <div class="back-to-top" id="back-to-top-btn" style="display:none">↑</div>
    <div class="pagination" id="artists-pagination">
      <button class="btn btn-ghost" id="prev-page-btn" disabled>← Prev</button>
      <span class="page-info" id="page-info">Page 1 / 1</span>
      <button class="btn btn-ghost" id="next-page-btn">Next →</button>
    </div>
  `;

  const input = UI['search-input'];
  const grid = UI['artists-grid'];
  const countEl = UI['artists-count'];
  const headerEl = UI['artists-header'];
  const backToTopBtn = UI['back-to-top-btn'];
  const mainEl = UI['main'];
  const pagination = UI['artists-pagination'];
  const prevBtn = UI['prev-page-btn'];
  const nextBtn = UI['next-page-btn'];
  const pageInfo = UI['page-info'];
  const filters = UI['artist-region-filters'];

  function renderFilters() {
    const focusedRegion = filters.contains(document.activeElement)
      ? document.activeElement.dataset.region : null;
    const items = [{ region: '', label: 'All', count: allTotal }, ...regionFacets];
    filters.innerHTML = items.map(item => `
      <button type="button" class="artist-region-filter" data-region="${escHtml(item.region)}"
              aria-pressed="${item.region === currentRegion}">
        ${escHtml(item.label)} <span>${item.count}</span>
      </button>`).join('');
    if (focusedRegion != null) {
      [...filters.querySelectorAll('button')].find(button => button.dataset.region === focusedRegion)
        ?.focus({ preventScroll: true });
    }
  }
  filters.addEventListener('click', event => {
    const button = event.target.closest('button[data-region]');
    if (!button || currentRegion === button.dataset.region) return;
    clearTimeout(searchTimer);
    currentRegion = button.dataset.region;
    // Preserve the focused button while indicating the new selection.
    filters.querySelectorAll('button').forEach(item =>
      item.setAttribute('aria-pressed', String(item.dataset.region === currentRegion)));
    loadPage({ reset: true });
  });

  // Scroll to top on header click
  headerEl.addEventListener('click', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
    getScrollRoot().scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Show/hide back-to-top button based on scroll position
  setViewScrollHandler('artists', mainEl, () => {
    backToTopBtn.style.display = getScrollRoot().scrollTop > 300 ? 'flex' : 'none';
  });

  // Back to top button click
  backToTopBtn.addEventListener('click', () => {
    getScrollRoot().scrollTo({ top: 0, behavior: 'smooth' });
  });

  function updatePagination() {
    const pages = Math.max(1, Math.ceil(totalArtists / ARTISTS_PER_PAGE));
    pageInfo.textContent = `Page ${currentPage} / ${pages}`;
    countEl.textContent = `${totalArtists} artists · ${pages} pages`;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= pages;
  }

  function renderGrid() {
    grid._velvetCardObserver?.disconnect();
    grid.innerHTML = '';
    if (!allArtists.length) {
      grid.innerHTML = '<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No artists found.</div>';
      updatePagination();
      return;
    }
    renderCardsOnDemand(grid, allArtists, makeArtistCard);
    updatePagination();
  }

  async function loadPage({ reset = false } = {}) {
    if (reset) currentPage = 1;
    const thisRequest = ++serial;
    const params = new URLSearchParams({
      limit: String(ARTISTS_PER_PAGE),
      offset: String((currentPage - 1) * ARTISTS_PER_PAGE),
      sort: currentSort,
      region: currentRegion,
    });
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    grid.setAttribute('aria-busy', 'true');
    try {
      const page = await api('/artists/page?' + params.toString());
      if (thisRequest !== serial || state.view !== 'artists') return;
      allArtists = page.artists || [];
      totalArtists = Number(page.total || 0);
      regionFacets = page.regions || [];
      allTotal = Number(page.all_total ?? page.total ?? 0);
      renderedPage = currentPage;
      renderedRegion = currentRegion;
      renderedQuery = searchQuery;
      renderFilters();
      renderGrid();
    } catch {
      if (thisRequest === serial) {
        currentPage = renderedPage;
        currentRegion = renderedRegion;
        searchQuery = renderedQuery;
        input.value = searchQuery;
        renderFilters();
        renderGrid();
        showToast('Unable to load artists. Try again.');
      }
    } finally {
      if (thisRequest === serial) grid.setAttribute('aria-busy', 'false');
    }
  }

  // main's region/query rollback state (a353624). The sort select was
  // removed from this page by 37ab8e6, so only its sort param remains.
  let renderedRegion = '';
  let renderedQuery = '';
  // Global catalog search from any tab; local page controls remain available.
  // setupSearch owns #search-input, so this view must not also bind it.
  setupSearch({ autoFocus: false });

  prevBtn.addEventListener('click', () => {
    if (currentPage <= 1) return;
    currentPage--;
    getScrollRoot().scrollTo({ top: 0, behavior: 'smooth' });
    loadPage();
  });
  nextBtn.addEventListener('click', () => {
    if (currentPage >= Math.max(1, Math.ceil(totalArtists / ARTISTS_PER_PAGE))) return;
    currentPage++;
    getScrollRoot().scrollTo({ top: 0, behavior: 'smooth' });
    loadPage();
  });

  renderFilters();
  renderGrid();
}

async function renderComposers(vc) {
  let searchQuery = '';
  let allComposers = await api('/composers?limit=1000');
  allComposers = allComposers || [];

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('composers')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Composers</div>
      <div class="section-sub" id="composers-count">${allComposers.length} composers</div>
    </div>
    <div class="focus-bar"><input id="composer-filter" type="search" aria-label="Filter composers" placeholder="Filter composers…" maxlength="128" class="catalog-filter-input"></div>
    <div class="grid artist-grid" id="composers-grid"></div>
  `;

  const input = UI['search-input'];
  const grid = UI['composers-grid'];
  const countEl = UI['composers-count'];

  function getFiltered() {
    const q = searchQuery.trim();
    return q ? allComposers.filter(item => chineseMatch(item.composer || '', q)) : allComposers;
  }

  function renderGrid() {
    const data = getFiltered();
    grid.innerHTML = '';
    countEl.textContent = `${data.length} composer${data.length === 1 ? '' : 's'}`;
    if (!data.length) {
      grid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No composers found.</div>`;
      return;
    }
    const fragment = document.createDocumentFragment();
    data.forEach(item => fragment.appendChild(makeComposerCard(item)));
    grid.appendChild(fragment);
  }

  setupSearch({ autoFocus: false });
  const filter = UI['composer-filter'];
  filter?.addEventListener('input', () => {
    searchQuery = filter.value.trim();
    renderGrid();
  });
  renderGrid();

}

async function renderComposer(vc, { id }) {
  const composer = await api(`/composers/${encodeURIComponent(id)}`);
  const works = composer.works || [];
  const tracks = composer.tracks || [];
  const albums = composer.albums || [];

  vc.innerHTML = `
    <div class="topbar">
      <button class="back-btn" onclick="handleHistoryBack('composers')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
      <div class="discover-actions">
        <button class="btn btn-ghost" id="composer-open-all-btn">All Composers</button>
      </div>
    </div>
    <div class="artist-hero">
      <div class="artist-hero-art composer-placeholder">${escHtml((composer.name || '?').charAt(0).toUpperCase())}</div>
      <div class="artist-hero-info">
        <div class="section-title">${escHtml(composer.name)}</div>
        <div class="section-sub">${composer.work_count || 0} works · ${composer.album_count || 0} albums · ${composer.track_count || 0} tracks · ${composer.total_duration_hours || 0}h</div>
      </div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Works</div>
          <div class="discover-section-sub">Grouped by work title when metadata is available.</div>
        </div>
      </div>
      <div class="credits-grid" id="composer-works-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Albums</div>
          <div class="discover-section-sub">Albums containing tracks by this composer.</div>
        </div>
      </div>
      <div class="grid" id="composer-albums-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Representative Tracks</div>
          <div class="discover-section-sub">Direct jump points into the catalog.</div>
        </div>
      </div>
      <div class="discover-track-list" id="composer-tracks-list"></div>
    </div>
  `;

  UI['composer-open-all-btn']?.addEventListener('click', () => navigate('composers'));

  const worksGrid = UI['composer-works-grid'];
  if (!works.length) {
    worksGrid.innerHTML = `<div class="credits-card"><div class="zone-title">No work metadata</div><div class="zone-sub">This composer is present, but the source files do not expose separate work titles yet.</div></div>`;
  } else {
    const workFragment = document.createDocumentFragment();
    works.forEach(work => {
      const card = document.createElement('div');
      card.className = 'credits-card';
      card.style.cursor = 'pointer';
      card.innerHTML = `
        <div class="credits-title">Work</div>
        <div class="zone-title">${escHtml(work.work_title)}</div>
        <div class="zone-sub">${work.track_count || 0} tracks · ${work.album_count || 0} albums</div>
        <div class="zone-meta">${work.first_year || 'Unknown year'}${work.latest_year && work.latest_year !== work.first_year ? ` → ${work.latest_year}` : ''}</div>
      `;
      card.addEventListener('click', () => navigate('work', { id: makeWorkId(composer.name, work.work_title) }));
      workFragment.appendChild(card);
    });
    worksGrid.appendChild(workFragment);
  }

  const albumsGrid = UI['composer-albums-grid'];
  if (!albums.length) {
    albumsGrid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No albums found.</div>`;
  } else {
    const albumFragment = document.createDocumentFragment();
    albums.forEach(album => albumFragment.appendChild(makeAlbumCard(album)));
    albumsGrid.appendChild(albumFragment);
  }

  const trackList = UI['composer-tracks-list'];
  if (!tracks.length) {
    trackList.innerHTML = `<div style="color:var(--muted);font-size:13px">No tracks found.</div>`;
  } else {
    const trackFragment = document.createDocumentFragment();
    tracks.slice(0, 60).forEach((track, index) => {
      const row = makeTrackRow(track, index + 1, true);
      row.addEventListener('click', () => playTrack(track, tracks, index));
      trackFragment.appendChild(row);
    });
    trackList.appendChild(trackFragment);
  }
}

async function renderWorks(vc) {
  let allWorks = await api('/works?limit=1000');
  allWorks = allWorks || [];

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('works')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Works</div>
      <div class="section-sub" id="works-count">${allWorks.length} works</div>
    </div>
    <div class="focus-bar"><input id="works-filter" type="search" aria-label="Filter works" placeholder="Filter works and composers…" maxlength="128" class="catalog-filter-input"></div>
    <div class="credits-grid" id="works-grid"></div>
  `;

  const grid = UI['works-grid'];
  const countEl = UI['works-count'];

  const filterWorks = () => {
    const q = (UI['works-filter']?.value || '').trim();
    return q ? allWorks.filter(item =>
      chineseMatch(item.work_title || '', q) || chineseMatch(item.composer || '', q)
    ) : allWorks;
  };

  const renderGrid = () => {
    const data = filterWorks();
    countEl.textContent = `${data.length} work${data.length === 1 ? '' : 's'}`;
    if (!data.length) {
      grid.innerHTML = `<div class="credits-card"><div class="zone-title">No works found</div><div class="zone-sub">Try a different composer or work title.</div></div>`;
      return;
    }
    grid.innerHTML = '';
    const fragment = document.createDocumentFragment();
    data.forEach(work => fragment.appendChild(makeWorkCard(work)));
    grid.appendChild(fragment);
  };

  setupSearch({ autoFocus: false });
  UI['works-filter']?.addEventListener('input', renderGrid);
  renderGrid();
}

async function renderWork(vc, { id, role = '', credit = '', version = '' }) {
  const { composer, title } = parseWorkId(id);
  const filterQuery = role && credit ? `&role=${encodeURIComponent(role)}&name=${encodeURIComponent(credit)}` : '';
  const versionQuery = version ? `&version=${encodeURIComponent(version)}` : '';
  const work = await api(`/works/detail?composer=${encodeURIComponent(composer)}&title=${encodeURIComponent(title)}${filterQuery}${versionQuery}`);
  const albums = work.albums || [];
  const tracks = work.tracks || [];
  const performers = work.performers || [];
  const conductors = work.conductors || [];
  const ensembles = work.ensembles || [];
  const selectedFilter = work.selected_filter;
  const versions = work.versions || [];
  const selectedVersion = work.selected_version;

  const renderFilterPills = (items, pillRole) => items.length
    ? items.map(item => {
        const isActive = selectedFilter && selectedFilter.role === pillRole && selectedFilter.name === item.name;
        return `<button class="credits-pill work-filter-pill" data-role="${pillRole}" data-name="${escHtml(item.name)}" style="${isActive ? 'background:var(--gold);color:#111;border-color:var(--gold)' : ''}">${escHtml(item.name)} <span style="color:${isActive ? '#111' : 'var(--dim)'}">${item.track_count}</span></button>`;
      }).join('')
    : '<div class="zone-sub">No tags found.</div>';

  vc.innerHTML = `
    <div class="topbar">
      <button class="back-btn" onclick="handleHistoryBack('works')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
      <div class="discover-actions">
        <button class="btn btn-ghost" id="work-open-all-btn">All Works</button>
      </div>
    </div>
    <div class="artist-hero">
      <div class="artist-hero-art composer-placeholder">${escHtml((work.work_title || '?').charAt(0).toUpperCase())}</div>
      <div class="artist-hero-info">
        <div class="section-title">${escHtml(work.work_title)}</div>
        <div class="section-sub">${escHtml(work.composer)} · ${work.album_count || 0} albums · ${work.track_count || 0} tracks · ${work.total_duration_hours || 0}h</div>
        <div class="album-meta-row" style="margin-top:12px">
          ${work.first_year ? `<div class="album-meta-item">${work.first_year}${work.latest_year && work.latest_year !== work.first_year ? ` → ${work.latest_year}` : ''}</div>` : ''}
          <div class="album-meta-item">Work View</div>
        </div>
      </div>
    </div>
    <div class="credits-grid">
      <div class="credits-card">
        <div class="credits-title">Composer</div>
        <div class="credits-list">
          <button class="credits-pill" id="work-composer-pill">${escHtml(work.composer)}</button>
        </div>
      </div>
      ${selectedFilter ? `
        <div class="credits-card">
          <div class="credits-title">Recording Filter</div>
          <div class="credits-list">
            <span class="credits-pill" style="background:var(--gold);color:#111;border-color:var(--gold)">${escHtml(selectedFilter.label)}: ${escHtml(selectedFilter.name)}</span>
            <button class="credits-pill" id="work-clear-filter-pill">Clear</button>
          </div>
        </div>
      ` : ''}
      ${selectedVersion ? `
        <div class="credits-card">
          <div class="credits-title">Selected Version</div>
          <div class="credits-list">
            <span class="credits-pill" style="background:var(--gold);color:#111;border-color:var(--gold)">${escHtml(selectedVersion.headline)}</span>
            <button class="credits-pill" id="work-clear-version-pill">All Versions</button>
          </div>
        </div>
      ` : ''}
      <div class="credits-card">
        <div class="credits-title">Conductors</div>
        <div class="credits-list">
          ${renderFilterPills(conductors, 'conductor')}
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Ensembles</div>
        <div class="credits-list">
          ${renderFilterPills(ensembles, 'ensemble')}
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Performers</div>
        <div class="credits-list">
          ${renderFilterPills(performers, 'performer')}
        </div>
      </div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Versions</div>
          <div class="discover-section-sub">Grouped by conductor, ensemble, performer, and year to separate different recordings.</div>
        </div>
      </div>
      <div class="credits-grid" id="work-versions-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Albums</div>
          <div class="discover-section-sub">Recordings in your library for this work.</div>
        </div>
      </div>
      <div class="grid" id="work-albums-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Tracks</div>
          <div class="discover-section-sub">Play any captured performance directly.</div>
        </div>
      </div>
      <div class="discover-track-list" id="work-tracks-list"></div>
    </div>
  `;

  UI['work-open-all-btn']?.addEventListener('click', () => navigate('works'));
  UI['work-composer-pill']?.addEventListener('click', () => navigate('composer', { id: work.composer }));
  UI['work-clear-filter-pill']?.addEventListener('click', () => navigate('work', { id, version }));
  UI['work-clear-version-pill']?.addEventListener('click', () => navigate('work', role && credit ? { id, role, credit } : { id }));
  $$('.work-filter-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const nextRole = btn.dataset.role;
      const nextCredit = btn.dataset.name;
      const isSame = selectedFilter && selectedFilter.role === nextRole && selectedFilter.name === nextCredit;
      navigate('work', isSame ? { id, version } : { id, role: nextRole, credit: nextCredit });
    });
  });

  const versionsGrid = UI['work-versions-grid'];
  if (!versions.length) {
    versionsGrid.innerHTML = `<div class="credits-card"><div class="zone-title">No versions detected</div><div class="zone-sub">This work currently resolves to a single recording shape in your library.</div></div>`;
  } else {
    const fragment = document.createDocumentFragment();
    versions.forEach(item => {
      const card = document.createElement('div');
      const isActive = selectedVersion && selectedVersion.key === item.key;
      card.className = 'credits-card';
      card.style.cursor = 'pointer';
      if (isActive) {
        card.style.borderColor = 'var(--gold)';
        card.style.boxShadow = '0 0 0 1px rgba(215,184,122,0.2) inset';
      }
      card.innerHTML = `
        <div class="credits-title">Version</div>
        <div class="zone-title">${escHtml(item.headline || 'Recording')}</div>
        <div class="zone-sub">${item.album_count || 0} albums · ${item.track_count || 0} tracks · ${item.total_duration_minutes || 0} min</div>
        <div class="zone-meta">
          ${item.conductor ? `Conductor: ${escHtml(item.conductor)} · ` : ''}
          ${item.ensemble ? `Ensemble: ${escHtml(item.ensemble)} · ` : ''}
          ${item.performer ? `Performer: ${escHtml(item.performer)} · ` : ''}
          ${item.year || 'Unknown year'}
        </div>
        ${item.albums?.length ? `<div class="zone-meta" style="margin-top:8px">${item.albums.slice(0, 3).map(album => escHtml(album.title)).join(' · ')}</div>` : ''}
      `;
      card.addEventListener('click', () => {
        const base = role && credit ? { id, role, credit } : { id };
        navigate('work', isActive ? base : { ...base, version: item.key });
      });
      fragment.appendChild(card);
    });
    versionsGrid.appendChild(fragment);
  }

  const albumsGrid = UI['work-albums-grid'];
  if (!albums.length) {
    albumsGrid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No albums found.</div>`;
  } else {
    const albumFragment = document.createDocumentFragment();
    albums.forEach(album => albumFragment.appendChild(makeAlbumCard(album)));
    albumsGrid.appendChild(albumFragment);
  }

  const trackList = UI['work-tracks-list'];
  if (!tracks.length) {
    trackList.innerHTML = `<div style="color:var(--muted);font-size:13px">No tracks found.</div>`;
  } else {
    const trackFragment = document.createDocumentFragment();
    tracks.slice(0, 80).forEach((track, index) => {
      const row = makeTrackRow(track, index + 1, true);
      row.addEventListener('click', () => playTrack(track, tracks, index));
      trackFragment.appendChild(row);
    });
    trackList.appendChild(trackFragment);
  }
}

async function renderCredits(vc) {
  const [performersRes, conductorsRes, ensemblesRes] = await Promise.all([
    api('/credits?role=performer&limit=120'),
    api('/credits?role=conductor&limit=120'),
    api('/credits?role=ensemble&limit=120'),
  ]);

  const buckets = {
    performer: performersRes?.items || [],
    conductor: conductorsRes?.items || [],
    ensemble: ensemblesRes?.items || [],
  };

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('credits')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Credits</div>
      <div class="section-sub">Browse your library by performers, conductors, and ensembles.</div>
    </div>
    <div class="focus-bar"><input id="credits-filter" type="search" aria-label="Filter credits" placeholder="Filter performers, conductors, ensembles…" maxlength="128" class="catalog-filter-input"></div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Performers</div>
          <div class="discover-section-sub" id="performers-count">${buckets.performer.length} performers</div>
        </div>
      </div>
      <div class="credits-grid" id="performers-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Conductors</div>
          <div class="discover-section-sub" id="conductors-count">${buckets.conductor.length} conductors</div>
        </div>
      </div>
      <div class="credits-grid" id="conductors-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Ensembles</div>
          <div class="discover-section-sub" id="ensembles-count">${buckets.ensemble.length} ensembles</div>
        </div>
      </div>
      <div class="credits-grid" id="ensembles-grid"></div>
    </div>
  `;

  function renderBucket(role, label) {
    const query = (UI['credits-filter']?.value || '').trim();
    const countEl = UI[`${role}s-count`];
    const grid = UI[`${role}s-grid`];
    const items = query
      ? buckets[role].filter(item => chineseMatch(item.name || '', query))
      : buckets[role];

    if (countEl) countEl.textContent = `${items.length} ${label.toLowerCase()}${items.length === 1 ? '' : 's'}`;
    if (!grid) return;
    grid.innerHTML = '';
    if (!items.length) {
      grid.innerHTML = `<div class="credits-card"><div class="zone-title">No ${label.toLowerCase()}s found</div><div class="zone-sub">Try a different search term.</div></div>`;
      return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach(item => fragment.appendChild(makeCreditCard(item, role, label)));
    grid.appendChild(fragment);
  }

  const rerender = () => {
    renderBucket('performer', 'Performer');
    renderBucket('conductor', 'Conductor');
    renderBucket('ensemble', 'Ensemble');
  };

  setupSearch({ autoFocus: false });
  UI['credits-filter']?.addEventListener('input', rerender);
  rerender();
}

async function renderCreditEntity(vc, { id }) {
  const { role, name } = parseCreditId(id);
  const detail = await api(`/credits/detail?role=${encodeURIComponent(role)}&name=${encodeURIComponent(name)}`);
  const works = detail.works || [];
  const albums = detail.albums || [];
  const tracks = detail.tracks || [];
  const composers = detail.composers || [];

  vc.innerHTML = `
    <div class="topbar">
      <button class="back-btn" onclick="handleHistoryBack('credits')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
      <div class="discover-actions">
        <button class="btn btn-ghost" id="credit-open-all-btn">All Credits</button>
      </div>
    </div>
    <div class="artist-hero">
      <div class="artist-hero-art composer-placeholder">${escHtml((detail.name || '?').charAt(0).toUpperCase())}</div>
      <div class="artist-hero-info">
        <div class="album-label">${escHtml(detail.label || 'Credit')}</div>
        <div class="section-title">${escHtml(detail.name)}</div>
        <div class="section-sub">${detail.work_count || 0} works · ${detail.album_count || 0} albums · ${detail.track_count || 0} tracks · ${detail.total_duration_hours || 0}h</div>
      </div>
    </div>
    <div class="credits-grid">
      <div class="credits-card">
        <div class="credits-title">Role</div>
        <div class="credits-list"><span class="credits-pill">${escHtml(detail.label || '')}</span></div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Composers</div>
        <div class="credits-list">
          ${composers.length ? composers.slice(0, 10).map(item => `<button class="credits-pill credit-composer-pill" data-composer="${escHtml(item.name)}">${escHtml(item.name)} <span style="color:var(--dim)">${item.track_count}</span></button>`).join('') : '<div class="zone-sub">No composer data found.</div>'}
        </div>
      </div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Works</div>
          <div class="discover-section-sub">Pieces associated with this credit.</div>
        </div>
      </div>
      <div class="credits-grid" id="credit-works-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Albums</div>
          <div class="discover-section-sub">Albums where this credit appears.</div>
        </div>
      </div>
      <div class="grid" id="credit-albums-grid"></div>
    </div>
    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Tracks</div>
          <div class="discover-section-sub">Direct play access to matching tracks.</div>
        </div>
      </div>
      <div class="discover-track-list" id="credit-tracks-list"></div>
    </div>
  `;

  UI['credit-open-all-btn']?.addEventListener('click', () => navigate('credits'));
  $$('.credit-composer-pill').forEach(btn => {
    btn.addEventListener('click', () => navigate('composer', { id: btn.dataset.composer }));
  });

  const worksGrid = UI['credit-works-grid'];
  if (!works.length) {
    worksGrid.innerHTML = `<div class="credits-card"><div class="zone-title">No works found</div><div class="zone-sub">This credit exists, but no grouped work metadata is available yet.</div></div>`;
  } else {
    const fragment = document.createDocumentFragment();
    works.forEach(work => fragment.appendChild(makeWorkCard(work, { role: detail.role, credit: detail.name })));
    worksGrid.appendChild(fragment);
  }

  const albumsGrid = UI['credit-albums-grid'];
  if (!albums.length) {
    albumsGrid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No albums found.</div>`;
  } else {
    const fragment = document.createDocumentFragment();
    albums.forEach(album => fragment.appendChild(makeAlbumCard(album)));
    albumsGrid.appendChild(fragment);
  }

  const trackList = UI['credit-tracks-list'];
  if (!tracks.length) {
    trackList.innerHTML = `<div style="color:var(--muted);font-size:13px">No tracks found.</div>`;
  } else {
    const fragment = document.createDocumentFragment();
    tracks.slice(0, 80).forEach((track, index) => {
      const row = makeTrackRow(track, index + 1, true);
      row.addEventListener('click', () => playTrack(track, tracks, index));
      fragment.appendChild(row);
    });
    trackList.appendChild(fragment);
  }
}

// ── Tracks ────────────────────────────────────────────────────────────────────

async function renderTracks(vc) {
  let currentPage = 1;
  let currentSort = 'title';
  let pageRequestSerial = 0;
  let searchTimer;
  const PAGE_SIZE = 200;

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('tracks')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
      <select class="sort-select" id="track-sort">
        <option value="title">Track</option>
        <option value="artist">Artist</option>
        <option value="album">Album</option>
        <option value="composer">Composer</option>
        <option value="quality">Quality</option>
        <option value="recent">Recently Added</option>
      </select>
    </div>
    <div class="section-header">
      <div class="section-title">Tracks</div>
      <div class="section-sub" id="tracks-count">Loading…</div>
    </div>
    <div class="track-list" id="tracks-list"></div>
    <div class="pagination" id="tracks-pagination">
      <button class="btn btn-ghost" id="tracks-prev-btn" disabled>← Prev</button>
      <span class="page-info" id="tracks-page-info">Page 1</span>
      <button class="btn btn-ghost" id="tracks-next-btn">Next →</button>
    </div>
  `;

  const list = UI['tracks-list'];
  const countEl = UI['tracks-count'];
  const prevBtn = UI['tracks-prev-btn'];
  const nextBtn = UI['tracks-next-btn'];
  const pageInfo = UI['tracks-page-info'];
  const sortSelect = UI['track-sort'];
  const input = UI['search-input'];

  async function renderPage() {
    clearTimeout(searchTimer);
    if (state.view !== 'tracks' || !list.isConnected) return;
    const serial = ++pageRequestSerial;
    const page = currentPage;
    const offset = (page - 1) * PAGE_SIZE;
    const data = await api(`/tracks?limit=${PAGE_SIZE}&offset=${offset}&sort=${encodeURIComponent(currentSort)}`);
    // Sorting and paging share one latest-request boundary. A response from a
    // previous view instance must not update even its detached DOM.
    if (serial !== pageRequestSerial || state.view !== 'tracks' || !list.isConnected) return;
    const tracks = data?.tracks || [];
    const total = data?.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

    list.innerHTML = '';
    if (!tracks.length) {
      list.innerHTML = `<div style="padding:24px 32px;color:var(--muted);font-size:13px">No tracks found.</div>`;
    } else {
      const fragment = document.createDocumentFragment();
      tracks.forEach((track, index) => {
        const row = makeTrackRow(track, offset + index + 1, true);
        row.addEventListener('click', () => playTrack(track, tracks, index));
        fragment.appendChild(row);
      });
      list.appendChild(fragment);
    }

    countEl.textContent = `${total.toLocaleString()} tracks`;
    pageInfo.textContent = `Page ${page} / ${totalPages}`;
    prevBtn.disabled = page <= 1;
    nextBtn.disabled = page >= totalPages;
  }

  // Typing in the shared topbar navigates to the unified Artists -> Albums ->
  // Tracks search view, so this view must not bind its own listener to the
  // same #search-input element.
  setupSearch({ autoFocus: false });

  sortSelect.addEventListener('change', e => {
    currentSort = e.target.value;
    currentPage = 1;
    renderPage();
  });

  prevBtn.addEventListener('click', () => {
    if (currentPage <= 1) return;
    currentPage--;
    renderPage();
  });

  nextBtn.addEventListener('click', () => {
    currentPage++;
    renderPage();
  });

  await renderPage();
}

// ── Genres ────────────────────────────────────────────────────────────────────

/* Genre strings come from a multi-value retail taxonomy, so they land in the
   library as comma-joined labels ("Musiques du monde, Asie, Japon, J-pop",
   "Pop, Rock, Metal", "粤语流行"). A flat list of those is unreadable, so we
   fold them into browsable families. Rules are ordered most-specific first and
   the first match wins, which lets the leading tag decide the family; the last
   family has no `match` and acts as the catch-all. A family may carry either a
   `match` regex or a `test` predicate, never both. */
const GENRE_FAMILIES = [
  {
    id: 'film', label: 'Film & Score', blurb: 'Soundtracks, TV themes & game scores', hue: 258,
    match: /film|bandes\s+originales|sound[\s-]?track|anime|jeux\s+vidéo|séries\s+tv|comédies\s+musicales/i,
  },
  {
    id: 'classical', label: 'Classical', blurb: 'Orchestral, chamber & instrumental', hue: 44,
    match: /classique|classical|symphonique|concertante|concertos?|musique\s+de\s+chambre|profane|instrumental/i,
  },
  {
    id: 'asianpop', label: 'Asian Pop', blurb: 'C-pop, J-pop & K-pop', hue: 348,
    match: /j-?pop|k-?pop|mandopop|cantopop|国语|粤語|粤语|華語|华语|広東|ポップ/i,
  },
  {
    id: 'electronic', label: 'Electronic', blurb: 'Club, techno & downtempo', hue: 180,
    match: /électronique|electronic|techno|house|drum\s*&\s*bass|d\.?n\.?b|lounge|downtempo|trance|\bdance\b/i,
  },
  {
    id: 'jazz', label: 'Jazz & Blues', blurb: 'Standards, vocal, fusion & blues', hue: 28,
    match: /jazz|blues|gospel|be\s?bop/i,
  },
  {
    id: 'hiphop', label: 'Hip-Hop, Soul & R&B', blurb: 'Rap, funk, disco & modern soul', hue: 288,
    match: /hip-?hop|\brap\b|soul|funk|r&b|disco/i,
  },
  {
    id: 'indie', label: 'Indie & Alternative', blurb: 'Alternative, singer-songwriter & new wave', hue: 96,
    match: /ind[eé]|alternative|alternativa|singer-?songwriter/i,
  },
  {
    id: 'folk', label: 'Folk & Country', blurb: 'Roots, acoustic & traditional', hue: 70,
    match: /country|folk|roots|bluegrass|celtic/i,
  },
  {
    id: 'rock', label: 'Rock & Metal', blurb: 'Hard rock, punk, metal & prog', hue: 8,
    // Two opt-outs keep the mainstream tags out of Rock while leaving the real
    // ones in: a bare "Pop, Rock" / "Rock, Pop" umbrella tag is pop-chart
    // vocabulary, and "Variété internationale" is a pop-chart marker — but
    // "Pop, Rock, Hard Rock" and "Pop, Rock, Punk - New Wave" are genuinely
    // Rock. `rock\b` is what keeps Reggae's "Rocksteady" out.
    test: s => /rock\b|metal|punk|new\s*wave|nouvelle\s*vague/i.test(s)
      && !/variété/i.test(s)
      && !/^\s*(pop\s*,\s*rock|rock\s*,\s*pop)\s*$/i.test(s),
  },
  {
    id: 'pop', label: 'Pop & Mainstream', blurb: 'Chart pop, rock-pop & variety', hue: 322,
    match: /\bpop\b|variété/i,
  },
  {
    id: 'world', label: 'World & Regional', blurb: 'Asia, Latin, Africa & Europe', hue: 202,
    match: /monde|asie|asia|afrique|europe|latin|reggae|reggaeton|bossa|indienne|indien|corée|kraut|\bdub\b/i,
  },
  {
    id: 'mood', label: 'Mood & Scene', blurb: 'Ambient, holiday, spoken & uncategorised', hue: 45, sat: 16,
  },
];

function genreFamilyOf(name) {
  const raw = String(name || '');
  for (const fam of GENRE_FAMILIES) {
    if (fam.match ? fam.match.test(raw) : fam.test?.(raw)) return fam;
  }
  return GENRE_FAMILIES[GENRE_FAMILIES.length - 1];
}

/* Display names only — the raw string stays the key the /genres and
   /genres/<id>/albums endpoints are called with, so translating this never
   changes what a chip filters to. The library mixes French retail labels,
   English ones and CJK titles in the same list ("Musiques du monde, Asie",
   "Pop", "粤语流行"), so this maps one comma-separated segment at a time and
   passes anything unknown through untouched. */
const GENRE_SEGMENT_LABELS = {
  // world / regional
  'musiques du monde': 'World Music',
  'musique du monde': 'World Music',
  'asie': 'Asia',
  'asia': 'Asia',
  'international/far east & asia': 'International / Far East & Asia',
  'japon': 'Japan',
  'corée': 'Korea',
  'coree': 'Korea',
  'afrique': 'Africa',
  'europe': 'Europe',
  'amérique latine': 'Latin America',
  'amerique latine': 'Latin America',
  'brésil': 'Brazil',
  'bresil': 'Brazil',
  'allemagne': 'Germany',
  'italie': 'Italy',
  'musique indienne': 'Indian Music',
  'chanson française': 'French Chanson',
  'chanson francaise': 'French Chanson',
  'reggae': 'Reggae',
  'dub': 'Dub',
  'ska & rocksteady': 'Ska & Rocksteady',
  'afrobeat': 'Afrobeat',
  'reggaeton': 'Reggaeton',
  'urban latino': 'Urban Latino',
  'bossa nova': 'Bossa Nova',
  // pop / rock
  'pop': 'Pop',
  'rock': 'Rock',
  'hard rock': 'Hard Rock',
  'metal': 'Metal',
  'punk - new wave': 'Punk – New Wave',
  'rock progressif': 'Progressive Rock',
  'rock en español': 'Rock en Español',
  'variété internationale': 'International Pop',
  'variete internationale': 'International Pop',
  'alternatif et indé': 'Alternative & Indie',
  'alternatif et inde': 'Alternative & Indie',
  'indie pop': 'Indie Pop',
  'alternative': 'Alternative',
  'alternativa': 'Alternative',
  'singer-songwriter': 'Singer-Songwriter',
  // hip-hop / soul / r&b
  'rap': 'Rap',
  'hip-hop': 'Hip-Hop',
  'hip-hop/rap': 'Hip-Hop / Rap',
  'soul': 'Soul',
  'funk': 'Funk',
  'disco': 'Disco',
  'r&b': 'R&B',
  'r&b/disco funk': 'R&B / Disco Funk',
  'r&b/pop': 'R&B / Pop',
  'contemporary r&b': 'Contemporary R&B',
  // jazz / blues / folk
  'jazz': 'Jazz',
  'jazz vocal': 'Vocal Jazz',
  'jazz fusion & jazz rock': 'Jazz Fusion & Jazz-Rock',
  'jazz contemporain': 'Contemporary Jazz',
  'be bop': 'Be Bop',
  'gospel': 'Gospel',
  'blues': 'Blues',
  'country': 'Country',
  'country & folk': 'Country & Folk',
  'folk': 'Folk',
  // electronic
  'électronique': 'Electronic',
  'electronique': 'Electronic',
  'dance': 'Dance',
  'house': 'House',
  'techno': 'Techno',
  'drum & bass': 'Drum & Bass',
  'lounge': 'Lounge',
  'downtempo': 'Downtempo',
  // classical
  'classique': 'Classical',
  'classical': 'Classical',
  'musique concertante': 'Concert Music',
  'concertos pour violoncelle': 'Cello Concertos',
  'musique de chambre': 'Chamber Music',
  'trios': 'Trios',
  'musique symphonique': 'Symphonic Music',
  'musique vocale (profane et sacrée)': 'Vocal Music (Secular & Sacred)',
  'instrumental': 'Instrumental',
  // film
  'film': 'Film',
  'bandes originales de films': 'Film Soundtracks',
  'film soundtracks': 'Film Soundtracks',
  'soundtrack': 'Soundtrack',
  'anime': 'Anime',
  'jeux vidéo': 'Video Games',
  'jeux video': 'Video Games',
  'séries tv': 'TV Series',
  'series tv': 'TV Series',
  'comédies musicales': 'Musical Comedies',
  'comedies musicales': 'Musical Comedies',
  // mood / misc
  'ambiance': 'Ambient',
  'musiques de noël': 'Christmas Music',
  'musiques de noel': 'Christmas Music',
  'diction': 'Spoken Word',
  'humour': 'Humor',
  'enfants': "Children's",
  'divers': 'Variety',
  // asia-pop
  'j-pop': 'J-Pop',
  'k-pop': 'K-Pop',
  'mandopop': 'Mandopop',
  'cantopop': 'Cantopop',
  '国语流行': 'Mandarin Pop',
  '粤語流行': 'Cantonese Pop',
  '粤语流行': 'Cantonese Pop',
  '広東ポップ': 'Cantonese Pop',
  'ポップ': 'J-Pop',
  '华语流行乐': 'Chinese Pop',
  '華語流行樂': 'Chinese Pop',
  '华语组合': 'Chinese Groups',
  '華語組合': 'Chinese Groups',
  'コンテンポラリー r&b': 'Contemporary R&B',
  'alternativa y rock en español': 'Alternative & Spanish Rock',
};

function genreSegmentLabel(seg) {
  const key = String(seg || '').trim().toLowerCase();
  return GENRE_SEGMENT_LABELS[key] || String(seg || '').trim();
}

function genreDisplayLabel(raw) {
  return String(raw || '').split(',').map(genreSegmentLabel).join(', ');
}

/* "Pop", "POP" and "pop" are three separate rows in the database but one style
   to a reader. Fold them together, sum the counts, and keep the variant with
   the most albums as the row that a click opens.

   The key is the *set* of translated tags, so anything that reads the same to a
   user merges regardless of spelling or word order: "Asie, Musiques du monde"
   joins "Musiques du monde, Asie", "Rock, Pop" joins "Pop, Rock", and the
   Japanese 広東ポップ joins the Chinese 粤语流行. */
function genreMergeKey(raw) {
  return String(raw || '')
    .split(',')
    .map(seg => genreSegmentLabel(seg).toLowerCase())
    .sort()
    .join(' | ');
}

function mergeGenreAliases(items) {
  const merged = new Map();
  for (const item of items) {
    const count = Number(item.count || 0);
    const key = genreMergeKey(item.genre);
    const hit = merged.get(key);
    if (!hit) {
      merged.set(key, {
        ...item, count,
        label: genreDisplayLabel(item.genre),
        aliases: [item.genre],
        raw: item.genre,
        topCount: count,
      });
      continue;
    }
    hit.aliases.push(item.genre);
    hit.count += count;
    // Keep the spelling the API is most likely to recognise as the click target.
    if (count > hit.topCount) {
      hit.raw = item.genre;
      hit.topCount = count;
    }
  }
  return [...merged.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

function groupGenresByFamily(genres) {
  const buckets = GENRE_FAMILIES.map(f => ({ ...f, items: [], albums: 0 }));
  const byId = new Map(buckets.map(b => [b.id, b]));
  for (const g of genres) {
    const bucket = byId.get(genreFamilyOf(g.genre).id);
    bucket.items.push(g);
    bucket.albums += Number(g.count || 0);
  }
  for (const bucket of buckets) {
    bucket.items = mergeGenreAliases(bucket.items);
    // Album totals are counted from the raw rows, but the shelf reports the
    // merged ones so the tile total always equals the sum of its chips.
    bucket.albums = bucket.items.reduce((s, i) => s + Number(i.count || 0), 0);
  }
  return buckets
    .filter(b => b.items.length)
    .sort((a, b) => b.albums - a.albums || a.label.localeCompare(b.label));
}

async function renderGenres(vc, params = {}) {
  const genres = await api('/genres') || [];
  const families = groupGenresByFamily(genres);
  const biggest = families.reduce((m, f) => Math.max(m, f.albums), 1);

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('genres')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Genres</div>
      <div class="section-sub" id="genres-count">${genres.length} styles · ${families.length} families</div>
    </div>
    <div class="focus-bar">
      <input id="genres-filter" type="search" aria-label="Filter genres" placeholder="Filter styles…" maxlength="128" class="catalog-filter-input">
      <span class="gnr-tip">Select a family to browse its styles</span>
    </div>
    <div class="gnr-stage">
      <div class="gnr-grid" id="gnr-grid" role="tablist" aria-label="Genre families">
        ${families.map((f, i) => genreTileHtml(f, i, biggest)).join('')}
      </div>
      <div class="gnr-panels" id="gnr-panels">
        ${families.map(f => genrePanelHtml(f, biggest)).join('')}
      </div>
      <div class="gnr-empty" id="gnr-empty" hidden>No styles match that filter.</div>
    </div>
  `;

  setupSearch({ autoFocus: false });

  const gridEl = UI['gnr-grid'];
  const panelsEl = UI['gnr-panels'];
  const emptyEl = UI['gnr-empty'];
  const countEl = UI['genres-count'];
  const filterEl = UI['genres-filter'];

  let activeId = families.some(f => f.id === params?.family) ? params.family : families[0]?.id || null;

  function setActive(id) {
    activeId = id;
    for (const tile of gridEl.querySelectorAll('.gnr-tile')) {
      const on = tile.dataset.family === id;
      tile.setAttribute('aria-selected', on ? 'true' : 'false');
      tile.classList.toggle('is-open', on);
      tile.tabIndex = on ? 0 : -1;
    }
    for (const panel of panelsEl.querySelectorAll('.gnr-panel')) {
      panel.hidden = panel.dataset.family !== id;
    }
  }

  /* A tap on a tile must hand the user off to the styles below it.

   `block: 'start'` rather than 'nearest': on a phone the twelve tiles fill the
   first screen, so a tap that leaves the page exactly where it was gives no
   feedback at all and the section that was just chosen stays off-screen. This
   always parks the panel just under the sticky header, so the next thing on
   screen is the list to pick from.

   The browser resolves the scroll ancestor itself — #main or the document,
   depending on layout — so no guesswork about the scroller is needed.
   scroll-margin-top is set from the live header rather than a hardcoded padding
   so the panel is never tucked up underneath it.

   (An earlier version never scrolled at all: it guarded the call behind "was
   this tile closed?", which setActive had already answered on the line above.)

   A panel that is already on screen is left alone: on a desktop window the
   shelf and its panel share the viewport, and scrolling on every tap leaves
   the page still drifting under the next click. */
  function revealGenrePanel(panel, viewRoot) {
    if (!panel) return;

    panel.classList.remove('is-revealed');
    void panel.offsetWidth;              // restart the flash when re-tapping
    panel.classList.add('is-revealed');

    const topbar = viewRoot?.querySelector('.topbar');
    const safeTop = Math.ceil((topbar?.getBoundingClientRect().bottom || 0) + 10);

    const box = panel.getBoundingClientRect();
    const viewport = window.innerHeight || document.documentElement.clientHeight;
    if (box.height > 0 && box.top >= safeTop && box.bottom <= viewport - 8) return;

    panel.style.scrollMarginTop = `${safeTop}px`;
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  /* A press has to reach the tile no matter what the shelf is doing underneath
     the cursor. Pointer capture pins the whole press — down, up, and the
     compatibility click that follows — to the tile that took it, so a page still
     sliding from the previous tap can no longer swallow the next one.

     Capture also reports the tile even when the pointer ended up somewhere else
     by the time it was released, so the press origin decides tap from drag.
     Keyboard activation sends no pointer events and is unaffected.

     `press` dies on pointerup but `dragged` stays latched until the click that
     follows consumes it: the two events are separated by mouseup, so clearing
     the origin early would erase the very move that made it a drag. */
  let press = null;
  let dragged = false;

  gridEl.addEventListener('pointerdown', e => {
    const tile = e.target.closest('.gnr-tile');
    if (!tile || tile.classList.contains('is-empty')) { press = null; return; }
    press = { tile, x: e.clientX, y: e.clientY };
    try { tile.setPointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
  });

  gridEl.addEventListener('pointermove', e => {
    if (press && (Math.abs(e.clientX - press.x) > 8 || Math.abs(e.clientY - press.y) > 8)) dragged = true;
  });

  gridEl.addEventListener('pointerup', () => { press = null; });
  gridEl.addEventListener('pointercancel', () => { press = null; dragged = false; });

  gridEl.addEventListener('click', e => {
    const tile = e.target.closest('.gnr-tile');
    if (!tile || tile.classList.contains('is-empty')) return;
    if (dragged) { dragged = false; return; }
    const id = tile.dataset.family;
    setActive(id);
    revealGenrePanel(panelsEl.querySelector(`.gnr-panel[data-family="${id}"]`), vc);
  });

  gridEl.addEventListener('keydown', e => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const tiles = [...gridEl.querySelectorAll('.gnr-tile:not(.is-empty)')];
    const at = tiles.indexOf(document.activeElement);
    if (at < 0) return;
    e.preventDefault();
    const next = tiles[(at + (e.key === 'ArrowRight' ? 1 : tiles.length - 1)) % tiles.length];
    next.focus();
  });

  panelsEl.addEventListener('click', e => {
    const chip = e.target.closest('.gnr-chip');
    if (!chip) return;
    navigate('genre', { id: chip.dataset.genre });
  });

  function applyFilter(raw) {
    const q = String(raw || '').trim().toLowerCase();
    let hits = 0;
    let firstMatch = null;

    for (const fam of families) {
      const matched = q ? fam.items.filter(i => i.genre.toLowerCase().includes(q)) : fam.items;
      const tile = gridEl.querySelector(`.gnr-tile[data-family="${fam.id}"]`);
      const panel = panelsEl.querySelector(`.gnr-panel[data-family="${fam.id}"]`);
      tile.classList.toggle('is-empty', !matched.length);
      panel.classList.toggle('is-empty', !matched.length);
      if (matched.length) { hits += matched.length; if (!firstMatch) firstMatch = fam.id; }

      const shown = matched.slice(0, 14);
      panel.querySelector('.gnr-chips').innerHTML = shown.map(i => genreChipHtml(i, fam.albums)).join('');
      const more = matched.length - shown.length;
      const moreEl = panel.querySelector('.gnr-more');
      moreEl.hidden = more <= 0;
      moreEl.textContent = more > 0 ? `+${more} more — refine the filter to narrow down` : '';
      panel.querySelector('.gnr-panel-count').textContent =
        `${matched.length} of ${fam.items.length} · ${matched.reduce((s, i) => s + Number(i.count || 0), 0).toLocaleString()} albums`;
    }

    emptyEl.hidden = hits > 0;
    countEl.textContent = q
      ? `${hits} of ${genres.length} styles · ${families.length} families`
      : `${genres.length} styles · ${families.length} families`;

    if (q && activeId && gridEl.querySelector(`.gnr-tile[data-family="${activeId}"]`)?.classList.contains('is-empty')) {
      setActive(firstMatch);
      // The panel just swapped under the user; put it back in view so the
      // matches the filter just found are actually on screen.
      if (firstMatch) revealGenrePanel(panelsEl.querySelector(`.gnr-panel[data-family="${firstMatch}"]`), vc);
    }
  }

  filterEl?.addEventListener('input', e => applyFilter(e.target.value));

  attachGenreTilt(gridEl);
  applyFilter('');
  if (activeId) setActive(activeId);
}

function genreTileHtml(fam, index, biggest) {
  const width = Math.max(6, Math.round(fam.albums / biggest * 100));
  const styles = `${fam.items.length} style${fam.items.length === 1 ? '' : 's'}`;
  // The button is a stationary hit target; the card inside it owns the tilt and
  // the lift, so hovering can never slide the press target out from under the
  // cursor. Keep every painted layer inside the card.
  return `
    <button type="button" class="gnr-tile" role="tab" data-family="${fam.id}"
            aria-selected="false" tabindex="-1"
            style="--h:${fam.hue};--s:${fam.sat ?? 48};--delay:${index * 32}ms">
      <span class="gnr-tile-card">
        <span class="gnr-tile-art" aria-hidden="true">
          <img src="/static/img/genres/${fam.id}.jpg" alt="" loading="lazy" decoding="async" onerror="this.remove()">
        </span>
        <span class="gnr-tile-veil" aria-hidden="true"></span>
        <span class="gnr-tile-sheen" aria-hidden="true"></span>
        <span class="gnr-tile-body">
          <span class="gnr-tile-kicker">${styles}</span>
          <span class="gnr-tile-label">${escHtml(fam.label)}</span>
          <span class="gnr-tile-blurb">${escHtml(fam.blurb)}</span>
          <span class="gnr-tile-foot">
            <span class="gnr-tile-count">${fam.albums.toLocaleString()}</span>
            <span class="gnr-tile-unit">albums</span>
          </span>
          <span class="gnr-tile-meter" aria-hidden="true"><span class="gnr-tile-fill" style="width:${width}%"></span></span>
        </span>
      </span>
    </button>`;
}

function genrePanelHtml(fam, biggest) {
  const shown = fam.items.slice(0, 14);
  const more = fam.items.length - shown.length;
  return `
    <section class="gnr-panel" data-family="${fam.id}" style="--h:${fam.hue}" aria-label="${escHtml(fam.label)}" hidden>
      <div class="gnr-panel-head">
        <span class="gnr-panel-dot" aria-hidden="true"></span>
        <h2 class="gnr-panel-title">${escHtml(fam.label)}</h2>
        <span class="gnr-panel-count">${fam.items.length} of ${fam.items.length} · ${fam.albums.toLocaleString()} albums</span>
      </div>
      <div class="gnr-chips">
        ${shown.map(i => genreChipHtml(i, biggest)).join('')}
      </div>
      <div class="gnr-more" hidden></div>
    </section>`;
}

function genreChipHtml(item, biggest) {
  const width = Math.max(4, Math.round(Number(item.count || 0) / biggest * 100));
  // data-genre carries the raw database string so a click always queries a
  // label the API knows; only the visible name is translated.
  const merged = (item.aliases || []).length > 1;
  return `
    <button type="button" class="gnr-chip" data-genre="${escHtml(item.raw)}"
            title="${escHtml((item.aliases || []).join(' · '))}">
      <span class="gnr-chip-name">${escHtml(item.label)}${merged ? `<span class="gnr-chip-alias">+${item.aliases.length - 1}</span>` : ''}</span>
      <span class="gnr-chip-count">${Number(item.count || 0).toLocaleString()}</span>
      <span class="gnr-chip-meter" aria-hidden="true"><span class="gnr-chip-fill" style="width:${width}%"></span></span>
    </button>`;
}

/* Pointer-driven depth. Tiles lean toward the cursor and the sheen tracks it, so
   the grid reads as a shallow 3D shelf rather than a flat list. Everything is
   skipped for coarse pointers and for reduced-motion users.

   Two details keep the lean from fighting the user:

   - The rotation is driven by the button, which never transforms, so its box is
     the hit target and the cursor can no longer fall off a tile that just moved.
   - The rotation reads a normalised cursor position with the outer 18% of each
     edge clamped flat. Near an edge the old per-pixel angle swung the card's near
     rim away from the cursor, hover reset, the rim swung back — a visible
     flicker that also swallowed presses. The bloom still tracks the real cursor. */
function attachGenreTilt(root) {
  const fine = window.matchMedia?.('(hover: hover) and (pointer: fine)');
  const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  if (!fine?.matches || calm?.matches) return;

  const EDGE = 0.18;
  const flatten = v => (v < EDGE ? EDGE : v > 1 - EDGE ? 1 - EDGE : v);

  for (const tile of root.querySelectorAll('.gnr-tile')) {
    let frame = 0;
    let rx = 0, ry = 0, mx = 50, my = 50;

    const paint = () => {
      frame = 0;
      tile.style.setProperty('--rx', rx.toFixed(2) + 'deg');
      tile.style.setProperty('--ry', ry.toFixed(2) + 'deg');
      tile.style.setProperty('--mx', mx.toFixed(1) + '%');
      tile.style.setProperty('--my', my.toFixed(1) + '%');
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(paint); };

    tile.addEventListener('pointermove', e => {
      const box = tile.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const nx = (e.clientX - box.left) / box.width;
      const ny = (e.clientY - box.top) / box.height;
      mx = nx * 100;
      my = ny * 100;
      const fx = flatten(nx), fy = flatten(ny);
      ry = (fx - 0.5) * 13;
      rx = (0.5 - fy) * 10;
      schedule();
    });

    tile.addEventListener('pointerleave', () => {
      rx = 0; ry = 0; mx = 50; my = 50;
      schedule();
    });
  }
}

async function renderGenre(vc, { id }) {
  const data = await api(`/genres/${encodeURIComponent(id)}/albums?limit=1000&sort=year`);
  const albums = data?.albums || [];
  const family = genreFamilyOf(id);

  vc.innerHTML = `
    <div class="topbar">
      <button class="back-btn" onclick="handleHistoryBack('genres')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
    </div>
    <div class="section-header">
      <div class="section-title">${escHtml(genreDisplayLabel(id))}</div>
      <div class="section-sub">
        <button type="button" class="gnr-crumb" data-family="${family.id}" style="--h:${family.hue}">${escHtml(family.label)}</button>
        <span> · ${albums.length} albums</span>
      </div>
    </div>
    <div class="grid" id="genre-albums-grid"></div>
  `;

  UI['gnr-crumb']?.addEventListener('click', () => navigate('genres', { family: family.id }));

  const grid = UI['genre-albums-grid'];
  if (!albums.length) {
    grid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No albums in this genre yet.</div>`;
    return;
  }
  const fragment = document.createDocumentFragment();
  albums.forEach(album => fragment.appendChild(makeAlbumCard(album)));
  grid.appendChild(fragment);
}

// ── Playlists ─────────────────────────────────────────────────────────────────

async function renderPlaylists(vc) {
  const playlists = await api('/playlists') || [];

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('playlists')}
      <button class="btn btn-gold" id="create-playlist-btn">New Playlist</button>
    </div>
    <div class="section-header">
      <div class="section-title">Playlists</div>
      <div class="section-sub">${playlists.length} playlists</div>
    </div>
    <div class="grid" id="playlists-grid"></div>
  `;

  UI['create-playlist-btn'].addEventListener('click', async () => {
    const name = prompt('Playlist name', 'New Playlist');
    if (!name) return;
    const created = await api('/playlists', {
      method: 'POST',
      body: JSON.stringify({ name })
    });
    if (created?.id) {
      showToast(`Created ${created.name}`);
      navigate('playlist', { id: created.id });
    }
  });

  const grid = UI['playlists-grid'];
  if (!playlists.length) {
    grid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No playlists yet. Create one from this page or add a track with the ＋ button.</div>`;
    return;
  }

  const fragment = document.createDocumentFragment();
  playlists.forEach(playlist => fragment.appendChild(makePlaylistCard(playlist)));
  grid.appendChild(fragment);
}

async function renderPlaylist(vc, { id }) {
  const playlist = await api(`/playlists/${id}`);
  if (!playlist) {
    vc.innerHTML = `<div class="empty-state"><div class="empty-state-title">Playlist not found</div></div>`;
    return;
  }

  vc.innerHTML = `
    <div class="topbar playlist-toolbar">
      <button class="btn btn-ghost back-btn playlist-back-btn" onclick="handleHistoryBack('playlists')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
      <div class="playlist-toolbar-actions">
        <button class="btn btn-ghost" id="rename-playlist-btn">Rename</button>
        <button class="btn btn-ghost" id="delete-playlist-btn">Delete</button>
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">${escHtml(playlist.name)}</div>
      <div class="section-sub">${playlist.tracks.length} tracks</div>
    </div>
    <div class="track-list" id="playlist-tracks-list"></div>
  `;

  UI['rename-playlist-btn'].addEventListener('click', async () => {
    const name = prompt('Rename playlist', playlist.name);
    if (!name || name === playlist.name) return;
    const result = await api(`/playlists/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name })
    });
    if (result?.success) {
      showToast('Playlist renamed');
      navigate('playlist', { id }, false);
      renderPlaylist(vc, { id });
    }
  });

  UI['delete-playlist-btn'].addEventListener('click', async () => {
    const confirmed = confirm(`Delete playlist "${playlist.name}"?`);
    if (!confirmed) return;
    const result = await api(`/playlists/${id}`, { method: 'DELETE' });
    if (result?.success) {
      showToast('Playlist deleted');
      navigate('playlists');
    }
  });

  const list = UI['playlist-tracks-list'];
  if (!playlist.tracks.length) {
    list.innerHTML = `<div style="padding:24px 32px;color:var(--muted);font-size:13px">This playlist is empty. Add tracks with the ＋ button in albums, search, or tracks.</div>`;
    return;
  }

  const fragment = document.createDocumentFragment();
  playlist.tracks.forEach((track, index) => {
    const row = makeTrackRow(track, index + 1, true);
    row.querySelector('.track-add-btn')?.remove();
    const swipe = makeTrackSwipeRow(row, () => playTrack(track, playlist.tracks, index), () => {
      showConfirm({
        title: 'Remove from playlist?',
        body: `Remove “${escHtml(track.title)}” from “${escHtml(playlist.name)}”?`,
        confirmText: 'Confirm',
        onConfirm: async () => {
          const button = swipe.querySelector('.playlist-track-delete');
          if (button.disabled) return;
          button.disabled = true;
          try {
            const result = await api(`/playlists/${id}/tracks/${track.id}`, { method: 'DELETE' });
            if (result?.success) {
              showToast('Removed from playlist');
              if (swipe.isConnected) await renderPlaylist(vc, { id });
            } else showToast('Unable to remove track. Try again.');
          } catch { showToast('Unable to remove track. Try again.'); }
          finally { button.disabled = false; }
        },
      });
    });
    swipe.querySelector('.playlist-track-delete').setAttribute('aria-label', `Remove ${track.title} from playlist`);
    fragment.appendChild(swipe);
  });
  list.appendChild(fragment);
}

function makeTrackSwipeRow(row, play, action, { add = false } = {}) {
  const wrapper = document.createElement('div');
  wrapper.className = 'track-swipe-row ' + (add ? 'album-swipe-row' : 'playlist-swipe-row');
  const label = add ? 'Add to playlist' : 'Remove from playlist';
  const icon = add ? 'M3 6h8M3 11h8M3 16h5M16 17V5l5-1v11M16 17a2 2 0 1 1-2-2h2M21 15a2 2 0 1 1-2-2h2'
    : 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7';
  wrapper.innerHTML = `<button type="button" class="track-swipe-action ${add ? 'album-track-add' : 'playlist-track-delete'}" title="${label}" aria-label="${label}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
      <path d="${icon}"/>
    </svg></button>`;
  wrapper.appendChild(row);
  const button = wrapper.querySelector('button');
  let gesture = null;
  let suppressUntil = 0;
  const setOpen = open => {
    if (open) wrapper.parentElement?.querySelectorAll('.track-swipe-row.is-open').forEach(other => {
      if (other !== wrapper) other.classList.remove('is-open');
    });
    wrapper.classList.toggle('is-open', open);
  };
  row.tabIndex = 0;
  row.setAttribute('role', 'button');
  row.setAttribute('aria-description', `Swipe left or press Left Arrow to reveal ${label}.`);
  row.addEventListener('pointerdown', event => {
    if (event.isPrimary === false || event.button !== 0) return;
    gesture = { id: event.pointerId, x: event.clientX, y: event.clientY,
      base: wrapper.classList.contains('is-open') ? -72 : 0, offset: 0, dragging: false };
  });
  row.addEventListener('pointermove', event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (!gesture.dragging) {
      if (Math.abs(dy) > 10 && Math.abs(dy) >= Math.abs(dx)) { gesture = null; return; }
      if (Math.abs(dx) < 10) return;
      gesture.dragging = true;
      row.setPointerCapture?.(event.pointerId);
      wrapper.classList.add('is-dragging');
    }
    gesture.offset = Math.max(-72, Math.min(0, gesture.base + dx));
    row.style.transform = `translateX(${gesture.offset}px)`;
  });
  const finish = (event, cancelled = false) => {
    if (!gesture || gesture.id !== event.pointerId) return;
    if (gesture.dragging) {
      setOpen(cancelled ? gesture.base < 0 : gesture.offset < -36);
      suppressUntil = performance.now() + 400;
    }
    row.style.removeProperty('transform');
    wrapper.classList.remove('is-dragging');
    gesture = null;
  };
  row.addEventListener('pointerup', event => finish(event));
  row.addEventListener('pointercancel', event => finish(event, true));
  row.addEventListener('click', event => {
    if (performance.now() < suppressUntil) { event.preventDefault(); return; }
    if (wrapper.classList.contains('is-open')) { setOpen(false); return; }
    play();
  });
  row.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft') { event.preventDefault(); setOpen(true); button.focus(); }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.click(); }
    if (event.key === 'Escape' || event.key === 'ArrowRight') { event.preventDefault(); setOpen(false); }
  });
  button.addEventListener('click', event => { event.stopPropagation(); action(); });
  button.addEventListener('keydown', event => {
    if (event.key === 'Escape') { setOpen(false); row.focus(); }
  });
  return wrapper;
}

async function renderZones(vc, { refresh = false } = {}) {
  const [zones, playbackStatus] = await Promise.all([
    api(refresh ? '/zones?refresh=1' : '/zones'),
    api('/playback/status')
  ]);

  const activeZone = getActiveZone();
  const nativeDevices = zones?.native?.devices || [];
  const networkDevices = zones?.network?.devices || [];

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('zones')}
      <div class="discover-actions">
        <button class="btn btn-ghost" id="zones-refresh-btn">Refresh Devices</button>
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Zones</div>
      <div class="section-sub">Route playback between browser, native audio devices, and network renderers.</div>
    </div>
    <div class="discover-hero">
      <div class="discover-hero-copy">
        <div class="discover-kicker">Active Zone</div>
        <div class="section-title">${escHtml(activeZone.name)}</div>
        <div class="discover-sub">
          ${state.currentTrack ? `Current track: ${escHtml(state.currentTrack.title || 'Unknown Title')}` : 'No active playback yet.'}
          ${activeZone.type === 'native' && playbackStatus?.playing ? '<br>Native playback session is active.' : ''}
        </div>
      </div>
      <div class="discover-actions">
        <button class="btn btn-gold" id="zones-open-discover">Discover</button>
        <button class="btn btn-ghost" id="zones-open-settings">Settings</button>
      </div>
    </div>

    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Browser Output</div>
          <div class="discover-section-sub">Best for quick control in this tab.</div>
        </div>
      </div>
      <div class="zones-grid" id="zones-browser-grid"></div>
    </div>

    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Native Audio Devices</div>
          <div class="discover-section-sub">WASAPI / ASIO style outputs available on this machine.</div>
        </div>
      </div>
      <div class="zones-grid" id="zones-native-grid"></div>
    </div>

    <div class="discover-section">
      <div class="discover-section-head">
        <div class="discover-section-copy">
          <div class="discover-section-title">Network Renderers</div>
          <div class="discover-section-sub">DLNA / UPnP devices discovered on your LAN.</div>
        </div>
      </div>
      <div class="zones-grid" id="zones-network-grid"></div>
    </div>
  `;

  UI['zones-open-discover']?.addEventListener('click', () => navigate('discover'));
  UI['zones-open-settings']?.addEventListener('click', () => navigate('settings'));
  UI['zones-refresh-btn']?.addEventListener('click', async () => {
    showToast('Refreshing devices…');
    await renderZones(vc, { refresh: true });
  });

  const browserGrid = UI['zones-browser-grid'];
  const nativeGrid = UI['zones-native-grid'];
  const networkGrid = UI['zones-network-grid'];

  const makeZoneCard = (zone, subtitle, meta) => {
    const isActive = activeZone.type === zone.type && String(activeZone.id) === String(zone.id);
    const card = document.createElement('div');
    card.className = `zone-card${isActive ? ' active' : ''}`;
    card.innerHTML = `
      <div class="zone-type">${zone.type}</div>
      <div class="zone-title">${escHtml(zone.name)}</div>
      <div class="zone-sub">${subtitle}</div>
      <div class="zone-meta">${meta}</div>
      <div class="zone-actions">
        <button class="btn ${isActive ? 'btn-gold' : 'btn-ghost'} zone-activate-btn">${isActive ? 'Active Zone' : 'Set Active'}</button>
        <button class="btn btn-ghost zone-transfer-btn">${state.currentTrack ? 'Transfer Here' : 'Ready'}</button>
      </div>
    `;

    card.querySelector('.zone-activate-btn')?.addEventListener('click', () => activateZone(zone, false));
    card.querySelector('.zone-transfer-btn')?.addEventListener('click', () => {
      if (!state.currentTrack) {
        showToast(`Output ready: ${zone.name}`);
        activateZone(zone, false);
        return;
      }
      activateZone(zone, true);
    });
    return card;
  };

  browserGrid.appendChild(makeZoneCard(
    { type: 'browser', id: 'browser', name: 'Local Web Player' },
    'Playback happens inside the current browser session.',
    'Ideal for editing queues, testing DSP, and desktop control.'
  ));

  if (!zones?.native?.available) {
    nativeGrid.innerHTML = `<div class="zone-card"><div class="zone-title">Native output unavailable</div><div class="zone-sub">${escHtml(zones?.native?.error || 'Install sounddevice support to enable direct hardware playback.')}</div></div>`;
  } else if (!nativeDevices.length) {
    nativeGrid.innerHTML = `<div class="zone-card"><div class="zone-title">No native devices found</div><div class="zone-sub">VELVET did not detect any output-capable local audio device.</div></div>`;
  } else {
    nativeDevices.forEach(dev => {
      nativeGrid.appendChild(makeZoneCard(
        { type: 'native', id: dev.device_id, device_id: dev.device_id, name: dev.name },
        'Direct machine output for DACs, interfaces, or default system playback.',
        `${dev.channels || 0} ch${dev.sample_rate ? ` · ${fmtSR(dev.sample_rate)}` : ''}`
      ));
    });
  }

  if (!networkDevices.length) {
    networkGrid.innerHTML = `<div class="zone-card"><div class="zone-title">No network renderers</div><div class="zone-sub">Use Refresh Devices after your streamer or amplifier comes online.</div></div>`;
  } else {
    networkDevices.forEach(dev => {
      networkGrid.appendChild(makeZoneCard(
        { ...dev, type: 'cast', id: dev.udn },
        'DLNA / UPnP target reachable over the local network.',
        dev.location ? escHtml(dev.location) : 'Network renderer'
      ));
    });
  }
}

// ── Artist Detail ─────────────────────────────────────────────────────────────

async function renderArtist(vc, { id }) {
  if (!id) {
    vc.innerHTML = `<div class="empty-state"><div class="empty-state-title">Artist not found</div></div>`;
    return;
  }
  const cacheKey = `artist:${id}`;
  let artist, albums;
  const cached = _getCachedData(cacheKey);
  if (cached) {
    artist = cached.artist;
    albums = cached.albums;
  } else {
    [artist, albums] = await Promise.all([
      api(`/artists/${id}`),
      api(`/artists/${id}/albums`)
    ]);
    _cacheData(cacheKey, { artist, albums });
  }
  recordHomeVisit('artist', artist);

  const isCollaboration = album => album.artist_role === 'collaboration' || (
    album.artist_id != null && String(album.artist_id) !== String(id)
  );
  const primaryAlbums = albums.filter(album => !isCollaboration(album));
  const collaborations = albums.filter(isCollaboration);

  vc.innerHTML = `
    <div class="topbar">
      <button class="back-btn" onclick="handleHistoryBack('artists')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
    </div>
    <div class="artist-hero">
      <div class="artist-hero-art">
        <img src="/api/artists/${artist.id}/image?v=2" id="artist-hero-img"
             onerror="this.style.display='none'">
        <div class="artist-art-placeholder" id="artist-art-placeholder">${artist.name.charAt(0).toUpperCase()}</div>
      </div>
      <div class="artist-hero-info">
        <div class="section-title">${escHtml(artist.name)}</div>
        <div class="section-sub artist-aliases" id="artist-aliases-list"
             style="${(artist.aliases || []).length ? '' : 'display:none;'}">
          Also known as ${escHtml((artist.aliases || []).join(' · '))}
        </div>
        <div class="section-sub">${primaryAlbums.length} albums · ${primaryAlbums.reduce((s, a) => s + (a.track_count || 0), 0)} tracks${collaborations.length ? ` · ${collaborations.length} collaborations` : ''}</div>
      </div>
    </div>
    <div class="artist-bio" id="artist-bio"><div class="bio-loading">Loading artist bio…</div></div>
    <div style="padding: 0 24px; margin-bottom: 16px;">
      <button class="btn btn-gold" id="artist-radio-btn">Artist Radio</button>
      <button class="btn btn-ghost" id="artist-add-alias-btn" title="Add a stage name, translated name, or romanized alias">+ Alias</button>
      <button class="btn btn-ghost" id="refresh-artist-btn" title="Re-read artist name and all album metadata from files">🔄 Re-read Tags</button>
    </div>
    <section aria-label="Albums">
      <div class="section-header"><div class="section-title">Albums</div></div>
      <div class="grid" id="artist-albums-grid"></div>
    </section>
    ${collaborations.length ? `
      <section aria-label="Collaborations and appearances">
        <div class="section-header"><div class="section-title">Collaborations &amp; Appearances</div></div>
        <div class="grid" id="artist-collaborations-grid"></div>
      </section>` : ''}
  `;

  const grid = UI['artist-albums-grid'];
  // Use DocumentFragment for better performance
  const fragment = document.createDocumentFragment();
  primaryAlbums.forEach(a => fragment.appendChild(makeAlbumCard({ ...a, artist_name: a.artist_name || artist.name })));
  grid.appendChild(fragment);
  if (!primaryAlbums.length) {
    grid.innerHTML = '<div style="grid-column:1/-1;color:var(--muted)">No albums as the primary artist.</div>';
  }
  const collaborationGrid = UI['artist-collaborations-grid'];
  if (collaborationGrid) renderCardsOnDemand(collaborationGrid, collaborations, makeAlbumCard);

  UI['artist-radio-btn']?.addEventListener('click', () => startRadio('artist', id));

  // Both automatic and user-confirmed aliases persist locally. Search will
  // pick up dirty artist/album/track keys immediately, even before reindex.
  const refreshAliasNames = aliases => {
    artist.aliases = aliases || [];
    _cacheData(cacheKey, { artist, albums });
    const label = UI['artist-aliases-list'];
    if (label) {
      label.style.display = artist.aliases.length ? '' : 'none';
      label.textContent = artist.aliases.length
        ? 'Also known as ' + artist.aliases.join(' · ') : '';
    }
  };

  UI['artist-add-alias-btn']?.addEventListener('click', async () => {
    const value = prompt('Add an alternate name for ' + artist.name + '\\nFor example: a stage name or romanization', '');
    const name = (value || '').trim();
    if (!name) return;
    if (name.length > 128) {
      showToast('Artist aliases must be 128 characters or fewer');
      return;
    }
    const result = await api(`/artists/${id}/aliases`, {
      method: 'POST', body: JSON.stringify({ name }),
    });
    if (!result || result.status !== 'ok') return;
    refreshAliasNames(result.aliases);
    showToast('Artist alias saved. All search tabs will now recognize it.');
  });

  // Refresh artist metadata
  UI['refresh-artist-btn']?.addEventListener('click', async () => {
    showToast('Re-reading metadata from files…');
    try {
      const res = await api(`/artists/${id}/refresh`, { method: 'POST' });
      if (res.success) {
        showToast(res.message);
        // Reload page to show updated data
        loadArtist(id);
      } else {
        showToast('Refresh failed');
      }
    } catch (e) {
      showToast('Error: ' + e.message);
    }
  });

  // Load bio separately
  try {
    const bioWrap = UI['artist-bio'];
    const result = await api(`/artists/${id}/enrich-bio`, { method: 'POST' });
    if (result.bio) {
      bioWrap.innerHTML = `<div class="bio-text">${escHtml(result.bio)}</div>`;
    } else {
      bioWrap.innerHTML = '';
    }
  } catch {
    if (UI['artist-bio']) UI['artist-bio'].innerHTML = '';
  }
}

// ── Album Detail ──────────────────────────────────────────────────────────────

async function renderAlbum(vc, { id }) {
  // Try data cache first for faster back navigation
  const cacheKey = `album:${id}`;
  let album, credits;
  const cached = _getCachedData(cacheKey);
  if (cached) {
    album = cached.album;
    credits = cached.credits;
  } else {
    [album, credits] = await Promise.all([
      api(`/albums/${id}`),
      api(`/albums/${id}/credits`)
    ]);
    _cacheData(cacheKey, { album, credits });
  }
  recordHomeVisit('album', album);
  const tracks = album.tracks || [];

  const totalDur = tracks.reduce((s, t) => s + (t.duration || 0), 0);

  vc.innerHTML = `
    <div class="topbar">
      <button class="back-btn" onclick="handleHistoryBack('albums')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
    </div>

    <div class="album-hero">
      <div class="album-hero-art" id="album-art-hero">
        <div class="album-art-placeholder"></div>
      </div>
      <div class="album-hero-info">
        <div class="album-label">Album</div>
        <div class="album-hero-title">
          ${escHtml(album.title)}
          ${album.is_hires ? `<img src="/static/logo-hires.png" class="hires-badge-detail" alt="Hi-Res">` : ''}
        </div>
        <div class="album-hero-artist" id="hero-artist">${escHtml(album.artist_name || '')}</div>
        <div class="album-meta-row">
          ${album.year ? `<div class="album-meta-item">${album.year}</div>` : ''}
          <div class="album-meta-item">${tracks.length} tracks</div>
          <div class="album-meta-item">${fmt(totalDur)}</div>
          ${album.genre ? `<button type="button" class="album-meta-item album-meta-genre" id="album-genre-link" data-genre="${escHtml(album.genre)}">${escHtml(album.genre)}</button>` : ''}
        </div>
        <div class="album-actions">
          <button class="btn btn-gold" id="play-all-btn">
            <svg viewBox="0 0 24 24" fill="currentColor" style="width:14px;height:14px"><path d="M8 5v14l11-7z"/></svg>
            Play All
          </button>
          <button class="btn btn-ghost" id="shuffle-all-btn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/></svg>
            Shuffle
          </button>
          <button class="btn btn-ghost" id="favorite-btn" style="color:${album.is_favorite ? '#ff6b6b' : 'var(--muted)'}">
            ${album.is_favorite ? '♥' : '♡'} Favorite
          </button>
          <div class="album-more-btn">
            <button class="btn btn-ghost" id="album-more-trigger">⋯ Manage</button>
            <div class="album-more-menu" id="album-more-menu">
              <button class="album-more-item" id="album-radio-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0"><path d="M22 17H2a3 3 0 0 0 0 6h20v-6zM2 5h20M6 5V3M18 5V3M16 17v-5a4 4 0 0 0-8 0v5"/></svg>
                Album Radio
              </button>
              <button class="album-more-item" id="search-itunes-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                Search Covers
              </button>
              <button class="album-more-item" id="enrich-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                Auto Cover
              </button>
              <button class="album-more-item" id="custom-cover-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                Custom Cover
              </button>
              <button class="album-more-item" id="open-folder-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                Open Folder
              </button>
              <button class="album-more-item" id="refresh-album-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.53"/></svg>
                Re-read Tags
              </button>
              <button class="album-more-item danger" id="del-album-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                Delete Album
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
    </div>

    <div class="album-meta-toggle-wrap">
      <button class="album-meta-toggle" id="album-meta-toggle" aria-expanded="false">
        <span class="album-meta-toggle-copy">
          <span class="album-meta-toggle-title">Credits & Works</span>
          <span class="album-meta-toggle-sub">Composers, works, conductors and performers</span>
        </span>
        <svg class="album-meta-toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:16px;height:16px">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>
    </div>

    <div class="credits-grid album-mobile-meta" id="album-mobile-meta">
      <div class="credits-card">
        <div class="credits-title">Primary Artist</div>
        <div class="credits-list">
          <button class="credits-pill" id="album-primary-artist-pill">${escHtml(album.artist_name || 'Unknown Artist')}</button>
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Composers</div>
        <div class="credits-list" id="album-composers-list">
          ${(credits?.composers || []).length
            ? credits.composers.map(item => `<button class="credits-pill composer-credit-pill" data-composer="${escHtml(item.composer)}">${escHtml(item.composer)} <span style="color:var(--dim)">${item.track_count}</span></button>`).join('')
            : '<div class="zone-sub">No composer tags found for this album.</div>'}
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Works</div>
        <div class="credits-list">
          ${(credits?.works || []).length
            ? credits.works.map(item => `<button class="credits-pill work-credit-pill" data-work-title="${escHtml(item.work_title)}" data-composer="${escHtml((credits.composers || [])[0]?.composer || '')}">${escHtml(item.work_title)} <span style="color:var(--dim)">${item.track_count}</span></button>`).join('')
            : '<div class="zone-sub">No separate work titles tagged.</div>'}
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Conductors</div>
        <div class="credits-list">
          ${(credits?.conductors || []).length
            ? credits.conductors.map(item => `<button class="credits-pill credit-role-pill" data-role="conductor" data-name="${escHtml(item.name)}">${escHtml(item.name)} <span style="color:var(--dim)">${item.track_count}</span></button>`).join('')
            : '<div class="zone-sub">No conductor tags found.</div>'}
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Ensembles</div>
        <div class="credits-list">
          ${(credits?.ensembles || []).length
            ? credits.ensembles.map(item => `<button class="credits-pill credit-role-pill" data-role="ensemble" data-name="${escHtml(item.name)}">${escHtml(item.name)} <span style="color:var(--dim)">${item.track_count}</span></button>`).join('')
            : '<div class="zone-sub">No ensemble tags found.</div>'}
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Featured Performers</div>
        <div class="credits-list">
          ${(credits?.performers || []).length
            ? credits.performers.slice(0, 10).map(item => `<button class="credits-pill credit-role-pill" data-role="performer" data-name="${escHtml(item.name)}">${escHtml(item.name)} <span style="color:var(--dim)">${item.track_count}</span></button>`).join('')
            : '<div class="zone-sub">No performer tags found.</div>'}
        </div>
      </div>
      <div class="credits-card">
        <div class="credits-title">Album Facts</div>
        <div class="credits-list">
          ${credits?.genre ? `<span class="credits-pill">${escHtml(credits.genre)}</span>` : ''}
          ${credits?.label ? `<span class="credits-pill">${escHtml(credits.label)}</span>` : ''}
          ${credits?.year ? `<span class="credits-pill">${escHtml(String(credits.year))}</span>` : ''}
          ${((credits?.artists || []).length > 1)
            ? credits.artists.slice(0, 6).map(artist => `<span class="credits-pill">${escHtml(artist.name)}</span>`).join('')
            : ''}
        </div>
      </div>
    </div>

    <div class="track-list">
      <div class="track-list-header">
        <div>#</div><div>Title</div>
        <div>Format</div><div>Quality</div><div>Duration</div>
      </div>
      <div id="tracks-container"></div>
    </div>
  `;

  // Cover - use medium thumbnail for faster loading, fall back to full cover
  const heroArt = UI['album-art-hero'];
  const placeholder = heroArt.querySelector('.album-art-placeholder');
  const img = document.createElement('img');
  const albumTs = album.updated_at ? `&t=${album.updated_at}` : `&t=${Date.now()}`;
  img.src = `/api/covers/${album.id}/thumb?size=medium${albumTs}`;
  img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block';
  img.onerror = function() {
    if (this.src.indexOf('/thumb') > -1) {
      this.src = `/api/covers/${album.id}${albumTs}`;
    } else {
      this.style.display = 'none';
      if (placeholder) placeholder.classList.add('visible');
    }
  };
  img.onload = function() {
    this.style.display = 'block';
    if (placeholder) placeholder.classList.remove('visible');
  };
  heroArt.prepend(img);

  // Artist click
  UI['hero-artist'].addEventListener('click', () => {
    if (album.artist_id) {
      navigate('artist', { id: album.artist_id });
    } else {
      showToast('Artist information missing');
    }
  });

  UI['album-primary-artist-pill']?.addEventListener('click', () => {
    if (album.artist_id) navigate('artist', { id: album.artist_id });
  });

  $$('.composer-credit-pill').forEach(btn => {
    btn.addEventListener('click', () => navigate('composer', { id: btn.dataset.composer }));
  });
  $$('.work-credit-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const composerName = btn.dataset.composer || (credits?.composers || [])[0]?.composer;
      if (composerName) navigate('work', { id: makeWorkId(composerName, btn.dataset.workTitle) });
    });
  });
  $$('.credit-role-pill').forEach(btn => {
    btn.addEventListener('click', () => navigate('credit', { id: makeCreditId(btn.dataset.role, btn.dataset.name) }));
  });

  const albumMetaToggle = UI['album-meta-toggle'];
  const albumMetaPanel = UI['album-mobile-meta'];
  if (albumMetaToggle && albumMetaPanel) {
    const isMobile = isCompactLayout() || isShortLandscapeLayout();
    const setAlbumMetaExpanded = (expanded) => {
      albumMetaToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      albumMetaPanel.classList.toggle('is-collapsed', !expanded);
    };
    setAlbumMetaExpanded(!isMobile);
    albumMetaToggle.addEventListener('click', () => {
      const expanded = albumMetaToggle.getAttribute('aria-expanded') === 'true';
      setAlbumMetaExpanded(!expanded);
    });
  }


  // Render tracks using DocumentFragment for better performance
  const container = UI['tracks-container'];
  const fragment = document.createDocumentFragment();
  tracks.forEach((t, i) => {
    const row = makeTrackRow(t, i + 1);
    row.querySelector('.track-add-btn')?.remove();
    const trackWithInfo = { ...t, artist_name: t.artist_name || album.artist_name, album_title: album.title };
    const swipe = makeTrackSwipeRow(row, () => {
      state.prevAlbumQueue = tracks;
      playTrack(trackWithInfo, tracks, i);
    }, () => showPlaylistPicker(trackWithInfo), { add: true });
    swipe.querySelector('.album-track-add').setAttribute('aria-label', `Add ${t.title} to playlist`);
    fragment.appendChild(swipe);
  });
  container.appendChild(fragment);

  // Play all
  UI['play-all-btn'].addEventListener('click', () => {
    if (tracks.length) playTrack(tracks[0], tracks, 0);
  });

  // Genre badge opens the family shelf, pre-focused on this album's family.
  UI['album-genre-link']?.addEventListener('click', () => {
    navigate('genres', { family: genreFamilyOf(album.genre).id });
  });

  // Overflow manage menu toggle
  UI['album-more-trigger'].addEventListener('click', (e) => {
    e.stopPropagation();
    const menu = UI['album-more-menu'];
    menu.classList.toggle('open');
  });
  const albumMenuCloseAc = new AbortController();
  document.addEventListener('click', () => {
    UI['album-more-menu']?.classList.remove('open');
  }, { signal: albumMenuCloseAc.signal });
  // Clean up when navigating away
  window.__albumMenuCloseAc?.abort();
  window.__albumMenuCloseAc = albumMenuCloseAc;

  UI['album-radio-btn'].addEventListener('click', () => startRadio('album', album.id));

  // Shuffle all
  UI['shuffle-all-btn'].addEventListener('click', () => {
    const shuffled = shuffleArray(tracks);
    if (shuffled.length) { state.shuffle = true; playTrack(shuffled[0], shuffled, 0); }
  });

  // Enrich cover
  UI['enrich-btn'].addEventListener('click', async () => {
    showToast('Searching all sources (Netease, QQ, Apple Music…)…');
    try {
      const res = await managementFetch(`/api/albums/${album.id}/fetch-cover`, { method: 'POST' });
      if (res.adminCancelled) return;
      const data = await res.json();
      if (data.success) {
        showToast(`Cover found from ${data.source || 'web'}!`);
        // Force refresh by invalidating server cover cache
        img.src = `/api/covers/${album.id}?t=${Date.now()}`;
      } else {
        showToast('No cover found, try manual search');
      }
    } catch (e) {
      showToast('Failed to fetch cover');
    }
  });

  // Search iTunes
  UI['search-itunes-btn'].addEventListener('click', () => showCoverPicker(album.id));

  // Open folder
  UI['open-folder-btn'].addEventListener('click', async () => {
    try {
      const res = await api(`/albums/${album.id}/open-folder`, { method: 'POST' });
      if (!res.success) showToast('Failed to open folder');
    } catch (e) {
      showToast('Error: ' + e.message);
    }
  });

  // Refresh album metadata
  UI['refresh-album-btn'].addEventListener('click', async () => {
    showToast('Re-reading metadata from files…');
    try {
      const res = await api(`/albums/${album.id}/refresh`, { method: 'POST' });
      if (res.success) {
        showToast(res.message);
        // Reload the album page to show updated metadata
        navigate('album', { id: album.id });
      } else {
        showToast('Refresh failed');
      }
    } catch (e) {
      showToast('Error: ' + e.message);
    }
  });

  // Toggle favorite
  let isFavorite = album.is_favorite;
  const favBtn = UI['favorite-btn'];
  favBtn.addEventListener('click', async () => {
    try {
      const res = await api(`/albums/${album.id}/favorite`, { method: 'POST' });
      if (res.success) {
        isFavorite = res.is_favorite;
        favBtn.style.color = isFavorite ? '#ff6b6b' : 'var(--muted)';
        favBtn.textContent = isFavorite ? '♥ Favorite' : '♡ Favorite';
        showToast(isFavorite ? 'Added to favorites' : 'Removed from favorites');
      }
    } catch (e) {
      showToast('Error: ' + e.message);
    }
  });

  // Custom cover upload
  UI['custom-cover-btn'].addEventListener('click', () => {
    const input = UI['cover-upload-input'];
    input.onchange = async () => {
      if (!input.files[0]) return;
      const formData = new FormData();
      formData.append('file', input.files[0]);
      showToast('Uploading cover…');
      try {
        const res = await managementFetch(`/api/albums/${album.id}/cover`, {
          method: 'POST',
          body: formData
        });
        if (res.adminCancelled) return;
        if (res.ok) {
          showToast('Cover updated!');
          // Force refresh after upload with timestamp to bust CDN/browser cache
          img.src = `/api/covers/${album.id}?t=${Date.now()}`;
        } else {
          showToast('Upload failed');
        }
      } catch (e) {
        showToast('Error: ' + e.message);
      }
    };
    input.click();
  });

  // Delete album
  UI['del-album-btn'].addEventListener('click', () => {
    showConfirm({
      title: 'Delete Album',
      body: 'Remove this album from the library?<br><br>Albums referenced by playlists or play history cannot be removed. File restore requires a re-scan and may assign new Track IDs.',
      extra: `
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer;margin-top:10px">
          <input type="checkbox" id="delete-files-check"> Also archive audio files (recoverable)
        </label>
      `,
      confirmText: 'Delete Now',
      onConfirm: async () => {
        const delFiles = UI['delete-files-check']?.checked;
        showToast(delFiles ? 'Archiving files and removing library entry…' : 'Removing from library…');
        try {
          const res = await api(`/albums/${album.id}?delete_files=${delFiles}`, { method: 'DELETE' });
          // api() returns null after toasting a refusal (e.g. 409 with reason).
          if (res?.success) {
            showToast(res.message || 'Album removed');
            navigate('albums');
            loadStats(); // Update sidebar counts
          }
        } catch (e) {
          showToast('Delete failed: ' + e.message);
        }
      }
    });
  });

  if (state.currentTrack) highlightPlaying(state.currentTrack.id);
}

// ── Search ────────────────────────────────────────────────────────────────────

// A response to an older IME/input query must never replace newer results.
let globalSearchRenderSerial = 0;

async function renderSearch(vc, { q } = {}) {
  const serial = ++globalSearchRenderSerial;
  const draft = String(q || '').slice(0, 128);
  const query = draft.trim();
  const sourceInput = vc.querySelector('#search-input');
  const preserveDraft = sourceInput?.value.trim() === query;
  const results = query
    ? (await api(`/search?q=${encodeURIComponent(query)}&limit=80`) || {})
    : { artists: [], albums: [], tracks: [] };

  if (serial !== globalSearchRenderSerial || state.view !== 'search'
      || String(state.viewParams?.q || '').trim().slice(0, 128) !== query) return;

  const artists = results.artists || [];
  const albums = results.albums || [];
  const tracks = results.tracks || [];
  const totalArtists = Number(results.total_artists ?? artists.length);
  const totalAlbums = Number(results.total_albums ?? albums.length);
  const totalTracks = Number(results.total_tracks ?? tracks.length);

  // A space typed while this request was in flight still belongs to the draft.
  const currentInput = vc.querySelector('#search-input');
  const inputValue = preserveDraft && currentInput === sourceInput ? currentInput.value : draft;
  const existingResults = vc.querySelector('#global-search-results');

  if (currentInput && currentInput.closest('.topbar')?.parentElement === vc) {
    // Updating results must not detach the active editor, lose its caret, or
    // close a mobile keyboard between words (including IME composition).
    if (currentInput.value !== inputValue) currentInput.value = inputValue;
    const topbar = currentInput.closest('.topbar');
    for (const node of [...vc.childNodes]) {
      if (node !== topbar && node !== existingResults) node.remove();
    }
    const resultContainer = existingResults || document.createElement('div');
    if (!existingResults) {
      resultContainer.id = 'global-search-results';
      resultContainer.className = 'search-results';
      resultContainer.setAttribute('aria-label', 'Music library search results');
      vc.appendChild(resultContainer);
    }
    resultContainer.innerHTML = '<div id="global-search-groups"></div>';
    if (!preserveDraft) {
      currentInput.focus({ preventScroll: true });
      currentInput.setSelectionRange(inputValue.length, inputValue.length);
    }
  } else {
    vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('search')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" maxlength="128" placeholder="Search artists, albums, tracks…" value="${escHtml(inputValue)}">
      </div>
    </div>
    <div class="search-results" id="global-search-results"
         aria-label="Music library search results">
      <div id="global-search-groups"></div>
    </div>
  `;
    setupSearch();
  }

  const container = UI['global-search-groups'];
  if (!query) {
    container.innerHTML = '<div class="empty-state-sub" style="padding:24px 0">Search artists, albums, and tracks from any page.</div>';
    return;
  }
  if (!artists.length && !albums.length && !tracks.length) {
    container.innerHTML = `
      <div class="empty-state-sub" style="padding:24px 0 16px">
        No artists, albums, or tracks found in your library.
      </div>
      <div class="section-sub" style="padding:10px 0">
        If this artist is in your library under a different name, open the artist page
        and use + Alias to add a stage name, translated name or romanised alias.
      </div>`;
    return;
  }

  // Keep the currently displayed track set mutable: Load more can extend the
  // queue without changing the play gesture or the correct selected index.
  const visibleTracks = [...tracks];
  const makeSearchTrackRow = (t, i) => {
    const el = makeTrackRow(t, i + 1, true);
    el.addEventListener('click', () => {
      playTrack(t, visibleTracks, i);
      if (!t.album_id) return;
      (async () => {
        await navigate('album', { id: t.album_id });
        try {
          const albumData = await api(`/albums/${t.album_id}`);
          const albumTracks = albumData?.tracks || [];
          const trackIdx = albumTracks.findIndex(tr => tr.id === t.id);
          if (trackIdx === -1 || !state.currentTrack
              || String(state.currentTrack.id) !== String(t.id)) return;
          state.queue = albumTracks;
          state.queueIdx = trackIdx;
          state.currentTrack = { ...state.currentTrack, ...albumTracks[trackIdx] };
          updatePlayerUI(state.currentTrack);
          highlightPlaying(state.currentTrack.id);
          if (UI['queue-panel']?.classList.contains('open')) renderQueue();
        } catch (error) {
          console.warn('Failed to sync search playback queue:', error);
        }
      })();
    });
    return el;
  };

  // Artists are always above Albums, which are always above Tracks.
  const addSection = (kind, heading, count, items, createCard, classes) => {
    if (!items.length) return;
    const titleEl = document.createElement('div');
    titleEl.className = 'result-section-title search-section-header';
    titleEl.textContent = `${heading} — ${count} ${count === 1 ? 'result' : 'results'}`;
    container.appendChild(titleEl);

    const listEl = document.createElement('div');
    listEl.className = classes;
    const visible = kind === 'tracks' ? visibleTracks : [...items];
    const append = (batch, startingIndex) => {
      const fragment = document.createDocumentFragment();
      batch.forEach((item, index) => fragment.appendChild(createCard(item, startingIndex + index)));
      listEl.appendChild(fragment);
    };
    append(items, 0);
    container.appendChild(listEl);

    if (visible.length >= count) return;
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'btn btn-ghost search-load-more';
    more.dataset.searchCategory = kind;
    more.textContent = `Load more ${heading.toLowerCase()} (${count - visible.length} remaining)`;
    container.appendChild(more);
    more.addEventListener('click', async () => {
      if (more.disabled) return;
      const offset = visible.length;
      more.disabled = true;
      more.textContent = 'Loading…';
      try {
        const page = await api(
          `/search/category?kind=${kind}&q=${encodeURIComponent(query)}&offset=${offset}&limit=40`
        );
        if (serial !== globalSearchRenderSerial || state.view !== 'search'
            || state.viewParams?.q !== query || !more.isConnected) return;
        if (!page || !Array.isArray(page.items)) {
          more.textContent = 'Retry loading more';
          return;
        }
        if (!page.items.length) {
          more.remove();
          return;
        }
        const startIndex = visible.length;
        visible.push(...page.items);
        append(page.items, startIndex);
        if (visible.length >= count) {
          more.remove();
        } else {
          more.textContent = `Load more ${heading.toLowerCase()} (${count - visible.length} remaining)`;
        }
      } finally {
        if (more.isConnected) more.disabled = false;
      }
    });
  };

  addSection('artists', 'Artists', totalArtists, artists, makeArtistCard,
             'grid artist-grid search-artist-grid');
  addSection('albums', 'Albums', totalAlbums, albums, makeAlbumCard,
             'grid search-albums-grid');
  addSection('tracks', 'Tracks', totalTracks, tracks, makeSearchTrackRow,
             'search-track-list');
}

// ── Settings ──────────────────────────────────────────────────────────────────
