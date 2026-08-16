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
    }
  }

  // ------------------------------------------------------------------------
  // Overview Tab
  // ------------------------------------------------------------------------
  async loadOverview() {
    try {
      const data = await this.api('/v1/admin/overview');

      document.getElementById('stat-memories').innerText = data.memories.active;
      document.getElementById('stat-memories-sub').innerText = `${data.memories.total} total stored (${data.memories.tombstoned} tombstoned)`;

      document.getElementById('stat-files').innerText = data.files.active;
      document.getElementById('stat-files-sub').innerText = `${data.files.total_characters.toLocaleString()} chars across ${data.files.total} docs`;

      document.getElementById('stat-segments').innerText = data.files.total_segments;
      document.getElementById('stat-segments-sub').innerText = `${data.files.deduplicated_references} deduplicated chunk references`;

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
  // Memories Tab
  // ------------------------------------------------------------------------
  async loadMemories(searchQuery = null) {
    const query = searchQuery !== null ? searchQuery : document.getElementById('memories-search').value;
    const category = document.getElementById('memories-category-filter').value;
    const status = document.getElementById('memories-status-filter').value;

    const tbody = document.getElementById('memories-table-body');
    tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">Loading memories...</td></tr>';

    try {
      const params = new URLSearchParams({
        query: query || '',
        category: category || '',
        status_filter: status || 'active',
        limit: 50,
      });

      const data = await this.api(`/v1/admin/memories?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">No memories found.</td></tr>';
        return;
      }

      tbody.innerHTML = data.items
        .map(
          (m) => `
        <tr>
          <td><strong>${this.escapeHtml(m.statement)}</strong></td>
          <td><span class="badge badge-${m.category}">${m.category}</span></td>
          <td><code>${this.escapeHtml(m.native_user_id)}</code></td>
          <td>${Math.round(m.confidence * 100)}%</td>
          <td><span class="badge badge-${m.state}">${m.state}</span></td>
          <td><small class="text-muted">${this.formatDate(m.created_at)}</small></td>
          <td>
            <div style="display: flex; gap: 6px;">
              <button class="btn-icon" title="Edit" onclick="app.openEditMemoryModal('${m.id}', '${this.escapeJsString(m.statement)}', '${m.category}', ${m.confidence}, '${this.escapeJsString(m.native_user_id)}')">
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
      `
        )
        .join('');
    } catch {
      tbody.innerHTML = '<tr><td colspan="7" class="loading-cell text-danger">Failed to load memories.</td></tr>';
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
    document.getElementById('memory-evidence-input').value = '';
    document.getElementById('memory-modal').style.display = 'flex';
  }

  openEditMemoryModal(id, statement, category, confidence, userId) {
    document.getElementById('memory-modal-title').innerText = 'Edit Memory';
    document.getElementById('memory-form-id').value = id;
    document.getElementById('memory-user-input').value = userId;
    document.getElementById('memory-user-input').disabled = true;
    document.getElementById('memory-statement-input').value = statement;
    document.getElementById('memory-category-input').value = category;
    document.getElementById('memory-confidence-input').value = confidence;
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
    const evidence = document.getElementById('memory-evidence-input').value.trim() || null;

    try {
      if (id) {
        // Update existing
        await this.api(`/v1/admin/memories/${id}`, {
          method: 'PATCH',
          body: JSON.stringify({ statement, category }),
        });
        this.showToast('Memory updated', 'success');
      } else {
        // Create new
        await this.api('/v1/admin/memories', {
          method: 'POST',
          body: JSON.stringify({
            native_user_id: userId,
            statement,
            category,
            confidence,
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
    tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Loading documents...</td></tr>';

    try {
      const params = new URLSearchParams({
        query: query || '',
        status_filter: status || 'active',
        limit: 50,
      });

      const data = await this.api(`/v1/admin/files?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">No documents found.</td></tr>';
        return;
      }

      tbody.innerHTML = data.items
        .map(
          (f) => `
        <tr>
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
    } catch {
      tbody.innerHTML = '<tr><td colspan="8" class="loading-cell text-danger">Failed to load documents.</td></tr>';
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
    tbody.innerHTML = '<tr><td colspan="5" class="loading-cell">Loading conversations...</td></tr>';

    try {
      const params = new URLSearchParams({
        query: query || '',
        limit: 50,
      });

      const data = await this.api(`/v1/admin/conversations?${params.toString()}`);

      if (!data.items || data.items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading-cell">No conversations found.</td></tr>';
        return;
      }

      tbody.innerHTML = data.items
        .map(
          (c) => `
        <tr>
          <td><div style="max-width: 320px; word-break: break-word;">${this.escapeHtml(c.user_content)}</div></td>
          <td><div style="max-width: 380px; word-break: break-word; color: var(--text-muted);">${this.escapeHtml(c.assistant_content)}</div></td>
          <td><code>${this.escapeHtml(c.native_chat_id || 'unknown')}</code></td>
          <td><code>${this.escapeHtml(c.native_user_id)}</code></td>
          <td><small class="text-muted">${this.formatDate(c.occurred_at)}</small></td>
        </tr>
      `
        )
        .join('');
    } catch {
      tbody.innerHTML = '<tr><td colspan="5" class="loading-cell text-danger">Failed to load conversations.</td></tr>';
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
