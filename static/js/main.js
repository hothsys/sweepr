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
        <strong>Sweepr has been updated.</strong> Restart it to start using the new version, then reload this page.
        <details>
          <summary>How do I restart it?</summary>
          <ul>
            <li><strong>Started from a terminal window:</strong> click that window, press Control-C, then run the same command again (<code>python3 server.py</code>).</li>
            <li><strong>Runs automatically when you log in:</strong> log out and back in, or restart your Mac.</li>
          </ul>
        </details>
      </div>`);
    }
  } catch {}
}
checkForRestart();
setInterval(checkForRestart, 60000);
