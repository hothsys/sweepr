// ── Orphans tab ──

// Canonical order for category display
const ORPHAN_CATEGORY_ORDER = [
  'Caches', 'Application Support', 'Containers', 'Preferences',
  'Preferences (ByHost)', 'Saved Application State', 'Group Containers',
  'HTTP Storages', 'Accounts', 'System Launch Agents',
  'System Launch Daemons', 'Privileged Helpers',
  'Logs', 'Launch Agents', 'Spotlight',
];

let orphanCategories = []; // derived from orphanGroups, grouped by category

function _buildOrphanCategories() {
  // Flatten all files from vendor groups, regroup by category
  const byCategory = {};
  for (const group of orphanGroups) {
    for (const app of group.apps) {
      for (const file of app.files) {
        const cat = file.category || 'Other';
        if (!byCategory[cat]) byCategory[cat] = { category: cat, files: [], total_size: 0 };
        byCategory[cat].files.push({ ...file, app_name: app.app_name, vendor: group.vendor });
        byCategory[cat].total_size += file.size;
      }
    }
  }
  // Sort categories by canonical order, then alphabetically for any extras
  const cats = Object.values(byCategory);
  cats.sort((a, b) => {
    const ai = ORPHAN_CATEGORY_ORDER.indexOf(a.category);
    const bi = ORPHAN_CATEGORY_ORDER.indexOf(b.category);
    if (ai === -1 && bi === -1) return a.category.localeCompare(b.category);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
  orphanCategories = cats;
}

async function fetchOrphans() {
  const gen = ++fetchGeneration;
  appListEl.innerHTML = '<div class="loading-files"><span class="spinner"></span> Scanning for orphaned files...</div>';
  try {
    const data = await fetchJson('/api/orphans');
    orphanGroups = data.groups || [];
  } catch (e) {
    if (gen === fetchGeneration && currentTab === 'orphans') {
      appListEl.innerHTML = `<div class="loading-files">Orphan scan failed: ${escHtml(e.message)}</div>`;
    }
    return;
  }
  _buildOrphanCategories();
  if (gen === fetchGeneration && currentTab === 'orphans') renderOrphanList();
}

function getFilteredOrphans() {
  if (!orphanCategories || !orphanCategories.length) return [];
  const q = searchBox.value.toLowerCase().trim();
  if (!q) return orphanCategories;
  return orphanCategories.map(cat => ({
    ...cat,
    files: cat.files.filter(f =>
      f.app_name.toLowerCase().includes(q) ||
      (f.bundle_id || '').toLowerCase().includes(q) ||
      (f.vendor || '').toLowerCase().includes(q) ||
      f.path.toLowerCase().includes(q)
    ),
  })).filter(cat => cat.files.length > 0).map(cat => ({
    ...cat,
    total_size: cat.files.reduce((s, f) => s + f.size, 0),
  }));
}

function renderOrphanList() {
  const filtered = getFilteredOrphans();
  let html = '';
  const totalSize = filtered.reduce((s, c) => s + c.total_size, 0);
  const totalFiles = filtered.reduce((s, c) => s + c.files.length, 0);
  for (const cat of filtered) {
    const sel = selectedOrphan && selectedOrphan.category === cat.category ? ' selected' : '';
    html += `<div class="orphan-group${sel}" data-id="${escHtml(cat.category)}" onclick="selectOrphan(this.dataset.id)">
      <div class="orphan-group-header">
        <div>
          <div class="orphan-group-name">${escHtml(cat.category)}</div>
          <div class="orphan-group-meta">${cat.files.length} file${cat.files.length !== 1 ? 's' : ''}</div>
        </div>
        <div class="app-size">${formatBytes(cat.total_size)}</div>
      </div>
    </div>`;
  }
  document.getElementById('tabCount').textContent = filtered.length + ' categor' + (filtered.length !== 1 ? 'ies' : 'y') + ' · ' + totalFiles + ' files · ' + formatBytes(totalSize) + ' reclaimable';
  appListEl.innerHTML = html;
}

function selectOrphan(category) {
  const filtered = getFilteredOrphans();
  selectedOrphan = filtered.find(c => c.category === category);
  if (!selectedOrphan) return;
  renderOrphanList();
  renderOrphanDetail();
}

function renderOrphanDetail() {
  const cat = selectedOrphan;
  if (!cat) return;

  // Group files within this category by app_name
  const byApp = {};
  for (const f of cat.files) {
    const key = f.app_name || f.bundle_id || f.path;
    if (!byApp[key]) byApp[key] = { app_name: key, vendor: f.vendor, files: [], total_size: 0 };
    byApp[key].files.push(f);
    byApp[key].total_size += f.size;
  }
  const apps = Object.values(byApp).sort((a, b) => b.total_size - a.total_size);

  let appsHtml = '';
  let fileIdx = 0;
  for (const app of apps) {
    const sortedFiles = [...app.files].sort((a, b) => b.size - a.size);
    appsHtml += `<div class="section-title" style="margin-top:16px">
      <span>${escHtml(app.app_name)}</span>
      <span style="font-weight:400;font-size:15px;color:var(--text-secondary)">${formatBytes(app.total_size)}</span>
    </div>`;
    appsHtml += '<div class="files-list">';
    for (const f of sortedFiles) {
      appsHtml += `<div class="file-item">
        <input type="checkbox" checked data-idx="${fileIdx}" class="orphan-cb">
        <span class="file-category">${escHtml(f.vendor || '')}</span>
        <span class="file-path">${escHtml(shortenPath(f.path))}</span>
        <span class="file-size">${formatBytes(f.size)}</span>
      </div>`;
      fileIdx++;
    }
    appsHtml += '</div>';
  }

  detailPanel.innerHTML = `
    <div class="detail-header">
      <div class="brew-icon" style="width:64px;height:64px;border-radius:14px;font-size:30px;margin-right:16px">&#128193;</div>
      <div>
        <div class="detail-title">${escHtml(cat.category)}</div>
        <div class="detail-version">${cat.files.length} orphaned file${cat.files.length !== 1 ? 's' : ''} — no longer associated with any installed app</div>
        <div class="detail-size">Total size: ${formatBytes(cat.total_size)}</div>
      </div>
    </div>
    ${appsHtml}
    ${renderRiskBanner({ level: 'low', reason: 'Named for apps that are no longer installed. Matching is by name, so review the list; everything goes to the Trash and can be restored' })}
    <div class="action-bar">
      <div class="total-size">Selected: <span>${formatBytes(cat.total_size)}</span></div>
      <button class="btn-cleanup" onclick="confirmOrphanCleanup()">Clean Up</button>
    </div>
  `;

  const allFiles = _getCategoryFiles();
  detailPanel.querySelectorAll('.orphan-cb').forEach(cb => {
    cb.addEventListener('change', () => {
      let total = 0;
      detailPanel.querySelectorAll('.orphan-cb').forEach(c => {
        if (c.checked) total += allFiles[parseInt(c.dataset.idx)].size;
      });
      const el = detailPanel.querySelector('.total-size span');
      if (el) el.textContent = formatBytes(total);
    });
  });
}

function _getCategoryFiles() {
  const cat = selectedOrphan;
  if (!cat) return [];
  const byApp = {};
  for (const f of cat.files) {
    const key = f.app_name || f.bundle_id || f.path;
    if (!byApp[key]) byApp[key] = { total_size: 0, files: [] };
    byApp[key].files.push(f);
    byApp[key].total_size += f.size;
  }
  return Object.values(byApp)
    .sort((a, b) => b.total_size - a.total_size)
    .flatMap(a => [...a.files].sort((x, y) => y.size - x.size));
}

function confirmOrphanCleanup() {
  if (!selectedOrphan) return;
  const paths = getSelectedOrphanPaths();
  openModal(`
      <h3>Clean up orphaned files?</h3>
      <p>This will move ${paths.length} orphaned file${paths.length !== 1 ? 's' : ''} from <strong>${escHtml(selectedOrphan.category)}</strong> to the Trash.</p>
      <div class="modal-actions">
        <button class="btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn-confirm" onclick="doOrphanCleanup()">Move to Trash</button>
      </div>
  `);
}

function getSelectedOrphanPaths() {
  const allFiles = _getCategoryFiles();
  const paths = [];
  detailPanel.querySelectorAll('.orphan-cb').forEach(cb => {
    if (cb.checked) {
      const idx = parseInt(cb.dataset.idx);
      paths.push(allFiles[idx].path);
    }
  });
  return paths;
}

async function doOrphanCleanup() {
  document.querySelector('.modal-overlay')?.remove();
  if (!selectedOrphan) return;

  const btn = detailPanel.querySelector('.btn-cleanup');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Cleaning...'; }

  const paths = getSelectedOrphanPaths();
  try {
    const result = await postJson('/api/orphans/trash', { paths });
    if (result.success && result.success.length > 0) {
      const failedNote = result.failed && result.failed.length ? `, ${result.failed.length} failed` : '';
      showToast(`Cleaned up ${result.success.length} file(s)${failedNote}`, result.failed && result.failed.length ? 'error' : 'success');
      selectedOrphan = null;
      if (currentTab === 'orphans') detailPanel.innerHTML = '<div class="detail-empty">Select a category to view files</div>';
      await fetchOrphans();
    } else if (result.failed && result.failed.length > 0) {
      showToast(`Failed: ${result.failed[0].error}`, 'error');
      if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Clean Up'; }
    }
  } catch (e) {
    showToast(`Cleanup failed: ${e.message}`, 'error');
    if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Clean Up'; }
  }
}
