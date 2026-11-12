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
        <div class="app-name">${escHtml(pkg.name)}${pkg.type === 'cask' ? '<span class="brew-type-badge cask">cask</span>' : ''}${pkg.installed_on_request === false ? '<span class="dep-badge">dependency</span>' : ''}${pkg.service ? '<span class="dep-badge">service</span>' : ''}${pkg.app_missing ? '<span class="system-badge" style="background:var(--warning)">app missing</span>' : ''}</div>
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
          ${pkg.tap ? '<br>From tap: ' + escHtml(pkg.tap) : ''}
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

// Taps that ship with Homebrew (mirrors CORE_TAPS in lib/brew_scanner.py)
const CORE_TAPS = ['homebrew/core', 'homebrew/cask', 'homebrew/services', 'homebrew/bundle',
                   'homebrew/cask-fonts', 'homebrew/cask-versions'];

// Offer to remove the tap when it's third-party and nothing else installed uses it:
// a leftover tap keeps updating, and its packages can come back
function tapToRemove(pkg) {
  if (!pkg.tap || CORE_TAPS.includes(pkg.tap.toLowerCase())) return null;
  const others = brewPackages.filter(p => p !== pkg && p.tap && p.tap.toLowerCase() === pkg.tap.toLowerCase());
  return others.length ? null : pkg.tap;
}

// Options shared by the Homebrew tab and the Apps tab's "Uninstall with Homebrew"
function brewUninstallOptions(pkg) {
  const tap = tapToRemove(pkg);
  let html = '';
  if (pkg.type === 'cask' && pkg.has_zap !== false) {
    html += `<label class="modal-option"><input type="checkbox" id="brewZap" checked>
      Also remove its settings and data (Homebrew's cleanup list for this app; most items go to the Trash)</label>`;
  }
  if (tap) {
    html += `<label class="modal-option"><input type="checkbox" id="brewUntap" checked>
      Also remove the <strong>${escHtml(tap)}</strong> tap (nothing else installed uses it), so it stops updating and can't bring the package back</label>`;
  }
  return html;
}

function readBrewUninstallOptions() {
  return {
    zap: !!document.getElementById('brewZap')?.checked,
    untap: !!document.getElementById('brewUntap')?.checked,
  };
}

function confirmBrewUninstall() {
  if (!selectedBrew) return;
  openModal(`
      <h3>Uninstall ${escHtml(selectedBrew.name)}?</h3>
      <p>This will run <strong>brew uninstall ${escHtml(selectedBrew.name)}</strong> and then <strong>brew autoremove</strong>, which removes every dependency on this Mac that nothing needs any more (not only this package's). This cannot be undone.</p>
      ${brewUninstallOptions(selectedBrew)}
      <div class="modal-actions">
        <button class="btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn-confirm" onclick="doBrewUninstall()">Uninstall</button>
      </div>
  `);
}

// Runs the uninstall and reports everything that happened; shared with the Apps tab
async function runBrewUninstall(name, type, options) {
  const result = await postJson(`/api/brew/${encodeURIComponent(name)}/uninstall`, { type, ...options });
  const notes = [];
  if (result.autoremoved && result.autoremoved.length) notes.push(`autoremove also removed ${result.autoremoved.join(', ')}`);
  if (result.untapped) notes.push(`removed the ${result.tap} tap`);
  else if (options.untap && result.untap_note) notes.push(`kept the tap: ${result.untap_note}`);
  showToast(`Uninstalled ${name}${notes.length ? '; ' + notes.join('; ') : ''}`, 'success');
  if (result.leftovers && result.leftovers.length) showBrewLeftovers(name, result.leftovers);
  return result;
}

// Data the package left in the home folder (~/.anylinuxfs): offer it for the Trash
function showBrewLeftovers(name, leftovers) {
  const rows = leftovers.map((l, i) => `<div class="file-item">
      <input type="checkbox" checked data-idx="${i}" class="leftover-cb">
      <span class="file-path">${escHtml(shortenPath(l.path))}${trashHint(l.path)}</span>
      <span class="file-size">${formatBytes(l.size)}</span>
    </div>`).join('');
  _brewLeftovers = leftovers;
  openModal(`
      <h3>${escHtml(name)} left data behind</h3>
      <p>Homebrew doesn't remove data a program kept in your home folder. Move it to the Trash too?</p>
      <div class="files-list">${rows}</div>
      ${trashNotes(leftovers.map(l => l.path))}
      <div class="modal-actions">
        <button class="btn-cancel" onclick="this.closest('.modal-overlay').remove()">Keep</button>
        <button class="btn-confirm" onclick="trashBrewLeftovers()">Move to Trash</button>
      </div>
  `);
}

let _brewLeftovers = [];
async function trashBrewLeftovers() {
  const overlay = document.querySelector('.modal-overlay');
  const paths = [...overlay.querySelectorAll('.leftover-cb')].filter(cb => cb.checked)
    .map(cb => _brewLeftovers[parseInt(cb.dataset.idx)].path);
  overlay.remove();
  if (!paths.length) return;
  try {
    const result = await postJson('/api/brew/leftovers/trash', { paths });
    if (result.failed && result.failed.length) showToast(`Failed: ${result.failed[0].error}`, 'error');
    else showToast(`Moved ${result.success.length} item(s) to the Trash`, 'success');
  } catch (e) {
    showToast(`Couldn't move leftovers: ${e.message}`, 'error');
  }
}

async function doBrewUninstall() {
  const options = readBrewUninstallOptions();
  document.querySelector('.modal-overlay')?.remove();
  const pkg = selectedBrew;
  if (!pkg) return;

  const btn = detailPanel.querySelector('.btn-uninstall');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Uninstalling...'; }

  try {
    await runBrewUninstall(pkg.name, pkg.type, options);
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
