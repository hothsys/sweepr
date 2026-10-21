// ── Shared utilities ──

let _toastTimer = null;
function showToast(msg, type) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = `toast ${type}`;
  // Clear the previous toast's timer so it can't hide this one early
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.add('hidden'), 3500);
}

// fetch() that throws on non-2xx, using the server's {"error": ...} message when present
async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  let data = null;
  try { data = await resp.json(); } catch {}
  if (!resp.ok) throw new Error((data && data.error) || `Request failed (${resp.status})`);
  return data;
}

function postJson(url, body) {
  return fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

// Show a confirmation modal unless one is already open. The trigger button keeps
// focus behind the overlay, so Enter/Space would otherwise stack a second one.
function openModal(innerHtml) {
  if (document.querySelector('.modal-overlay')) return null;
  document.activeElement?.blur();
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal">${innerHtml}</div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.btn-cancel')?.focus();
  return overlay;
}

function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const val = bytes / Math.pow(1024, i);
  return val.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    if (isNaN(d)) return dateStr;
    const now = new Date();
    const diff = now - d;
    const days = Math.floor(diff / 86400000);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 30) return days + 'd ago';
    if (days < 365) return Math.floor(days / 30) + 'mo ago';
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch { return dateStr; }
}

function formatFullDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    if (isNaN(d)) return dateStr;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
  } catch { return dateStr; }
}

function formatDetailDates(app) {
  const parts = [];
  if (app.last_updated) parts.push('App version date: ' + formatFullDate(app.last_updated));
  if (app.last_used) parts.push('Last used: ' + formatFullDate(app.last_used));
  return parts.join('<br>');
}

function shortenPath(p) {
  const home = '/Users/';
  const idx = p.indexOf('/', home.length);
  if (idx > 0) return '~' + p.substring(idx);
  return p;
}

// Safe in text and in quoted attributes (quotes are escaped too)
function escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ── Risk assessment ──

function getAppRisk(app) {
  if (app.is_system) return { level: 'high', reason: 'System application — cannot be removed' };
  if (app.is_self) return { level: 'high', reason: 'This is Sweepr itself. To uninstall it, run: bash launchagent/uninstall.sh' };
  if (app.webloc) return { level: 'low', reason: 'Web shortcut — safe to remove' };
  if (app.leftover) return { level: 'low', reason: 'Leftover folder from an uninstalled app — safe to remove' };
  if (app.uninstaller) return { level: 'high', reason: 'This app has deep system integration — use its official uninstaller instead' };
  if (app.last_used) {
    const d = new Date(app.last_used);
    const months = (Date.now() - d) / (1000 * 60 * 60 * 24 * 30);
    if (months > 6) return { level: 'low', reason: `Not used in ${Math.floor(months)} months — safe to remove` };
  }
  return null;
}

function getBrewRisk(pkg) {
  const usedBy = pkg.used_by || [];
  if (pkg.pinned) return { level: 'high', reason: 'This package is pinned. It was intentionally locked to this version' };
  if (usedBy.length) {
    const names = usedBy.slice(0, 3).join(', ') + (usedBy.length > 3 ? ` and ${usedBy.length - 3} more` : '');
    return { level: 'high', reason: `Required by ${usedBy.length} installed package${usedBy.length !== 1 ? 's' : ''} (${names}). Homebrew will refuse to uninstall it while they are installed` };
  }
  if (pkg.installed_on_request === null || pkg.installed_on_request === undefined) {
    return { level: 'high', reason: 'Could not confirm how this was installed or what needs it' };
  }
  if (pkg.service) return { level: 'high', reason: 'Runs as a background service (brew services). Anything that connects to it will stop working' };
  if (!pkg.installed_on_request) return { level: 'low', reason: 'Leftover dependency: nothing installed needs it any more. Check you don\'t use it directly' };
  if (pkg.type === 'cask') return { level: 'low', reason: 'App installed by you' };
  return { level: 'low', reason: 'Installed by you, and no other installed package needs it' };
}

function getNpmRisk(pkg) {
  if (pkg.protected) return { level: 'high', reason: 'Ships with Node.js. Removing it would break npm and node, so Sweepr won\'t uninstall it' };
  return { level: 'low', reason: 'Standalone CLI tool — safe to remove' };
}

function renderRiskBanner(risk) {
  if (!risk) return '';
  const icon = risk.level === 'high' ? '&#9888;' : '&#10003;';
  const label = risk.level === 'high' ? 'Caution' : 'Safe to remove';
  return `<div class="risk-banner ${risk.level}">
    <span class="risk-icon">${icon}</span>
    <div class="risk-text"><strong>${label}</strong><span>${risk.reason}</span></div>
  </div>`;
}
