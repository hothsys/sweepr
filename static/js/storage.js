// ── Storage analyzer (separate full-screen mode, not a cleaner tab) ──
//
// Storage is read-only analysis, so it lives in its own overlay reached from the
// header "Analyze Storage" button, with its own controls and a single-pane
// explorer. It does not share the tab list/detail layout or header controls.

let storageRoots = [];
let storagePath = null;          // null = showing the roots (locations) screen
let storageEntries = [];
let storageSizes = {};           // child path -> bytes, filled in live by du
let storageBreadcrumb = [];
let storageComplete = false;
let selectedStorage = null;
let storageOpen = false;         // is the overlay showing
let storageGen = 0;              // own generation counter to discard stale async work
let _storagePollTimer = null;
let _storageViewWired = false;
let _storageLastSort = 0;         // when the list was last fully re-sorted
const STORAGE_RESORT_MS = 2500;   // between progress ticks, patch sizes in place instead

let storageViewEl, storageItemsEl, storageCrumbEl, storageSearchEl, storageSortEl;

function _initStorageView() {
  if (_storageViewWired) return;
  _storageViewWired = true;
  storageViewEl = document.getElementById('storageView');
  storageItemsEl = document.getElementById('storageItems');
  storageCrumbEl = document.getElementById('storageBreadcrumb');
  storageSearchEl = document.getElementById('storageSearch');
  storageSortEl = document.getElementById('storageSort');

  document.getElementById('storageBack').addEventListener('click', closeStorageMode);
  document.getElementById('storageRefresh').addEventListener('click', () => {
    if (!storagePath) fetchStorage();
    else openStorageDir(storagePath);
  });
  storageSearchEl.addEventListener('input', debounce(_renderStorageCurrent, 150));
  storageSortEl.addEventListener('change', _renderStorageCurrent);

  // Delegated handlers survive innerHTML swaps; pointerdown highlights on press.
  storageItemsEl.addEventListener('click', _onStorageClick);
  storageItemsEl.addEventListener('pointerdown', _onStoragePointerDown);
  storageCrumbEl.addEventListener('click', _onCrumbClick);
}

function openStorageMode() {
  _initStorageView();
  storageOpen = true;
  storageViewEl.classList.remove('hidden');
  fetchStorage();
}

function closeStorageMode() {
  storageOpen = false;
  if (_storagePollTimer) clearTimeout(_storagePollTimer);
  storageViewEl.classList.add('hidden');
}

function _onCrumbClick(e) {
  const crumb = e.target.closest('.breadcrumb-seg');
  if (!crumb) return;
  if (crumb.dataset.home) fetchStorage();
  else openStorageDir(crumb.dataset.path);
}

function _onStorageClick(e) {
  const reveal = e.target.closest('[data-reveal]');
  if (reveal) { revealStorage(reveal.dataset.reveal); return; }
  const row = e.target.closest('.app-item.storage');
  if (!row) return;
  if (row.dataset.back) { _storageBack(); return; }
  if (row.dataset.isdir === '1') openStorageDir(row.dataset.path);
  else selectStorageEntry(row.dataset.path);
}

function _onStoragePointerDown(e) {
  if (e.button !== 0) return;
  const row = e.target.closest('.app-item.storage');
  if (!row || row.dataset.back || row.dataset.isdir !== '0') return;
  selectStorageEntry(row.dataset.path);
}

function _storageBack() {
  if (storageBreadcrumb.length >= 2) openStorageDir(storageBreadcrumb[storageBreadcrumb.length - 2].path);
  else fetchStorage();
}

async function fetchStorage() {
  const gen = ++storageGen;
  storagePath = null;
  selectedStorage = null;
  storageCrumbEl.innerHTML = '';
  storageItemsEl.innerHTML = '<div class="loading-files"><span class="spinner"></span> Loading storage locations...</div>';
  try {
    const data = await fetchJson('/api/storage/roots');
    if (gen !== storageGen || !storageOpen) return;
    storageRoots = data.roots || [];
    renderStorageRoots();
    pollStorageRoots(gen);
  } catch (e) {
    if (gen === storageGen) storageItemsEl.innerHTML = `<div class="loading-files">Failed to load storage: ${escHtml(e.message)}</div>`;
  }
}

async function pollStorageRoots(gen) {
  if (storageRoots.every(r => !r.scanning)) return;
  await new Promise(r => { _storagePollTimer = setTimeout(r, 900); });
  if (gen !== storageGen || !storageOpen || storagePath !== null) return;
  try {
    const data = await fetchJson('/api/storage/roots');
    if (gen !== storageGen || !storageOpen || storagePath !== null) return;
    storageRoots = data.roots || [];
    renderStorageRoots();
  } catch {
    // keep the last good values and try again on the next tick
  }
  pollStorageRoots(gen);
}

async function openStorageDir(path, isRestore) {
  const gen = ++storageGen;
  const prevPath = storagePath;
  clearTimeout(_storagePollTimer);
  const leaf = path.slice(path.lastIndexOf('/') + 1) || path;
  storageItemsEl.innerHTML = '<div class="loading-files"><span class="spinner"></span> Scanning ' + escHtml(leaf) + '...</div>';
  let data;
  try {
    data = await postJson('/api/storage/list', { path });
  } catch (e) {
    if (gen !== storageGen || !storageOpen) return;
    showToast(`Cannot open folder: ${e.message}`, 'error');
    // Put back whatever was showing before instead of leaving the spinner up
    if (prevPath && !isRestore) openStorageDir(prevPath, true);
    else fetchStorage();
    return;
  }
  if (gen !== storageGen || !storageOpen) return;
  storagePath = data.path;
  selectedStorage = null;
  storageEntries = data.entries || [];
  storageBreadcrumb = data.breadcrumb || [];
  storageComplete = !!data.complete;
  storageSizes = {};
  for (const e of storageEntries) {
    if (e.is_dir) storageSizes[e.path] = e.size;
  }
  renderStorageDir();
  if (!storageComplete) pollStorageProgress(data.path, gen, 0);
}

async function pollStorageProgress(path, gen, failures) {
  clearTimeout(_storagePollTimer);
  let data;
  try {
    data = await postJson('/api/storage/progress', { path });
  } catch {
    if (gen !== storageGen || !storageOpen || storagePath !== path) return;
    if (failures < 5) _storagePollTimer = setTimeout(() => pollStorageProgress(path, gen, failures + 1), 1500);
    return;
  }
  if (gen !== storageGen || !storageOpen || storagePath !== path) return;
  let changed = false;
  for (const [p, size] of Object.entries(data.sizes || {})) {
    if (storageSizes[p] !== size) { storageSizes[p] = size; changed = true; }
  }
  storageComplete = !!data.complete;
  if (storageComplete || Date.now() - _storageLastSort > STORAGE_RESORT_MS) renderStorageDir();
  else if (changed) _patchStorageSizes();
  if (!storageComplete) {
    _storagePollTimer = setTimeout(() => pollStorageProgress(path, gen, 0), 700);
  }
}

// Update size cells and the summary in place, without re-sorting or rebuilding rows
function _patchStorageSizes() {
  for (const row of storageItemsEl.querySelectorAll('.app-item.storage[data-isdir="1"]')) {
    const size = storageSizes[row.dataset.path];
    const cell = row.querySelector('.app-size');
    if (cell && size) cell.textContent = formatBytes(size);
  }
  const summary = storageItemsEl.querySelector('.storage-summary');
  if (summary) {
    const entries = _storageSized();
    const total = entries.reduce((s, e) => s + (e._size || 0), 0);
    summary.textContent = `${entries.length} item${entries.length !== 1 ? 's' : ''} · ${formatBytes(total)} · scanning...`;
  }
}

function _renderStorageCurrent() {
  if (!storagePath) renderStorageRoots();
  else renderStorageDir();
}

function renderStorageRoots() {
  storageCrumbEl.innerHTML = '<span class="breadcrumb-current">All locations</span>';
  let html = '';
  for (const r of storageRoots) {
    let sizeCell;
    if (r.size != null) {
      sizeCell = r.scanning
        ? formatBytes(r.size) + ' <span class="spinner storage-inline-spinner"></span>'
        : formatBytes(r.size);
    } else {
      sizeCell = '<span class="spinner"></span>';
    }
    html += `<div class="app-item storage" data-path="${escHtml(r.path)}" data-isdir="1">
      <div class="storage-dir-icon">${r.emoji || '\u{1F4C1}'}</div>
      <div class="app-info">
        <div class="app-name">${escHtml(r.label)}</div>
        <div class="app-meta">${escHtml(shortenPath(r.path))}</div>
      </div>
      <button class="btn-reveal-row" data-reveal="${escHtml(r.path)}">Show in Finder</button>
      <div class="app-size">${sizeCell}</div>
    </div>`;
  }
  storageItemsEl.innerHTML = html;
}

// Entries with an effective size and the search filter applied, but unsorted.
function _storageSized() {
  let list = storageEntries.map(e => ({
    ...e,
    _size: e.is_dir ? (storageSizes[e.path] != null ? storageSizes[e.path] : e.size) : e.size,
  }));
  const q = storageSearchEl.value.toLowerCase().trim();
  if (q) list = list.filter(e => e.name.toLowerCase().includes(q));
  return list;
}

function getFilteredStorage() {
  const list = _storageSized();
  if (storageSortEl.value === 'name') list.sort((a, b) => a.name.localeCompare(b.name));
  else list.sort((a, b) => (b._size || 0) - (a._size || 0));
  return list;
}

function renderStorageDir() {
  let crumbs = '<span class="breadcrumb-seg" data-home="1">All locations</span><span class="breadcrumb-sep">›</span>';
  crumbs += storageBreadcrumb.map((c, i) => {
    const last = i === storageBreadcrumb.length - 1;
    if (last) return `<span class="breadcrumb-current">${escHtml(c.label)}</span>`;
    return `<span class="breadcrumb-seg" data-path="${escHtml(c.path)}">${escHtml(c.label)}</span><span class="breadcrumb-sep">›</span>`;
  }).join('');
  storageCrumbEl.innerHTML = crumbs;

  _storageLastSort = Date.now();
  const entries = getFilteredStorage();
  const total = entries.reduce((s, e) => s + (e._size || 0), 0);
  const backLabel = storageBreadcrumb.length >= 2
    ? storageBreadcrumb[storageBreadcrumb.length - 2].label
    : 'All locations';

  let html = `<div class="storage-summary">${entries.length} item${entries.length !== 1 ? 's' : ''} · ${formatBytes(total)}${storageComplete ? '' : ' · scanning...'}</div>`;
  html += `<div class="app-item storage storage-back" data-back="1">
    <div class="storage-dir-icon">‹</div>
    <div class="app-info"><div class="app-name">Back</div><div class="app-meta">${escHtml(backLabel)}</div></div>
  </div>`;
  for (const e of entries) {
    const sel = selectedStorage === e.path ? ' selected' : '';
    const icon = e.is_dir ? '\u{1F4C1}' : '\u{1F4C4}';
    const sizeCell = (e.is_dir && !storageComplete && !e._size) ? '<span class="spinner"></span>' : formatBytes(e._size);
    html += `<div class="app-item storage${sel}" data-path="${escHtml(e.path)}" data-isdir="${e.is_dir ? '1' : '0'}">
      <div class="storage-dir-icon">${icon}</div>
      <div class="app-info">
        <div class="app-name">${escHtml(e.name)}</div>
        <div class="app-meta">${e.is_dir ? 'Folder' : 'File'}</div>
      </div>
      <button class="btn-reveal-row" data-reveal="${escHtml(e.path)}">Show in Finder</button>
      <div class="app-size">${sizeCell}</div>
    </div>`;
  }
  storageItemsEl.innerHTML = html;
}

function selectStorageEntry(path) {
  selectedStorage = path;
  storageItemsEl.querySelectorAll('.app-item.storage.selected').forEach(r => r.classList.remove('selected'));
  for (const r of storageItemsEl.querySelectorAll('.app-item.storage')) {
    if (r.dataset.path === selectedStorage) { r.classList.add('selected'); break; }
  }
}

async function revealStorage(path) {
  if (!path) return;
  try {
    await postJson('/api/storage/reveal', { path });
  } catch (e) {
    showToast(`Could not reveal in Finder: ${e.message}`, 'error');
  }
}
