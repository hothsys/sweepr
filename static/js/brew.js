// ── Homebrew tab ──

let _brewPromise = null;
let _brew_detailed_local = false;
let _brewPollTimer = null;
let _brewError = null;  // {message, command} when brew's detailed scan failed

async function fetchBrew(refresh) {
  if (refresh) _brew_detailed_local = false;
  if (!refresh && _brewPromise) {
    await _brewPromise;
    return;
  }
  const gen = ++fetchGeneration;
  const url = refresh ? '/api/brew/refresh' : '/api/brew';
  try {
    const data = await fetchJson(url);
    brewPackages = data.packages || [];
  } catch (e) {
    showToast(`Could not load Homebrew packages: ${e.message}`, 'error');
    return;
  }
  if (gen === fetchGeneration && currentTab === 'brew') renderBrewList();
  pollBrewDetails();
}

let _brewPollGen = 0;

// Starts a fresh polling chain; an older chain (even mid-request) stops itself.
function pollBrewDetails() {
  clearTimeout(_brewPollTimer);
  _pollBrewTick(++_brewPollGen, 0);
}

async function _pollBrewTick(token, failures) {
  let status, data;
  try {
    status = await fetchJson('/api/brew/status');
    if (status.detailed) data = await fetchJson('/api/brew');
  } catch (e) {
    if (token !== _brewPollGen) return;
    // Retry with backoff rather than leaving the loading banner up forever
    if (failures < 5) {
      _brewPollTimer = setTimeout(() => _pollBrewTick(token, failures + 1), 1500 * 2 ** failures);
    } else {
      _brew_detailed_local = true;
      _brewError = { message: `Lost contact with Sweepr: ${e.message}`, command: null };
      setLoading('brew', false);
      if (currentTab === 'brew') renderBrewList();
    }
    return;
  }
  if (token !== _brewPollGen) return;
  if (!status.detailed) {
    _brewPollTimer = setTimeout(() => _pollBrewTick(token, 0), 1500);
    return;
  }
  brewPackages = data.packages || [];
  _brew_detailed_local = true;
  _brewError = status.error ? { message: status.error, command: status.fix_command } : null;
  setLoading('brew', false);
  if (selectedBrew) selectedBrew = brewPackages.find(p => brewKey(p) === brewKey(selectedBrew)) || null;
  if (currentTab === 'brew') {
    renderBrewList();
    if (selectedBrew) renderBrewDetail();
  }
}

function getFilteredBrew() {
  let filtered = [...brewPackages];
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
  } else if (sort === 'updated' || sort === 'last_used') {
    filtered.sort((a, b) => (b.installed_at || '').localeCompare(a.installed_at || ''));
  } else {
    filtered.sort((a, b) => a.name.localeCompare(b.name));
  }
  return filtered;
}

function renderBrewList() {
  const filtered = getFilteredBrew();
  let html = '';
  if (!_brew_detailed_local) {
    html += `<div class="loading-files" style="border-bottom:1px solid var(--border-primary)"><span class="spinner"></span> Loading Homebrew packages...</div>`;
  } else if (_brewError) {
    html += renderBrewErrorBanner(_brewError);
  }
  for (const pkg of filtered) {
    const sel = selectedBrew && brewKey(selectedBrew) === brewKey(pkg) ? ' selected' : '';
    const typeIcon = pkg.type === 'cask' ? '&#9671;' : '&#9670;';
    const meta = pkg.version + (pkg.installed_at ? ' · ' + formatDate(pkg.installed_at) : '');
    html += `<div class="app-item${sel}" data-id="${escHtml(brewKey(pkg))}" onclick="selectBrew(this.dataset.id)">
      <div class="brew-icon">${typeIcon}</div>
      <div class="app-info">
        <div class="app-name">${escHtml(pkg.name)}${pkg.type === 'cask' ? '<span class="brew-type-badge cask">cask</span>' : ''}${pkg.installed_on_request === false ? '<span class="dep-badge">dependency</span>' : ''}${pkg.service ? '<span class="dep-badge">service</span>' : ''}</div>
        <div class="app-meta">${escHtml(meta)}</div>
      </div>
      <div class="app-size">${pkg.size ? formatBytes(pkg.size) : ''}</div>
    </div>`;
  }
  appListEl.innerHTML = html;
  const formulaCount = filtered.filter(p => p.type === 'formula').length;
  const caskCount = filtered.filter(p => p.type === 'cask').length;
  document.getElementById('tabCount').textContent = formulaCount + ' formula' + (formulaCount !== 1 ? 'e' : '') + ', ' + caskCount + ' cask' + (caskCount !== 1 ? 's' : '');
}

function renderBrewErrorBanner(err) {
  const fix = err.command
    ? `<div class="brew-fix">Run this in Terminal, then click Check again:
        <div class="brew-fix-row">
          <code>${escHtml(err.command)}</code>
          <button class="btn-refresh" onclick="copyBrewFix()">Copy</button>
        </div>
      </div>`
    : `<div class="brew-fix">Homebrew reported: <code>${escHtml(err.message.replace(/\s+/g, ' '))}</code></div>`;
  return `<div class="risk-banner high brew-error-banner">
    <span class="risk-icon">&#9888;</span>
    <div class="risk-text">
      <strong>Homebrew needs attention</strong>
      <span>Some package details couldn't be loaded: descriptions, sizes and update status are missing. Install reasons and dependencies are still read from Homebrew's own records.</span>
      ${fix}
      <button class="btn-refresh brew-recheck" onclick="fetchBrew(true)">Check again</button>
    </div>
  </div>`;
}

async function copyBrewFix() {
  if (!_brewError?.command) return;
  try {
    await navigator.clipboard.writeText(_brewError.command);
    showToast('Command copied', 'success');
  } catch {
    showToast('Could not copy; select the command and copy it manually', 'error');
  }
}

// A formula and a cask can share a name, so identify packages by both
function brewKey(pkg) {
  return pkg.type + ':' + pkg.name;
}

function selectBrew(key) {
  selectedBrew = brewPackages.find(p => brewKey(p) === key);
  if (!selectedBrew) return;
  renderBrewList();
  renderBrewDetail();
}

function renderBrewDetail() {
  const pkg = selectedBrew;
  if (!pkg) return;
  const typeIcon = pkg.type === 'cask' ? '&#9671;' : '&#9670;';

  detailPanel.innerHTML = `
    <div class="detail-header">
      <div class="brew-icon" style="width:64px;height:64px;border-radius:14px;font-size: 30px;margin-right:16px">${typeIcon}</div>
      <div>
        <div class="detail-title">${escHtml(pkg.name)}${pkg.type === 'cask' ? ' <span class="brew-type-badge cask">cask</span>' : ''}</div>
        <div class="detail-version">${escHtml(pkg.version)}${pkg.outdated ? ' · <span style="color:var(--warning)">outdated</span>' : ''}</div>
        ${pkg.description ? `<div class="detail-desc">${escHtml(pkg.description)}</div>` : ''}
        <div class="detail-dates">
          ${pkg.installed_at ? 'Installed: ' + formatFullDate(pkg.installed_at) : ''}
          ${pkg.size ? '<br>Size: ' + formatBytes(pkg.size) : ''}
          ${pkg.dependencies ? '<br>Dependencies: ' + pkg.dependencies : ''}
          ${pkg.pinned ? '<br>Pinned (will not be auto-removed)' : ''}
          ${pkg.installed_on_request === false ? '<br>Installed as a dependency' : ''}
          ${pkg.service ? '<br>Registered as a background service' : ''}
          ${pkg.used_by && pkg.used_by.length ? '<br>Required by: ' + escHtml(pkg.used_by.join(', ')) : ''}
        </div>
      </div>
    </div>
    ${renderRiskBanner(getBrewRisk(pkg))}
    <div class="action-bar">
      <div class="total-size">Uninstall via <span>brew uninstall ${escHtml(pkg.name)}</span></div>
      <button class="btn-uninstall" onclick="confirmBrewUninstall()">Uninstall</button>
    </div>
  `;
}

function confirmBrewUninstall() {
  if (!selectedBrew) return;
  openModal(`
      <h3>Uninstall ${escHtml(selectedBrew.name)}?</h3>
      <p>This will run <strong>brew uninstall ${escHtml(selectedBrew.name)}</strong> and then <strong>brew autoremove</strong>, which removes every dependency on this Mac that nothing needs any more (not only this package's). This cannot be undone.</p>
      <div class="modal-actions">
        <button class="btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn-confirm" onclick="doBrewUninstall()">Uninstall</button>
      </div>
  `);
}

async function doBrewUninstall() {
  document.querySelector('.modal-overlay')?.remove();
  const pkg = selectedBrew;
  if (!pkg) return;

  const btn = detailPanel.querySelector('.btn-uninstall');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Uninstalling...'; }

  try {
    const result = await postJson(`/api/brew/${encodeURIComponent(pkg.name)}/uninstall`, { type: pkg.type });
    const extra = result.autoremoved && result.autoremoved.length
      ? `; autoremove also removed ${result.autoremoved.join(', ')}` : '';
    showToast(`Uninstalled ${pkg.name}${extra}`, 'success');
    if (selectedBrew === pkg) {
      selectedBrew = null;
      if (currentTab === 'brew') detailPanel.innerHTML = '<div class="detail-empty">Select a package to view details</div>';
    }
    await fetchBrew(true);
  } catch (e) {
    showToast(`Uninstall failed: ${e.message}`, 'error');
    if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Uninstall'; }
  }
}
