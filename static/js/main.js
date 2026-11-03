// ── Event listeners + bootstrap (must load last) ──

searchBox.addEventListener('input', debounce(renderCurrentList, 150));
sortSelect.addEventListener('change', renderCurrentList);
hideSystem.addEventListener('change', renderCurrentList);
refreshBtn.addEventListener('click', () => {
  if (currentTab === 'apps') fetchApps(true);
  else if (currentTab === 'brew') fetchBrew(true);
  else if (currentTab === 'npm') fetchNpm(true);
  else if (currentTab === 'orphans') fetchOrphans();
  else if (currentTab === 'agents') fetchAgents();
});
document.getElementById('themeToggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  localStorage.setItem('theme', next);
  applyTheme(next);
});
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
  if (!localStorage.getItem('theme')) applyTheme(e.matches ? 'dark' : 'light');
});

// Storage analyzer opens as a separate full-screen mode, not a tab.
document.getElementById('analyzeStorageBtn').addEventListener('click', openStorageMode);

appListEl.innerHTML = '<div class="loading-files"><span class="spinner"></span> Loading applications...</div>';
fetchApps(false);

// A running server keeps the code it started with. If Sweepr's files were updated
// since, say so: otherwise fixes look like they "didn't work".
async function checkForRestart() {
  try {
    const health = await fetchJson('/api/health');
    if (health.restart_needed && !document.getElementById('restartBanner')) {
      document.body.insertAdjacentHTML('afterbegin', `<div id="restartBanner" class="restart-banner">
        Sweepr was updated after it started, so it's still running the previous version.
        Restart it to apply the changes: press Ctrl+C where you started it and run <code>python3 server.py</code> again
        (or, for the login item, <code>launchctl kickstart -k gui/$UID/com.sweepr.server</code>), then reload this page.
      </div>`);
    }
  } catch {}
}
checkForRestart();
setInterval(checkForRestart, 60000);
