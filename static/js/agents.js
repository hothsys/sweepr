// ── Agents tab ──

async function fetchAgents() {
  const gen = ++fetchGeneration;
  appListEl.innerHTML = '<div class="loading-files"><span class="spinner"></span> Scanning launch agents...</div>';
  try {
    const data = await fetchJson('/api/agents');
    launchAgents = data.agents || [];
  } catch (e) {
    if (gen === fetchGeneration && currentTab === 'agents') {
      appListEl.innerHTML = `<div class="loading-files">Could not scan launch agents: ${escHtml(e.message)}</div>`;
    }
    return;
  }
  if (gen === fetchGeneration && currentTab === 'agents') renderAgentList();
}

function renderAgentList() {
  if (!launchAgents) return;
  const q = searchBox.value.toLowerCase().trim();
  let filtered = [...launchAgents];
  if (q) {
    filtered = filtered.filter(a => a.label.toLowerCase().includes(q) || a.executable.toLowerCase().includes(q));
  }
  const sort = sortSelect.value;
  if (sort === 'name') filtered.sort((a, b) => a.label.localeCompare(b.label));
  // Problems first: stale, then failing, then the rest by name
  else filtered.sort((a, b) => (b.stale - a.stale) || (b.failing - a.failing) || a.label.localeCompare(b.label));

  let html = '';
  for (const agent of filtered) {
    const sel = selectedAgent && selectedAgent.path === agent.path ? ' selected' : '';
    const staleClass = agent.stale ? ' style="color:var(--danger)"' : '';
    html += `<div class="app-item${sel}" data-id="${escHtml(agent.path)}" onclick="selectAgent(this.dataset.id)">
      <div class="brew-icon">${agent.stale ? '&#9888;' : '&#9881;'}</div>
      <div class="app-info">
        <div class="app-name"${staleClass}>${escHtml(agent.label)}${agent.stale ? '<span class="system-badge" style="background:var(--danger)">stale</span>' : ''}${agent.failing ? '<span class="system-badge" style="background:var(--warning)">failing</span>' : ''}</div>
        <div class="app-meta">${escHtml(agent.scope)}${agent.pid ? ' · Running' : ''}${agent.run_at_load ? ' · Runs at login' : ''}</div>
      </div>
    </div>`;
  }
  appListEl.innerHTML = html || '<div class="loading-files">No launch agents found</div>';
  const staleCount = filtered.filter(a => a.stale).length;
  const failingCount = filtered.filter(a => a.failing).length;
  document.getElementById('tabCount').textContent = filtered.length + ' agent' + (filtered.length !== 1 ? 's' : '')
    + (staleCount ? ' · ' + staleCount + ' stale' : '') + (failingCount ? ' · ' + failingCount + ' failing' : '');
}

function selectAgent(path) {
  selectedAgent = launchAgents.find(a => a.path === path);
  if (!selectedAgent) return;
  renderAgentList();
  renderAgentDetail();
}

function renderAgentDetail() {
  const agent = selectedAgent;
  if (!agent) return;

  const risk = agent.is_self
    ? { level: 'high', reason: 'This is Sweepr\'s own agent. To uninstall Sweepr, run: bash launchagent/uninstall.sh' }
    : agent.stale
    ? { level: 'low', reason: `This agent can't run because ${agent.stale_reason || 'its program is missing'}, so it is safe to remove` }
    : agent.failing
    ? { level: 'high', reason: `This job keeps failing (last exit code ${agent.last_exit}). It may be a duplicate or broken setup; find out what it belongs to before removing it` }
    : agent.parse_error
    ? { level: 'high', reason: 'Sweepr couldn\'t read this plist, so it can\'t tell what it runs' }
    : { level: 'high', reason: 'This agent is active. Removing it may affect running services' };
  const status = agent.pid ? `Running (pid ${agent.pid})`
    : !agent.loaded ? 'Not loaded'
    : agent.failing ? `<strong style="color:var(--warning)">Failing (last exit code ${agent.last_exit})</strong>`
    : 'Loaded, not running';

  detailPanel.innerHTML = `
    <div class="detail-header">
      <div class="brew-icon" style="width:64px;height:64px;border-radius:14px;font-size:30px;margin-right:16px">${agent.stale ? '&#9888;' : '&#9881;'}</div>
      <div>
        <div class="detail-title">${escHtml(agent.label)}</div>
        <div class="detail-version">${escHtml(agent.scope)} agent${agent.stale ? ' — <span style="color:var(--danger);font-weight:600">STALE</span>' : ''}</div>
        <div class="detail-dates">
          Executable: ${escHtml(agent.executable || 'N/A')}<br>
          ${agent.script ? 'Script: ' + escHtml(agent.script) + '<br>' : ''}
          Exists: ${agent.exe_exists === null ? 'Unknown (not readable or not checkable)' : agent.exe_exists ? 'Yes' : '<strong style="color:var(--danger)">No (missing)</strong>'}<br>
          Status: ${status}<br>
          Runs at login: ${agent.run_at_load ? 'Yes' : 'No'}<br>
          Keep alive: ${agent.keep_alive ? 'Yes' : 'No'}
        </div>
      </div>
    </div>
    <div class="section-title">Plist File</div>
    <div class="files-list">
      <div class="file-item">
        <span class="file-category">${escHtml(agent.scope)}</span>
        <span class="file-path">${escHtml(agent.path)}</span>
      </div>
    </div>
    ${renderRiskBanner(risk)}
    <div class="action-bar">
      <div class="total-size">${agent.stale ? `Stale: ${escHtml(agent.stale_reason || 'program missing')}` : agent.failing ? 'Failing agent' : 'Active agent'}</div>
      <button class="btn-trash" ${agent.is_self ? 'disabled' : 'onclick="confirmAgentRemove()"'}${agent.stale || agent.is_self ? '' : ' style="background:var(--warning)"'}>
        ${agent.is_self ? 'Use uninstall.sh' : agent.stale ? 'Remove Stale Agent' : 'Remove Agent'}
      </button>
    </div>
  `;
}

function confirmAgentRemove() {
  if (!selectedAgent || selectedAgent.is_self) return;
  openModal(`
      <h3>Remove launch agent?</h3>
      <p>This will move <strong>${escHtml(selectedAgent.label)}</strong> plist to the Trash.${selectedAgent.stale ? '' : ' This agent is currently active — removing it will prevent it from running.'}</p>
      <div class="modal-actions">
        <button class="btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
        <button class="btn-confirm" onclick="doAgentRemove()">Remove</button>
      </div>
  `);
}

async function doAgentRemove() {
  document.querySelector('.modal-overlay')?.remove();
  const agent = selectedAgent;
  if (!agent) return;

  const btn = detailPanel.querySelector('.btn-trash');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Removing...'; }

  try {
    const result = await postJson('/api/agents/trash', { paths: [agent.path] });
    if (result.success && result.success.length > 0) {
      if (result.warnings && result.warnings.length > 0) {
        showToast(`Removed ${agent.label}, but it keeps running until restart (needs admin to stop now)`, 'error');
      } else {
        showToast(`Removed and stopped ${agent.label}`, 'success');
      }
      if (selectedAgent === agent) {
        selectedAgent = null;
        if (currentTab === 'agents') detailPanel.innerHTML = '<div class="detail-empty">Select an agent to view details</div>';
      }
      await fetchAgents();
    } else if (result.failed && result.failed.length > 0) {
      showToast(`Failed: ${result.failed[0].error}`, 'error');
      if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Remove'; }
    }
  } catch (e) {
    showToast(`Failed to remove agent: ${e.message}`, 'error');
    if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Remove'; }
  }
}
