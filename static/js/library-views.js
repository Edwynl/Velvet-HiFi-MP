/* VELVET library-views: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
async function renderHome(vc) {
  log('[VELVET] renderHome started');
  
  // Load stats first
  let stats = { artists: 0, albums: 0, tracks: 0, total_duration_hours: 0 };
  try {
    const rawStats = await api('/stats');
    if (rawStats) stats = { ...stats, ...rawStats };
    log('[VELVET] Stats loaded:', stats);
  } catch (e) {
    console.warn('[VELVET] Failed to load stats for home:', e);
  }

  // Update sidebar stats (safe to do even if api fails as we have defaults)
  if (UI['stat-artists']) UI['stat-artists'].textContent  = (stats.artists || 0).toLocaleString();
  if (UI['stat-albums']) UI['stat-albums'].textContent   = (stats.albums || 0).toLocaleString();
  if (UI['stat-tracks']) UI['stat-tracks'].textContent   = (stats.tracks || 0).toLocaleString();
  if (UI['stat-duration']) UI['stat-duration'].textContent = (stats.total_duration_hours || 0) + 'h';

  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  let scanBanner = '';
  if (stats.tracks === 0) {
    scanBanner = `
    <div class="scan-banner" id="scan-banner">
      <div class="scan-banner-title">📂 Library not scanned yet</div>
      <div class="scan-banner-sub">Set your music folder in Settings, then scan your library to get started.</div>
      <button class="btn btn-gold" onclick="navigate('settings')">Go to Settings</button>
    </div>`;
  }

  log('[VELVET] Setting home innerHTML');
  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('home')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" placeholder="Search artists, albums, tracks…">
      </div>
    </div>

    <div class="home-hero">
      <div class="home-greeting">${greeting}.</div>
      <div class="home-sub">${(stats.tracks || 0).toLocaleString()} tracks · ${(stats.albums || 0).toLocaleString()} albums · ${stats.total_duration_hours || 0}h of music</div>
    </div>

    ${scanBanner}

    <div class="home-section">
      <div class="home-section-title">Recently Added</div>
      <div class="grid" id="recent-grid">
        <div class="loading-spinner" style="padding: 32px">Loading recent music...</div>
      </div>
    </div>
  `;

  setupSearch();
  
  // Now load recently added separately
  log('[VELVET] Loading recent albums');
  try {
    const recent = await api('/recently-added?limit=20');
    const grid = UI['recent-grid'];
    if (!grid) return;

    if (!recent || !recent.length) {
      grid.innerHTML = '<div style="padding:0;color:var(--muted);font-size:13px">No albums yet. Scan your library to get started.</div>';
      log('[VELVET] No recent albums found');
      return;
    }

    grid.innerHTML = '';
    const fragment = document.createDocumentFragment();
    recent.forEach(album => fragment.appendChild(makeAlbumCard(album)));
    grid.appendChild(fragment);
    log('[VELVET] Recent albums rendered:', recent.length);
  } catch (e) {
    console.warn('[VELVET] Failed to load recent albums:', e);
    const grid = UI['recent-grid'];
    if (grid) grid.innerHTML = '<div style="color:var(--muted)">Failed to load recent music</div>';
  }
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
        <input id="search-input" type="text" placeholder="Search and jump anywhere…">
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

const ALBUMS_PER_PAGE = 400;
const ARTISTS_PER_PAGE = 400;

async function renderAlbums(vc) {
  const isMobile = window.innerWidth <= 768;
  // Fetch all albums but render only current page
  const allAlbums = await api('/albums?limit=15000');
  const genres = [...new Set(allAlbums.map(album => (album.genre || '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const decades = [...new Set(allAlbums.map(album => decadeLabel(album.year)).filter(label => label !== 'Unknown'))].sort((a, b) => Number(b) - Number(a));
  let showFavoritesOnly = false;
  let currentSort = 'title';
  let currentPage = 1;
  let searchQuery = '';
  let currentGenre = '';
  let currentDecade = '';
  let searchMode = false;  // Track search mode
  let searchResults = [];   // Matching tracks
  let searchAlbums = [];    // Matching albums
  let allTracks = [];

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('albums')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" placeholder="Search albums or tracks…" value="${escHtml(searchQuery)}">
      </div>
      <button class="btn btn-ghost" id="favorites-filter-btn" style="padding:6px 10px;color:var(--muted);font-size:12px">♡ Favorites</button>
      <div class="v-select" id="album-sort-select">
        <div class="v-select-trigger" style="min-width:80px">
          <span class="v-select-label">${{title:'A–Z',year:'Year',artist:'Artist',added:'Added'}[currentSort] || 'A–Z'}</span>
          <svg class="v-select-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
        </div>
        <div class="v-select-options">
          <div class="v-option ${currentSort === 'title' ? 'active' : ''}" data-value="title">A–Z</div>
          <div class="v-option ${currentSort === 'year' ? 'active' : ''}" data-value="year">Year</div>
          <div class="v-option ${currentSort === 'artist' ? 'active' : ''}" data-value="artist">Artist</div>
          <div class="v-option ${currentSort === 'added' ? 'active' : ''}" data-value="added">Added</div>
        </div>
      </div>
    </div>
    <div class="focus-bar">
      <div class="focus-chip">
        <span class="focus-chip-label">Genre</span>
        <div class="v-select" id="album-genre-select">
          <div class="v-select-trigger">
            <span class="v-select-label">${currentGenre || 'All genres'}</span>
            <svg class="v-select-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="v-select-options">
            <div class="v-option ${!currentGenre ? 'active' : ''}" data-value="">All genres</div>
            ${genres.map(g => `<div class="v-option ${currentGenre === g ? 'active' : ''}" data-value="${escHtml(g)}">${escHtml(g)}</div>`).join('')}
          </div>
        </div>
      </div>
      <div class="focus-chip">
        <span class="focus-chip-label">Decade</span>
        <div class="v-select" id="album-decade-select">
          <div class="v-select-trigger">
            <span class="v-select-label">${currentDecade || 'All decades'}</span>
            <svg class="v-select-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="v-select-options">
            <div class="v-option ${!currentDecade ? 'active' : ''}" data-value="">All decades</div>
            ${decades.map(d => `<div class="v-option ${currentDecade === d ? 'active' : ''}" data-value="${escHtml(d)}">${escHtml(d)}</div>`).join('')}
          </div>
        </div>
      </div>
      <button class="btn btn-ghost" id="clear-focus-btn">Clear Focus</button>
    </div>
    <div class="section-header" id="albums-header" title="Click to scroll to top">
      <div class="section-title">Albums</div>
      <div class="section-sub" id="albums-count">${allAlbums.length} albums</div>
    </div>
    <div class="grid" id="albums-grid"></div>
    <div class="back-to-top" id="back-to-top-btn" style="display:none">↑</div>
    <div class="pagination" id="albums-pagination">
      <button class="btn btn-ghost" id="prev-page-btn" disabled>← Prev</button>
      <span class="page-info" id="page-info">Page 1 / 1</span>
      <button class="btn btn-ghost" id="next-page-btn">Next →</button>
    </div>
  `;

  const input = UI['search-input'];
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

  // Scroll to top on header click
  headerEl.addEventListener('click', (e) => {
    if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
    mainEl.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Show/hide back-to-top button based on scroll position
  mainEl.addEventListener('scroll', () => {
    if (mainEl.scrollTop > 300) {
      backToTopBtn.style.display = 'flex';
    } else {
      backToTopBtn.style.display = 'none';
    }
  }, { passive: true });

  // Back to top button click
  backToTopBtn.addEventListener('click', () => {
    mainEl.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Sort albums helper
  function sortAlbums(data, sortBy) {
    return [...data].sort((a, b) => {
      switch (sortBy) {
        case 'title': return (a.title || '').localeCompare(b.title || '');
        case 'year': return (b.year || 0) - (a.year || 0);
        case 'artist': return (a.artist_name || '').localeCompare(b.artist_name || '');
        case 'added': return new Date(b.created_at || 0) - new Date(a.created_at || 0);
        default: return 0;
      }
    });
  }

  function getFilteredData() {
    return allAlbums.filter(album => {
      if (showFavoritesOnly && !album.is_favorite) return false;
      if (currentGenre && (album.genre || '') !== currentGenre) return false;
      if (currentDecade && decadeLabel(album.year) !== currentDecade) return false;
      return true;
    });
  }

  function getPagedData() {
    const filtered = sortAlbums(getFilteredData(), currentSort);
    const totalPages = Math.ceil(filtered.length / ALBUMS_PER_PAGE) || 1;
    if (currentPage > totalPages) currentPage = totalPages;
    const start = (currentPage - 1) * ALBUMS_PER_PAGE;
    const end = start + ALBUMS_PER_PAGE;
    return filtered.slice(start, end);
  }

  function updatePagination() {
    const filtered = sortAlbums(getFilteredData(), currentSort);
    const totalPages = Math.ceil(filtered.length / ALBUMS_PER_PAGE) || 1;

    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
    pageInfo.textContent = `Page ${currentPage} / ${totalPages}`;

    countEl.textContent = showFavoritesOnly
      ? `${filtered.length} favorite albums · ${totalPages} pages`
      : `${filtered.length} albums · ${totalPages} pages`;
    clearFocusBtn.style.opacity = (currentGenre || currentDecade) ? '1' : '0.6';
  }

  // Make a track row for search results (links to album)
  function makeSearchRow(track) {
    const el = document.createElement('div');
    el.className = 'history-item';
    el.innerHTML = `
      <div class="history-cover">
        <img src="/api/covers/${track.album_id}/thumb?size=small" onerror="this.style.display='none'" loading="lazy">
      </div>
      <div class="history-info">
        <div class="history-title">${escHtml(track.title)}</div>
        <div class="history-sub">${escHtml(track.artist_name || '')} · ${escHtml(track.album_title || '')}</div>
      </div>
    `;
    el.addEventListener('click', () => {
      navigate('album', { id: track.album_id });
    });
    return el;
  }

  // Render search results (albums + tracks)
  function renderSearchResults() {
    grid.innerHTML = '';
    grid.classList.remove('grid');
    grid.classList.remove('tracks-list');
    grid.style.cssText = '';

    const fragment = document.createDocumentFragment();

    // Section 1: Matching albums (as grid)
    if (searchAlbums.length > 0) {
      const albumsHeader = document.createElement('div');
      albumsHeader.className = 'search-section-header';
      albumsHeader.textContent = `Albums (${searchAlbums.length})`;
      fragment.appendChild(albumsHeader);

      const albumsGrid = document.createElement('div');
      albumsGrid.className = 'search-albums-grid';
      const albumsFragment = document.createDocumentFragment();
      searchAlbums.forEach(album => albumsFragment.appendChild(makeAlbumCard(album)));
      albumsGrid.appendChild(albumsFragment);
      fragment.appendChild(albumsGrid);
    }

    // Section 2: Matching tracks (as list)
    if (searchResults.length > 0) {
      const tracksHeader = document.createElement('div');
      tracksHeader.className = 'search-section-header';
      tracksHeader.textContent = searchAlbums.length > 0 ? `Tracks (${searchResults.length})` : `Tracks (${searchResults.length})`;
      fragment.appendChild(tracksHeader);

      const tracksList = document.createElement('div');
      tracksList.className = 'tracks-list';
      tracksList.style.cssText = 'display:flex;flex-direction:column;gap:4px;padding:0 12px 16px;';
      const tracksFragment = document.createDocumentFragment();
      searchResults.forEach(track => tracksFragment.appendChild(makeSearchRow(track)));
      tracksList.appendChild(tracksFragment);
      fragment.appendChild(tracksList);
    }

    // No results
    if (!searchAlbums.length && !searchResults.length) {
      grid.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:16px;text-align:center">No results found.</div>';
      return;
    }

    grid.appendChild(fragment);
  }

  // Render albums grid
  function renderGrid() {
    if (searchMode) {
      renderSearchResults();
      return;
    }

    grid.classList.add('grid');
    grid.classList.remove('tracks-list');
    grid.style.cssText = '';

    const data = getPagedData();
    grid.innerHTML = '';
    if (!data.length) {
      const msg = showFavoritesOnly ? 'No favorite albums yet.' : 'No albums found.';
      grid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">${msg}</div>`;
      return;
    }

    // Use DocumentFragment for better performance
    const fragment = document.createDocumentFragment();
    data.forEach(a => fragment.appendChild(makeAlbumCard(a)));
    grid.appendChild(fragment);

    updatePagination();
  }

  renderGrid();

  // Search functionality
  let searchTimer;
  async function performSearch(q) {
    if (!q.trim()) {
      // Clear search mode
      searchMode = false;
      searchResults = [];
      searchAlbums = [];
      sectionTitle.textContent = 'Albums';
      countEl.textContent = `${allAlbums.length} albums`;
      pagination.style.display = 'flex';
      renderGrid();
      return;
    }

    // Fetch tracks if not loaded
    if (!allTracks.length) {
      const result = await api('/search?q=');
      allTracks = result.tracks || [];
    }

    // First, search albums by album title (using chineseMatch for S/T support)
    const matchedAlbums = allAlbums.filter(a => {
      const title = a.title || '';
      const artist = a.artist_name || '';
      return chineseMatch(title, q) || chineseMatch(artist, q);
    });

    // Then, search tracks by track title
    if (!allTracks.length) {
      const result = await api('/search?q=');
      allTracks = result.tracks || [];
    }
    const matchedTracks = allTracks.filter(t => {
      const title = t.title || '';
      const artist = t.artist_name || '';
      return chineseMatch(title, q) || chineseMatch(artist, q);
    });

    // Store both results
    searchResults = matchedTracks;
    searchAlbums = matchedAlbums;
    searchMode = true;

    // Show albums section header
    sectionTitle.textContent = matchedAlbums.length > 0 ? 'Albums' : 'Tracks';
    countEl.textContent = `${matchedAlbums.length} albums, ${matchedTracks.length} tracks found`;
    pagination.style.display = 'none';
    scrollToTop();
    renderGrid();
  }

  // Input event handler
  let isComposing = false;
  input.addEventListener('compositionstart', () => { isComposing = true; });
  input.addEventListener('compositionend', () => {
    isComposing = false;
    input.dispatchEvent(new Event('input'));
  });

  input.addEventListener('input', () => {
    if (isComposing) return;
    searchQuery = input.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => performSearch(searchQuery), 300);
  });

  // Clear search and go back when input is emptied
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Backspace' && input.value.length === 0) {
      history.back();
    }
  });

  // Focus and set cursor
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);

  // Pagination buttons (disabled in search mode)
  prevBtn.addEventListener('click', () => {
    if (searchMode || currentPage <= 1) return;
    currentPage--;
    scrollToTop();
    renderGrid();
  });

  nextBtn.addEventListener('click', () => {
    if (searchMode) return;
    const filtered = sortAlbums(getFilteredData(), currentSort);
    const totalPages = Math.ceil(filtered.length / ALBUMS_PER_PAGE) || 1;
    if (currentPage < totalPages) {
      currentPage++;
      scrollToTop();
      renderGrid();
    }
  });

  // Favorites filter
  favBtn.addEventListener('click', () => {
    if (searchMode) return;
    showFavoritesOnly = !showFavoritesOnly;
    currentPage = 1;
    favBtn.style.color = showFavoritesOnly ? '#ff6b6b' : 'var(--muted)';
    favBtn.textContent = showFavoritesOnly ? '♥ Favorites' : '♡ Favorites';
    renderGrid();
  });

  // Custom Select Interaction Logic
  function setupVSelect(el, onSelect) {
    if (!el) return;
    const trigger = el.querySelector('.v-select-trigger');
    const options = el.querySelectorAll('.v-option');

    trigger.onclick = (e) => {
      e.stopPropagation();
      const isOpen = el.classList.contains('open');
      document.querySelectorAll('.v-select').forEach(s => s.classList.remove('open'));
      if (!isOpen) el.classList.add('open');
    };

    options.forEach(opt => {
      opt.onclick = (e) => {
        e.stopPropagation();
        options.forEach(o => o.classList.remove('active'));
        opt.classList.add('active');
        const val = opt.dataset.value;
        el.classList.remove('open');
        onSelect(val);
      };
    });
  }

  setupVSelect(sortSelect, val => {
    if (searchMode) return;
    currentSort = val;
    currentPage = 1;
    renderGrid();
  });

  setupVSelect(genreSelect, val => {
    if (searchMode) return;
    currentGenre = val;
    currentPage = 1;
    renderGrid();
  });

  setupVSelect(decadeSelect, val => {
    if (searchMode) return;
    currentDecade = val;
    currentPage = 1;
    renderGrid();
  });

  // Global click to close v-selects (use AbortController to prevent listener leak across page navigations)
  const closeAllVSelects = () => document.querySelectorAll('.v-select').forEach(s => s.classList.remove('open'));
  const vSelectCloseAc = new AbortController();
  window.addEventListener('click', closeAllVSelects, { signal: vSelectCloseAc.signal });
  // Clean up previous instance if navigated back to albums
  window.__vSelectCloseAc?.abort();
  window.__vSelectCloseAc = vSelectCloseAc;

  clearFocusBtn.addEventListener('click', () => {
    if (searchMode) return;
    currentGenre = '';
    currentDecade = '';
    // Reset labels manually since we bypass full render for focus chips sometimes
    genreSelect.querySelector('.v-select-label').textContent = 'All genres';
    decadeSelect.querySelector('.v-select-label').textContent = 'All decades';
    genreSelect.querySelectorAll('.v-option').forEach(o => o.classList.remove('active'));
    decadeSelect.querySelectorAll('.v-option').forEach(o => o.classList.remove('active'));
    currentPage = 1;
    renderGrid();
  });
}

// ── Artists ───────────────────────────────────────────────────────────────────

async function renderArtists(vc) {
  const allArtists = await api('/artists?limit=5000');
  let searchQuery = '';
  let artists = allArtists;
  let currentPage = 1;

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('artists')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" placeholder="Search artists…" value="${escHtml(searchQuery)}">
      </div>
    </div>
    <div class="section-header" id="artists-header" title="Click to scroll to top">
      <div class="section-title">Artists</div>
      <div class="section-sub" id="artists-count">${allArtists.length} artists</div>
    </div>
    <div class="grid artist-grid" id="artists-grid"></div>
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

  // Scroll to top on header click
  headerEl.addEventListener('click', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
    mainEl.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Show/hide back-to-top button based on scroll position
  mainEl.addEventListener('scroll', () => {
    if (mainEl.scrollTop > 300) {
      backToTopBtn.style.display = 'flex';
    } else {
      backToTopBtn.style.display = 'none';
    }
  }, { passive: true });

  // Back to top button click
  backToTopBtn.addEventListener('click', () => {
    mainEl.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // Helper functions for pagination
  function getFilteredData() {
    const q = searchQuery.trim();
    if (!q) {
      return allArtists;
    }
    return allArtists.filter(a => {
      const name = a.name || '';
      return chineseMatch(name, q);
    });
  }

  function getPagedData() {
    const filtered = getFilteredData();
    const totalPages = Math.ceil(filtered.length / ARTISTS_PER_PAGE) || 1;
    if (currentPage > totalPages) currentPage = totalPages;
    const start = (currentPage - 1) * ARTISTS_PER_PAGE;
    const end = start + ARTISTS_PER_PAGE;
    return filtered.slice(start, end);
  }

  function updatePagination() {
    const filtered = getFilteredData();
    const totalPages = Math.ceil(filtered.length / ARTISTS_PER_PAGE) || 1;

    prevBtn.disabled = currentPage <= 1 || searchQuery.trim() !== '';
    nextBtn.disabled = currentPage >= totalPages || searchQuery.trim() !== '';
    pageInfo.textContent = searchQuery.trim() !== ''
      ? `Search mode`
      : `Page ${currentPage} / ${totalPages}`;

    countEl.textContent = searchQuery.trim() !== ''
      ? `${filtered.length} artist${filtered.length !== 1 ? 's' : ''} found`
      : `${filtered.length} artists · ${totalPages} pages`;
  }

  // Render grid with current data
  function renderGrid() {
    grid.innerHTML = '';
    const data = searchQuery.trim() ? getFilteredData() : getPagedData();
    if (!data.length) {
      grid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">${searchQuery.trim() ? 'No artists found.' : 'No artists found.'}</div>`;
      pagination.style.display = searchQuery.trim() ? 'none' : 'flex';
      return;
    }
    pagination.style.display = searchQuery.trim() ? 'none' : 'flex';
    const fragment = document.createDocumentFragment();
    data.forEach(a => fragment.appendChild(makeArtistCard(a)));
    grid.appendChild(fragment);
    updatePagination();
  }

  // Local search filter
  function filterArtists() {
    const q = searchQuery.trim();
    if (!q) {
      artists = allArtists;
      currentPage = 1; // Reset to first page when clearing search
    } else {
      artists = allArtists.filter(a => {
        const name = a.name || '';
        return chineseMatch(name, q);
      });
    }
    renderGrid();
  }

  // Input event handler
  let isComposing = false;
  input.addEventListener('compositionstart', () => { isComposing = true; });
  input.addEventListener('compositionend', () => {
    isComposing = false;
    input.dispatchEvent(new Event('input'));
  });

  input.addEventListener('input', () => {
    if (isComposing) return;
    searchQuery = input.value;
    filterArtists();
  });

  // Backspace to previous page when empty
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Backspace' && input.value.length === 0) {
      history.back();
    }
  });

  input.focus();
  const val = input.value;
  input.setSelectionRange(val.length, val.length);

  // Pagination buttons (disabled in search mode)
  prevBtn.addEventListener('click', () => {
    if (searchQuery.trim() || currentPage <= 1) return;
    currentPage--;
    mainEl.scrollTo({ top: 0, behavior: 'smooth' });
    renderGrid();
  });

  nextBtn.addEventListener('click', () => {
    if (searchQuery.trim()) return;
    const filtered = getFilteredData();
    const totalPages = Math.ceil(filtered.length / ARTISTS_PER_PAGE) || 1;
    if (currentPage < totalPages) {
      currentPage++;
      mainEl.scrollTo({ top: 0, behavior: 'smooth' });
      renderGrid();
    }
  });

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
        <input id="search-input" type="text" placeholder="Search composers…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Composers</div>
      <div class="section-sub" id="composers-count">${allComposers.length} composers</div>
    </div>
    <div class="grid artist-grid" id="composers-grid"></div>
  `;

  const input = UI['search-input'];
  const grid = UI['composers-grid'];
  const countEl = UI['composers-count'];

  function getFiltered() {
    const q = searchQuery.trim();
    if (!q) return allComposers;
    return allComposers.filter(item => chineseMatch(item.composer || '', q));
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

  let searchTimer;
  input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchQuery = input.value.trim();
      renderGrid();
    }, 200);
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
        <input id="search-input" type="text" placeholder="Search works, composers, conductors…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Works</div>
      <div class="section-sub" id="works-count">${allWorks.length} works</div>
    </div>
    <div class="credits-grid" id="works-grid"></div>
  `;

  const grid = UI['works-grid'];
  const countEl = UI['works-count'];

  const filterWorks = () => {
    const q = (UI['search-input']?.value || '').trim().toLowerCase();
    if (!q) return allWorks;
    return allWorks.filter(item =>
      chineseMatch(item.work_title || '', q) ||
      chineseMatch(item.composer || '', q)
    );
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

  UI['search-input']?.addEventListener('input', renderGrid);
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
        <input id="search-input" type="text" placeholder="Search performers, conductors, ensembles…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Credits</div>
      <div class="section-sub">Browse your library by performers, conductors, and ensembles.</div>
    </div>
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
    const query = ((UI['search-input']?.value || '').trim().toLowerCase());
    const countEl = UI[`${role}s-count`];
    const grid = UI[`${role}s-grid`];
    const items = !query
      ? buckets[role]
      : buckets[role].filter(item => chineseMatch(item.name || '', query));

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

  UI['search-input']?.addEventListener('input', rerender);
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
  let searchQuery = '';
  let currentPage = 1;
  let currentSort = 'title';
  const PAGE_SIZE = 200;

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('tracks')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" placeholder="Search tracks, artists, works, composers, conductors…">
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
    const offset = (currentPage - 1) * PAGE_SIZE;
    const data = await api(`/tracks?limit=${PAGE_SIZE}&offset=${offset}&sort=${encodeURIComponent(currentSort)}&search=${encodeURIComponent(searchQuery)}`);
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

    countEl.textContent = searchQuery
      ? `${total.toLocaleString()} track matches`
      : `${total.toLocaleString()} tracks`;
    pageInfo.textContent = `Page ${currentPage} / ${totalPages}`;
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
  }

  let searchTimer;
  input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchQuery = input.value.trim();
      currentPage = 1;
      renderPage();
    }, 250);
  });

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

async function renderGenres(vc) {
  const genres = await api('/genres') || [];

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('genres')}
      <div class="search-wrap">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input id="search-input" type="text" placeholder="Filter genres…">
      </div>
    </div>
    <div class="section-header">
      <div class="section-title">Genres</div>
      <div class="section-sub" id="genres-count">${genres.length} genres</div>
    </div>
    <div class="grid" id="genres-grid"></div>
  `;

  const grid = UI['genres-grid'];
  const input = UI['search-input'];
  const countEl = UI['genres-count'];

  function draw(items) {
    grid.innerHTML = '';
    countEl.textContent = `${items.length} genres`;
    if (!items.length) {
      grid.innerHTML = `<div style="padding:0;color:var(--muted);font-size:13px;grid-column:1/-1">No genres found.</div>`;
      return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach(item => {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <div class="card-art" style="display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,var(--s2),var(--s4));font-family:'Cormorant Garamond',serif;font-size:28px;color:var(--gold)">
          ${escHtml(item.genre.charAt(0).toUpperCase())}
        </div>
        <div class="card-title">${escHtml(item.genre)}</div>
        <div class="card-sub">${item.count} albums</div>
      `;
      card.addEventListener('click', () => navigate('genre', { id: item.genre }));
      fragment.appendChild(card);
    });
    grid.appendChild(fragment);
  }

  input.addEventListener('input', () => {
    const query = input.value.trim().toLowerCase();
    draw(genres.filter(item => item.genre.toLowerCase().includes(query)));
  });

  draw(genres);
}

async function renderGenre(vc, { id }) {
  const data = await api(`/genres/${encodeURIComponent(id)}/albums?limit=1000&sort=year`);
  const albums = data?.albums || [];

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
      <div class="section-title">${escHtml(id)}</div>
      <div class="section-sub">${albums.length} albums</div>
    </div>
    <div class="grid" id="genre-albums-grid"></div>
  `;

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
  playlists.forEach(playlist => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="card-art" style="display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,var(--s2),var(--s4));color:var(--gold)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:36px;height:36px">
          <path d="M9 19V6l12-2v13"></path>
          <circle cx="6" cy="18" r="3"></circle>
          <circle cx="18" cy="16" r="3"></circle>
        </svg>
      </div>
      <div class="card-title">${escHtml(playlist.name)}</div>
      <div class="card-sub">${playlist.track_count || 0} tracks</div>
    `;
    card.addEventListener('click', () => navigate('playlist', { id: playlist.id }));
    fragment.appendChild(card);
  });
  grid.appendChild(fragment);
}

async function renderPlaylist(vc, { id }) {
  const playlist = await api(`/playlists/${id}`);
  if (!playlist) {
    vc.innerHTML = `<div class="empty-state"><div class="empty-state-title">Playlist not found</div></div>`;
    return;
  }

  vc.innerHTML = `
    <div class="topbar">
      <button class="back-btn" onclick="handleHistoryBack('playlists')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
      <button class="btn btn-ghost" id="rename-playlist-btn">Rename</button>
      <button class="btn btn-ghost" id="delete-playlist-btn">Delete</button>
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
    row.addEventListener('click', () => playTrack(track, playlist.tracks, index));

    const removeBtn = row.querySelector('.track-add-btn');
    if (removeBtn) {
      const deleteBtn = removeBtn.cloneNode(true);
      deleteBtn.innerHTML = '−';
      deleteBtn.title = 'Remove from playlist';
      removeBtn.replaceWith(deleteBtn);
      deleteBtn.addEventListener('click', async e => {
        e.stopPropagation();
        const result = await api(`/playlists/${id}/tracks/${track.id}`, { method: 'DELETE' });
        if (result?.success) {
          showToast('Removed from playlist');
          renderPlaylist(vc, { id });
        }
      });
    }

    fragment.appendChild(row);
  });
  list.appendChild(fragment);
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
        <img src="/api/artists/${artist.id}/image" id="artist-hero-img"
             onerror="this.style.display='none'">
        <div class="artist-art-placeholder" id="artist-art-placeholder">${artist.name.charAt(0).toUpperCase()}</div>
      </div>
      <div class="artist-hero-info">
        <div class="section-title">${escHtml(artist.name)}</div>
        <div class="section-sub">${albums.length} albums · ${albums.reduce((s, a) => s + (a.track_count || 0), 0)} tracks</div>
      </div>
    </div>
    <div class="artist-bio" id="artist-bio"><div class="bio-loading">Loading artist bio…</div></div>
    <div style="padding: 0 24px; margin-bottom: 16px;">
      <button class="btn btn-gold" id="artist-radio-btn">Artist Radio</button>
      <button class="btn btn-ghost" id="refresh-artist-btn" title="Re-read artist name and all album metadata from files">🔄 Re-read Tags</button>
    </div>
    <div class="grid" id="artist-albums-grid"></div>
  `;

  const grid = UI['artist-albums-grid'];
  // Use DocumentFragment for better performance
  const fragment = document.createDocumentFragment();
  albums.forEach(a => fragment.appendChild(makeAlbumCard({ ...a, artist_name: artist.name })));
  grid.appendChild(fragment);

  UI['artist-radio-btn']?.addEventListener('click', () => startRadio('artist', id));

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
          ${album.genre ? `<div class="album-meta-item">${escHtml(album.genre)}</div>` : ''}
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
        <div>Format</div><div>Quality</div><div>Duration</div><div></div>
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
    const isMobile = window.matchMedia('(max-width: 768px), (orientation: landscape) and (max-height: 500px)').matches;
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
    const el = makeTrackRow(t, i + 1);
    const trackIndex = i;
    el.addEventListener('click', () => {
      state.prevAlbumQueue = tracks;
      const trackWithInfo = {
        ...t,
        artist_name: album.artist_name,
        album_title: album.title
      };
      playTrack(trackWithInfo, tracks, trackIndex);
    });
    fragment.appendChild(el);
  });
  container.appendChild(fragment);

  // Play all
  UI['play-all-btn'].addEventListener('click', () => {
    if (tracks.length) playTrack(tracks[0], tracks, 0);
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
      const res = await fetch(`/api/albums/${album.id}/fetch-cover`, { method: 'POST' });
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
        const res = await fetch(`/api/albums/${album.id}/cover`, {
          method: 'POST',
          body: formData
        });
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
      body: `Are you sure you want to delete "${album.title}"?<br><br>Choose how to delete:`,
      extra: `
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer;margin-top:10px">
          <input type="checkbox" id="delete-files-check"> Also delete files from hard drive
        </label>
      `,
      confirmText: 'Delete Now',
      onConfirm: async () => {
        const delFiles = UI['delete-files-check']?.checked;
        showToast(delFiles ? 'Deleting files and library entry…' : 'Removing from library…');
        try {
          const res = await api(`/albums/${album.id}?delete_files=${delFiles}`, { method: 'DELETE' });
          if (res.success) {
            showToast('Album deleted');
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

async function renderSearch(vc, { q }) {
  const results = await api(`/search?q=${encodeURIComponent(q)}`);
  const tracks = results.tracks || [];
  const containerId = 'search-tracks';
  const existingContainer = UI[containerId];

  if (!existingContainer) {
    vc.innerHTML = `
      <div class="topbar">
        ${getTopNavHtml('search')}
        <div class="search-wrap">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input id="search-input" type="text" placeholder="Search…" value="${escHtml(q)}">
        </div>
      </div>
      <div class="search-results">
        <div class="result-section-title" id="search-results-title">Tracks — ${tracks.length} results</div>
        <div id="${containerId}"></div>
      </div>
    `;
    setupSearch();
  } else {
    UI['search-results-title'].textContent = `Tracks — ${tracks.length} result${tracks.length !== 1 ? 's' : ''}`;
  }

  const container = UI[containerId];
  container.innerHTML = '';
  if (!tracks.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:16px 0">No results found.</div>';
    return;
  }

  // Use DocumentFragment for better performance
  const fragment = document.createDocumentFragment();
  tracks.forEach((t, i) => {
    const el = makeTrackRow(t, i + 1, true);
    const trackIndex = i;
    el.addEventListener('click', () => {
      // Keep mobile user-gesture playback chain intact: play immediately, then
      // load richer album context asynchronously.
      playTrack(t, tracks, trackIndex);
      if (!t.album_id) return;
      (async () => {
        await navigate('album', { id: t.album_id });
        try {
          const albumData = await api(`/albums/${t.album_id}`);
          const albumTracks = albumData.tracks || [];
          const trackIdx = albumTracks.findIndex(tr => tr.id === t.id);
          if (trackIdx === -1) return;
          if (!state.currentTrack || String(state.currentTrack.id) !== String(t.id)) return;
          state.queue = albumTracks;
          state.queueIdx = trackIdx;
          state.currentTrack = { ...state.currentTrack, ...albumTracks[trackIdx] };
          updatePlayerUI(state.currentTrack);
          if (state.currentTrack) highlightPlaying(state.currentTrack.id);
          if (UI['queue-panel']?.classList.contains('open')) renderQueue();
        } catch (e) {
          console.warn('Failed to sync search playback queue:', e);
        }
      })();
    });
    fragment.appendChild(el);
  });
  container.appendChild(fragment);
}

// ── Settings ──────────────────────────────────────────────────────────────────
