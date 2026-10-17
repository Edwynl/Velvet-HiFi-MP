/* VELVET remote-settings: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
async function renderRemote(vc) {
  const api = scopedViewApi(vc);
  // Get Tailscale status and security status
  const [status, security] = await Promise.all([
    api('/remote/status'),
    api('/security/status')
  ]);

  const isTailscaleRunning = status.status === 'running';
  const tailscaleUrl = status.url || '';
  const isAvailable = status.status !== 'not_installed';
  const securityEnabled = security.enabled;
  const securityBorder = securityEnabled ? '1px solid var(--gold)' : '1px solid #ff6b6b';

  // Status message based on state
  let statusMessage = '';
  let statusClass = 'disconnected';
  if (status.status === 'not_installed') {
    statusMessage = 'Tailscale not installed';
    statusClass = 'disconnected';
  } else if (status.status === 'not_running') {
    statusMessage = 'Tailscale not connected';
    statusClass = 'disconnected';
  } else if (isTailscaleRunning) {
    statusMessage = 'VPN Active';
    statusClass = 'connected';
  }

  if (!isCurrentView(vc)) return;
  vc.innerHTML = `
    <div class="topbar"></div>
    <div class="section-header">
      <div class="section-title">Remote Access</div>
      <div class="section-sub">Access your music library via Tailscale VPN</div>
    </div>

    <div class="settings-section">
      <!-- Tailscale Status Card -->
      <div class="settings-card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
          <div>
            <div class="settings-title" style="font-size:16px">Tailscale VPN</div>
            <div class="settings-desc" style="margin-top:4px">${statusMessage}</div>
          </div>
          <div class="remote-status-badge ${statusClass}">${statusClass === 'connected' ? 'Active' : 'Inactive'}</div>
        </div>

        ${status.status === 'not_installed' ? `
        <div style="background:rgba(201,168,76,0.1);border:1px solid var(--gold);border-radius:var(--radius);padding:16px;margin-bottom:20px">
          <div style="color:var(--gold);font-weight:500;margin-bottom:8px">Tailscale Not Installed</div>
          <div class="settings-desc" style="margin-bottom:12px">Download and install Tailscale to enable remote access:</div>
          <a href="https://tailscale.com/download" target="_blank" class="btn btn-gold" style="display:inline-block;text-decoration:none">
            Download Tailscale
          </a>
        </div>
        ` : ''}

        ${status.status === 'not_running' ? `
        <div style="background:rgba(255,165,0,0.1);border:1px solid #ffa500;border-radius:var(--radius);padding:16px;margin-bottom:20px">
          <div style="color:#ffa500;font-weight:500;margin-bottom:8px">Start Tailscale</div>
          <div class="settings-desc" style="margin-bottom:12px">Open the Tailscale app and connect to your Tailnet.</div>
          <div style="padding:12px;background:var(--s3);border-radius:var(--radius);font-family:monospace;font-size:13px">
            Make sure you're logged in and click Connect
          </div>
        </div>
        ` : ''}

        ${isTailscaleRunning && tailscaleUrl ? `
        <div style="margin-bottom:20px">
          <div class="settings-title" style="margin-bottom:8px">Your Access URL</div>
          <div class="settings-desc" style="margin-bottom:12px">Share this URL with others on your Tailscale network</div>
          <div class="remote-url-box">
            <input type="text" id="remote-url" value="${tailscaleUrl}" readonly class="settings-input">
            <button class="btn-copy" onclick="copyRemoteUrl()" title="Copy URL">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2"/>
                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
              </svg>
            </button>
          </div>
          <button class="btn btn-gold" style="margin-top:12px;width:100%" onclick="openRemoteUrl()" id="btn-open-remote">
            Open VELVET
          </button>
        </div>
        ` : ''}

        <div style="border-top:1px solid var(--border);padding-top:20px;margin-top:20px">
          <div class="settings-title" style="font-size:12px;color:var(--muted);margin-bottom:8px">How it works</div>
          <div class="settings-desc">
            Tailscale creates a secure VPN connection. Anyone on your Tailscale network (Tailnet) can access VELVET using the URL above. No port forwarding or tunnel services needed.
          </div>
        </div>
      </div>

      <!-- Secure browser origin: required for PWA and Service Workers on mobile -->
      <div class="settings-card" style="margin-top:20px">
        <div class="settings-title">Secure PWA Access (HTTPS)</div>
        <div class="settings-desc" style="margin-top:8px">
          The http://100.x Tailscale address is encrypted inside your VPN but is not a
          secure browser origin. Android/iOS browsers usually require HTTPS for
          Service Worker caching and installed PWA features.
        </div>
        ${status.https_ready && status.secure_url ? `
          <div class="settings-desc" style="margin-top:12px;color:var(--gold)">Tailscale Serve is active.</div>
          <a href="${escHtml(status.secure_url)}" target="_blank" rel="noopener"
             class="btn btn-gold" style="display:inline-block;margin-top:12px;text-decoration:none">
            Open secure VELVET
          </a>
        ` : `
          <div class="settings-desc" style="margin-top:12px">On your Windows server, open PowerShell and run:</div>
          <code style="display:block;overflow:auto;padding:12px;margin-top:8px;background:var(--s3)">tailscale serve --bg 8765</code>
          <div class="settings-desc" style="margin-top:8px">
            Enable HTTPS certificates/MagicDNS in your tailnet if prompted.
            Verify with <code>tailscale serve status</code>. This uses Serve within
            your tailnet only; do not use public Funnel.
          </div>
        `}
      </div>
      <!-- Setup Guide Card -->
      <div class="settings-card" style="margin-top:24px">
        <div class="settings-title" style="margin-bottom:16px">Setup Guide</div>

        <div style="margin-bottom:20px">
          <div style="color:var(--gold);font-weight:500;margin-bottom:8px">1. Install Tailscale</div>
          <div class="settings-desc" style="margin-bottom:8px">Download from <a href="https://tailscale.com/download" target="_blank" style="color:var(--gold)">tailscale.com/download</a></div>
        </div>

        <div style="margin-bottom:20px">
          <div style="color:var(--gold);font-weight:500;margin-bottom:8px">2. Sign In</div>
          <div class="settings-desc" style="margin-bottom:8px">Use your email, Google, GitHub, or Microsoft account to sign in.</div>
        </div>

        <div style="margin-bottom:20px">
          <div style="color:var(--gold);font-weight:500;margin-bottom:8px">3. Connect</div>
          <div class="settings-desc" style="margin-bottom:8px">Click "Connect" in the Tailscale app. Your Tailscale IP will appear above.</div>
        </div>

        <div>
          <div style="color:var(--gold);font-weight:500;margin-bottom:8px">4. Share Access</div>
          <div class="settings-desc">Give the URL to family members and help them install Tailscale too. Everyone on the same Tailnet can access VELVET!</div>
        </div>
      </div>

      <!-- Security Protection Card -->
      <div class="settings-card" style="border:${securityBorder};margin-top:24px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
          <div>
            <div class="settings-title" style="font-size:16px">Security Protection</div>
            <div class="settings-desc" style="margin-top:4px">
              ${securityEnabled
                ? 'Password protection is enabled. Remote access requires authentication.'
                : 'No password set. Anyone on Tailscale can access your library.'}
            </div>
          </div>
          <div class="remote-status-badge ${securityEnabled ? 'connected' : 'disconnected'}">
            ${securityEnabled ? 'Protected' : 'Unprotected'}
          </div>
        </div>

        ${!securityEnabled ? `
        <div style="background:rgba(255,107,107,0.1);border:1px solid #ff6b6b;border-radius:var(--radius);padding:14px;margin-bottom:20px">
          <div style="color:#ff6b6b;font-weight:500;font-size:13px">
            Warning: Your library is not protected! Set a password to secure remote access.
          </div>
        </div>
        ` : ''}

        ${securityEnabled ? `
        <div style="margin-bottom:20px">
          <div class="settings-title" style="color:var(--gold);margin-bottom:8px">API Key</div>
          <div class="settings-desc" style="margin-bottom:12px">Use this key for programmatic access (apps, scripts)</div>
          <div class="remote-url-box">
            <input type="text" id="api-key-display" value="${security.has_api_key ? '••••••••••••••••' : 'Not generated'}" readonly class="settings-input" style="font-family:monospace">
            <button class="btn-copy" onclick="showApiKey()" title="Show API Key">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
            </button>
          </div>
        </div>
        ` : ''}

        <div style="border-top:1px solid var(--border);padding-top:20px;margin-bottom:20px">
          <div class="settings-title" style="margin-bottom:8px">DLNA Library Browsing</div>
          <div class="settings-desc" id="dlna-browse-status" style="margin-bottom:12px">${dlnaBrowseStatusText(null)}</div>
          <div style="display:flex;gap:12px;flex-wrap:wrap">
            <button class="btn btn-gold" onclick="setDlnaBrowseAccess(true)">Allow for 1 Hour</button>
            <button class="btn" style="background:var(--s3)" onclick="setDlnaBrowseAccess(false)">Stop Browsing</button>
          </div>
        </div>

        <div style="border-top:1px solid var(--border);padding-top:20px">
          <div class="settings-title" style="margin-bottom:12px">${securityEnabled ? 'Change Password' : 'Set Password'}</div>
          <input type="password" id="new-password" placeholder="Enter new password (min 8 chars)" class="settings-input" style="width:100%;margin-bottom:12px;background:var(--s3)">
          <div style="display:flex;gap:12px;flex-wrap:wrap">
            <button class="btn btn-gold" onclick="savePassword()">
              ${securityEnabled ? 'Update Password' : 'Enable Protection'}
            </button>
            ${securityEnabled ? `
            <button class="btn" style="background:var(--s3)" onclick="generateNewApiKey()">
              Generate New API Key
            </button>
            ` : ''}
          </div>
        </div>

        <div style="border-top:1px solid var(--border);padding-top:20px;margin-top:20px">
          <div class="settings-title" style="font-size:12px;color:var(--muted);margin-bottom:8px">Security Info</div>
          <div class="settings-desc" style="line-height:1.8">
            <span style="color:#4caf50">&#10003;</span> Password uses SHA-256 hashing<br>
            <span style="color:#4caf50">&#10003;</span> 5 failed attempts = 5 min lockout<br>
            <span style="color:#4caf50">&#10003;</span> Local network access still works<br>
            <span style="color:#4caf50">&#10003;</span> Tailscale encrypts all traffic
          </div>
        </div>
      </div>
    </div>

    <style>
      .remote-status-badge {
        padding: 6px 14px;
        border-radius: 16px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        flex-shrink:0;
      }
      .remote-status-badge.connected {
        background: rgba(76, 175, 80, 0.2);
        color: #4caf50;
      }
      .remote-status-badge.disconnected {
        background: rgba(255, 107, 107, 0.15);
        color: #ff6b6b;
      }
      .remote-url-box {
        display: flex;
        gap: 8px;
      }
      .remote-url-box .settings-input {
        flex: 1;
        background: var(--s3);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 12px 14px;
        color: var(--gold);
        font-family: 'DM Mono', monospace;
        font-size: 13px;
      }
      .btn-copy {
        background: var(--s3);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 10px 12px;
        color: var(--muted);
        cursor: pointer;
        transition: all 0.15s;
      }
      .btn-copy:hover {
        background: var(--s4);
        color: var(--text);
      }
    </style>
  `;

  // Update sidebar status dot
  updateRemoteStatusDot(isTailscaleRunning);
  api('/dlna/browse-access').then(renderDlnaBrowseStatus).catch(() => {});
}

function dlnaBrowseStatusText(result) {
  if (result?.active && result.expires_at) {
    const until = new Date(result.expires_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return `DLNA apps on your home network can browse and play until ${until}.`;
  }
  return 'Off. HEOS, StreamMagic and other DLNA apps on your home network cannot sign in, so allow browsing for a limited time.';
}

function renderDlnaBrowseStatus(result) {
  const el = document.getElementById('dlna-browse-status');
  if (el && result) el.textContent = dlnaBrowseStatusText(result);
}

async function setDlnaBrowseAccess(enable) {
  try {
    const result = await api('/dlna/browse-access', enable
      ? { method: 'POST', body: JSON.stringify({ minutes: 60 }) }
      : { method: 'DELETE' });
    renderDlnaBrowseStatus(result);
  } catch (e) {
      if (e.name === 'AbortError') return;
    alert('Failed to update DLNA browsing: ' + e.message);
  }
}

function updateRemoteStatusDot(connected) {
  const dot = document.getElementById('remote-status-dot');
  if (dot) {
    dot.style.display = connected ? 'block' : 'none';
  }
}

function copyRemoteUrl() {
  const input = document.getElementById('remote-url');
  if (input) {
    input.select();
    document.execCommand('copy');
    // Show feedback
    const btn = document.querySelector('.btn-copy');
    if (btn) {
      const originalColor = btn.style.color;
      btn.style.color = 'var(--gold)';
      setTimeout(() => btn.style.color = originalColor, 500);
    }
  }
}

function openRemoteUrl() {
  const input = document.getElementById('remote-url');
  if (input && input.value) {
    window.open(input.value, '_blank');
  }
}

async function savePassword() {
  const password = document.getElementById('new-password').value;
  if (!password || password.length < 8) {
    alert('Password must be at least 8 characters');
    return;
  }

  try {
    const result = await api('/security/setup-password', {
      method: 'POST',
      body: JSON.stringify({ password })
    });

    if (result && result.success) {
      // A password change keeps the API key, so purge here as well.
      await purgeCoverCaches();
      // When enabling security for the first time the existing API key is
      // absent. Use the newly configured password for key issuance.
      const keyResp = await fetch('/api/security/login', {
        method: 'POST',
        headers: { 'X-Password': password }
      });
      if (keyResp.ok) {
        const keyData = await keyResp.json();
        if (keyData.success && keyData.api_key) {
          storeApiKey(keyData.api_key);
          adoptStreamTicket(keyData);
        }
      }
      alert(result.message);
      // Refresh the view
      state.view = 'remote';
      await render();
    } else {
      alert(result.message);
    }
  } catch (e) {
      if (e.name === 'AbortError') return;
    alert('Failed to save password: ' + e.message);
  }
}

async function generateNewApiKey() {
  try {
    const result = await api('/security/generate-api-key', { method: 'POST' });
    if (result && result.success && result.api_key) {
      storeApiKey(result.api_key);
      adoptStreamTicket(result);
      // Show the key in an alert (one-time view)
      alert('Your new API Key:\n\n' + result.api_key + '\n\nSave this now! It will not be shown again.');
      // Update the display
      const input = document.getElementById('api-key-display');
      if (input) input.value = '••••••••••••••••';
    }
  } catch (e) {
      if (e.name === 'AbortError') return;
    alert('Failed to generate API key: ' + e.message);
  }
}

// Auto-refresh remote status in background
setInterval(async () => {
  if (state.view !== 'remote') {
    try {
      const status = await api('/remote/status');
      updateRemoteStatusDot(status.status === 'connected');
    } catch (e) {
        if (e.name === 'AbortError') return;}
  }
}, 10000);

// ─── History View ─────────────────────────────────────────────────────────────
