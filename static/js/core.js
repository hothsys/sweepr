// ── App shell: theme, shared state, tab switching, cross-tab loading ──

function getPreferredTheme() {
  const stored = localStorage.getItem('theme');
  if (stored) return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('themeToggle');
  if (btn) btn.textContent = theme === 'dark' ? 'Light' : 'Dark';
}
applyTheme(getPreferredTheme());

// ── Shared state ──

let apps = [];
let brewPackages = [];
let npmPackages = [];
let orphanGroups = [];
let launchAgents = [];
let selectedApp = null;
let selectedBrew = null;
let selectedNpm = null;
let selectedOrphan = null;
let selectedAgent = null;
let associatedFiles = [];
let isLoadingFiles = false;
let isRemoving = false;
let currentTab = 'apps';
let fetchGeneration = 0;

// ── DOM references ──

const searchBox = document.getElementById('searchBox');
const sortSelect = document.getElementById('sortSelect');
const hideSystem = document.getElementById('hideSystem');
const appListEl = document.getElementById('appList');
const detailPanel = document.getElementById('detailPanel');
const refreshBtn = document.getElementById('refreshBtn');

// ── Tab switching ──

function switchTab(tab) {
  currentTab = tab;
  fetchGeneration++;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  const emptyMsgs = { apps: 'Select an app to view details', brew: 'Select a package to view details', npm: 'Select a package to view details', orphans: 'Select a category to view files', agents: 'Select an agent to view details' };
  detailPanel.innerHTML = `<div class="detail-empty">${emptyMsgs[tab] || ''}</div>`;
  selectedApp = null;
  selectedBrew = null;
  selectedNpm = null;
  selectedOrphan = null;
  selectedAgent = null;

  hideSystem.parentElement.style.display = tab === 'apps' ? '' : 'none';

  const labels = { apps: 'applications', brew: 'Homebrew packages', npm: 'npm packages', orphans: 'orphaned files', agents: 'launch agents' };
  function showLoading() {
    appListEl.innerHTML = `<div class="loading-files"><span class="spinner"></span> Loading ${labels[tab]}...</div>`;
  }

  if (tab === 'apps') {
    if (apps.length > 0) renderAppList();
    else { showLoading(); fetchApps(false); }
  } else if (tab === 'brew') {
    if (brewPackages.length > 0) renderBrewList();
    else { showLoading(); fetchBrew(false); }
  } else if (tab === 'npm') {
    if (npmPackages.length > 0) renderNpmList();
    else { showLoading(); fetchNpm(false); }
  } else if (tab === 'orphans') {
    showLoading();
    fetchOrphans();
  } else if (tab === 'agents') {
    showLoading();
    fetchAgents();
  }
}

function renderCurrentList() {
  if (currentTab === 'apps') renderAppList();
  else if (currentTab === 'brew') renderBrewList();
  else if (currentTab === 'npm') renderNpmList();
  else if (currentTab === 'orphans') renderOrphanList();
  else if (currentTab === 'agents') renderAgentList();
}

// ── Cross-tab background loading ──

function setLoading(task, active) {
}

function preloadOtherTabs() {
  if (!_brewPromise && brewPackages.length === 0) {
    setLoading('brew', true);
    _brewPromise = fetch('/api/brew').then(r => r.json()).then(d => {
      brewPackages = d.packages || [];
      _brewPromise = null;
      if (currentTab === 'brew') renderBrewList();
      pollBrewDetails();
    }).catch(() => { _brewPromise = null; setLoading('brew', false); });
  }
  if (!_npmPromise && npmPackages.length === 0) {
    setLoading('npm', true);
    _npmPromise = fetch('/api/npm').then(r => r.json()).then(d => {
      npmPackages = d.packages || [];
      _npmPromise = null;
      setLoading('npm', false);
      if (currentTab === 'npm') renderNpmList();
    }).catch(() => { _npmPromise = null; setLoading('npm', false); });
  }
}
