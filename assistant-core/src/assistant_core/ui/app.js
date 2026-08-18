/**
 * Assistant Core Web Dashboard
 * High-performance, zero-dependency client application.
 */

class DashboardApp {
  constructor() {
    this.activeTab = 'overview';
    this.authenticated = false;
    this.currentFileContent = '';
    this.debounceTimers = {};
    this.selectedMemories = new Set();
    this.selectedFiles = new Set();
    this.selectedConvs = new Set();
    this.batchDeleteType = null;

    this.init();
  }

  async init() {
    this.setupEventListeners();
    await this.checkAuth();
  }

  // ------------------------------------------------------------------------
  // API Fetch Helper
  // ------------------------------------------------------------------------
  async api(path, options = {}) {
    const defaultHeaders = {
      'Content-Type': 'application/json',
    };

    try {
      const res = await fetch(path, {
        ...options,
        headers: {
          ...defaultHeaders,
          ...options.headers,
        },
      });

      if (res.status === 401) {
        this.showAuthScreen();
        throw new Error('Unauthorized');
      }

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `Request failed with status ${res.status}`);
      }

      return await res.json();
    } catch (err) {
      if (err.message !== 'Unauthorized') {
        this.showToast(err.message, 'error');
      }
      throw err;
    }
  }

  // ------------------------------------------------------------------------
  // Authentication & Initialization
  // ------------------------------------------------------------------------
  async checkAuth() {
    try {
      const data = await this.api('/v1/admin/auth/check');
      if (data.authenticated) {
        this.showMainLayout();
        this.loadCurrentTab();
      }
    } catch {
      this.showAuthScreen();
    }
  }

  showAuthScreen() {
    this.authenticated = false;
    document.getElementById('auth-screen').style.display = 'flex';
    document.getElementById('main-layout').style.display = 'none';
  }

  showMainLayout() {
    this.authenticated = true;
    document.getElementById('auth-screen').style.display = 'none';
    document.getElementById('main-layout').style.display = 'flex';
  }

  setupEventListeners() {
    // Login form
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const token = document.getElementById('admin-token-input').value.trim();
      const btn = document.getElementById('login-btn');
      const errorDiv = document.getElementById('login-error');

      btn.disabled = true;
      btn.innerText = 'Signing in...';
      errorDiv.style.display = 'none';

      try {
        await this.api('/v1/admin/auth/login', {
          method: 'POST',
          body: JSON.stringify({ token }),
        });
        this.showMainLayout();
        this.loadCurrentTab();
        this.showToast('Successfully signed in', 'success');
      } catch (err) {
        errorDiv.innerText = err.message || 'Invalid admin token';
        errorDiv.style.display = 'block';
      } finally {
        btn.disabled = false;
        btn.innerText = 'Sign In';
      }
    });

    // Logout button
    document.getElementById('logout-btn').addEventListener('click', async () => {
      await this.api('/v1/admin/auth/logout', { method: 'POST' });
      this.showAuthScreen();
      this.showToast('Signed out', 'success');
    });

    // Tab navigation buttons
    document.querySelectorAll('.sidebar-nav .nav-item').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        this.switchTab(tab);
      });
    });

    // Refresh button
    document.getElementById('refresh-btn').addEventListener('click', () => {
      this.loadCurrentTab();
      this.showToast('Data refreshed', 'success');
    });

    // Debounced search filters
    document.getElementById('memories-search').addEventListener('input', (e) => {
      this.debounce('memories', () => this.loadMemories(e.target.value), 300);
    });

    document.getElementById('memories-category-filter').addEventListener('change', () => {
      this.loadMemories();
    });

    document.getElementById('memories-timeline-filter').addEventListener('change', () => {
      this.loadMemories();
    });

    document.getElementById('memories-status-filter').addEventListener('change', () => {
      this.loadMemories();
    });

    document.getElementById('files-search').addEventListener('input', (e) => {
      this.debounce('files', () => this.loadFiles(e.target.value), 300);
    });

    document.getElementById('files-status-filter').addEventListener('change', () => {
      this.loadFiles();
    });

    document.getElementById('conversations-search').addEventListener('input', (e) => {
      this.debounce('conversations', () => this.loadConversations(e.target.value), 300);
    });

    document.getElementById('jobs-status-filter').addEventListener('change', () => {
      this.loadJobs();
    });

    // Memory form submit
    document.getElementById('memory-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      await this.saveMemory();
    });

    // File modal tabs
    document.querySelectorAll('.modal-tabs .tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.modal-tabs .tab-btn').forEach((b) => b.classList.remove('active'));
        document.querySelectorAll('.file-tab-pane').forEach((p) => p.classList.remove('active'));

        btn.classList.add('active');
        const targetTab = btn.getAttribute('data-file-tab');
        document.getElementById(`file-tab-${targetTab}`).classList.add('active');
      });
    });
  }

  debounce(key, fn, delay) {
    clearTimeout(this.debounceTimers[key]);
    this.debounceTimers[key] = setTimeout(fn, delay);
  }

  // ------------------------------------------------------------------------
  // Tab Routing
  // ------------------------------------------------------------------------
  switchTab(tabName) {
    this.activeTab = tabName;

    document.querySelectorAll('.sidebar-nav .nav-item').forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
    });

    document.querySelectorAll('.tab-pane').forEach((pane) => {
      pane.classList.toggle('active', pane.id === `tab-${tabName}`);
    });

    const titles = {
      overview: { title: 'Overview', sub: 'Real-time telemetry and brain vitals.' },
      memories: { title: 'Memories', sub: 'Manage explicit facts, user preferences, and profile statements.' },
      files: { title: 'Documents', sub: 'Inspect indexed files, full markdown texts, and chunk segment trees.' },
      conversations: { title: 'Conversations', sub: 'Search and inspect indexed conversation turns.' },
      jobs: { title: 'Worker Jobs', sub: 'Monitor asynchronous indexing and re-queue dead jobs.' },
      consolidation: { title: 'Consolidation History', sub: 'Audit logs of AI conflict resolution, supersessions, and evolutionary memory diffs.' },
      artifacts: { title: 'Artifacts', sub: 'Inspect and launch web editing for generated spreadsheets and documents.' },
      playground: { title: 'Search Playground', sub: 'Interactive hybrid RRF retrieval tester and prompt context preview.' },
    };

    document.getElementById('page-title').innerText = titles[tabName]?.title || 'Dashboard';
    document.getElementById('page-subtitle').innerText = titles[tabName]?.sub || '';

    this.loadCurrentTab();
  }

  loadCurrentTab() {
    switch (this.activeTab) {
      case 'overview':
        this.loadOverview();
        break;
      case 'memories':
        this.loadMemoryCategories();
        this.loadMemories();
        break;
      case 'files':
        this.loadFiles();
        break;
      case 'conversations':
        this.loadConversations();
        break;
      case 'jobs':
        this.loadJobs();
        break;
      case 'consolidation':
        this.loadConsolidationRuns();
        break;
      case 'artifacts':
        this.loadArtifacts();
        break;
      case 'playground':
        break;
    }
  }

  // ------------------------------------------------------------------------
  // Overview Tab
  // ------------------------------------------------------------------------
  async loadOverview() {
    try {
      const data = await this.api('/v1/admin/overview');

      document.getElementById('stat-memories').innerText = data.memories.active;
      document.getElementById('stat-memories-sub').innerText = `${data.memories.total} total stored (${data.memories.archived} archived)`;

      document.getElementById('stat-files').innerText = data.files.active;
      document.getElementById('stat-files-sub').innerText = `${data.files.total_characters.toLocaleString()} chars across ${data.files.total} docs`;

      document.getElementById('stat-segments').innerText = data.files.total_segments;
      document.getElementById('stat-segments-sub').innerText = `${data.files.deduplicated_references} deduplicated chunk references`;

      if (data.artifacts) {
        document.getElementById('stat-artifacts').innerText = data.artifacts.active;
        document.getElementById('stat-artifacts-sub').innerText = `${data.artifacts.total_versions} total versions recorded`;
        document.getElementById('badge-artifacts').innerText = data.artifacts.active;
      }

      if (data.consolidation) {
        const consBadge = document.getElementById('badge-consolidation');
        if (consBadge) {
          consBadge.innerText = data.consolidation.total_runs;
        }
      }

      document.getElementById('stat-jobs').innerText = `${data.jobs.queued} queued`;
      document.getElementById('stat-jobs-sub').innerText = `${data.jobs.dead} dead / failed jobs`;

      // Update sidebar badges
      document.getElementById('badge-memories').innerText = data.memories.active;
      document.getElementById('badge-files').innerText = data.files.active;

      const deadBadge = document.getElementById('badge-dead-jobs');
      if (data.jobs.dead > 0) {
        deadBadge.innerText = `${data.jobs.dead} dead`;
        deadBadge.style.display = 'inline-block';
      } else {
        deadBadge.style.display = 'none';
      }
    } catch {
      // Error handled in api()
    }
  }

  // ------------------------------------------------------------------------
  // Selection & Bulk Actions
  // ------------------------------------------------------------------------
  getSelection(type) {
    if (type === 'memories') return this.selectedMemories;
    if (type === 'files') return this.selectedFiles;
    if (type === 'convs') return this.selectedConvs;
    return new Set();
  }

  toggleRowSelection(type, id, checked) {
    const selection = this.getSelection(type);
    if (checked) {
      selection.add(id);
    } else {
      selection.delete(id);
    }
    this.updateBulkBar(type);
  }

  toggleSelectAll(type, checked) {
    const selection = this.getSelection(type);
    const checkboxes = document.querySelectorAll(`#${type === 'convs' ? 'conversations' : type}-table-body .row-checkbox`);
    checkboxes.forEach((cb) => {
      cb.checked = checked;
      if (checked) {
        selection.add(cb.value);
      } else {
        selection.delete(cb.value);
      }
    });
    this.updateBulkBar(type);
  }

  clearSelection(type) {
    const selection = this.getSelection(type);
    selection.clear();
    const selectAll = document.getElementById(`${type}-select-all`);
    if (selectAll) selectAll.checked = false;
    const checkboxes = document.querySelectorAll(`#${type === 'convs' ? 'conversations' : type}-table-body .row-checkbox`);
    checkboxes.forEach((cb) => (cb.checked = false));
    this.updateBulkBar(type);
  }

  updateBulkBar(type) {
    const selection = this.getSelection(type);
    const bulkBar = document.getElementById(`${type}-bulk-bar`);
    const countEl = document.getElementById(`${type}-selected-count`);
    const selectAll = document.getElementById(`${type}-select-all`);

    if (bulkBar && countEl) {
      countEl.innerText = selection.size;
      bulkBar.style.display = selection.size > 0 ? 'flex' : 'none';
    }

    const checkboxes = document.querySelectorAll(`#${type === 'convs' ? 'conversations' : type}-table-body .row-checkbox`);
    if (selectAll && checkboxes.length > 0) {
      selectAll.checked = Array.from(checkboxes).every((cb) => cb.checked);
    }
  }

  openBatchDeleteModal(type) {
    const selection = this.getSelection(type);
    if (selection.size === 0) return;

    this.batchDeleteType = type;
    const titles = {
      memories: 'Memories',
      files: 'Documents',
      convs: 'Conversations',
    };
    document.getElementById('batch-delete-modal-title').innerText = `Delete ${titles[type] || 'Items'}`;
    document.getElementById('batch-delete-modal-msg').innerText = `Are you sure you want to permanently delete ${selection.size} selected ${type}? This action cannot be undone.`;
    document.getElementById('batch-delete-modal').style.display = 'flex';
  }

  closeBatchDeleteModal() {
    document.getElementById('batch-delete-modal').style.display = 'none';
    this.batchDeleteType = null;
  }

  async executeBatchDelete() {
    const type = this.batchDeleteType;
    if (!type) return;
    const selection = this.getSelection(type);
    const items = Array.from(selection);
    if (items.length === 0) return;

    const btn = document.getElementById('confirm-batch-delete-btn');
    btn.disabled = true;
    btn.innerText = 'Deleting...';

    try {
      if (type === 'memories') {
        await this.api('/v1/admin/memories/batch-delete', {
          method: 'POST',
          body: JSON.stringify({ ids: items }),
        });
        this.showToast(`Deleted ${items.length} memories`, 'success');
        this.clearSelection('memories');
        this.loadMemories();
      } else if (type === 'files') {
        await this.api('/v1/admin/files/batch-delete', {
          method: 'POST',
          body: JSON.stringify({ native_file_ids: items }),
        });
        this.showToast(`Deleted ${items.length} documents`, 'success');
        this.clearSelection('files');
        this.loadFiles();
      } else if (type === 'convs') {
        await this.api('/v1/admin/conversations/batch-delete', {
          method: 'POST',
          body: JSON.stringify({ ids: items }),
        });
        this.showToast(`Deleted ${items.length} conversation turns`, 'success');
        this.clearSelection('convs');
        this.loadConversations();
      }

      this.closeBatchDeleteModal();
      this.loadOverview();
    } catch {
      // Handled in api()
    } finally {
      btn.disabled = false;
      btn.innerText = 'Delete Permanently';
    }
  }

  // ------------------------------------------------------------------------
  // Double-Confirmation System Purge
  // ------------------------------------------------------------------------
  openPurgeModal() {
    document.getElementById('purge-scope-input').value = 'all';
    document.getElementById('purge-confirm-input').value = '';
    document.getElementById('confirm-purge-btn').disabled = true;
    this.updatePurgeScopeText();
    document.getElementById('purge-modal').style.display = 'flex';
  }

  closePurgeModal() {
    document.getElementById('purge-modal').style.display = 'none';
  }

  updatePurgeScopeText() {
    const scope = document.getElementById('purge-scope-input').value;
    const explanations = {
      all: 'You are about to permanently erase all indexed personal memories, files, chunk embeddings, conversation histories, and event logs from the database.',
      memories: 'You are about to permanently erase all user explicit memories, evidence provenance, and profile snapshots.',
      files: 'You are about to permanently erase all uploaded document records, unchunked texts, chunk segments, and 1536d embeddings.',
      conversations: 'You are about to permanently erase all recorded completed conversation turns, message links, and passage embeddings.',
      jobs: 'You are about to wipe all background worker job records and incoming event logs.',
    };
    document.getElementById('purge-scope-explanation').innerText = explanations[scope] || explanations.all;
  }

  onPurgeInput(val) {
    const btn = document.getElementById('confirm-purge-btn');
    btn.disabled = val.trim().toUpperCase() !== 'PURGE';
  }

  async executePurge() {
    const scope = document.getElementById('purge-scope-input').value;
    const confirmation = document.getElementById('purge-confirm-input').value.trim();
    if (confirmation.toUpperCase() !== 'PURGE') return;

    const btn = document.getElementById('confirm-purge-btn');
    btn.disabled = true;
    btn.innerText = 'Purging Database...';

    try {
      const res = await this.api('/v1/admin/system/purge', {
        method: 'POST',
        body: JSON.stringify({ confirmation, scope }),
      });

      this.showToast(`System purge completed for scope: ${res.scope}`, 'success');
      this.closePurgeModal();

      // Clear all local selection caches
      this.selectedMemories.clear();
      this.selectedFiles.clear();
      this.selectedConvs.clear();

      // Refresh everything
      this.loadOverview();
      this.loadCurrentTab();
    } catch {
      // Handled in api()
    } finally {
      btn.disabled = false;
      btn.innerText = 'Permanently Purge Data';
    }
  }

  // ------------------------------------------------------------------------
  // Memories Tab
  // ------------------------------------------------------------------------
  async loadMemoryCategories() {
    try {
      const data = await this.api('/v1/admin/memories/categories');
      if (!data.categories) return;

      const select = document.getElementById('memories-category-filter');
      if (select) {
        const currentVal = select.value;
        select.innerHTML = '<option value="">All Categories</option>' +
          data.categories.map((c) => `<option value="${this.escapeHtml(c)}">${this.escapeHtml(c.charAt(0).toUpperCase() + c.slice(1))}</option>`).join('');
        if (currentVal && data.categories.includes(currentVal)) {
          select.value = currentVal;
        }
      }

      const datalist = document.getElementById('memory-category-options');
      if (datalist) {
        datalist.innerHTML = data.categories.map((c) => `<option value="${this.escapeHtml(c)}">`).join('');
      }
    } catch (err) {
      console.warn('Failed to load memory categories:', err);
    }
  }

  async loadMemories(searchQuery = null) {
    const query = searchQuery !== null ? searchQuery : document.getElementById('memories-search').value;
    const category = document.getElementById('memories-category-filter').value;
    const timeline = document.getElementById('memories-timeline-filter')?.value || 'all';
    const status = document.getElementById('memories-status-filter').value;

    const tbody = document.getElementById('memories-table-body');
    tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Loading memories...</td></tr>';

    try {
      const params = new URLSearchParams({
        query: query || '',
        category: category || '',
        status_filter: status || 'active',
        timeline_filter: timeline,
        limit: 50,
      });

      const data = await this.api(`/v1/admin/memories?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">No memories found.</td></tr>';
        this.updateBulkBar('memories');
        return;
      }

      tbody.innerHTML = data.items
        .map(
          (m) => {
            let validityBadge = '<span class="badge" style="background:rgba(255,255,255,0.06);color:#94a3b8;">Permanent 🔒</span>';
            if (m.validity_status === 'expired') {
              validityBadge = `<span class="badge badge-tombstoned" title="Expired at ${this.formatDate(m.expires_at)}">Expired ⚪</span>`;
            } else if (m.validity_status === 'upcoming') {
              validityBadge = `<span class="badge badge-pending" title="Valid from ${this.formatDate(m.valid_from)}">Upcoming 📅</span>`;
            } else if (m.validity_status === 'active_expiring') {
              const tagStr = m.temporal_tag ? ` [${m.temporal_tag}]` : '';
              validityBadge = `<span class="badge badge-running" title="Expires ${this.formatDate(m.expires_at)}">Exp: ${this.formatDate(m.expires_at)}${tagStr} ⏳</span>`;
            }

            return `
        <tr>
          <td class="checkbox-cell">
            <input type="checkbox" class="row-checkbox" value="${m.id}" ${this.selectedMemories.has(m.id) ? 'checked' : ''} onchange="app.toggleRowSelection('memories', '${m.id}', this.checked)">
          </td>
          <td><strong>${this.escapeHtml(m.statement)}</strong></td>
          <td><span class="badge badge-${m.category}">${m.category}</span></td>
          <td><code>${this.escapeHtml(m.native_user_id)}</code></td>
          <td>${validityBadge}</td>
          <td><span class="badge badge-${m.state}">${m.state}</span></td>
          <td><small class="text-muted">${this.formatDate(m.created_at)}</small></td>
          <td>
            <div style="display: flex; gap: 6px;">
              <button class="btn-icon" title="Edit" onclick="app.openEditMemoryModal('${m.id}', '${this.escapeJsString(m.statement)}', '${m.category}', ${m.confidence}, '${this.escapeJsString(m.native_user_id)}', '${m.expires_at || ''}', '${m.temporal_tag || ''}')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              </button>
              ${m.state === 'active' ? `
                <button class="btn-icon btn-danger-icon" title="Delete / Deactivate" onclick="app.deleteMemory('${m.id}')">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              ` : ''}
            </div>
          </td>
        </tr>
      `;
          }
        )
        .join('');

      this.updateBulkBar('memories');
    } catch {
      tbody.innerHTML = '<tr><td colspan="8" class="loading-cell text-danger">Failed to load memories.</td></tr>';
    }
  }

  openNewMemoryModal() {
    document.getElementById('memory-modal-title').innerText = 'New Memory';
    document.getElementById('memory-form-id').value = '';
    document.getElementById('memory-user-input').value = 'user-1';
    document.getElementById('memory-user-input').disabled = false;
    document.getElementById('memory-statement-input').value = '';
    document.getElementById('memory-category-input').value = 'preference';
    document.getElementById('memory-confidence-input').value = '1.0';
    document.getElementById('memory-expires-at-input').value = '';
    document.getElementById('memory-temporal-tag-input').value = '';
    document.getElementById('memory-evidence-input').value = '';
    document.getElementById('memory-modal').style.display = 'flex';
  }

  openEditMemoryModal(id, statement, category, confidence, userId, expiresAt = '', temporalTag = '') {
    document.getElementById('memory-modal-title').innerText = 'Edit Memory';
    document.getElementById('memory-form-id').value = id;
    document.getElementById('memory-user-input').value = userId;
    document.getElementById('memory-user-input').disabled = true;
    document.getElementById('memory-statement-input').value = statement;
    document.getElementById('memory-category-input').value = category;
    document.getElementById('memory-confidence-input').value = confidence;
    document.getElementById('memory-expires-at-input').value = expiresAt ? expiresAt.substring(0, 16) : '';
    document.getElementById('memory-temporal-tag-input').value = temporalTag || '';
    document.getElementById('memory-evidence-input').value = '';
    document.getElementById('memory-modal').style.display = 'flex';
  }

  closeMemoryModal() {
    document.getElementById('memory-modal').style.display = 'none';
  }

  async saveMemory() {
    const id = document.getElementById('memory-form-id').value;
    const userId = document.getElementById('memory-user-input').value.trim();
    const statement = document.getElementById('memory-statement-input').value.trim();
    const category = document.getElementById('memory-category-input').value;
    const confidence = parseFloat(document.getElementById('memory-confidence-input').value);
    const expiresAtRaw = document.getElementById('memory-expires-at-input').value;
    const expiresAt = expiresAtRaw ? new Date(expiresAtRaw).toISOString() : null;
    const temporalTag = document.getElementById('memory-temporal-tag-input').value || null;
    const evidence = document.getElementById('memory-evidence-input').value.trim() || null;

    try {
      if (id) {
        await this.api(`/v1/admin/memories/${id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            statement,
            category,
            expires_at: expiresAt,
            temporal_tag: temporalTag,
          }),
        });
        this.showToast('Memory updated', 'success');
      } else {
        await this.api('/v1/admin/memories', {
          method: 'POST',
          body: JSON.stringify({
            native_user_id: userId,
            statement,
            category,
            confidence,
            expires_at: expiresAt,
            temporal_tag: temporalTag,
            evidence_quote: evidence,
          }),
        });
        this.showToast('Memory created', 'success');
      }
      this.closeMemoryModal();
      this.loadMemories();
    } catch {
      // Error handled in api()
    }
  }

  async deleteMemory(id) {
    if (!confirm('Are you sure you want to deactivate this memory?')) return;
    try {
      await this.api(`/v1/admin/memories/${id}`, { method: 'DELETE' });
      this.showToast('Memory deactivated', 'success');
      this.loadMemories();
    } catch {
      // Error handled in api()
    }
  }

  // ------------------------------------------------------------------------
  // Documents Tab
  // ------------------------------------------------------------------------
  async loadFiles(searchQuery = null) {
    const query = searchQuery !== null ? searchQuery : document.getElementById('files-search').value;
    const status = document.getElementById('files-status-filter').value;

    const tbody = document.getElementById('files-table-body');
    tbody.innerHTML = '<tr><td colspan="9" class="loading-cell">Loading documents...</td></tr>';

    try {
      const params = new URLSearchParams({
        query: query || '',
        status_filter: status || 'active',
        limit: 50,
      });

      const data = await this.api(`/v1/admin/files?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="loading-cell">No documents found.</td></tr>';
        this.updateBulkBar('files');
        return;
      }

      tbody.innerHTML = data.items
        .map(
          (f) => `
        <tr>
          <td class="checkbox-cell">
            <input type="checkbox" class="row-checkbox" value="${f.native_file_id}" ${this.selectedFiles.has(f.native_file_id) ? 'checked' : ''} onchange="app.toggleRowSelection('files', '${f.native_file_id}', this.checked)">
          </td>
          <td><strong>${this.escapeHtml(f.filename)}</strong><br><code class="text-muted" style="font-size: 11px;">${f.native_file_id}</code></td>
          <td><span class="badge">${f.mime_type || 'unknown'}</span></td>
          <td><code>${this.escapeHtml(f.native_user_id)}</code></td>
          <td><strong>${f.total_chunks}</strong></td>
          <td>${f.total_characters.toLocaleString()}</td>
          <td><span class="badge badge-${f.status}">${f.status}</span></td>
          <td><small class="text-muted">${this.formatDate(f.created_at)}</small></td>
          <td>
            <div style="display: flex; gap: 6px;">
              <button class="btn btn-secondary btn-sm" onclick="app.viewFileDetail('${f.native_file_id}')">
                Inspect
              </button>
              ${f.status === 'active' ? `
                <button class="btn-icon btn-danger-icon" title="Delete / Tombstone" onclick="app.deleteFile('${f.native_file_id}')">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              ` : ''}
            </div>
          </td>
        </tr>
      `
        )
        .join('');

      this.updateBulkBar('files');
    } catch {
      tbody.innerHTML = '<tr><td colspan="9" class="loading-cell text-danger">Failed to load documents.</td></tr>';
    }
  }

  async viewFileDetail(nativeFileId) {
    try {
      const data = await this.api(`/v1/admin/files/${nativeFileId}`);

      document.getElementById('file-modal-title').innerText = data.filename;
      document.getElementById('file-modal-subtitle').innerText = `ID: ${data.native_file_id} | ${data.total_chunks} chunks | ${data.total_characters.toLocaleString()} characters`;

      this.currentFileContent = data.content || '';
      document.getElementById('file-raw-content').innerText = data.content || 'No text extracted.';

      // Render markdown using Marked.js if available
      const renderedContainer = document.getElementById('file-tab-rendered');
      if (window.marked) {
        renderedContainer.innerHTML = marked.parse(data.content || '*No content*');
      } else {
        renderedContainer.innerText = data.content || 'No content';
      }

      // Render chunk breakdown
      document.getElementById('file-chunks-count').innerText = data.chunks?.length || 0;
      const chunksList = document.getElementById('file-chunks-list');
      if (data.chunks && data.chunks.length > 0) {
        chunksList.innerHTML = data.chunks
          .map(
            (c) => `
          <div class="chunk-card">
            <div class="chunk-header">
              <span>Chunk #${c.ordinal} — ${this.escapeHtml(c.header_path || 'Top Level')}</span>
              <span class="badge ${c.has_embedding ? 'badge-completed' : 'badge-queued'}">${c.has_embedding ? '1536d Embedded' : 'Lexical Only'}</span>
            </div>
            <div class="chunk-hash">SHA: ${c.content_sha256} | Length: ${c.char_length} chars</div>
          </div>
        `
          )
          .join('');
      } else {
        chunksList.innerHTML = '<p class="text-muted">No individual chunks found.</p>';
      }

      document.getElementById('file-modal').style.display = 'flex';
    } catch {
      // Error handled in api()
    }
  }

  closeFileModal() {
    document.getElementById('file-modal').style.display = 'none';
  }

  copyFileContent() {
    if (this.currentFileContent) {
      navigator.clipboard.writeText(this.currentFileContent);
      this.showToast('Document text copied to clipboard', 'success');
    }
  }

  async deleteFile(nativeFileId) {
    if (!confirm('Are you sure you want to delete and tombstone this document?')) return;
    try {
      await this.api(`/v1/admin/files/${nativeFileId}`, { method: 'DELETE' });
      this.showToast('Document tombstoned', 'success');
      this.loadFiles();
    } catch {
      // Error handled in api()
    }
  }

  // ------------------------------------------------------------------------
  // Conversations Tab
  // ------------------------------------------------------------------------
  async loadConversations(searchQuery = null) {
    const query = searchQuery !== null ? searchQuery : document.getElementById('conversations-search').value;
    const tbody = document.getElementById('conversations-table-body');
    tbody.innerHTML = '<tr><td colspan="6" class="loading-cell">Loading conversations...</td></tr>';

    try {
      const params = new URLSearchParams({
        query: query || '',
        limit: 50,
      });

      const data = await this.api(`/v1/admin/conversations?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="loading-cell">No conversations found.</td></tr>';
        this.updateBulkBar('convs');
        return;
      }

      tbody.innerHTML = data.items
        .map(
          (c) => `
        <tr>
          <td class="checkbox-cell">
            <input type="checkbox" class="row-checkbox" value="${c.id}" ${this.selectedConvs.has(c.id) ? 'checked' : ''} onchange="app.toggleRowSelection('convs', '${c.id}', this.checked)">
          </td>
          <td><div style="max-width: 300px; word-break: break-word;">${this.escapeHtml(c.user_content)}</div></td>
          <td><div style="max-width: 340px; word-break: break-word; color: var(--text-muted);">${this.escapeHtml(c.assistant_content)}</div></td>
          <td><code>${this.escapeHtml(c.native_chat_id || 'unknown')}</code></td>
          <td><code>${this.escapeHtml(c.native_user_id)}</code></td>
          <td><small class="text-muted">${this.formatDate(c.occurred_at)}</small></td>
        </tr>
      `
        )
        .join('');

      this.updateBulkBar('convs');
    } catch {
      tbody.innerHTML = '<tr><td colspan="6" class="loading-cell text-danger">Failed to load conversations.</td></tr>';
    }
  }

  // ------------------------------------------------------------------------
  // Background Jobs Tab
  // ------------------------------------------------------------------------
  async loadJobs() {
    const status = document.getElementById('jobs-status-filter').value;
    const tbody = document.getElementById('jobs-table-body');
    tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">Loading background jobs...</td></tr>';

    try {
      const params = new URLSearchParams({
        status_filter: status || '',
        limit: 50,
      });

      const data = await this.api(`/v1/admin/jobs?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">No background jobs found.</td></tr>';
        return;
      }

      tbody.innerHTML = data.items
        .map(
          (j) => `
        <tr>
          <td><code>${j.id.slice(0, 8)}...</code></td>
          <td><strong>${j.kind}</strong><br><small class="text-muted">${j.identity_key}</small></td>
          <td><span class="badge badge-${j.status}">${j.status}</span></td>
          <td>${j.attempts} / ${j.max_attempts}</td>
          <td><div style="max-width: 280px; font-family: var(--font-mono); font-size: 11px; color: var(--danger); word-break: break-all;">${j.last_error ? this.escapeHtml(j.last_error) : '—'}</div></td>
          <td><small class="text-muted">${this.formatDate(j.created_at)}</small></td>
          <td>
            ${j.status === 'dead' ? `
              <button class="btn btn-primary btn-sm" onclick="app.retryJob('${j.id}')">
                ⚡ Retry
              </button>
            ` : '—'}
          </td>
        </tr>
      `
        )
        .join('');
    } catch {
      tbody.innerHTML = '<tr><td colspan="7" class="loading-cell text-danger">Failed to load jobs.</td></tr>';
    }
  }

  async retryJob(jobId) {
    try {
      await this.api(`/v1/admin/jobs/${jobId}/retry`, { method: 'POST' });
      this.showToast('Job re-queued successfully', 'success');
      this.loadJobs();
      this.loadOverview();
    } catch {
      // Error handled in api()
    }
  }

  // ------------------------------------------------------------------------
  // Memory Consolidation & History
  // ------------------------------------------------------------------------
  openConsolidationModal() {
    document.getElementById('consolidation-user-id').value = '';
    document.getElementById('consolidation-results-box').style.display = 'none';
    const btn = document.getElementById('start-consolidation-btn');
    btn.disabled = false;
    btn.innerText = 'Run Consolidation';
    document.getElementById('consolidation-modal').style.display = 'flex';
  }

  closeConsolidationModal() {
    document.getElementById('consolidation-modal').style.display = 'none';
  }

  async executeConsolidation() {
    const userId = document.getElementById('consolidation-user-id').value.trim() || null;
    const btn = document.getElementById('start-consolidation-btn');
    btn.disabled = true;
    btn.innerText = 'Consolidating Memories...';

    const resultsBox = document.getElementById('consolidation-results-box');
    const resultsTitle = document.getElementById('consolidation-results-title');
    const resultsList = document.getElementById('consolidation-results-list');

    try {
      const res = await this.api('/v1/admin/memories/consolidate', {
        method: 'POST',
        body: JSON.stringify({ native_user_id: userId }),
      });

      this.showToast(`Consolidation finished: ${res.total_superseded} memories updated`, 'success');
      resultsTitle.innerText = `Processed ${res.consolidated_users} user(s) — ${res.total_superseded} conflicts resolved`;

      if (res.details && res.details.length > 0) {
        resultsList.innerHTML = res.details
          .map((d) => {
            if (d.type === 'validity_update') {
              return `
                <div style="margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border-subtle);">
                  <div><span class="badge badge-running" style="font-size: 10px;">Validity: ${this.escapeHtml(d.action)}</span> <strong>${this.escapeHtml(d.statement)}</strong></div>
                  <div style="font-size: 12px; color: var(--text-secondary); margin: 3px 0;">Validity: <s>${this.escapeHtml(d.old_validity)}</s> ➔ <span class="text-success">${this.escapeHtml(d.new_validity)}</span> ${d.temporal_tag ? `<span class="badge">[${this.escapeHtml(d.temporal_tag)}]</span>` : ''}</div>
                  <div class="text-muted" style="font-size: 11px;">💡 ${this.escapeHtml(d.reason)}</div>
                </div>
              `;
            } else if (d.type === 'reclassification') {
              return `
                <div style="margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border-subtle);">
                  <div><span class="badge badge-${d.new_category || 'category'}" style="font-size: 10px;">Reclassified</span> <strong>${this.escapeHtml(d.statement)}</strong></div>
                  <div style="font-size: 12px; color: var(--text-secondary); margin: 3px 0;">Category: <s>${this.escapeHtml(d.old_category)}</s> ➔ <span class="badge badge-${d.new_category || 'category'}">${this.escapeHtml(d.new_category)}</span></div>
                  <div class="text-muted" style="font-size: 11px;">💡 ${this.escapeHtml(d.reason)}</div>
                </div>
              `;
            } else {
              return `
                <div style="margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border-subtle);">
                  <div><span class="badge badge-tombstoned" style="font-size: 10px;">Superseded</span> <s class="text-danger">${this.escapeHtml(d.superseded_statement || '')}</s></div>
                  <div class="text-success" style="margin: 3px 0;">↳ ${this.escapeHtml(d.superseding_statement || '')}</div>
                  <div class="text-muted" style="font-size: 11px;">💡 ${this.escapeHtml(d.reason)}</div>
                </div>
              `;
            }
          })
          .join('');
      } else {
        resultsList.innerHTML = '<p class="text-muted">No conflicting, temporary, or misclassified memories found.</p>';
      }

      resultsBox.style.display = 'block';
      this.loadMemories();
      this.loadConsolidationRuns();
      this.loadOverview();
    } catch (err) {
      resultsTitle.innerText = 'Consolidation Failed';
      resultsList.innerHTML = `<div class="card" style="background: hsla(0, 84%, 60%, 0.1); border-color: var(--danger); padding: 12px; color: var(--danger);">${this.escapeHtml(err.message || 'Server error')}</div>`;
      resultsBox.style.display = 'block';
    } finally {
      btn.disabled = false;
      btn.innerText = 'Run Consolidation Again';
    }
  }

  async loadConsolidationRuns(page = 1) {
    const user = document.getElementById('consolidation-user-filter')?.value.trim() || '';
    const trigger = document.getElementById('consolidation-trigger-filter')?.value || '';

    const tbody = document.getElementById('consolidation-table-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Loading consolidation history...</td></tr>';

    try {
      const params = new URLSearchParams({
        page: page,
        page_size: 50,
      });
      if (user) params.set('native_user_id', user);
      if (trigger) params.set('trigger', trigger);

      const data = await this.api(`/v1/admin/consolidation-runs?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">No consolidation runs recorded yet.</td></tr>';
        return;
      }

      const triggerLabels = {
        manual_admin: '<span class="badge">👤 Manual Admin</span>',
        worker_daily: '<span class="badge badge-category">🤖 Daily Worker</span>',
      };

      const statusBadges = {
        success: '<span class="badge badge-active">Success</span>',
        no_changes: '<span class="badge">No Changes</span>',
        failed: '<span class="badge badge-tombstoned">Failed</span>',
      };

      tbody.innerHTML = data.items
        .map(
          (r) => `
        <tr>
          <td><small>${this.formatDate(r.created_at)}</small></td>
          <td>${triggerLabels[r.trigger] || `<span class="badge">${r.trigger}</span>`}</td>
          <td><code>${this.escapeHtml(r.native_user_id)}</code></td>
          <td>${statusBadges[r.status] || `<span class="badge">${r.status}</span>`}</td>
          <td><strong>${r.memories_scanned}</strong></td>
          <td>${r.superseded_count > 0 ? `<strong class="text-success">${r.superseded_count} updated</strong>` : '<span class="text-muted">0</span>'}</td>
          <td><small class="text-muted">${r.duration_ms}ms</small></td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick="app.openConsolidationDiffModal('${r.id}')">
              Inspect Diff 🔍
            </button>
          </td>
        </tr>
      `
        )
        .join('');
    } catch {
      tbody.innerHTML = '<tr><td colspan="8" class="loading-cell text-danger">Failed to load consolidation runs.</td></tr>';
    }
  }

  async openConsolidationDiffModal(runId) {
    try {
      const data = await this.api(`/v1/admin/consolidation-runs/${runId}`);

      const triggerLabel = data.trigger === 'manual_admin' ? 'Manual Admin 👤' : (data.trigger === 'worker_daily' ? 'Daily Background Worker 🤖' : data.trigger);

      const metaBar = document.getElementById('diff-modal-meta-bar');
      metaBar.innerHTML = `
        <div class="diff-meta-item"><span class="diff-meta-label">User ID:</span> <span class="diff-meta-value"><code>${this.escapeHtml(data.native_user_id)}</code></span></div>
        <div class="diff-meta-item"><span class="diff-meta-label">Trigger:</span> <span class="diff-meta-value">${triggerLabel}</span></div>
        <div class="diff-meta-item"><span class="diff-meta-label">Executed At:</span> <span class="diff-meta-value">${this.formatDate(data.created_at)}</span></div>
        <div class="diff-meta-item"><span class="diff-meta-label">Memories Scanned:</span> <span class="diff-meta-value">${data.memories_scanned}</span></div>
        <div class="diff-meta-item"><span class="diff-meta-label">Updates Applied:</span> <span class="diff-meta-value ${data.superseded_count > 0 ? 'text-success' : ''}">${data.superseded_count}</span></div>
        <div class="diff-meta-item"><span class="diff-meta-label">Duration:</span> <span class="diff-meta-value">${data.duration_ms}ms</span></div>
      `;

      const changesList = document.getElementById('diff-modal-changes-list');
      if (!data.details || data.details.length === 0) {
        changesList.innerHTML = `
          <div class="diff-empty-state">
            <p><strong>No contradictions or updates detected during this run.</strong></p>
            <p class="text-secondary" style="font-size: 12px; margin-top: 6px;">All ${data.memories_scanned} evaluated active memories were verified to be mutually consistent and accurately classified.</p>
          </div>
        `;
      } else {
        changesList.innerHTML = data.details
          .map((d, idx) => {
            if (d.type === 'validity_update') {
              return `
                <div class="diff-card">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span class="badge badge-running" style="font-weight: 700;">Resolution #${idx + 1} — ⏳ Validity Adjusted (${this.escapeHtml(d.action || 'update')})</span>
                  </div>
                  <div style="margin-bottom: 10px;"><strong>${this.escapeHtml(d.statement || '')}</strong></div>
                  <div class="diff-comparison-row">
                    <div class="diff-box diff-box-superseded">
                      <div class="diff-box-header">Previous Validity</div>
                      <div><code>${this.escapeHtml(d.old_validity || 'permanent')}</code></div>
                    </div>
                    <div class="diff-box diff-box-superseding">
                      <div class="diff-box-header">Updated Validity</div>
                      <div><code>${this.escapeHtml(d.new_validity || 'none')}</code> ${d.temporal_tag ? `<span class="badge badge-category" style="margin-left: 6px;">${this.escapeHtml(d.temporal_tag)}</span>` : ''}</div>
                    </div>
                  </div>
                  <div class="diff-reason-box">
                    <div class="diff-reason-title">💡 AI Decision Rationale</div>
                    <div>${this.escapeHtml(d.reason || '')}</div>
                  </div>
                </div>
              `;
            } else if (d.type === 'reclassification') {
              return `
                <div class="diff-card">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span class="badge badge-${d.new_category || 'category'}" style="font-weight: 700;">Resolution #${idx + 1} — 🏷️ Category Reclassified</span>
                  </div>
                  <div style="margin-bottom: 10px;"><strong>${this.escapeHtml(d.statement || '')}</strong></div>
                  <div class="diff-comparison-row">
                    <div class="diff-box diff-box-superseded">
                      <div class="diff-box-header">Previous Category</div>
                      <div><span class="badge badge-${d.old_category || 'category'}">${this.escapeHtml(d.old_category || '')}</span></div>
                    </div>
                    <div class="diff-box diff-box-superseding">
                      <div class="diff-box-header">New Domain Category</div>
                      <div><span class="badge badge-${d.new_category || 'category'}">${this.escapeHtml(d.new_category || '')}</span></div>
                    </div>
                  </div>
                  <div class="diff-reason-box">
                    <div class="diff-reason-title">💡 AI Decision Rationale</div>
                    <div>${this.escapeHtml(d.reason || '')}</div>
                  </div>
                </div>
              `;
            } else {
              return `
                <div class="diff-card">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span class="badge badge-fact" style="font-weight: 700;">Resolution #${idx + 1} — 🔄 Supersession</span>
                  </div>
                  <div class="diff-comparison-row">
                    <div class="diff-box diff-box-superseded">
                      <div class="diff-box-header">🔴 Superseded (Obsolete)</div>
                      <div>${this.escapeHtml(d.superseded_statement || '')}</div>
                    </div>
                    <div class="diff-box diff-box-superseding">
                      <div class="diff-box-header">🟢 Superseding (Authoritative)</div>
                      <div>${this.escapeHtml(d.superseding_statement || '')}</div>
                    </div>
                  </div>
                  <div class="diff-reason-box">
                    <div class="diff-reason-title">💡 AI Decision Rationale</div>
                    <div>${this.escapeHtml(d.reason || '')}</div>
                  </div>
                </div>
              `;
            }
          })
          .join('');
      }

      document.getElementById('consolidation-diff-modal').style.display = 'flex';
    } catch (err) {
      this.showToast(`Failed to load consolidation run: ${err.message}`, 'error');
    }
  }

  closeConsolidationDiffModal() {
    document.getElementById('consolidation-diff-modal').style.display = 'none';
  }

  // ------------------------------------------------------------------------
  // Knowledge Base Document Reconciliation & History
  // ------------------------------------------------------------------------
  async triggerKBReconciliation() {
    this.showToast('Starting Knowledge Base reconciliation scan...', 'info');
    try {
      const res = await this.api('/v1/admin/files/reconcile-kb', { method: 'POST' });
      if (res.status === 'failed') {
        this.showToast(`Reconciliation failed: ${res.error_message || 'Unknown error'}`, 'error');
      } else if (res.pruned_count > 0) {
        this.showToast(`Reconciliation complete: Pruned ${res.pruned_count} Knowledge Base document(s)`, 'success');
      } else {
        this.showToast(`Reconciliation complete: All ${res.kb_files_scanned} KB files scanned, no duplicates found`, 'success');
      }
      this.loadFiles(1);
      this.loadOverview();
      const modal = document.getElementById('kb-reconciliation-modal');
      if (modal && modal.style.display === 'flex') {
        this.loadKBReconciliationRuns(1);
      }
    } catch (err) {
      this.showToast(`Reconciliation error: ${err.message}`, 'error');
    }
  }

  openKBReconciliationLogsModal() {
    document.getElementById('kb-reconciliation-modal').style.display = 'flex';
    document.getElementById('kb-run-details-box').style.display = 'none';
    this.loadKBReconciliationRuns(1);
  }

  closeKBReconciliationLogsModal() {
    document.getElementById('kb-reconciliation-modal').style.display = 'none';
  }

  async loadKBReconciliationRuns(page = 1) {
    const tbody = document.getElementById('kb-runs-table-body');
    const pagination = document.getElementById('kb-runs-pagination');
    const triggerFilter = document.getElementById('kb-runs-trigger-filter')?.value || 'all';

    tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">Loading reconciliation logs...</td></tr>';

    try {
      const params = new URLSearchParams({ page: page.toString(), page_size: '10' });
      if (triggerFilter !== 'all') {
        params.set('trigger', triggerFilter);
      }

      const data = await this.api(`/v1/admin/files/reconcile-kb/runs?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">No reconciliation runs recorded yet.</td></tr>';
        pagination.innerHTML = '';
        return;
      }

      tbody.innerHTML = data.items
        .map((r) => {
          const triggerBadge = r.trigger === 'manual_admin'
            ? '<span class="badge" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8;">👤 Manual Admin</span>'
            : '<span class="badge" style="background: rgba(168, 85, 247, 0.15); color: #a855f7;">🤖 Hourly Worker</span>';

          const statusBadge = r.status === 'success'
            ? '<span class="badge badge-success">Success</span>'
            : (r.status === 'no_changes'
              ? '<span class="badge" style="background: rgba(100, 116, 139, 0.2); color: #94a3b8;">No Changes</span>'
              : '<span class="badge badge-danger">Failed</span>');

          return `
            <tr>
              <td><code>${this.formatDate(r.created_at)}</code></td>
              <td>${triggerBadge}</td>
              <td>${statusBadge}</td>
              <td><strong>${r.kb_files_scanned}</strong></td>
              <td><span class="${r.pruned_count > 0 ? 'text-success font-semibold' : 'text-muted'}">${r.pruned_count}</span></td>
              <td><small class="text-muted">${r.duration_ms}ms</small></td>
              <td>
                <button class="btn btn-secondary btn-sm" onclick="app.viewKBRunDetails('${r.id}')">
                  ${r.pruned_count > 0 ? '🔍 Inspect Pruned (' + r.pruned_count + ')' : 'View Details'}
                </button>
              </td>
            </tr>
          `;
        })
        .join('');

      if (pagination) {
        pagination.innerHTML = `<small class="text-muted">Showing ${data.items.length} of ${data.total} run(s)</small>`;
      }
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" class="loading-cell text-danger">Failed to load reconciliation logs: ${this.escapeHtml(err.message)}</td></tr>`;
    }
  }

  async viewKBRunDetails(runId) {
    const detailsBox = document.getElementById('kb-run-details-box');
    const detailsTitle = document.getElementById('kb-run-details-title');
    const detailsList = document.getElementById('kb-run-details-list');

    try {
      const data = await this.api(`/v1/admin/files/reconcile-kb/runs/${runId}`);
      detailsBox.style.display = 'block';
      detailsTitle.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>Run <code>${data.id.slice(0, 8)}</code> Details — ${data.pruned_count} document(s) pruned</span>
          <small class="text-muted">${data.kb_files_scanned} KB file(s) evaluated</small>
        </div>
      `;

      if (data.status === 'failed') {
        detailsList.innerHTML = `<div class="text-danger" style="padding: 8px;"><strong>Failure Error:</strong> ${this.escapeHtml(data.error_message || 'Unknown error')}</div>`;
        return;
      }

      if (!data.details || data.details.length === 0) {
        detailsList.innerHTML = `
          <div style="padding: 10px; color: var(--text-secondary);">
            <p><strong>Scan Completed:</strong> 0 duplicate files found in Assistant Core Documents.</p>
            <p style="font-size: 11px; margin-top: 4px;">Total KB / Vault files identified during scan: <strong>${data.kb_files_scanned}</strong>.</p>
          </div>
        `;
        return;
      }

      detailsList.innerHTML = data.details
        .map(
          (d, idx) => `
        <div style="padding: 8px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center;">
          <div>
            <strong>#${idx + 1} ${this.escapeHtml(d.filename)}</strong><br>
            <small class="text-muted">ID: <code>${this.escapeHtml(d.native_file_id)}</code> | Match: <span class="badge" style="background: rgba(239, 68, 68, 0.15); color: #ef4444;">${this.escapeHtml(d.match_type)}</span></small>
          </div>
          <div style="text-align: right;">
            <span class="badge badge-danger">Pruned from Docs</span>
          </div>
        </div>
      `
        )
        .join('');
    } catch (err) {
      this.showToast(`Failed to load run details: ${err.message}`, 'error');
    }
  }

  // ------------------------------------------------------------------------
  // Search Playground
  // ------------------------------------------------------------------------
  async runPlaygroundSearch() {
    const query = document.getElementById('playground-query').value.trim();
    if (!query) {
      this.showToast('Please enter a search query', 'error');
      return;
    }

    const userId = document.getElementById('playground-user-id').value.trim() || 'user-1';
    const sourceType = document.getElementById('playground-source-type').value;
    const limit = parseInt(document.getElementById('playground-limit').value, 10) || 8;

    const btn = document.getElementById('playground-search-btn');
    btn.disabled = true;
    btn.innerText = 'Searching...';

    try {
      const data = await this.api('/v1/admin/playground/search', {
        method: 'POST',
        body: JSON.stringify({
          native_user_id: userId,
          query,
          limit,
          source_type: sourceType,
        }),
      });

      this.renderPlaygroundResults(data);
    } catch {
      // Handled in api()
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Search`;
    }
  }

  renderPlaygroundResults(data) {
    const statusBar = document.getElementById('playground-status-bar');
    const modeBadge = document.getElementById('playground-mode-badge');
    const statsText = document.getElementById('playground-stats-text');
    const resultsContainer = document.getElementById('playground-results-container');
    const matchesList = document.getElementById('playground-matches-list');
    const renderedPreview = document.getElementById('playground-rendered-preview');

    statusBar.style.display = 'flex';
    resultsContainer.style.display = 'grid';

    modeBadge.className = data.mode === 'hybrid' ? 'badge badge-running' : 'badge badge-queued';
    modeBadge.innerText = data.mode === 'hybrid' ? '⚡ Hybrid RRF (Vector + Lexical)' : '🔍 Lexical Only';
    statsText.innerText = `${data.total_results} results retrieved in ${data.duration_ms}ms for user "${data.native_user_id}"`;

    this.currentPlaygroundContext = data.rendered_llm_block || '';
    renderedPreview.innerText = data.rendered_llm_block || '/* No context generated */';

    if (!data.results || data.results.length === 0) {
      matchesList.innerHTML = '<div class="card"><p class="text-muted">No matching memories, documents, or conversation turns found.</p></div>';
      return;
    }

    const typeIcons = {
      memory: '🧠 Memory',
      file: '📁 Document',
      conversation: '💬 Conversation',
    };

    matchesList.innerHTML = data.results
      .map(
        (r, idx) => `
      <div class="playground-match-card">
        <div class="playground-match-header">
          <div class="playground-match-rank">
            <span>#${idx + 1}</span>
            <span class="badge badge-${r.category || r.source_type}">${typeIcons[r.source_type] || r.source_type}</span>
          </div>
          <span class="rrf-chip" title="Reciprocal Rank Fusion Score">RRF ${r.rrf_score.toFixed(4)}</span>
        </div>
        <div class="playground-match-text">${this.escapeHtml(r.statement)}</div>
        <div class="playground-match-meta">
          ${r.source_type === 'file' ? `<span>📄 ${this.escapeHtml(r.metadata?.filename || 'doc')} (chunk #${r.metadata?.chunk_ordinal})</span>` : ''}
          ${r.source_type === 'conversation' ? `<span>💬 ${this.escapeHtml(r.role || 'user')} turn in chat <code>${this.escapeHtml(r.metadata?.native_chat_id || '')}</code></span>` : ''}
          ${r.source_type === 'memory' ? `<span>🔑 <code>${this.escapeHtml(r.metadata?.key || '')}</code></span>` : ''}
          ${r.vector_score !== null ? `<span>• Cosine Sim: <strong>${r.vector_score}</strong></span>` : ''}
          ${r.lexical_rank ? `<span>• Lexical Rank: #${r.lexical_rank}</span>` : ''}
        </div>
      </div>
    `
      )
      .join('');
  }

  copyPlaygroundContext() {
    if (this.currentPlaygroundContext) {
      navigator.clipboard.writeText(this.currentPlaygroundContext);
      this.showToast('Prompt context copied to clipboard', 'success');
    }
  }

  // ------------------------------------------------------------------------
  // Artifacts Tab
  // ------------------------------------------------------------------------
  async loadArtifacts(search = '') {
    const grid = document.getElementById('artifacts-grid');
    grid.innerHTML = '<div class="loading-state">Loading artifacts...</div>';

    try {
      const data = await this.api('/v1/admin/artifacts?limit=100');
      this.artifactsList = data.artifacts || [];
      this.renderArtifacts();
    } catch {
      grid.innerHTML = '<div class="empty-state">Failed to load artifacts.</div>';
    }
  }

  setArtifactTypeFilter(type) {
    this.currentArtifactTypeFilter = type;
    document.querySelectorAll('[data-artifact-type]').forEach((pill) => {
      pill.classList.toggle('active', pill.getAttribute('data-artifact-type') === type);
    });
    this.renderArtifacts();
  }

  filterArtifacts() {
    this.renderArtifacts();
  }

  renderArtifacts() {
    const grid = document.getElementById('artifacts-grid');
    if (!this.artifactsList || this.artifactsList.length === 0) {
      grid.innerHTML = '<div class="empty-state">No generated artifacts found. Create documents or spreadsheets from chat!</div>';
      return;
    }

    const query = (document.getElementById('artifacts-search-input')?.value || '').toLowerCase().trim();
    const typeFilter = this.currentArtifactTypeFilter || 'all';

    const filtered = this.artifactsList.filter((art) => {
      const matchesType = typeFilter === 'all' || art.artifact_type.toLowerCase() === typeFilter.toLowerCase();
      const matchesQuery = !query || art.title.toLowerCase().includes(query) || art.slug.toLowerCase().includes(query) || art.native_user_id.toLowerCase().includes(query);
      return matchesType && matchesQuery;
    });

    if (filtered.length === 0) {
      grid.innerHTML = '<div class="empty-state">No matching artifacts found.</div>';
      return;
    }

    const icons = {
      xlsx: '📊',
      docx: '📄',
      pptx: '📽️',
      pdf: '📑',
      markdown: '📝',
    };

    grid.innerHTML = filtered
      .map((art) => {
        const icon = icons[art.artifact_type] || '📄';
        const typeBadge = `<span class="badge badge-category badge-${art.artifact_type}">${art.artifact_type.toUpperCase()}</span>`;
        const sizeKb = art.versions?.[0]?.file_size_bytes ? `${Math.round(art.versions[0].file_size_bytes / 1024)} KB` : '';

        return `
        <div class="card artifact-card" id="artifact-card-${art.id}">
          <div class="artifact-card-header">
            <div class="artifact-icon-wrap">${icon}</div>
            <div class="artifact-header-text">
              <h4 class="artifact-title">${this.escapeHtml(art.title)}</h4>
              <div class="artifact-meta">
                ${typeBadge}
                <span class="badge badge-version">v${art.current_version_num}</span>
                <span class="text-secondary">${sizeKb}</span>
                <span class="text-secondary">• ${this.formatDate(art.updated_at)}</span>
              </div>
            </div>
          </div>
          
          <div class="artifact-body">
            <div class="artifact-slug"><code>${this.escapeHtml(art.slug)}</code> • User: <code>${this.escapeHtml(art.native_user_id)}</code></div>
            ${art.versions?.[0]?.change_summary ? `<p class="artifact-summary"><em>"${this.escapeHtml(art.versions[0].change_summary)}"</em></p>` : ''}
          </div>

          <div class="artifact-actions">
            <a href="${art.download_url}" class="btn btn-secondary btn-sm" download>
              <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Download
            </a>
            <button class="btn btn-primary btn-sm" onclick="app.launchOnlyOffice('${art.id}')">
              <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
              Edit Online
            </button>
            <button class="btn btn-danger-ghost btn-sm" onclick="app.deleteArtifact('${art.id}')" title="Delete Artifact">
              <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </div>
      `;
      })
      .join('');
  }

  async launchOnlyOffice(artifactId) {
    try {
      const data = await this.api(`/v1/admin/artifacts/${artifactId}/onlyoffice/session`, { method: 'POST' });
      if (!data.onlyoffice_url) {
        this.showToast('OnlyOffice Document Server is not configured (ASSISTANT_ONLYOFFICE_URL). You can still download the file!', 'info');
        return;
      }
      const ooUrl = `${data.onlyoffice_url.replace(/\/$/, '')}/web-apps/apps/api/documents/api.js`;
      const editorWin = window.open('', '_blank');
      if (editorWin) {
        editorWin.document.write(`
          <!DOCTYPE html>
          <html>
            <head>
              <title>OnlyOffice Editor</title>
              <script src="${ooUrl}"></script>
              <style>html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; background: #111; }</style>
            </head>
            <body>
              <div id="placeholder" style="height: 100%;"></div>
              <script>
                const config = ${JSON.stringify(data.config)};
                new DocsAPI.DocEditor("placeholder", config);
              </script>
            </body>
          </html>
        `);
      }
    } catch (err) {
      this.showToast(`Failed to launch OnlyOffice: ${err.message}`, 'error');
    }
  }

  async deleteArtifact(artifactId) {
    if (!confirm('Are you sure you want to delete this artifact?')) return;
    try {
      await this.api(`/v1/admin/artifacts/${artifactId}`, { method: 'DELETE' });
      this.showToast('Artifact deleted', 'success');
      this.loadArtifacts();
      this.loadOverview();
    } catch (err) {
      this.showToast(`Delete failed: ${err.message}`, 'error');
    }
  }

  // ------------------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------------------
  formatDate(isoString) {
    if (!isoString) return '—';
    try {
      const d = new Date(isoString);
      return d.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  }

  escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  escapeJsString(str) {
    if (!str) return '';
    return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
  }

  showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerText = message;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.2s ease-out';
      setTimeout(() => toast.remove(), 200);
    }, 3000);
  }
}

// Instantiate global app instance
const app = new DashboardApp();
window.app = app;
