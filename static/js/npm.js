// ── npm tab ──

let _npmPromise = null;

async function fetchNpm(refresh) {
  if (!refresh && _npmPromise) {
    await _npmPromise;
    return;
  }
  const gen = ++fetchGeneration;
  const url = refresh ? '/api/npm/refresh' : '/api/npm';
  try {
    const data = await fetchJson(url);
    npmPackages = data.packages || [];
    if (data.error) showToast(`npm scan failed: ${data.error}`, 'error');
  } catch (e) {
    showToast(`Could not load npm packages: ${e.message}`, 'error');
    return;
  }
  if (gen === fetchGeneration && currentTab === 'npm') renderNpmList();
}

function getFilteredNpm() {
  let filtered = [...npmPackages];
  const q = searchBox.value.toLowerCase().trim();
  if (q) {
    filtered = filtered.filter(p =>
      p.name.toLowerCase().includes(q) ||
      (p.description || '').toLowerCase().includes(q)
    );
  }
  const sort = sortSelect.value;
  if (sort === 'size') {
    filtered.sort((a, b) => (b.size || 0) - (a.size || 0));
  } else {
    filtered.sort((a, b) => a.name.localeCompare(b.name));
  }
  return filtered;
}

function renderNpmList() {
  const filtered = getFilteredNpm();
  let html = '';
  for (const pkg of filtered) {
    const sel = selectedNpm && selectedNpm.name === pkg.name ? ' selected' : '';
    const meta = pkg.version + (pkg.installed_at ? ' · ' + formatDate(pkg.installed_at) : '');
    html += `<div class="app-item${sel}" data-id="${escHtml(pkg.name)}" onclick="selectNpm(this.dataset.id)">
      <div class="brew-icon">&#9729;</div>
      <div class="app-info">
        <div class="app-name">${escHtml(pkg.name)}${pkg.protected ? '<span class="system-badge">Node.js</span>' : ''}</div>
        <div class="app-meta">${escHtml(meta)}</div>
      </div>
      <div class="app-size">${pkg.size ? formatBytes(pkg.size) : ''}</div>
    </div>`;
  }
  appListEl.innerHTML = html;
  document.getElementById('tabCount').textContent = filtered.length + ' package' + (filtered.length !== 1 ? 's' : '');
}

function selectNpm(name) {
  selectedNpm = npmPackages.find(p => p.name === name);
  if (!selectedNpm) return;
  renderNpmList();
  renderNpmDetail();
}

function renderNpmDetail() {
  const pkg = selectedNpm;
  if (!pkg) return;

  detailPanel.innerHTML = `
    <div class="detail-header">
      <div class="brew-icon" style="width:64px;height:64px;border-radius:14px;font-size: 30px;margin-right:16px">&#9729;</div>
      <div>
        <div class="detail-title">${escHtml(pkg.name)}</div>
        <div class="detail-version">${escHtml(pkg.version)}</div>
        ${pkg.description ? `<div class="detail-desc">${escHtml(pkg.description)}</div>` : ''}
        <div class="detail-dates">
          ${pkg.installed_at ? 'Installed: ' + formatFullDate(pkg.installed_at) : ''}
          ${pkg.size ? '<br>Size: ' + formatBytes(pkg.size) : ''}
        </div>
      </div>
    </div>
    ${renderRiskBanner(getNpmRisk(pkg))}
    <div class="action-bar">
      <div class="total-size">Uninstall via <span>npm uninstall -g ${escHtml(pkg.name)}</span></div>
      <button class="btn-uninstall" ${pkg.protected ? 'disabled' : 'onclick="confirmNpmUninstall()"'}>${pkg.protected ? 'Part of Node.js' : 'Uninstall'}</button>
    </div>
  `;
}

function confirmNpmUninstall() {
  if (!selectedNpm || selectedNpm.protected) return;
  openModal(`
      <h3>Uninstall ${escHtml(selectedNpm.name)}?</h3>
      <p>This will run <strong>npm uninstall -g ${escHtml(selectedNpm.name)}</strong>. This cannot be undone.</p>
      <div class="modal-actions">
        <button class="btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn-confirm" onclick="doNpmUninstall()">Uninstall</button>
      </div>
  `);
}

async function doNpmUninstall() {
  document.querySelector('.modal-overlay')?.remove();
  const pkg = selectedNpm;
  if (!pkg) return;

  const btn = detailPanel.querySelector('.btn-uninstall');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Uninstalling...'; }

  try {
    await postJson(`/api/npm/${encodeURIComponent(pkg.name)}/uninstall`);
    showToast(`Uninstalled ${pkg.name}`, 'success');
    if (selectedNpm === pkg) {
      selectedNpm = null;
      if (currentTab === 'npm') detailPanel.innerHTML = '<div class="detail-empty">Select a package to view details</div>';
    }
    await fetchNpm(true);
  } catch (e) {
    showToast(`Uninstall failed: ${e.message}`, 'error');
    if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Uninstall'; }
  }
}
