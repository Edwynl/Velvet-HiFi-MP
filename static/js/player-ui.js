/* VELVET player-ui: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
function syncMobileLandscapeControls() {
  const upsampleEl = $('mobile-top-upsample-label');
  const dspEl = $('mobile-top-dsp-label');
  const castEl = $('mobile-top-cast');
  if (upsampleEl) {
    const selected = $('upsample-select')?.selectedOptions?.[0]?.textContent?.trim();
    upsampleEl.textContent = state.upsample && state.upsample !== 'none' ? (selected || state.upsample) : 'Source';
  }
  if (dspEl) {
    const failure = isBrowserZone() ? browserDspFailure : null;
    const profileId = state.dspProfile || failure?.profileId;
    const profile = state.dspProfiles.find(p => String(p.id) === String(profileId));
    const label = profile ? dspDisplayLabel(profile) : 'DSP';
    dspEl.textContent = failure ? `${label} unavailable` : state.dspProfile ? label : 'Off';
    dspEl.title = failure ? `Saved preset could not play (${failure.reason}); original audio is playing.` : '';
  }
  const eqButton = $('mobile-top-eq');
  eqButton?.classList.toggle('active', Boolean(state.dspProfile));
  eqButton?.setAttribute('aria-label', `Equalizer: ${dspEl?.textContent || 'Off'}`);
  castEl?.classList.toggle('active', isCastZone() || isNativeZone());
}

function showMobileSelectPicker(selectId, title) {
  const select = $(selectId);
  if (!select) return;

  const options = Array.from(select.options)
    .filter(option => !option.disabled)
    .map(option => ({
      value: String(option.value ?? ''),
      label: option.textContent?.trim() || String(option.value ?? ''),
      fullLabel: option.getAttribute('title') || option.textContent?.trim() || String(option.value ?? ''),
      category: option.dataset.category || '',
      selected: option.selected,
    }));

  if (!options.length) return;

  document.querySelector('.mobile-select-picker-backdrop')?.remove();

  const modal = document.createElement('div');
  modal.className = 'modal-backdrop mobile-select-picker-backdrop';

  const content = document.createElement('div');
  content.className = 'modal-content mobile-select-picker-modal';

  const header = document.createElement('div');
  header.className = 'modal-header';

  const heading = document.createElement('h3');
  heading.textContent = title;

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'btn btn-ghost close-btn';
  closeBtn.style.padding = '6px';
  closeBtn.innerHTML = `
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"></line>
      <line x1="6" y1="6" x2="18" y2="18"></line>
    </svg>`;

  header.appendChild(heading);
  header.appendChild(closeBtn);

  const body = document.createElement('div');
  body.className = 'modal-body';

  const list = document.createElement('div');
  list.className = 'mobile-select-picker-list';

  const close = () => {
    document.removeEventListener('keydown', onKeyDown);
    modal.remove();
  };

  const onKeyDown = (event) => {
    if (event.key === 'Escape') close();
  };

  options.forEach(option => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `mobile-select-picker-option${option.selected ? ' active' : ''}`;
    button.textContent = option.label;
    if (option.fullLabel && option.fullLabel !== option.label) {
      button.title = option.fullLabel;
      button.setAttribute('aria-label', `${option.label} - ${option.fullLabel}`);
    }
    button.dataset.value = option.value;
    button.addEventListener('click', () => {
      select.value = option.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      close();
    });
    list.appendChild(button);
  });

  body.appendChild(list);
  content.appendChild(header);
  content.appendChild(body);
  modal.appendChild(content);
  document.body.appendChild(modal);

  closeBtn.onclick = close;
  modal.onclick = (event) => {
    if (event.target === modal) close();
  };
  document.addEventListener('keydown', onKeyDown);

  requestAnimationFrame(() => {
    modal.querySelector('.mobile-select-picker-option.active, .mobile-select-picker-option')?.focus();
  });
}

function openMobileUpsamplePicker() {
  showMobileSelectPicker('upsample-select', 'Output Rate');
}

function getPlayerThumbUrl(albumId) {
  return `/api/covers/${albumId}/thumb?size=small`;
}

function getPlayerCoverUrl(albumId) {
  return `/api/covers/${albumId}`;
}

let activePlayerSelectPopover = null;

function closePlayerSelectPopover() {
  if (!activePlayerSelectPopover) return;
  activePlayerSelectPopover.cleanup?.();
  activePlayerSelectPopover = null;
}

function showPlayerSelectPopover(anchorEl, selectId, { openUp = true } = {}) {
  const select = $(selectId);
  if (!select || !anchorEl) return;

  closePlayerSelectPopover();

  const options = Array.from(select.options).map(option => ({
    value: option.value,
    label: option.textContent?.trim() || option.value,
    selected: option.selected,
  }));
  if (!options.length) return;

  const rect = anchorEl.getBoundingClientRect();
  const popover = document.createElement('div');
  popover.className = 'player-select-popover';
  popover.style.minWidth = `${Math.max(rect.width, 112)}px`;
  popover.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - Math.max(rect.width, 112) - 12))}px`;
  if (openUp) {
    popover.style.bottom = `${Math.max(12, window.innerHeight - rect.top + 8)}px`;
  } else {
    popover.style.top = `${Math.min(window.innerHeight - 12, rect.bottom + 8)}px`;
  }

  options.forEach(option => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `player-select-popover-option${option.selected ? ' active' : ''}`;
    button.textContent = option.label;
    button.addEventListener('click', () => {
      select.value = option.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      closePlayerSelectPopover();
    });
    popover.appendChild(button);
  });

  const onPointerDown = (event) => {
    if (popover.contains(event.target) || anchorEl.contains(event.target)) return;
    closePlayerSelectPopover();
  };
  const onKeyDown = (event) => {
    if (event.key === 'Escape') closePlayerSelectPopover();
  };
  const cleanup = () => {
    document.removeEventListener('pointerdown', onPointerDown, true);
    document.removeEventListener('keydown', onKeyDown);
    popover.remove();
  };

  activePlayerSelectPopover = { cleanup };
  document.body.appendChild(popover);
  requestAnimationFrame(() => {
    popover.classList.add('open');
    popover.querySelector('.player-select-popover-option.active, .player-select-popover-option')?.focus();
  });
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('keydown', onKeyDown);
}

function bindDesktopPlayerSelectPopover(selectId, options = {}) {
  const select = $(selectId);
  if (!select) return;

  const openPopover = (event) => {
    if (isCompactLayout()) return;
    event.preventDefault?.();
    event.stopPropagation?.();
    showPlayerSelectPopover(select, selectId, options);
  };

  select.addEventListener('pointerdown', openPopover);
  select.addEventListener('keydown', (event) => {
    if (!['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    openPopover(event);
  });
}

function updateMobileFavoriteButton(track) {
  const favBtn = $('mobile-btn-favorite');
  if (!favBtn) return;
  const isFavorite = !!(track?.album_is_favorite || track?.is_favorite);
  favBtn.classList.toggle('favorite-active', isFavorite);
  favBtn.setAttribute('aria-pressed', isFavorite ? 'true' : 'false');
  favBtn.title = isFavorite ? 'Remove favorite' : 'Favorite album';
}

function updatePlayingArtistLink(element, track) {
  if (!element) return;
  const name = track.artist_name || track.artist || track.album_artist || '—';
  element.textContent = name;
  if (!Number.isSafeInteger(Number(track.artist_id)) || Number(track.artist_id) <= 0) return;
  const link = document.createElement('a');
  link.href = '#artist/' + encodeURIComponent(track.artist_id);
  link.textContent = name;
  link.className = 'playing-library-link';
  link.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    openPlayingLibraryItem('artist', track.artist_id);
  });
  element.replaceChildren(link);
}

function openPlayingLibraryItem(view, id) {
  if (!Number.isSafeInteger(Number(id)) || Number(id) <= 0) return;
  if (vuMeterOpen) toggleVUMeters(false);
  toggleMobilePlayer(false, { source: 'dismiss' });
  navigate(view, { id: Number(id) });
}

function updatePlayerUI(track) {
  UI['player-title'].textContent  = track.title || '—';
  UI['player-artist'].textContent = track.artist_name || track.artist || track.album_artist || '—';

  const bitLabel = track.bit_depth ? `${track.bit_depth}bit` : (track.format || '');
  const rateLabel = track.sample_rate ? fmtCompactSR(track.sample_rate) : '';
  const compactFormat = [bitLabel, rateLabel].filter(Boolean).join(' ');
  const showHiRes = isHiResTrack(track);
  let fmt_label = track.format || '';
  if (track.sample_rate) fmt_label += ` · ${fmtSR(track.sample_rate)}`;
  if (track.bit_depth)   fmt_label += ` · ${track.bit_depth}bit`;
  if (state.upsample !== 'none') fmt_label += ` → ${state.upsample.toUpperCase()} Upsampled`;
  const dspProfile = state.dspProfiles.find(profile => String(profile.id) === String(state.dspProfile));
  fmt_label += ` · ${dspProfile ? `DSP ${dspProfile.name}` : 'DSP Off'}`;
  const activeZone = getActiveZone();
  fmt_label += ` · Output ${activeZone.name}`;
  const formatEl = UI['player-format'];
  formatEl.innerHTML = showHiRes
    ? '<img src="/static/logo-hires.png" class="player-format-badge" alt="Hi-Res">'
    : '';
  formatEl.style.display = showHiRes ? '' : 'none';
  formatEl.title = fmt_label || 'Open Signal Path';

  const albumId = track.album_id ? String(track.album_id) : '';

  // Cover art - avoid reloading the same image when only DSP/Source state changes.
  if (track.album_id) {
    const artWrap = UI['player-art'];
    let img = artWrap.querySelector('img');
    if (!img) {
      img = document.createElement('img');
      artWrap.innerHTML = '';
      artWrap.appendChild(img);
    }
    if (img.dataset.albumId !== albumId) {
      img.dataset.albumId = albumId;
      img.onerror = () => {
        img.onerror = null;
        img.src = getPlayerCoverUrl(track.album_id);
      };
      img.src = getPlayerThumbUrl(track.album_id);
    }
  }

  // Update play button to pause icon
  const pauseIcon = `<svg class="icon-play" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`;
  const playPauseHtml = `<svg viewBox="0 0 24 24" fill="currentColor">
    <rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>
  </svg>`;
  UI['btn-play'].innerHTML = playPauseHtml;
  UI['mobile-btn-play'].innerHTML = playPauseHtml;

  // ─── Mobile Player UI Update ───
  UI['mobile-player-track-title'].textContent = track.title || '—';
  updatePlayingArtistLink(UI['mobile-player-track-artist'], track);
  UI['mobile-landscape-track-title'].textContent = track.title || '—';
  updatePlayingArtistLink(UI['mobile-landscape-track-artist'], track);
  // Only write these when the value really changes. Writing the same value
  // again still counts as a mutation, and the phone repaints the artwork
  // when it sees one - so changing the DSP must not touch the cover.
  const albumCover = UI['mobile-player-cover'];
  const coverHasAlbum = Number.isSafeInteger(Number(track.album_id)) && Number(track.album_id) > 0;
  const coverLabel = 'Open album' + (track.album_title ? ': ' + track.album_title : '');
  if (coverHasAlbum) {
    if (albumCover.getAttribute('role') !== 'link') albumCover.setAttribute('role', 'link');
    if (albumCover.tabIndex !== 0) albumCover.tabIndex = 0;
    if (albumCover.getAttribute('aria-label') !== coverLabel) albumCover.setAttribute('aria-label', coverLabel);
    if (albumCover.title !== 'Open album') albumCover.title = 'Open album';
  } else {
    ['role', 'tabindex', 'aria-label', 'title'].forEach(attr => {
      if (albumCover.hasAttribute(attr)) albumCover.removeAttribute(attr);
    });
  }
  const mobileBadge = UI['mobile-player-hires-badge'];
  if (showHiRes || compactFormat) {
    mobileBadge.style.display = 'flex';
    UI['mobile-player-format-text'].textContent = compactFormat || 'Hi-Res';
  } else {
    mobileBadge.style.display = 'none';
  }
  UI['mobile-landscape-hires'].style.display = showHiRes ? 'inline-flex' : 'none';
  UI['mobile-landscape-quality-text'].textContent = compactFormat || (track.format || '');
  if (track.album_id) {
    const coverUrl = getPlayerCoverUrl(track.album_id);
    const mobileCover = UI['mobile-player-cover'];
    if (mobileCover.dataset.albumId !== albumId) {
      mobileCover.dataset.albumId = albumId;
      mobileCover.src = coverUrl;
    }
    const bgImg = UI['mobile-player-bg-image'];
    if (bgImg && bgImg.dataset.albumId !== albumId) {
      bgImg.dataset.albumId = albumId;
      bgImg.style.backgroundImage = `url("${coverUrl}")`;
    }
  }
  updateMobileFavoriteButton(track);
  syncMobileLandscapeControls();

  // ─── VU Overlay UI Update ───
  const vuTitle = UI['mobile-vu-track-title'];
  const vuArtist = UI['mobile-vu-track-artist'];
  if (vuTitle) vuTitle.textContent = track.title || '—';
  updatePlayingArtistLink(vuArtist, track);
  
  const vuFormat = UI['mobile-vu-format'];
  const vuSR = UI['mobile-vu-sr'];
  const vuCodec = UI['mobile-vu-codec'];
  if (vuFormat) vuFormat.textContent = track.bit_depth ? `${track.bit_depth}-BIT` : (track.format ? track.format.toUpperCase() : '—');
  if (vuSR) vuSR.textContent = rateLabel || '—';
  if (vuCodec) vuCodec.textContent = track.format ? track.format.toUpperCase() : '—';
}

async function toggleCurrentAlbumFavorite() {
  const track = state.currentTrack;
  if (!track?.album_id) {
    showToast('No album available for this track');
    return;
  }
  try {
    const res = await api(`/albums/${track.album_id}/favorite`, { method: 'POST' });
    if (!res?.success) {
      showToast('Failed to update favorite');
      return;
    }
    track.album_is_favorite = !!res.is_favorite;
    track.is_favorite = !!res.is_favorite;
    updateMobileFavoriteButton(track);
    const albumFavBtn = UI['favorite-btn'];
    if (albumFavBtn && state.view === 'album' && String(state.viewParams?.id) === String(track.album_id)) {
      albumFavBtn.style.color = res.is_favorite ? '#ff6b6b' : 'var(--muted)';
      albumFavBtn.textContent = res.is_favorite ? '♥ Favorite' : '♡ Favorite';
    }
    showToast(res.is_favorite ? 'Added to favorites' : 'Removed from favorites');
  } catch (e) {
    showToast(`Error: ${e.message}`);
  }
}

function highlightPlaying(trackId) {
  // Silently exit if no valid trackId — don't throw or log
  if (!trackId) return;
  // Optimization: Only update the previously playing track and the new one
  const prevPlaying = document.querySelector('.track-item.playing');
  const newPlaying = document.querySelector(`.track-item[data-track-id="${trackId}"]`);

  if (prevPlaying) {
    prevPlaying.classList.remove('playing');
    const num = prevPlaying.querySelector('.track-num');
    if (num) num.textContent = prevPlaying.dataset.num || '';
  }

  if (newPlaying) {
    newPlaying.classList.add('playing');
    const num = newPlaying.querySelector('.track-num');
    if (num) {
      num.innerHTML = `<div class="playing-indicator">
        <span></span><span></span><span></span>
      </div>`;
    }
  }
}

// Follow the CSS scroll owner: iPhone portrait uses the document so iOS can
// handle its native status-bar tap; other layouts retain the main scroller.
function getScrollRoot() {
  const main = UI['main'];
  return main && getComputedStyle(main).overflowY !== 'visible'
    ? main
    : (document.scrollingElement || document.documentElement);
}

// Scroll to top helper
function scrollToTop() {
  const main = UI['main'];
  if (main) {
    main.scrollTop = 0;
  }
  window.scrollTo({ top: 0, behavior: 'instant' });
}

// Throttle: caps timeupdate DOM work to ~15fps so progress bar updates don't
// cause continuous layout recalculation on mobile. Always fires the LAST pending
// update so progress bar is always accurate on screen.
let _timeupdateLast = 0;
let _timeupdateScheduled = false;
let _timeupdatePending = false;
