/**
 * Assistant Core - Memory Manager & System Diagnostics
 * Zero-Build Vanilla ES Module
 */

// Global State
const state = {
  token: sessionStorage.getItem('assistant_session_token') || null,
  authenticated: false,
  userId: localStorage.getItem('assistant_ui_user_id') || 'default',
  activeTab: 'memories',
  categoryFilter: 'all',
  statusFilter: 'active',
  searchQuery: '',
  memories: [],
  selectedMemoryIds: new Set(),
  stats: null,
  recentReferences: [],
  loadingMemories: false,
};

// DOM Elements cache
const els = {};

function initDOMElements() {
  els.authView = document.getElementById('auth-view');
  els.dashboardView = document.getElementById('dashboard-view');
  els.loginForm = document.getElementById('login-form');
  els.passwordInput = document.getElementById('password-input');
  els.togglePasswordBtn = document.getElementById('toggle-password-btn');
  els.loginError = document.getElementById('login-error');
  els.loginBtn = document.getElementById('login-btn');
  els.logoutBtn = document.getElementById('logout-btn');
  els.themeToggleBtn = document.getElementById('theme-toggle-btn');
  els.themeIconDark = document.getElementById('theme-icon-dark');
  els.themeIconLight = document.getElementById('theme-icon-light');

  els.userIdInput = document.getElementById('user-id-input');
  els.tabMemoriesBtn = document.getElementById('tab-memories-btn');
  els.tabDiagnosticsBtn = document.getElementById('tab-diagnostics-btn');
  els.tabPanelMemories = document.getElementById('tab-panel-memories');
  els.tabPanelDiagnostics = document.getElementById('tab-panel-diagnostics');
  els.memoriesCountBadge = document.getElementById('memories-count-badge');

  els.statActiveMemories = document.getElementById('stat-active-memories');
  els.statMemoriesSub = document.getElementById('stat-memories-sub');
  els.statConversationSegments = document.getElementById('stat-conversation-segments');
  els.statConversationSub = document.getElementById('stat-conversation-sub');
  els.statFileSegments = document.getElementById('stat-file-segments');
  els.statFilesSub = document.getElementById('stat-files-sub');
  els.statQueueStatus = document.getElementById('stat-queue-status');
  els.statQueueSub = document.getElementById('stat-queue-sub');

  els.searchInput = document.getElementById('search-input');
  els.searchClearBtn = document.getElementById('search-clear-btn');
  els.refreshBtn = document.getElementById('refresh-btn');
  els.memoriesContainer = document.getElementById('memories-grid');
  els.memoriesLoading = document.getElementById('memories-loading');
  els.memoriesEmpty = document.getElementById('memories-empty');

  els.floatingMergeBar = document.getElementById('floating-merge-bar');
  els.mergeSelectedCount = document.getElementById('merge-selected-count');
  els.mergeClearBtn = document.getElementById('merge-clear-btn');
  els.mergeOpenModalBtn = document.getElementById('merge-open-modal-btn');

  // Edit Modal
  els.editModal = document.getElementById('edit-modal');
  els.editMemoryForm = document.getElementById('edit-memory-form');
  els.editMemoryId = document.getElementById('edit-memory-id');
  els.editModalCategory = document.getElementById('edit-modal-category');
  els.editModalIdText = document.getElementById('edit-modal-id-text');
  els.editStatementInput = document.getElementById('edit-statement-input');
  els.editCharCount = document.getElementById('edit-char-count');
  els.editSaveBtn = document.getElementById('edit-save-btn');

  // Merge Modal
  els.mergeModal = document.getElementById('merge-modal');
  els.mergeMemoriesForm = document.getElementById('merge-memories-form');
  els.mergeSourcesContainer = document.getElementById('merge-sources-container');
  els.mergeCategorySelect = document.getElementById('merge-category-select');
  els.mergeStatementInput = document.getElementById('merge-statement-input');
  els.mergeCharCount = document.getElementById('merge-char-count');
  els.mergeSubmitBtn = document.getElementById('merge-submit-btn');

  // Diagnostics Tab
  els.refreshDiagnosticsBtn = document.getElementById('refresh-diagnostics-btn');
  els.recentReferencesTbody = document.getElementById('recent-references-tbody');
  els.toastContainer = document.getElementById('toast-container');
}

/* ==========================================================================
   API Client Helper
   ========================================================================== */

async function api(path, options = {}) {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const candidates = [cleanPath];
  if (!cleanPath.startsWith('/ui/')) {
    candidates.push(`/ui${cleanPath}`);
  }

  let response;
  let lastError;

  for (const url of candidates) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };

    if (state.token) {
      headers['x-assistant-session'] = state.token;
    }

    try {
      response = await fetch(url, {
        ...options,
        headers,
      });

      if (response.status === 404 && candidates.length > 1 && url === candidates[0]) {
        continue;
      }
      break;
    } catch (err) {
      lastError = err;
    }
  }

  if (!response) {
    throw lastError || new Error('Network error');
  }

  if (response.status === 401) {
    handleUnauthenticated();
    throw new Error('Authentication required');
  }

  if (!response.ok) {
    let errorDetail = `Request failed (${response.status})`;
    try {
      const errJson = await response.json();
      if (errJson.detail) {
        errorDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
      }
    } catch {
      // Ignore JSON parse errors on failure
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

/* ==========================================================================
   Toast Notifications
   ========================================================================== */

function showToast(message, type = 'info', duration = 3200) {
  if (!els.toastContainer) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  const iconSvg =
    type === 'success'
      ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`
      : type === 'error'
      ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`
      : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`;

  toast.innerHTML = `
    ${iconSvg}
    <span>${escapeHtml(message)}</span>
  `;

  els.toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('toast-out');
    setTimeout(() => {
      if (toast.parentElement) toast.parentElement.removeChild(toast);
    }, 160);
  }, duration);
}

/* ==========================================================================
   Authentication Flow
   ========================================================================== */

async function checkAuth() {
  try {
    const data = await api('/v1/auth/check', { method: 'GET' });
    if (data && data.authenticated) {
      setAuthenticated(true);
      return;
    }
  } catch {
    // 401 will trigger handleUnauthenticated
  }
  handleUnauthenticated();
}

function handleUnauthenticated() {
  state.authenticated = false;
  state.token = null;
  sessionStorage.removeItem('assistant_session_token');
  if (els.dashboardView) {
    els.dashboardView.classList.add('hidden');
    els.dashboardView.style.display = 'none';
  }
  if (els.authView) {
    els.authView.classList.remove('hidden');
    els.authView.style.display = 'flex';
  }
}

function setAuthenticated(isAuth) {
  state.authenticated = isAuth;
  if (isAuth) {
    if (els.authView) {
      els.authView.classList.add('hidden');
      els.authView.style.display = 'none';
    }
    if (els.dashboardView) {
      els.dashboardView.classList.remove('hidden');
      els.dashboardView.style.display = 'block';
    }
    if (els.userIdInput) {
      els.userIdInput.value = state.userId;
    }
    refreshDashboard();
  } else {
    handleUnauthenticated();
  }
}

async function handleLogin(e) {
  if (e && e.preventDefault) e.preventDefault();
  const password = els.passwordInput.value.trim();
  if (!password) {
    if (els.loginError) {
      els.loginError.textContent = 'Please enter your Admin Password.';
      els.loginError.classList.remove('hidden');
      els.loginError.style.display = 'block';
    }
    return;
  }

  setButtonLoading(els.loginBtn, true);
  if (els.loginError) {
    els.loginError.classList.add('hidden');
    els.loginError.style.display = 'none';
  }

  const endpoints = ['/v1/auth/login', '/ui/v1/auth/login'];
  let success = false;
  let errorMsg = 'Login failed. Please check credentials.';

  for (const endpoint of endpoints) {
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });

      if (res.status === 404 && endpoint === endpoints[0]) {
        continue;
      }

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 401) {
          errorMsg = 'Incorrect admin password. Please try again.';
        } else {
          errorMsg = data.detail || `Server error (${res.status})`;
        }
        break;
      }

      state.token = data.token;
      sessionStorage.setItem('assistant_session_token', data.token);
      els.passwordInput.value = '';
      setAuthenticated(true);
      showToast('Dashboard unlocked successfully', 'success');
      success = true;
      break;
    } catch (err) {
      errorMsg = err.message || 'Unable to connect to Assistant Core API.';
    }
  }

  if (!success && els.loginError) {
    els.loginError.textContent = errorMsg;
    els.loginError.classList.remove('hidden');
    els.loginError.style.display = 'block';
    if (els.passwordInput) {
      els.passwordInput.focus();
      els.passwordInput.select();
    }
  }

  setButtonLoading(els.loginBtn, false);
}

async function handleLogout() {
  try {
    await api('/v1/auth/logout', { method: 'POST' });
  } catch {
    // Fail silently on logout endpoint failure
  }
  handleUnauthenticated();
  showToast('Logged out of Assistant Core', 'info');
}

/* ==========================================================================
   Data Fetching & Rendering
   ========================================================================== */

async function refreshDashboard() {
  await Promise.all([
    loadMemories(),
    loadStats(),
    state.activeTab === 'diagnostics' ? loadRecentReferences() : Promise.resolve(),
  ]);
}

async function loadMemories() {
  state.loadingMemories = true;
  els.memoriesLoading.classList.remove('hidden');
  els.memoriesEmpty.classList.add('hidden');

  try {
    const payload = {
      native_user_id: state.userId,
      status: state.statusFilter,
      category: state.categoryFilter === 'all' ? null : state.categoryFilter,
      limit: 250,
    };

    const data = await api('/v1/personal-context/list', {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    state.memories = data.memories || [];
    updateCategoryCounts();
    renderMemoriesGrid();
  } catch (err) {
    showToast(`Failed to load memories: ${err.message}`, 'error');
  } finally {
    state.loadingMemories = false;
    els.memoriesLoading.classList.add('hidden');
  }
}

async function loadStats() {
  try {
    const stats = await api('/v1/inspection/stats', {
      method: 'POST',
      body: JSON.stringify({ native_user_id: state.userId }),
    });
    state.stats = stats;
    renderStats(stats);
  } catch (err) {
    // If stats call fails, display placeholder
    console.warn('Inspection stats error:', err);
  }
}

function renderStats(stats) {
  if (!stats) return;

  const totalSegments = stats.total_segments || 0;
  const embeddedSegments = stats.embedded_segments || 0;
  const lexicalSegments = stats.lexical_segments || 0;
  const totalFileSegments = stats.total_file_segments || 0;
  const queuedJobs = stats.queued_jobs || 0;
  const deadJobs = stats.dead_jobs || 0;

  els.statConversationSegments.textContent = totalSegments.toLocaleString();
  els.statConversationSub.textContent = `${embeddedSegments.toLocaleString()} embedded &bull; ${lexicalSegments.toLocaleString()} lexical`;

  els.statFileSegments.textContent = totalFileSegments.toLocaleString();
  els.statFilesSub.textContent = `${(stats.active_file_references || 0).toLocaleString()} active file references`;

  els.statQueueStatus.textContent = queuedJobs === 0 && deadJobs === 0 ? 'Healthy' : `${queuedJobs} queued`;
  els.statQueueSub.textContent = `${queuedJobs} queued &bull; ${deadJobs} dead jobs`;
}

async function loadRecentReferences() {
  try {
    const data = await api('/v1/inspection/recent', {
      method: 'POST',
      body: JSON.stringify({ native_user_id: state.userId, limit: 15 }),
    });
    state.recentReferences = data.results || [];
    renderRecentReferencesTable();
  } catch (err) {
    showToast(`Failed to load inspection references: ${err.message}`, 'error');
  }
}

function renderRecentReferencesTable() {
  const tbody = els.recentReferencesTbody;
  if (!state.recentReferences.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="table-empty">No indexed conversation references found for user '${escapeHtml(state.userId)}'.</td></tr>`;
    return;
  }

  tbody.innerHTML = state.recentReferences
    .map((ref) => {
      const isTombstoned = Boolean(ref.tombstoned_at);
      const statusBadge = isTombstoned
        ? `<span class="badge badge-archived">Tombstoned</span>`
        : `<span class="badge badge-active">Active</span>`;
      const dateStr = formatDate(ref.created_at);

      return `
      <tr>
        <td><code class="code-pill" title="${escapeHtml(ref.reference_id)}">${escapeHtml(ref.reference_id.slice(0, 8))}…</code></td>
        <td><span class="badge ${ref.role === 'user' ? 'badge-pref' : 'badge-inst'}">${escapeHtml(ref.role)}</span></td>
        <td><code class="code-pill" title="${escapeHtml(ref.native_chat_id)}">${escapeHtml(ref.native_chat_id.slice(0, 12))}…</code></td>
        <td><code class="code-pill" title="${escapeHtml(ref.native_message_id)}">${escapeHtml(ref.native_message_id.slice(0, 12))}…</code></td>
        <td>${ref.chunk_ordinal}</td>
        <td>${statusBadge}</td>
        <td>${dateStr}</td>
      </tr>
    `;
    })
    .join('');
}

/* ==========================================================================
   Memory Cards Rendering & Filtering
   ========================================================================== */

function updateCategoryCounts() {
  const counts = {
    all: state.memories.length,
    preference: 0,
    instruction: 0,
    fact: 0,
    project: 0,
    decision: 0,
  };

  for (const m of state.memories) {
    if (counts[m.category] !== undefined) {
      counts[m.category]++;
    }
  }

  els.memoriesCountBadge.textContent = counts.all;
  els.statActiveMemories.textContent = counts.all;
  els.statMemoriesSub.textContent = `${counts.preference} pref &bull; ${counts.instruction} inst &bull; ${counts.fact} fact`;

  document.getElementById('pill-count-all').textContent = counts.all;
  document.getElementById('pill-count-preference').textContent = counts.preference;
  document.getElementById('pill-count-instruction').textContent = counts.instruction;
  document.getElementById('pill-count-fact').textContent = counts.fact;
  document.getElementById('pill-count-project').textContent = counts.project;
  document.getElementById('pill-count-decision').textContent = counts.decision;
}

function getFilteredMemories() {
  const query = state.searchQuery.trim().toLowerCase();
  return state.memories.filter((m) => {
    // Category match
    if (state.categoryFilter !== 'all' && m.category !== state.categoryFilter) {
      return false;
    }

    // Status match
    if (state.statusFilter !== 'all') {
      const memStatus = m.status || m.state || 'active';
      if (memStatus !== state.statusFilter) return false;
    }

    // Search query match
    if (query) {
      const matchStatement = m.statement && m.statement.toLowerCase().includes(query);
      const matchKey = m.key && m.key.toLowerCase().includes(query);
      const matchEvidence = m.evidence_quote && m.evidence_quote.toLowerCase().includes(query);
      const matchChatId = m.native_chat_id && m.native_chat_id.toLowerCase().includes(query);
      if (!matchStatement && !matchKey && !matchEvidence && !matchChatId) {
        return false;
      }
    }

    return true;
  });
}

function renderMemoriesGrid() {
  const filtered = getFilteredMemories();
  const container = els.memoriesContainer;
  container.innerHTML = '';

  if (filtered.length === 0) {
    els.memoriesEmpty.classList.remove('hidden');
    return;
  }
  els.memoriesEmpty.classList.add('hidden');

  const fragment = document.createDocumentFragment();

  for (const memory of filtered) {
    const card = document.createElement('div');
    const isSelected = state.selectedMemoryIds.has(memory.id);
    const memStatus = memory.status || memory.state || 'active';

    card.className = `memory-card ${isSelected ? 'selected' : ''}`;
    card.id = `memory-card-${memory.id}`;

    const categoryBadgeClass = getCategoryBadgeClass(memory.category);
    const confidencePct = Math.round((memory.confidence || 1.0) * 100);
    const isArchived = memStatus === 'archived';
    const isSuperseded = memStatus === 'superseded';

    let statusBadgeHtml = '';
    if (isArchived) {
      statusBadgeHtml = `<span class="badge badge-archived">Archived</span>`;
    } else if (isSuperseded) {
      statusBadgeHtml = `<span class="badge badge-superseded">Superseded</span>`;
    }

    const hasEvidence = Boolean(memory.evidence_quote);
    const evidenceHtml = hasEvidence
      ? `
      <div class="evidence-accordion" id="accordion-${memory.id}">
        <button type="button" class="evidence-toggle" data-toggle-accordion="${memory.id}">
          <span>Evidence Quote &amp; Origin</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </button>
        <div class="evidence-content hidden" id="evidence-content-${memory.id}">
          <div class="evidence-quote">"${escapeHtml(memory.evidence_quote)}"</div>
          <div class="evidence-meta-row">
            ${memory.native_chat_id ? `<span>Chat: <code class="code-pill" title="${escapeHtml(memory.native_chat_id)}">${escapeHtml(memory.native_chat_id.slice(0, 10))}…</code></span>` : ''}
            ${memory.native_message_id ? `<span>Msg: <code class="code-pill" title="${escapeHtml(memory.native_message_id)}">${escapeHtml(memory.native_message_id.slice(0, 10))}…</code></span>` : ''}
          </div>
        </div>
      </div>
    `
      : '';

    card.innerHTML = `
      <div class="memory-card-header">
        <div class="card-header-left">
          <input 
            type="checkbox" 
            class="card-select-checkbox" 
            data-memory-id="${escapeHtml(memory.id)}"
            ${isSelected ? 'checked' : ''}
            aria-label="Select memory for merge"
          >
          <span class="badge ${categoryBadgeClass}">${escapeHtml(memory.category)}</span>
          ${statusBadgeHtml}
        </div>
        <div class="card-header-right">
          <span class="code-pill" title="${confidencePct}% confidence score">${confidencePct}%</span>
        </div>
      </div>

      <div class="memory-statement" id="statement-${memory.id}">
        ${escapeHtml(memory.statement)}
      </div>

      ${evidenceHtml}

      <div class="card-footer">
        <div class="card-meta">
          <span title="Created at">${formatDate(memory.created_at)}</span>
        </div>
        <div class="card-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-action="edit" data-id="${escapeHtml(memory.id)}" title="Edit statement">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 20h9"/>
              <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
            </svg>
            <span>Edit</span>
          </button>
          ${
            !isArchived
              ? `
            <button type="button" class="btn btn-danger-ghost btn-sm" data-action="archive" data-id="${escapeHtml(memory.id)}" title="Archive memory">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="21 8 21 21 3 21 3 8"/>
                <rect x="1" y="3" width="22" height="5"/>
                <line x1="10" y1="12" x2="14" y2="12"/>
              </svg>
              <span>Archive</span>
            </button>
          `
              : ''
          }
        </div>
      </div>
    `;

    fragment.appendChild(card);
  }

  container.appendChild(fragment);
  updateFloatingMergeBar();
}

function getCategoryBadgeClass(category) {
  switch (category) {
    case 'preference':
      return 'badge-pref';
    case 'instruction':
      return 'badge-inst';
    case 'fact':
      return 'badge-fact';
    case 'project':
      return 'badge-proj';
    case 'decision':
      return 'badge-dec';
    default:
      return 'badge-pref';
  }
}

/* ==========================================================================
   Memory Actions: Archive, Edit, Merge
   ========================================================================== */

async function handleArchiveMemory(memoryId) {
  const memory = state.memories.find((m) => m.id === memoryId);
  if (!memory) return;

  // Optimistic UI update: mark archived in local state
  const prevStatus = memory.status || memory.state;
  memory.status = 'archived';
  memory.state = 'archived';
  memory.archived_at = new Date().toISOString();

  // If viewing active only, remove from UI immediately
  if (state.statusFilter === 'active') {
    state.selectedMemoryIds.delete(memoryId);
    renderMemoriesGrid();
  } else {
    renderMemoriesGrid();
  }

  showToast('Memory archived', 'success');

  try {
    await api('/v1/personal-context/archive', {
      method: 'POST',
      body: JSON.stringify({
        native_user_id: state.userId,
        memory_id: memoryId,
      }),
    });
    updateCategoryCounts();
  } catch (err) {
    // Rollback on error
    memory.status = prevStatus;
    memory.state = prevStatus;
    renderMemoriesGrid();
    showToast(`Failed to archive memory: ${err.message}`, 'error');
  }
}

function openEditModal(memoryId) {
  const memory = state.memories.find((m) => m.id === memoryId);
  if (!memory) return;

  els.editMemoryId.value = memory.id;
  els.editModalIdText.textContent = memory.id;
  els.editModalCategory.className = `badge ${getCategoryBadgeClass(memory.category)}`;
  els.editModalCategory.textContent = memory.category;
  els.editStatementInput.value = memory.statement;
  els.editCharCount.textContent = memory.statement.length;

  els.editModal.classList.remove('hidden');
  els.editStatementInput.focus();
}

async function handleSaveEdit(e) {
  e.preventDefault();
  const memoryId = els.editMemoryId.value;
  const newStatement = els.editStatementInput.value.trim();
  if (!newStatement) return;

  setButtonLoading(els.editSaveBtn, true);

  try {
    const res = await api('/v1/personal-context/update', {
      method: 'POST',
      body: JSON.stringify({
        native_user_id: state.userId,
        memory_id: memoryId,
        new_statement: newStatement,
      }),
    });

    const memory = state.memories.find((m) => m.id === memoryId);
    if (memory) {
      memory.statement = res.statement;
      memory.updated_at = new Date().toISOString();
    }

    renderMemoriesGrid();
    closeModal(els.editModal);
    showToast('Memory updated successfully', 'success');
  } catch (err) {
    showToast(`Failed to update memory: ${err.message}`, 'error');
  } finally {
    setButtonLoading(els.editSaveBtn, false);
  }
}

function openMergeModal() {
  const selectedIds = Array.from(state.selectedMemoryIds);
  if (selectedIds.length < 2) return;

  const selectedMemories = state.memories.filter((m) => selectedIds.includes(m.id));

  // Render sources preview
  els.mergeSourcesContainer.innerHTML = selectedMemories
    .map(
      (m) => `
    <div class="merge-source-item">
      <div class="merge-source-header">
        <span class="badge ${getCategoryBadgeClass(m.category)}">${escapeHtml(m.category)}</span>
        <code class="code-pill">${escapeHtml(m.id.slice(0, 8))}…</code>
      </div>
      <div class="merge-source-statement">${escapeHtml(m.statement)}</div>
    </div>
  `
    )
    .join('');

  // Default target category to the category of the first source memory
  const firstCat = selectedMemories[0]?.category || 'preference';
  els.mergeCategorySelect.value = firstCat;

  // Auto-suggested merged text: join statements
  const suggestedStatement = selectedMemories.map((m) => m.statement).join('; ');
  els.mergeStatementInput.value = suggestedStatement;
  els.mergeCharCount.textContent = suggestedStatement.length;

  els.mergeModal.classList.remove('hidden');
  els.mergeStatementInput.focus();
}

async function handleSaveMerge(e) {
  e.preventDefault();
  const selectedIds = Array.from(state.selectedMemoryIds);
  if (selectedIds.length < 2) return;

  const targetCategory = els.mergeCategorySelect.value;
  const newStatement = els.mergeStatementInput.value.trim();
  if (!newStatement) return;

  setButtonLoading(els.mergeSubmitBtn, true);

  try {
    const res = await api('/v1/personal-context/merge', {
      method: 'POST',
      body: JSON.stringify({
        native_user_id: state.userId,
        source_memory_ids: selectedIds,
        target_category: targetCategory,
        new_statement: newStatement,
      }),
    });

    state.selectedMemoryIds.clear();
    closeModal(els.mergeModal);
    showToast(`Merged ${res.archived_source_ids.length} memories into a consolidated record`, 'success');
    await loadMemories();
  } catch (err) {
    showToast(`Failed to merge memories: ${err.message}`, 'error');
  } finally {
    setButtonLoading(els.mergeSubmitBtn, false);
  }
}

function updateFloatingMergeBar() {
  const count = state.selectedMemoryIds.size;
  if (count >= 2) {
    els.mergeSelectedCount.textContent = count;
    els.floatingMergeBar.classList.remove('hidden');
  } else {
    els.floatingMergeBar.classList.add('hidden');
  }
}

/* ==========================================================================
   UI Helpers & Event Wiring
   ========================================================================== */

function closeModal(modalEl) {
  if (modalEl) modalEl.classList.add('hidden');
}

function setButtonLoading(btn, loading) {
  const text = btn.querySelector('.btn-text');
  const spinner = btn.querySelector('.btn-spinner');
  if (loading) {
    btn.disabled = true;
    if (text) text.classList.add('hidden');
    if (spinner) spinner.classList.remove('hidden');
  } else {
    btn.disabled = false;
    if (text) text.classList.remove('hidden');
    if (spinner) spinner.classList.add('hidden');
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatDate(isoStr) {
  if (!isoStr) return '-';
  try {
    const d = new Date(isoStr);
    return d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoStr;
  }
}

/* Theme Management */
function initTheme() {
  const saved = localStorage.getItem('assistant_theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = saved || (prefersDark ? 'dark' : 'dark');
  applyTheme(theme);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('assistant_theme', theme);
  if (theme === 'dark') {
    els.themeIconDark.classList.remove('hidden');
    els.themeIconLight.classList.add('hidden');
  } else {
    els.themeIconDark.classList.add('hidden');
    els.themeIconLight.classList.remove('hidden');
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
}

/* Event Handlers Setup */
function setupEventListeners() {
  // Auth Form
  els.loginForm.addEventListener('submit', handleLogin);
  els.logoutBtn.addEventListener('click', handleLogout);
  els.themeToggleBtn.addEventListener('click', toggleTheme);

  // Toggle Password
  els.togglePasswordBtn.addEventListener('click', () => {
    const type = els.passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
    els.passwordInput.setAttribute('type', type);
  });

  // User ID input change
  let userDebounce;
  els.userIdInput.addEventListener('input', () => {
    clearTimeout(userDebounce);
    userDebounce = setTimeout(() => {
      const val = els.userIdInput.value.trim() || 'default';
      state.userId = val;
      localStorage.setItem('assistant_ui_user_id', val);
      state.selectedMemoryIds.clear();
      refreshDashboard();
      showToast(`Switched user context to '${val}'`, 'info');
    }, 450);
  });

  // Tab Navigation
  els.tabMemoriesBtn.addEventListener('click', () => switchTab('memories'));
  els.tabDiagnosticsBtn.addEventListener('click', () => switchTab('diagnostics'));

  // Search input
  let searchDebounce;
  els.searchInput.addEventListener('input', () => {
    const val = els.searchInput.value;
    state.searchQuery = val;
    els.searchClearBtn.classList.toggle('hidden', !val);
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      renderMemoriesGrid();
    }, 150);
  });

  els.searchClearBtn.addEventListener('click', () => {
    els.searchInput.value = '';
    state.searchQuery = '';
    els.searchClearBtn.classList.add('hidden');
    renderMemoriesGrid();
  });

  // Status Segmented Control
  document.querySelectorAll('.status-segmented-control .segment-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.status-segmented-control .segment-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.statusFilter = btn.getAttribute('data-status');
      loadMemories();
    });
  });

  // Category Pills
  document.querySelectorAll('.category-pills-row .pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.category-pills-row .pill').forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
      state.categoryFilter = pill.getAttribute('data-category');
      renderMemoriesGrid();
    });
  });

  // Refresh Buttons
  els.refreshBtn.addEventListener('click', () => {
    loadMemories();
    loadStats();
    showToast('Refreshed memories and statistics', 'info');
  });

  els.refreshDiagnosticsBtn.addEventListener('click', () => {
    loadRecentReferences();
    loadStats();
    showToast('Refreshed diagnostic telemetry', 'info');
  });

  // Card Grid Event Delegation (checkbox, archive, edit, accordion)
  els.memoriesContainer.addEventListener('click', (e) => {
    const target = e.target;

    // Checkbox toggle
    const checkbox = target.closest('.card-select-checkbox');
    if (checkbox) {
      const memoryId = checkbox.getAttribute('data-memory-id');
      if (checkbox.checked) {
        state.selectedMemoryIds.add(memoryId);
      } else {
        state.selectedMemoryIds.delete(memoryId);
      }
      const card = document.getElementById(`memory-card-${memoryId}`);
      if (card) card.classList.toggle('selected', checkbox.checked);
      updateFloatingMergeBar();
      return;
    }

    // Archive button
    const archiveBtn = target.closest('[data-action="archive"]');
    if (archiveBtn) {
      const memoryId = archiveBtn.getAttribute('data-id');
      handleArchiveMemory(memoryId);
      return;
    }

    // Edit button
    const editBtn = target.closest('[data-action="edit"]');
    if (editBtn) {
      const memoryId = editBtn.getAttribute('data-id');
      openEditModal(memoryId);
      return;
    }

    // Accordion toggle
    const toggleBtn = target.closest('[data-toggle-accordion]');
    if (toggleBtn) {
      const memoryId = toggleBtn.getAttribute('data-toggle-accordion');
      const accordion = document.getElementById(`accordion-${memoryId}`);
      const content = document.getElementById(`evidence-content-${memoryId}`);
      if (accordion && content) {
        accordion.classList.toggle('open');
        content.classList.toggle('hidden');
      }
      return;
    }
  });

  // Floating Merge Bar
  els.mergeClearBtn.addEventListener('click', () => {
    state.selectedMemoryIds.clear();
    renderMemoriesGrid();
  });

  els.mergeOpenModalBtn.addEventListener('click', openMergeModal);

  // Edit Modal
  els.editMemoryForm.addEventListener('submit', handleSaveEdit);
  els.editStatementInput.addEventListener('input', () => {
    els.editCharCount.textContent = els.editStatementInput.value.length;
  });

  // Merge Modal
  els.mergeMemoriesForm.addEventListener('submit', handleSaveMerge);
  els.mergeStatementInput.addEventListener('input', () => {
    els.mergeCharCount.textContent = els.mergeStatementInput.value.length;
  });

  // Modal Close buttons
  document.querySelectorAll('[data-close-modal]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const modalId = btn.getAttribute('data-close-modal');
      closeModal(document.getElementById(modalId));
    });
  });

  // Close modals on Escape or backdrop click
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeModal(els.editModal);
      closeModal(els.mergeModal);
    }
  });

  [els.editModal, els.mergeModal].forEach((modal) => {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeModal(modal);
    });
  });
}

function switchTab(tabName) {
  state.activeTab = tabName;
  if (tabName === 'memories') {
    els.tabMemoriesBtn.classList.add('active');
    els.tabDiagnosticsBtn.classList.remove('active');
    els.tabPanelMemories.classList.remove('hidden');
    els.tabPanelDiagnostics.classList.add('hidden');
  } else {
    els.tabMemoriesBtn.classList.remove('active');
    els.tabDiagnosticsBtn.classList.add('active');
    els.tabPanelMemories.classList.add('hidden');
    els.tabPanelDiagnostics.classList.remove('hidden');
    loadRecentReferences();
  }
}

/* ==========================================================================
   Initialization
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  initDOMElements();
  initTheme();
  setupEventListeners();
  checkAuth();
});
