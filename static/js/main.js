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
