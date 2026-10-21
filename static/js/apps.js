// ── Applications tab ──

async function fetchApps(refresh) {
  const gen = ++fetchGeneration;
  const url = refresh ? '/api/apps/refresh' : '/api/apps';
  let data;
  try {
    data = await fetchJson(url);
  } catch (e) {
    showToast(`Could not load apps: ${e.message}`, 'error');
    return;
  }
  apps = data.apps || [];
  indexApps();
  // Keep the selection pointing at the fresh object so size updates reach it
  if (selectedApp) selectedApp = apps.find(a => a.path === selectedApp.path) || null;
  if (gen === fetchGeneration && currentTab === 'apps') renderAppList();
  pollSizes();
  preloadOtherTabs();
}

// Bundle id -> how many apps share it, rebuilt whenever `apps` changes.
// appIdentifier() used to filter the whole list on every call.
let _bundleIdCounts = new Map();
function indexApps() {
  _bundleIdCounts = new Map();
  for (const a of apps) {
    if (a.bundle_id) _bundleIdCounts.set(a.bundle_id, (_bundleIdCounts.get(a.bundle_id) || 0) + 1);
  }
}

function getFilteredApps() {
  let filtered = [...apps];
  const q = searchBox.value.toLowerCase().trim();
  if (q) {
    filtered = filtered.filter(a =>
      a.name.toLowerCase().includes(q) ||
      a.bundle_id.toLowerCase().includes(q)
    );
  }
  if (hideSystem.checked) {
    filtered = filtered.filter(a => !a.is_system);
  }
  const sort = sortSelect.value;
  if (sort === 'size') {
    filtered.sort((a, b) => (b.size || 0) - (a.size || 0));
  } else if (sort === 'updated') {
    filtered.sort((a, b) => (b.last_updated || '').localeCompare(a.last_updated || ''));
  } else if (sort === 'last_used') {
    filtered.sort((a, b) => {
      if (!a.last_used && !b.last_used) return a.name.localeCompare(b.name);
      if (!a.last_used) return 1;
      if (!b.last_used) return -1;
      return a.last_used.localeCompare(b.last_used);
    });
  } else {
    filtered.sort((a, b) => a.name.localeCompare(b.name));
  }
  return filtered;
}

function appIdentifier(app) {
  if (app.bundle_id && _bundleIdCounts.get(app.bundle_id) > 1) return app.path;
  return app.bundle_id || app.path;
}

function renderAppList() {
  const filtered = getFilteredApps();
  let html = '';
  for (const app of filtered) {
    const id = appIdentifier(app);
    const sel = selectedApp && appIdentifier(selectedApp) === id ? ' selected' : '';
    const sys = app.is_system ? ' system' : '';
    const iconUrl = `/api/apps/${encodeURIComponent(id)}/icon`;
    const meta = app.version + (app.last_used ? ' · Used ' + formatDate(app.last_used) : app.last_updated ? ' · Released ' + formatDate(app.last_updated) : '');
    html += `<div class="app-item${sel}${sys}" data-id="${escHtml(id)}" onclick="selectApp(this.dataset.id)">
      <div class="app-icon">
        ${app.webloc ? '<div class="fallback">&#127760;</div>' : app.leftover ? '<div class="fallback">&#128448;</div>' : app.icon_file === '' ? '<div class="fallback">&#9634;</div>' : `<img src="${iconUrl}" onerror="this.parentElement.innerHTML='<div class=fallback>&#9634;</div>'">`}
      </div>
      <div class="app-info">
        <div class="app-name">${escHtml(app.name)}${app.is_system ? '<span class="system-badge">System</span>' : ''}${app.webloc ? '<span class="brew-type-badge formula" style="background:var(--text-secondary)">webloc</span>' : ''}${app.leftover ? '<span class="system-badge" style="background:var(--danger)">leftover</span>' : ''}</div>
        <div class="app-meta">${escHtml(meta)}</div>
      </div>
      <div class="app-size">${app.size ? formatBytes(app.size) : '—'}</div>
    </div>`;
  }
  appListEl.innerHTML = html;
  document.getElementById('tabCount').textContent = filtered.length + ' app' + (filtered.length !== 1 ? 's' : '');
}

async function selectApp(id) {
  const app = apps.find(a => appIdentifier(a) === id);
  if (!app) return;
  selectedApp = app;
  renderAppList();
  if (app.webloc || app.leftover) {
    associatedFiles = [];
    renderDetail();
    return;
  }
  renderDetailLoading();
  isLoadingFiles = true;
  let files = [];
  try {
    const [filesData, runningData] = await Promise.all([
      fetchJson(`/api/apps/${encodeURIComponent(id)}/files`),
      fetchJson(`/api/apps/${encodeURIComponent(id)}/running`),
    ]);
    files = filesData.files || [];
    app._running = runningData.running;
  } catch (e) {
    if (selectedApp === app) showToast(`Could not load associated files: ${e.message}`, 'error');
  }
  // Another app was clicked, or the tab changed, while this was loading
  if (selectedApp !== app || currentTab !== 'apps') return;
  isLoadingFiles = false;
  associatedFiles = files;
  renderDetail();
}

function renderDetailLoading() {
  const app = selectedApp;
  const id = appIdentifier(app);
  const iconUrl = `/api/apps/${encodeURIComponent(id)}/icon`;
  detailPanel.innerHTML = `
    <div class="detail-header">
      <div class="detail-icon">
        ${app.icon_file === '' ? '<div class="fallback">&#9634;</div>' : `<img src="${iconUrl}" onerror="this.parentElement.innerHTML='<div class=fallback>&#9634;</div>'">`}
      </div>
      <div>
        <div class="detail-title">${escHtml(app.name)}</div>
        <div class="detail-version">${escHtml(app.version)}${app.category ? ' · ' + escHtml(app.category) : ''}</div>
        <div class="detail-path">${escHtml(app.path)}</div>
        <div class="detail-size">${app.size ? formatBytes(app.size) : 'Calculating...'}</div>
        <div class="detail-dates">${formatDetailDates(app)}</div>
      </div>
    </div>
    <div class="section-title">Associated Files</div>
    <div class="loading-files"><span class="spinner"></span> Scanning for associated files...</div>
  `;
}

function renderDetail() {
  const app = selectedApp;
  if (!app) {
    detailPanel.innerHTML = '<div class="detail-empty">Select an app to view details</div>';
    return;
  }
  const id = appIdentifier(app);
  const iconUrl = `/api/apps/${encodeURIComponent(id)}/icon`;
  const runBadge = app._running ? '<span class="running-badge">Running</span>' : '';

  if (app.webloc) {
    detailPanel.innerHTML = `
      <div class="detail-header">
        <div class="detail-icon"><div class="fallback">&#127760;</div></div>
        <div>
          <div class="detail-title">${escHtml(app.name)}</div>
          <div class="detail-version">Web Shortcut</div>
          <div class="detail-desc">${app.webloc_url ? (/^https?:\/\//i.test(app.webloc_url)
            ? `<a href="${escHtml(app.webloc_url)}" target="_blank" rel="noopener" style="color:var(--accent)">${escHtml(app.webloc_url)}</a>`
            : escHtml(app.webloc_url)) : ''}</div>
        </div>
      </div>
      <div class="section-title">File</div>
      <div class="files-list">
        <div class="file-item">
          <span class="file-category">Web Shortcut</span>
          <span class="file-path">${escHtml(shortenPath(app.path))}</span>
          <span class="file-size">${formatBytes(app.size)}</span>
          <button class="btn-refresh" style="margin-left:10px;padding:4px 10px;font-size: 14px" onclick="revealInFinder()">Show in Finder</button>
        </div>
      </div>
      ${renderRiskBanner(getAppRisk(app))}
      <div class="action-bar">
        <div class="total-size"></div>
        <button class="btn-trash" onclick="confirmTrash()">Remove</button>
      </div>
    `;
    return;
  }

  if (app.leftover) {
    detailPanel.innerHTML = `
      <div class="detail-header">
        <div class="detail-icon"><div class="fallback">&#128448;</div></div>
        <div>
          <div class="detail-title">${escHtml(app.name)}</div>
          <div class="detail-version">Leftover folder — app was uninstalled but this folder was left behind</div>
          <div class="detail-size">${app.size ? formatBytes(app.size) : 'Calculating...'}</div>
        </div>
      </div>
      <div class="section-title">Location</div>
      <div class="files-list">
        <div class="file-item">
          <span class="file-category">Leftover</span>
          <span class="file-path">${escHtml(app.path)}</span>
          <span class="file-size">${app.size ? formatBytes(app.size) : '—'}</span>
          <button class="btn-refresh" style="margin-left:10px;padding:4px 10px;font-size:14px" onclick="revealInFinder()">Show in Finder</button>
        </div>
      </div>
      ${renderRiskBanner(getAppRisk(app))}
      <div class="action-bar">
        <div class="total-size">Total: ${app.size ? formatBytes(app.size) : '—'}</div>
        <button class="btn-trash" onclick="confirmTrash()">Remove</button>
      </div>
    `;
    return;
  }

  let filesHtml = '';
  if (associatedFiles.length === 0) {
    filesHtml = '<div class="loading-files">No associated files found</div>';
  } else {
    filesHtml = '<div class="files-list">';
    for (let i = 0; i < associatedFiles.length; i++) {
      const f = associatedFiles[i];
      // Files also used by another installed copy of the app start unchecked
      const shared = f.shared_with && f.shared_with.length;
      filesHtml += `<div class="file-item">
        <input type="checkbox" ${shared ? '' : 'checked'} data-idx="${i}">
        <span class="file-category">${escHtml(f.category)}</span>
        <span class="file-path">${escHtml(shortenPath(f.path))}${shared ? ` <span class="dep-badge" title="Also used by ${escHtml(f.shared_with.join(', '))}">shared</span>` : ''}</span>
        <span class="file-size">${formatBytes(f.size)}</span>
      </div>`;
    }
    filesHtml += '</div>';
  }

  // Computed from the data, not the DOM: the panel still holds the previous app's
  // checkboxes at this point, whose indexes may not exist in associatedFiles
  const totalSelected = associatedFiles
    .filter(f => !(f.shared_with && f.shared_with.length))
    .reduce((sum, f) => sum + (f.size || 0), 0);
  const canTrash = !app.is_system && !app.is_self && !isRemoving;

  detailPanel.innerHTML = `
    <div class="detail-header">
      <div class="detail-icon">
        ${app.icon_file === '' ? '<div class="fallback">&#9634;</div>' : `<img src="${iconUrl}" onerror="this.parentElement.innerHTML='<div class=fallback>&#9634;</div>'">`}
      </div>
      <div>
        <div class="detail-title">${escHtml(app.name)} ${runBadge}</div>
        <div class="detail-version">${escHtml(app.version)}${app.category ? ' · ' + escHtml(app.category) : ''}</div>
        <div class="detail-size">${app.size ? formatBytes(app.size) : 'Calculating...'}</div>
        <div class="detail-dates">${formatDetailDates(app)}</div>
      </div>
    </div>
    <div class="section-title">Main Application</div>
    <div class="files-list">
      <div class="file-item">
        <span class="file-category">${app.path.includes('/Users/') ? 'User App' : 'System App'}</span>
        <span class="file-path">${escHtml(shortenPath(app.path))}</span>
        <span class="file-size">${app.size ? formatBytes(app.size) : '—'}</span>
        <button class="btn-refresh" style="margin-left:10px;padding:4px 10px;font-size: 14px" onclick="revealInFinder()">Show in Finder</button>
      </div>
    </div>
    <div class="section-title">
      Associated Files
      <span style="font-weight:400;font-size: 15px;color:#86868b">${associatedFiles.length} item${associatedFiles.length !== 1 ? 's' : ''}</span>
    </div>
    ${filesHtml}
    ${app.uninstaller ? `
    <div class="uninstaller-bar">
      <div class="uninstaller-info">
        <span class="uninstaller-icon">&#9881;</span>
        <div>
          <div class="uninstaller-label">Official uninstaller available</div>
          <div class="uninstaller-desc">${escHtml(app.uninstaller.name)} &middot; ${escHtml(app.uninstaller.type)}</div>
        </div>
      </div>
      <button class="btn-official-uninstaller" onclick="launchUninstaller()">Run Uninstaller</button>
    </div>` : ''}
    ${renderRiskBanner(getAppRisk(app))}
    <div class="action-bar">
      <div class="total-size">Total to remove: <span>${formatBytes(app.size + totalSelected)}</span></div>
      <button class="btn-trash" ${canTrash ? '' : 'disabled'} onclick="confirmTrash()">
        ${app.is_system ? 'System App' : app.is_self ? 'Use uninstall.sh' : 'Move to Trash'}
      </button>
    </div>
  `;

  detailPanel.querySelectorAll('.file-item input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', updateTotal);
  });
}

function calcSelectedTotal() {
  let total = 0;
  const checkboxes = detailPanel.querySelectorAll('.file-item input[type="checkbox"]');
  checkboxes.forEach(cb => {
    if (cb.checked) {
      const idx = parseInt(cb.dataset.idx);
      if (associatedFiles[idx]) total += associatedFiles[idx].size || 0;
    }
  });
  return total;
}

function updateTotal() {
  if (!selectedApp) return;
  const total = calcSelectedTotal();
  const el = detailPanel.querySelector('.total-size span');
  if (el) el.textContent = formatBytes((selectedApp.size || 0) + total);
}

function confirmTrash() {
  if (!selectedApp || selectedApp.is_system || selectedApp.is_self) return;
  const warning = selectedApp._running
    ? `<strong>${escHtml(selectedApp.name)}</strong> is currently running. Sweepr will ask it to quit first, and force it to quit if it doesn't respond within a few seconds, so save any open work in it now.<br><br>`
    : '';
  const selected = getSelectedFiles();
  const fileCount = selected.paths.length;

  openModal(`
      <h3>Move to Trash?</h3>
      <p>${warning}This will move <strong>${escHtml(selectedApp.name)}</strong>${fileCount ? ` and ${fileCount} associated file${fileCount !== 1 ? 's' : ''}` : ''} to the Trash. You can recover them from the Trash if needed.</p>
      <div class="modal-actions">
        <button class="btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn-confirm" onclick="doTrash()">Move to Trash</button>
      </div>
  `);
}

function getSelectedFiles() {
  const paths = [];
  const sizes = [];
  detailPanel.querySelectorAll('.file-item input[type="checkbox"]').forEach(cb => {
    if (cb.checked) {
      const idx = parseInt(cb.dataset.idx);
      paths.push(associatedFiles[idx].path);
      sizes.push(associatedFiles[idx].size);
    }
  });
  return { paths, sizes };
}

async function doTrash() {
  document.querySelector('.modal-overlay')?.remove();
  // Capture now: the selection can change while the request is in flight
  const app = selectedApp;
  if (!app) return;

  isRemoving = true;
  const btn = detailPanel.querySelector('.btn-trash');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> ${app._running ? 'Quitting and removing...' : 'Removing...'}`;
  }

  const id = appIdentifier(app);
  const selected = getSelectedFiles();

  try {
    const result = await postJson(`/api/apps/${encodeURIComponent(id)}/trash`,
      { include_app: true, file_paths: selected.paths });

    if (result.success && result.success.length > 0) {
      const appTrashed = result.success.includes(app.path);
      const notes = [];
      if (result.quit && result.quit.was_running) notes.push(result.quit.forced ? 'force-quit it' : 'quit it');
      if (result.login_items_removed && result.login_items_removed.length) notes.push('removed its login item');
      showToast(`Moved ${result.success.length} item(s) to Trash${notes.length ? ' (' + notes.join(', ') + ')' : ''}`, 'success');
      if (appTrashed) {
        apps = apps.filter(a => a !== app);
        indexApps();
        if (selectedApp === app) {
          selectedApp = null;
          associatedFiles = [];
          if (currentTab === 'apps') detailPanel.innerHTML = '<div class="detail-empty">Select an app to view details</div>';
        }
        if (currentTab === 'apps') renderAppList();
      } else if (selectedApp === app) {
        associatedFiles = associatedFiles.filter(f => !result.success.includes(f.path));
        if (currentTab === 'apps') renderDetail();
      }
    } else if (result.failed && result.failed.length > 0) {
      showToast(`Failed: ${result.failed[0].error}`, 'error');
    }
  } catch (e) {
    showToast(`Failed to remove app: ${e.message}`, 'error');
  }
  isRemoving = false;
  if (selectedApp === app && currentTab === 'apps') renderDetail();
}

async function revealInFinder() {
  if (!selectedApp) return;
  const id = appIdentifier(selectedApp);
  try {
    await postJson(`/api/apps/${encodeURIComponent(id)}/reveal`);
  } catch (e) {
    showToast(`Could not reveal in Finder: ${e.message}`, 'error');
  }
}

async function launchUninstaller() {
  const app = selectedApp;
  if (!app?.uninstaller) return;
  const id = appIdentifier(app);
  try {
    const result = await postJson(`/api/apps/${encodeURIComponent(id)}/launch-uninstaller`);
    if (result.launched) showToast(`Launched ${app.uninstaller.name}`, 'success');
    else showToast('Could not launch uninstaller', 'error');
  } catch (e) {
    showToast(`Could not launch uninstaller: ${e.message}`, 'error');
  }
}

// ── App-size background polling ──

let sizesPollTimer = null;
let _sizesPollGen = 0;

// Each call starts a new chain and retires any older one, including one whose
// request is still in flight (clearTimeout alone only cancels a pending timer).
function pollSizes() {
  clearTimeout(sizesPollTimer);
  _pollSizesTick(++_sizesPollGen);
}

async function _pollSizesTick(token) {
  let data;
  try {
    data = await fetchJson('/api/apps/sizes');
  } catch {
    if (token === _sizesPollGen) sizesPollTimer = setTimeout(() => _pollSizesTick(token), 3000);
    return;
  }
  if (token !== _sizesPollGen) return;
  let changed = false;
  for (const app of apps) {
    const size = data.sizes[app.path];
    if (size !== undefined && app.size !== size) {
      app.size = size;
      changed = true;
    }
  }
  if (changed && currentTab === 'apps') {
    updateSizeCells();
    if (selectedApp && !selectedApp.webloc) {
      const sizeEl = detailPanel.querySelector('.detail-size');
      if (sizeEl) sizeEl.textContent = selectedApp.size ? formatBytes(selectedApp.size) : 'Calculating...';
      updateTotal();
    }
  }
  if (!data.complete) {
    sizesPollTimer = setTimeout(() => _pollSizesTick(token), 1500);
  } else {
    setLoading('sizes', false);
  }
}

function updateSizeCells() {
  const byId = new Map(apps.map(a => [appIdentifier(a), a]));
  appListEl.querySelectorAll('.app-item').forEach(el => {
    const app = byId.get(el.dataset.id);
    if (app) {
      const sizeEl = el.querySelector('.app-size');
      if (sizeEl) sizeEl.textContent = app.size ? formatBytes(app.size) : '—';
    }
  });
}
