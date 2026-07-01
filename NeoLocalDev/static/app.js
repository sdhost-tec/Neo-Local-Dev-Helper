const { createApp, ref, reactive, computed, onMounted, onUnmounted, onActivated, watch, provide, inject } = Vue;

// ── API helper ──────────────────────────────────────────────
const BASE = window.location.origin + '/admin/api';

async function api(endpoint, method = 'GET', body = null) {
  const token = localStorage.getItem('neold_token');
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(BASE + endpoint, opts);
    if (res.status === 401) { localStorage.removeItem('neold_token'); return { ok: false, _401: true }; }
    return await res.json();
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// ── Root App ─────────────────────────────────────────────────
const App = {
  template: `
    <div>
      <login-screen v-if="!authed" @login="onLogin" />
      <dashboard v-else
        :status="status"
        :toasts="toasts"
        @logout="onLogout"
        @toast="addToast"
        @reload-status="loadStatus"
      />
      <toast-list :toasts="toasts" @remove="removeToast" />
      <custom-modal v-if="modal.show" :options="modal" @close="closeModal" />
    </div>
  `,
  setup() {
    const authed  = ref(!!localStorage.getItem('neold_token'));
    const status  = reactive({ ok: false, domain: '', lan_ip: '', projects: [], system: {}, stats: {}, allowed_devices: [], rootca_available: false, admin_username: 'admin' });
    const toasts  = ref([]);
    let toastId   = 0;
    let pollTimer = null;

    const modal = reactive({
      show: false,
      title: '',
      message: '',
      type: 'confirm',
      inputValue: '',
      placeholder: '',
      resolve: null
    });

    function showConfirm(title, message) {
      modal.title = title;
      modal.message = message;
      modal.type = 'confirm';
      modal.inputValue = '';
      modal.show = true;
      return new Promise((resolve) => {
        modal.resolve = resolve;
      });
    }

    function showPrompt(title, message, defaultValue = '', placeholder = '') {
      modal.title = title;
      modal.message = message;
      modal.type = 'prompt';
      modal.inputValue = defaultValue;
      modal.placeholder = placeholder;
      modal.show = true;
      return new Promise((resolve) => {
        modal.resolve = resolve;
      });
    }

    function closeModal(result) {
      modal.show = false;
      if (modal.resolve) {
        modal.resolve(result);
        modal.resolve = null;
      }
    }

    provide('confirm', showConfirm);
    provide('prompt', showPrompt);

    function addToast(msg, isErr = false) {
      const id = ++toastId;
      toasts.value.push({ id, msg, isErr });
      setTimeout(() => removeToast(id), 4500);
    }
    function removeToast(id) { toasts.value = toasts.value.filter(t => t.id !== id); }

    async function loadStatus() {
      const d = await api('/status');
      if (!d.ok) { if (d._401) authed.value = false; return; }
      Object.assign(status, d);
    }

    function onLogin(token) {
      localStorage.setItem('neold_token', token);
      authed.value = true;
      loadStatus();
      pollTimer = setInterval(loadStatus, 4000);
    }

    function onLogout() {
      clearInterval(pollTimer);
      localStorage.removeItem('neold_token');
      authed.value = false;
    }

    onMounted(() => {
      if (authed.value) {
        loadStatus();
        pollTimer = setInterval(loadStatus, 4000);
      }
    });
    onUnmounted(() => clearInterval(pollTimer));

    return { authed, status, toasts, modal, addToast, removeToast, loadStatus, onLogin, onLogout, closeModal };
  }
};

// ── Toast List Component ──────────────────────────────────────
const ToastList = {
  props: ['toasts'],
  emits: ['remove'],
  template: `
    <div class="toast-container">
      <div v-for="t in toasts" :key="t.id"
        :class="['toast', t.isErr ? 'toast-err' : 'toast-ok']"
        @click="$emit('remove', t.id)">
        {{ t.msg }}
      </div>
    </div>
  `
};

// ── Custom Modal Component ─────────────────────────────────────
const CustomModal = {
  props: ['options'],
  emits: ['close'],
  template: `
    <div class="modal-backdrop" @click.self="$emit('close', null)">
      <div class="modal-card" style="max-width: 420px; border: 1px solid var(--border); box-shadow: 0 20px 50px rgba(0,0,0,0.6);">
        <div class="modal-title" style="display:flex; align-items:center; gap:0.5rem; margin-bottom: 0.75rem;">
          <span style="font-size:1.2rem;">🛡️</span>
          <span style="font-weight:700">{{ options.title }}</span>
        </div>
        <p style="color:var(--text); font-size:0.875rem; margin-bottom:1.25rem; line-height:1.5;">
          {{ options.message }}
        </p>
        <div v-if="options.type === 'prompt'" class="form-group" style="margin-bottom:1.25rem;">
          <input 
            v-model="inputValue" 
            class="input-control" 
            :placeholder="options.placeholder"
            ref="inputEl"
            @keydown.enter="$emit('close', inputValue)"
          />
        </div>
        <div style="display:flex; justify-content:flex-end; gap:0.5rem;">
          <button class="btn btn-outline btn-sm" @click="$emit('close', null)">Cancel</button>
          <button class="btn btn-primary btn-sm" @click="$emit('close', options.type === 'prompt' ? inputValue : true)">Confirm</button>
        </div>
      </div>
    </div>
  `,
  setup(props) {
    const inputValue = ref(props.options.inputValue || '');
    const inputEl = ref(null);
    onMounted(() => {
      if (inputEl.value) inputEl.value.focus();
    });
    return { inputValue, inputEl };
  }
};


// ── Login Screen ──────────────────────────────────────────────
const LoginScreen = {
  emits: ['login'],
  template: `
    <div class="login-screen">
      <div class="login-card">
        <img src="/admin/neo.png" style="width:64px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.3)">
        <h2>Neo LocalDev</h2>
        <p>Sign in to access your control center</p>
        <form @submit.prevent="submit">
          <div class="form-group">
            <label>Username</label>
            <input v-model="username" class="input-control" required autocomplete="username" placeholder="admin">
          </div>
          <div class="form-group">
            <label>Password</label>
            <input v-model="password" type="password" class="input-control" required autocomplete="current-password" autofocus placeholder="••••••••">
          </div>
          <p v-if="err" style="color:var(--red);font-size:0.8rem;margin-bottom:0.75rem">{{ err }}</p>
          <button type="submit" class="btn btn-primary" style="width:100%;padding:0.8rem" :disabled="loading">
            {{ loading ? 'Signing in…' : 'Sign In' }}
          </button>
        </form>
      </div>
    </div>
  `,
  setup(_, { emit }) {
    const username = ref('admin'), password = ref(''), err = ref(''), loading = ref(false);
    async function submit() {
      loading.value = true; err.value = '';
      const r = await fetch(BASE + '/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.value, password: password.value })
      });
      const d = await r.json();
      loading.value = false;
      if (d.ok) { password.value = ''; emit('login', d.token); }
      else err.value = d.error || 'Invalid credentials';
    }
    return { username, password, err, loading, submit };
  }
};

// ── Sidebar / Dashboard Shell ─────────────────────────────────
const Dashboard = {
  props: ['status', 'toasts'],
  emits: ['logout', 'toast', 'reload-status'],
  components: { ToastList },
  template: `
    <div class="app-layout">
      <!-- Sidebar -->
      <aside class="sidebar">
        <div class="sidebar-logo">
          <img src="/admin/neo.png" alt="Neo">
          <div>
            <div class="sidebar-logo-text">Neo LocalDev</div>
            <div class="sidebar-logo-sub">Control Center</div>
          </div>
        </div>

        <nav>
          <button v-for="item in navItems" :key="item.id"
            :class="['nav-item', { active: activeTab === item.id }]"
            @click="activeTab = item.id">
            <span class="nav-icon">{{ item.icon }}</span>
            <span>{{ item.label }}</span>
            <span v-if="item.id === 'settings' && pendingCount > 0" class="nav-badge">{{ pendingCount }}</span>
          </button>
        </nav>

        <div class="sidebar-footer">
          <a href="https://github.com/sdhost-tec/Neo-Local-Dev-Helper" target="_blank"
            class="btn btn-ghost btn-sm" style="justify-content:flex-start;gap:0.5rem">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/></svg>
            <span>GitHub</span>
          </a>
          <button class="btn btn-ghost btn-sm" style="justify-content:flex-start;gap:0.5rem;color:var(--red)" @click="$emit('logout')">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
            <span>Logout</span>
          </button>
        </div>
      </aside>

      <!-- Main -->
      <main class="main-content">
        <!-- Metrics Row -->
        <div class="metrics-row" style="margin-bottom:1.75rem">
          <div class="metric-card" v-for="g in gauges" :key="g.id">
            <div class="circle-chart">
              <svg viewBox="0 0 36 36" width="90" height="90">
                <path class="circle-chart-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                <path class="circle-chart-circle" :style="{ stroke: g.color, strokeDasharray: gaugeVal(g.id) + ',100' }"
                  d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
              </svg>
              <div class="circle-chart-text">{{ Math.round(gaugeVal(g.id)) }}%</div>
            </div>
            <div class="metric-label">{{ g.label }}</div>
          </div>
        </div>

        <!-- Tab Panes -->
        <keep-alive>
          <proxies-tab    v-if="activeTab==='proxies'"  :status="status" @toast="$emit('toast',$event)" @reload="$emit('reload-status')" />
          <launcher-tab   v-if="activeTab==='launcher'" :status="status" @toast="$emit('toast',$event)" @reload="$emit('reload-status')" />
          <database-tab   v-if="activeTab==='database'" :status="status" @toast="$emit('toast',$event)" />
          <services-tab   v-if="activeTab==='services'" :status="status" @toast="$emit('toast',$event)" @reload="$emit('reload-status')" />
          <settings-tab   v-if="activeTab==='settings'" :status="status" @toast="$emit('toast',$event)" @reload="$emit('reload-status')" @logout="$emit('logout')" />
        </keep-alive>
      </main>

      <!-- Guardian Popup -->
      <guardian-popup v-if="popupIp" :ip="popupIp" @approve="approvePopup" @reject="rejectPopup" @close="popupIp=null" />
    </div>
  `,
  setup(props, { emit }) {
    const activeTab   = ref('proxies');
    const pendingReqs = ref([]);
    const popupIp     = ref(null);
    const seenIps     = ref(new Set());
    const pendingCount = computed(() => pendingReqs.value.length);

    const navItems = [
      { id: 'proxies',  icon: '🌐', label: 'Active Proxies' },
      { id: 'launcher', icon: '🚀', label: 'Project Launcher' },
      { id: 'database', icon: '🗄️', label: 'Database' },
      { id: 'services', icon: '⚡', label: 'Services' },
      { id: 'settings', icon: '⚙️', label: 'Settings' },
    ];

    const gauges = [
      { id: 'cpu',  label: 'CPU Usage',    color: '#6366f1' },
      { id: 'ram',  label: 'RAM Usage',    color: '#06b6d4' },
      { id: 'disk', label: 'Disk Storage', color: '#10b981' },
    ];

    function gaugeVal(id) {
      const stats = props.status.stats || {};
      return stats[id] || 0;
    }

    // Poll pending guardian requests
    let guardianTimer = null;
    async function pollGuardian() {
      const r = await api('/guardian/pending');
      if (!r.ok) return;
      pendingReqs.value = r.requests || [];
      for (const ip of r.requests) {
        if (!seenIps.value.has(ip)) {
          seenIps.value.add(ip);
          if (!popupIp.value) popupIp.value = ip;
        }
      }
      // Remove seen IPs that are no longer pending
      for (const ip of seenIps.value) {
        if (!r.requests.includes(ip)) seenIps.value.delete(ip);
      }
    }

    async function approvePopup(duration) {
      const ip = popupIp.value;
      popupIp.value = null;
      const r = await api('/guardian/approve', 'POST', { ip, duration });
      emit('toast', r.ok ? r.message : (r.error || 'Failed'), !r.ok);
      emit('reload-status');
    }

    async function rejectPopup() {
      const ip = popupIp.value;
      popupIp.value = null;
      await api('/guardian/reject', 'POST', { ip });
      emit('toast', `Request from ${ip} rejected.`);
    }

    onMounted(() => { pollGuardian(); guardianTimer = setInterval(pollGuardian, 4000); });
    onUnmounted(() => clearInterval(guardianTimer));

    return { activeTab, navItems, gauges, gaugeVal, pendingCount, popupIp, approvePopup, rejectPopup };
  }
};

// ── Guardian Popup Component ──────────────────────────────────
const GuardianPopup = {
  props: ['ip'],
  emits: ['approve', 'reject', 'close'],
  template: `
    <div class="guardian-popup">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.75rem">
        <div style="display:flex;align-items:center;gap:0.5rem">
          <span style="font-size:1.4rem">🛡️</span>
          <div>
            <div style="font-weight:700;color:#fff;font-size:0.9rem">Access Request</div>
            <div style="font-size:0.72rem;color:var(--muted)">A device wants to join</div>
          </div>
        </div>
        <button class="btn-ghost" style="font-size:1rem;padding:0" @click="$emit('close')">✕</button>
      </div>
      <div style="font-family:'JetBrains Mono',monospace;color:#fda4af;font-size:0.85rem;background:rgba(244,63,94,0.08);padding:0.45rem 0.75rem;border-radius:8px;margin-bottom:0.9rem;text-align:center">
        {{ ip }}
      </div>
      <div style="display:flex;flex-direction:column;gap:0.4rem">
        <div style="display:flex;gap:0.35rem">
          <button class="btn btn-sm" style="flex:1;background:rgba(16,185,129,0.12);color:var(--green);border-color:rgba(16,185,129,0.3)" @click="$emit('approve','1d')">1 Day</button>
          <button class="btn btn-sm" style="flex:1;background:rgba(16,185,129,0.12);color:var(--green);border-color:rgba(16,185,129,0.3)" @click="$emit('approve','7d')">7 Days</button>
          <button class="btn btn-sm" style="flex:1;background:rgba(16,185,129,0.12);color:var(--green);border-color:rgba(16,185,129,0.3)" @click="$emit('approve','30d')">30 Days</button>
        </div>
        <div style="display:flex;gap:0.35rem">
          <button class="btn btn-primary btn-sm" style="flex:1" @click="$emit('approve','permanent')">✓ Permanent</button>
          <button class="btn btn-danger btn-sm" style="flex:1" @click="$emit('reject')">✕ Reject</button>
        </div>
      </div>
    </div>
  `
};

// ── Tab: Active Proxies ───────────────────────────────────────
const ProxiesTab = {
  props: ['status'],
  emits: ['toast', 'reload'],
  template: `
    <div class="fade-enter">
      <div class="page-header">
        <div class="page-title">🌐 Active Proxies <span>running dev servers</span></div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">📡 Discovered Development Servers</div>
        </div>
        <div style="overflow-x:auto">
          <table class="dev-table">
            <thead><tr>
              <th>Project</th><th>Framework</th><th>Runtime</th>
              <th>Port</th><th>Secure URL</th><th>Action</th>
            </tr></thead>
            <tbody>
              <tr v-if="!status.projects || status.projects.length === 0">
                <td colspan="6" style="text-align:center;color:var(--muted);padding:2rem">
                  No dev servers detected yet. Start your dev server (npm run dev, etc.) and it will appear here automatically.
                </td>
              </tr>
              <tr v-for="p in status.projects" :key="p.pid">
                <td><strong style="color:#a5b4fc">{{ p.name }}</strong></td>
                <td><span class="badge badge-warn" style="background:rgba(99,102,241,0.08);color:#a5b4fc">{{ p.framework }}</span></td>
                <td style="color:var(--muted);font-size:0.8rem">{{ p.runtime }}</td>
                <td><code style="color:var(--accent2)">:{{ p.port }}</code></td>
                <td>
                  <a :href="'https://'+status.domain+':'+p.secure_port" target="_blank"
                    style="color:#a5b4fc;text-decoration:none;font-weight:500;font-size:0.82rem">
                    https://{{ status.domain }}:{{ p.secure_port }}
                  </a>
                  <div style="font-size:0.7rem;color:var(--muted);margin-top:0.1rem">
                    LAN:
                    <a :href="'https://'+status.lan_ip+':'+p.secure_port" target="_blank" style="color:var(--muted)">
                      {{ status.lan_ip }}:{{ p.secure_port }}
                    </a>
                  </div>
                </td>
                <td>
                  <button class="btn btn-danger btn-sm" @click="stop(p.pid, p.name)">Stop</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `,
  setup(props, { emit }) {
    const confirm = inject('confirm');
    async function stop(pid, name) {
      if (!await confirm('Stop Project', `Stop project: ${name}?`)) return;
      const r = await api('/project/stop', 'POST', { pid, name });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) emit('reload');
    }
    return { stop };
  }
};

// ── Tab: Launcher ─────────────────────────────────────────────
const LauncherTab = {
  props: ['status'],
  emits: ['toast', 'reload'],
  template: `
    <div class="fade-enter">
      <div class="page-header">
        <div class="page-title">🚀 Project Launcher <span>spawn & manage dev servers</span></div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;margin-bottom:1.25rem">
        <div class="panel" style="margin:0">
          <div class="panel-title">📁 Auto-Discover Root</div>
          <form @submit.prevent="saveRoot" style="display:flex;gap:0.5rem">
            <input v-model="rootPath" class="input-control" placeholder="/home/user/projects" style="flex:1">
            <button type="button" class="btn btn-outline btn-sm" @click="openExplorer('root')">📁</button>
            <button type="submit" class="btn btn-primary btn-sm">Scan</button>
          </form>
        </div>
        <div class="panel" style="margin:0">
          <div class="panel-title">➕ Add Single Project</div>
          <form @submit.prevent="addProject" style="display:flex;gap:0.5rem">
            <input v-model="singlePath" class="input-control" placeholder="/home/user/projects/my-app" style="flex:1">
            <button type="button" class="btn btn-outline btn-sm" @click="openExplorer('single')">📁</button>
            <button type="submit" class="btn btn-primary btn-sm">Add</button>
          </form>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1.2fr 1fr;gap:1.25rem">
        <div class="panel" style="margin:0">
          <div class="panel-title">🔎 Available Projects</div>
          <div style="max-height:480px;overflow-y:auto">
            <div v-if="discovered.length===0&&custom.length===0" style="color:var(--muted);text-align:center;padding:2rem;font-size:0.875rem">
              Set a root folder or add a project above.
            </div>
            <project-card v-for="p in allProjects" :key="p.path"
              :project="p" :spawned-map="spawnedMap"
              @launch="launchProject" @stop="stopSpawned"
              @remove="removeProject" @rename="renameProject"
            />
          </div>
        </div>
        <div class="panel" style="margin:0">
          <div class="panel-title">🟢 Running in Background</div>
          <div style="max-height:480px;overflow-y:auto">
            <div v-if="spawned.length===0" style="color:var(--muted);text-align:center;padding:2rem;font-size:0.875rem">
              No projects running in the background.
            </div>
            <spawned-card v-for="p in spawned" :key="p.pid"
              :project="p" :active-proxies="status.projects||[]" :domain="status.domain"
              @stop="stopSpawned" @toast="$emit('toast',$event)"
            />
          </div>
        </div>
      </div>

      <!-- File Explorer Modal -->
      <div v-if="explorerOpen" class="modal-backdrop" @click.self="explorerOpen=false">
        <div class="modal-card" style="max-width:580px;max-height:80vh;display:flex;flex-direction:column">
          <div class="modal-title">📁 Select Directory</div>
          <div style="font-size:0.8rem;color:var(--accent2);background:rgba(0,0,0,0.25);padding:0.5rem 0.75rem;border-radius:6px;margin-bottom:0.75rem;font-family:'JetBrains Mono',monospace">
            {{ explorerCurrent }}
          </div>
          <div style="flex:1;overflow-y:auto;min-height:200px;background:rgba(0,0,0,0.15);border-radius:6px;padding:0.4rem;margin-bottom:1rem">
            <div v-if="explorerDirs.length===0" style="color:var(--muted);text-align:center;padding:1.5rem;font-size:0.85rem">Empty directory</div>
            <div v-for="d in explorerDirs" :key="d.path" class="explorer-item" @click="loadExplorer(d.path)">
              📁 {{ d.name }}
            </div>
          </div>
          <div style="display:flex;justify-content:space-between">
            <button class="btn btn-outline btn-sm" :disabled="!explorerParent" @click="loadExplorer(explorerParent)">⬆ Up</button>
            <div style="display:flex;gap:0.5rem">
              <button class="btn btn-outline btn-sm" @click="explorerOpen=false">Cancel</button>
              <button class="btn btn-primary btn-sm" @click="confirmExplorer">Select</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
  components: {},
  setup(props, { emit }) {
    const confirm = inject('confirm');
    const prompt = inject('prompt');
    const rootPath = ref(''), singlePath = ref('');
    const discovered = ref([]), custom = ref([]), spawned = ref([]);
    const spawnedMap = ref({});
    const explorerOpen = ref(false), explorerTarget = ref('');
    const explorerCurrent = ref(''), explorerParent = ref(''), explorerDirs = ref([]);

    const allProjects = computed(() => [
      ...discovered.value.map(p => ({ ...p, isCustom: false })),
      ...custom.value.map(p => ({ ...p, isCustom: true }))
    ]);

    async function load() {
      const [discRes, spawnRes] = await Promise.all([
        api('/projects/discover'),
        api('/project/spawned/list')
      ]);
      if (discRes.ok) {
        rootPath.value = discRes.projects_root || '';
        discovered.value = discRes.projects || [];
        custom.value = discRes.custom_projects || [];
      }
      if (spawnRes.ok) {
        spawned.value = spawnRes.projects || [];
        spawnedMap.value = {};
        (spawnRes.projects || []).forEach(p => spawnedMap.value[p.path] = p.pid);
      }
    }

    async function saveRoot() {
      const r = await api('/projects/root/update', 'POST', { projects_root: rootPath.value });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) load();
    }

    async function addProject() {
      if (!singlePath.value.trim()) return;
      const r = await api('/project/custom/add', 'POST', { path: singlePath.value.trim() });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) { singlePath.value = ''; load(); }
    }

    async function launchProject({ name, path, command }) {
      const r = await api('/project/spawn', 'POST', { name, path, command });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) load();
    }

    async function stopSpawned(pid) {
      if (!await confirm('Stop Project', `Stop project PID ${pid}?`)) return;
      const r = await api('/project/spawned/stop', 'POST', { pid });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      load();
    }

    async function removeProject(path) {
      if (!await confirm('Remove Project', 'Remove project from list?')) return;
      const r = await api('/project/custom/remove', 'POST', { path });
      emit('toast', r.ok ? 'Removed' : r.error, !r.ok);
      if (r.ok) load();
    }

    async function renameProject({ path, currentName }) {
      const name = await prompt('Rename Project', 'New display name:', currentName);
      if (!name || name === currentName) return;
      const r = await api('/project/rename', 'POST', { path, new_name: name });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) load();
    }

    async function loadExplorer(path) {
      const r = await api('/fs/list?path=' + encodeURIComponent(path || ''));
      if (r.ok) {
        explorerCurrent.value = r.current;
        explorerParent.value = r.parent || '';
        explorerDirs.value = r.dirs || [];
      }
    }

    function openExplorer(target) {
      explorerTarget.value = target;
      explorerOpen.value = true;
      loadExplorer(target === 'root' ? rootPath.value : singlePath.value);
    }

    function confirmExplorer() {
      if (explorerTarget.value === 'root') rootPath.value = explorerCurrent.value;
      else singlePath.value = explorerCurrent.value;
      explorerOpen.value = false;
    }

    onMounted(load);

    return {
      rootPath, singlePath, discovered, custom, spawned, allProjects, spawnedMap,
      explorerOpen, explorerCurrent, explorerParent, explorerDirs,
      saveRoot, addProject, launchProject, stopSpawned, removeProject, renameProject,
      openExplorer, loadExplorer, confirmExplorer
    };
  }
};

// ── Project Card (sub-component) ─────────────────────────────
const ProjectCard = {
  props: ['project', 'spawnedMap'],
  emits: ['launch', 'stop', 'remove', 'rename'],
  template: `
    <div :class="['project-card', isRunning ? 'running' : '']">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
        <div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap">
          <strong style="color:#a5b4fc">{{ project.name }}</strong>
          <span v-if="project.isCustom" style="font-size:0.7rem;color:var(--accent2)">(Manual)</span>
          <span v-if="isRunning" class="badge badge-ok" style="font-size:0.65rem">● Running</span>
        </div>
        <div style="display:flex;gap:0.35rem;align-items:center">
          <span class="badge" style="background:rgba(99,102,241,0.1);color:#a5b4fc;font-size:0.65rem">{{ project.type }}</span>
          <button class="btn-ghost" style="font-size:0.85rem;padding:0.1rem 0.2rem" @click="$emit('rename',{path:project.path,currentName:project.name})" title="Rename">✏️</button>
          <button v-if="project.isCustom" class="btn-ghost" style="color:var(--red);font-size:1rem;padding:0" @click="$emit('remove',project.path)">×</button>
        </div>
      </div>
      <div style="font-size:0.72rem;color:var(--muted);margin-bottom:0.6rem">{{ project.path }}</div>

      <!-- Running state -->
      <div v-if="isRunning" style="display:flex;align-items:center;gap:0.5rem">
        <span style="font-size:0.75rem;color:var(--muted);flex:1">PID: <code>{{ spawnedMap[project.path] }}</code></span>
        <button class="btn btn-danger btn-sm" @click="$emit('stop', spawnedMap[project.path])">⏹ Stop</button>
      </div>

      <!-- Not running: command picker -->
      <div v-else>
        <div style="margin-bottom:0.4rem">
          <div style="font-size:0.72rem;color:var(--muted);margin-bottom:0.25rem">Quick command:</div>
          <div style="display:flex;gap:0.35rem;flex-wrap:wrap">
            <button
              v-for="cmd in presetCmds" :key="cmd.label"
              :class="['btn','btn-xs', customCmd===cmd.cmd ? 'btn-primary' : 'btn-outline']"
              @click="customCmd=cmd.cmd"
              style="font-size:0.7rem">
              {{ cmd.label }}
            </button>
          </div>
        </div>
        <div style="display:flex;gap:0.4rem">
          <input v-model="customCmd" class="input-control"
            style="flex:1;padding:0.3rem 0.5rem;font-size:0.78rem"
            placeholder="Custom command…"
            @keydown.enter="launch">
          <button class="btn btn-primary btn-sm" @click="launch" :disabled="!customCmd.trim()">🚀 Run</button>
        </div>
      </div>
    </div>
  `,
  setup(props, { emit }) {
    const isRunning = computed(() => props.project.path in props.spawnedMap);

    // Build preset commands based on project type — clean and correct
    const presetCmds = computed(() => {
      const type = props.project.type || '';
      const map = {
        'Node':        [
          { label: 'dev',     cmd: 'npm run dev'   },
          { label: 'start',   cmd: 'npm start'      },
          { label: 'build',   cmd: 'npm run build'  },
          { label: 'install', cmd: 'npm install'    },
        ],
        'Python':      [
          { label: 'main.py',   cmd: 'python3 main.py'                   },
          { label: 'app.py',    cmd: 'python3 app.py'                    },
          { label: 'Django',    cmd: 'python3 manage.py runserver 0.0.0.0:8000' },
          { label: 'Flask',     cmd: 'flask run --host=0.0.0.0 --port=5000'     },
          { label: 'pip install', cmd: 'pip install -r requirements.txt'         },
        ],
        'PHP':         [
          { label: 'artisan',  cmd: 'php artisan serve --host=0.0.0.0 --port=8000' },
          { label: 'built-in', cmd: 'php -S 0.0.0.0:8000'                         },
        ],
        'Static HTML': [
          { label: 'http.server', cmd: 'python3 -m http.server 8080' },
        ],
      };
      return map[type] || [
        { label: 'http.server', cmd: 'python3 -m http.server 8080' },
      ];
    });

    // Default to first preset command
    const customCmd = ref(presetCmds.value[0]?.cmd || '');

    // If type changes (rare), reset
    watch(presetCmds, cmds => { if (!customCmd.value) customCmd.value = cmds[0]?.cmd || ''; });

    function launch() {
      const cmd = customCmd.value.trim();
      if (!cmd) return;
      emit('launch', { name: props.project.name, path: props.project.path, command: cmd });
    }

    return { isRunning, presetCmds, customCmd, launch };
  }
};
LauncherTab.components = { ProjectCard };

// ── Spawned Card (sub-component) ─────────────────────────────
const SpawnedCard = {
  props: ['project', 'activeProxies', 'domain'],
  emits: ['stop', 'toast'],
  template: `
    <div class="spawned-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem">
        <strong style="color:var(--green)">● {{ project.name }}</strong>
        <span style="font-size:0.72rem;color:var(--muted)">PID {{ project.pid }} · {{ project.uptime }}s</span>
      </div>
      <div style="font-size:0.72rem;color:var(--muted);margin-bottom:0.5rem;font-family:'JetBrains Mono',monospace">$ {{ project.command }}</div>

      <!-- Metrics bar -->
      <div style="display:flex;align-items:center;gap:1.5rem;font-size:0.78rem;background:rgba(0,0,0,0.2);padding:0.35rem 0.75rem;border-radius:6px;margin-bottom:0.5rem">
        <span>CPU: <code style="color:var(--accent2)">{{ cpu }}%</code></span>
        <span>RAM: <code style="color:#a5b4fc">{{ ram }}%</code></span>
        <a v-if="proxyUrl" :href="proxyUrl" target="_blank"
          style="margin-left:auto;color:var(--accent2);font-size:0.72rem;text-decoration:none;display:flex;align-items:center;gap:0.25rem">
          🔗 Open Site
        </a>
        <span v-else style="margin-left:auto;font-size:0.7rem;color:var(--muted)">No proxy yet</span>
      </div>

      <!-- Stress test options -->
      <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;font-size:0.75rem">
        <span style="color:var(--muted)">Test Profile:</span>
        <select v-model="stressLevel" style="flex:1;background:rgba(0,0,0,0.2);border:1px solid var(--border-light);color:var(--text);border-radius:4px;padding:0.2rem 0.4rem;font-size:0.72rem;outline:none">
          <option value="100-10" style="background:#070814;color:#fff">Light (100 reqs, 10 conn)</option>
          <option value="500-50" style="background:#070814;color:#fff">Medium (500 reqs, 50 conn)</option>
          <option value="1000-100" style="background:#070814;color:#fff">Heavy (1000 reqs, 100 conn)</option>
          <option value="5000-200" style="background:#070814;color:#fff">Extreme (5000 reqs, 200 conn)</option>
        </select>
      </div>

      <!-- Actions -->
      <div style="display:flex;gap:0.4rem;margin-bottom:0.4rem">
        <button class="btn btn-outline btn-sm" @click="showLogs=!showLogs;if(showLogs)loadLogs()">
          {{ showLogs ? '🙈 Hide Logs' : '📄 Logs' }}
        </button>
        <button class="btn btn-outline btn-sm" @click="runStress" :disabled="stressLoading" title="Run load test against this project">
          {{ stressLoading ? '⏳ Testing…' : '🔥 Stress Test' }}
        </button>
        <button class="btn btn-danger btn-sm" style="flex:1" @click="$emit('stop', project.pid)">⏹ Stop</button>
      </div>

      <!-- Logs console -->
      <div v-if="showLogs" style="margin-top:0.4rem">
        <div class="console" style="max-height:180px;font-size:0.7rem">{{ logContent }}</div>
      </div>

      <!-- Stress Results -->
      <div v-if="stressResult" style="margin-top:0.5rem;background:rgba(0,0,0,0.2);border:1px solid var(--border-light);border-radius:8px;padding:0.75rem;font-size:0.78rem">
        <div style="font-weight:600;color:#a5b4fc;margin-bottom:0.35rem;font-size:0.75rem">
          🔥 Stress Test Results
          <span style="color:var(--muted);font-weight:400"> — {{ stressResult.url }}</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0.4rem;text-align:center;margin-bottom:0.5rem">
          <div style="background:rgba(255,255,255,0.03);padding:0.4rem;border-radius:6px">
            <div style="color:var(--muted);font-size:0.65rem;margin-bottom:0.15rem">REQUESTS</div>
            <strong>{{ stressResult.total }}</strong>
          </div>
          <div style="background:rgba(16,185,129,0.08);padding:0.4rem;border-radius:6px">
            <div style="color:var(--muted);font-size:0.65rem;margin-bottom:0.15rem">THROUGHPUT</div>
            <strong style="color:var(--accent2)">{{ stressResult.rps }} RPS</strong>
          </div>
          <div style="background:rgba(244,63,94,0.08);padding:0.4rem;border-radius:6px">
            <div style="color:var(--muted);font-size:0.65rem;margin-bottom:0.15rem">FAILURES</div>
            <strong style="color:var(--red)">{{ stressResult.failures }}</strong>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:0.25rem;border-top:1px solid var(--border-light);padding-top:0.4rem;color:var(--muted);font-size:0.72rem">
          <div style="display:flex;justify-content:space-between">
            <span>Avg Latency: <strong style="color:var(--text)">{{ stressResult.avg_latency_ms }} ms</strong></span>
            <span>Range: <strong style="color:var(--text)">{{ stressResult.min_latency_ms }} - {{ stressResult.max_latency_ms }} ms</strong></span>
          </div>
          <div style="display:flex;justify-content:space-between">
            <span>Duration: <strong style="color:var(--text)">{{ stressResult.duration_seconds }} s</strong></span>
            <span>Success Rate: <strong :style="{color: stressResult.success_rate>=90?'var(--green)':'var(--red)'}">{{ stressResult.success_rate }}%</strong></span>
          </div>
          <!-- HTTP codes breakdown if failures exist -->
          <div v-if="stressResult.failures > 0" style="margin-top:0.25rem;background:rgba(244,63,94,0.05);padding:0.3rem;border-radius:4px;border:1px solid rgba(244,63,94,0.1)">
            <span style="font-weight:600;color:var(--red)">Errors: </span>
            <template v-for="(count, code) in stressResult.status_codes" :key="code">
              <span v-if="code != 200" style="margin-right:0.5rem">
                <code style="color:var(--text)">{{ code }}</code>: {{ count }}
              </span>
            </template>
          </div>

          <!-- CPU Spike Sparkline Chart -->
          <div v-if="stressResult.cpu_samples && stressResult.cpu_samples.length > 0" style="margin-top:0.4rem;background:rgba(0,0,0,0.15);padding:0.4rem;border-radius:6px;border:1px solid var(--border-light)">
            <div style="display:flex;justify-content:space-between;margin-bottom:0.2rem;font-size:0.65rem;color:var(--muted)">
              <span>💻 CPU Load Spike Trend</span>
              <span style="color:var(--text);font-weight:600">Peak: {{ Math.max(...stressResult.cpu_samples) }}%</span>
            </div>
            <svg viewBox="0 0 100 25" style="width:100%;height:32px;display:block">
              <path :d="generateSvgPath(stressResult.cpu_samples)" fill="rgba(6,182,212,0.1)" stroke="var(--accent2)" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </div>
        </div>
      </div>
    </div>
  `,
  setup(props, { emit }) {
    const prompt = inject('prompt');
    const cpu = ref(0), ram = ref(0);
    const showLogs = ref(false), logContent = ref('Loading…');
    const stressResult = ref(null), stressLoading = ref(false);
    const stressLevel = ref('100-10');

    // Find the HTTPS proxy URL for this spawned project
    // The spawned project has a `pids` array (process group)
    // activeProxies (from /api/status projects) have a `pid` field
    const proxyUrl = computed(() => {
      if (!props.activeProxies || !props.domain) return null;
      const match = props.activeProxies.find(p => p.path === props.project.path);
      return match ? `https://${props.domain}:${match.secure_port}` : null;
    });

    let metricTimer = null;

    async function pollMetrics() {
      const r = await api(`/project/monitor?pid=${props.project.pid}`);
      if (r.ok) { cpu.value = r.cpu; ram.value = r.ram; }
    }

    async function loadLogs() {
      logContent.value = 'Loading…';
      // Try log by name first (more reliable than PID after restart)
      const r = await api(`/project/logs?pid=${props.project.pid}`);
      logContent.value = r.ok ? (r.lines.join('\n') || '(no output yet)') : 'Could not load logs.';
    }

    async function runStress() {
      stressResult.value = null;
      stressLoading.value = true;

      // Find target URL automatically
      let url = proxyUrl.value;

      // Fallback: ask user for a port and build a direct URL
      if (!url) {
        const port = await prompt(
          `Stress Test`,
          `Enter the port your project is listening on (e.g. 3000):`
        );
        if (!port) { stressLoading.value = false; return; }
        url = `http://127.0.0.1:${port.trim()}`;
      }

      const [reqs, conn] = stressLevel.value.split('-').map(Number);
      const r = await api('/project/stresstest', 'POST', { url, requests: reqs, concurrency: conn });
      stressLoading.value = false;
      if (r.ok) {
        stressResult.value = { ...r.results, url };
      } else {
        emit('toast', r.error || 'Stress test failed', true);
      }
    }

    function generateSvgPath(samples) {
      if (!samples || samples.length === 0) return '';
      if (samples.length === 1) {
        return `M 0,${24 - (samples[0]/100)*23} L 100,${24 - (samples[0]/100)*23}`;
      }
      const points = [];
      const dx = 100 / (samples.length - 1);
      for (let i = 0; i < samples.length; i++) {
        const x = i * dx;
        const val = Math.min(100, Math.max(0, samples[i]));
        const y = 24 - (val / 100) * 23;
        points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
      }
      return `M 0,24 L ${points.join(' L ')} L 100,24 Z`;
    }

    onMounted(() => { pollMetrics(); metricTimer = setInterval(pollMetrics, 3000); });
    onUnmounted(() => clearInterval(metricTimer));

    return { cpu, ram, showLogs, logContent, proxyUrl, stressResult, stressLoading, stressLevel, loadLogs, runStress, generateSvgPath };
  }
};
LauncherTab.components.SpawnedCard = SpawnedCard;

// ── Tab: Database ─────────────────────────────────────────────
const DatabaseTab = {
  props: ['status'],
  emits: ['toast'],
  template: `
    <div class="fade-enter">
      <div class="page-header">
        <div class="page-title">🗄️ Database Manager <span>MariaDB</span></div>
      </div>
      <div v-if="!dbRunning" class="panel" style="text-align:center;padding:2rem;color:var(--muted)">
        MariaDB is not running. Start it from the Services tab.
      </div>
      <div v-else class="db-grid">
        <!-- Databases -->
        <div class="panel" style="margin:0">
          <div class="panel-header">
            <div class="panel-title">📁 Databases</div>
          </div>
          <div style="display:flex;gap:0.5rem;margin-bottom:0.9rem">
            <input v-model="newDb" class="input-control" placeholder="New database name" style="flex:1">
            <button class="btn btn-primary btn-sm" @click="createDb" :disabled="!newDb.trim()">Create</button>
          </div>
          <div class="db-list">
            <div v-if="databases.length===0" style="color:var(--muted);font-size:0.85rem;padding:0.5rem">No databases found.</div>
            <div v-for="db in databases" :key="db" class="db-row">
              <span style="font-family:'JetBrains Mono',monospace;font-size:0.82rem">{{ db }}</span>
              <button class="btn btn-danger btn-xs" @click="dropDb(db)">×</button>
            </div>
          </div>
        </div>
        <!-- Users -->
        <div class="panel" style="margin:0">
          <div class="panel-header">
            <div class="panel-title">👥 Database Users</div>
          </div>
          <div class="db-list">
            <div v-if="users.length===0" style="color:var(--muted);font-size:0.85rem;padding:0.5rem">No users found.</div>
            <div v-for="u in users" :key="u.user+'@'+u.host" class="db-row">
              <span style="font-family:'JetBrains Mono',monospace;font-size:0.82rem">{{ u.user }}@{{ u.host }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
  setup(props, { emit }) {
    const confirm = inject('confirm');
    const databases = ref([]), users = ref([]), newDb = ref('');
    const dbRunning = computed(() => props.status?.system?.mariadb?.running ?? false);

    async function load() {
      if (!dbRunning.value) return;
      const [d, u] = await Promise.all([api('/db/databases'), api('/db/users')]);
      if (d.ok) databases.value = d.databases;
      if (u.ok) users.value = u.users;
    }

    async function createDb() {
      const r = await api('/db/create', 'POST', { name: newDb.value.trim() });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) { newDb.value = ''; load(); }
    }

    async function dropDb(name) {
      if (!await confirm('Delete Database', `Delete database "${name}"? All data will be lost!`)) return;
      const r = await api('/db/drop', 'POST', { name });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) load();
    }

    watch(dbRunning, v => { if (v) load(); });
    onMounted(load);
    return { databases, users, newDb, dbRunning, createDb, dropDb };
  }
};

// ── Tab: Services ─────────────────────────────────────────────
const ServicesTab = {
  props: ['status'],
  emits: ['toast', 'reload'],
  template: `
    <div class="fade-enter">
      <div class="page-header">
        <div class="page-title">⚡ Service Controls</div>
      </div>

      <div class="panel" style="max-width:640px">
        <div class="panel-title" style="margin-bottom:1.25rem">🛠️ Services</div>
        <div v-for="svc in services" :key="svc.id" class="service-item">
          <div>
            <div class="service-name">{{ svc.label }}</div>
            <div class="service-version">{{ svcInfo(svc.id)?.version || '—' }}</div>
          </div>
          <div style="display:flex;align-items:center;gap:0.75rem">
            <span :class="['badge', svcInfo(svc.id)?.running ? 'badge-ok' : (svcInfo(svc.id)?.installed ? 'badge-err' : 'badge-warn')]">
              {{ svcInfo(svc.id)?.running ? 'Running' : (svcInfo(svc.id)?.installed ? 'Stopped' : 'Not Installed') }}
            </span>
            <div style="display:flex;gap:0.35rem">
              <template v-if="!svcInfo(svc.id)?.installed">
                <button class="btn btn-primary btn-sm" @click="install(svc.id)">Install</button>
              </template>
              <template v-else-if="svc.id === 'pma'">
                <a href="/pma/" target="_blank" class="btn btn-outline btn-sm" style="text-decoration:none">🔗 Open</a>
              </template>
              <template v-else>
                <button class="btn btn-outline btn-sm" @click="control(svc.apiId || svc.id, svcInfo(svc.id)?.running ? 'stop' : 'start')">
                  {{ svcInfo(svc.id)?.running ? 'Stop' : 'Start' }}
                </button>
                <button class="btn btn-outline btn-sm" @click="control(svc.apiId || svc.id, 'restart')">↻</button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- PHP Extensions -->
      <div class="panel" style="max-width:860px">
        <div class="panel-header">
          <div class="panel-title">🐘 PHP Extensions</div>
          <input v-model="extSearch" class="input-control" placeholder="Search extensions…"
            style="max-width:220px;padding:0.35rem 0.75rem;font-size:0.82rem">
        </div>
        <div v-if="extensions.length===0" style="color:var(--muted);text-align:center;padding:2rem;font-size:0.875rem">
          PHP-FPM not installed or no extensions found.
        </div>
        <div v-else class="ext-grid">
          <div v-for="ext in filteredExt" :key="ext.name" class="ext-card">
            <div>
              <div style="font-weight:600;font-size:0.85rem;color:#f3f4f6">{{ ext.name }}</div>
              <div :style="{fontSize:'0.7rem',color: ext.installed ? 'var(--green)' : 'var(--muted)'}">
                {{ ext.installed ? 'Installed ✓' : 'Not installed' }}
              </div>
            </div>
            <div>
              <button v-if="!ext.installed" class="btn btn-primary btn-xs" @click="installExt(ext.name)">📥 Install</button>
              <label v-else class="switch">
                <input type="checkbox" :checked="ext.enabled" @change="toggleExt(ext.name, $event.target.checked)">
                <span class="slider"></span>
              </label>
            </div>
          </div>
        </div>
      </div>

      <!-- Sudo Modal -->
      <div v-if="sudoOpen" class="modal-backdrop" @click.self="sudoOpen=false">
        <div class="modal-card" style="text-align:center;max-width:420px">
          <div style="font-size:2.5rem;margin-bottom:0.75rem">🔒</div>
          <div class="modal-title" style="justify-content:center">Admin Privileges Required</div>
          <p style="color:var(--muted);font-size:0.82rem;margin-bottom:1.25rem">
            This action requires sudo to manage PHP extensions and restart PHP-FPM.
          </p>
          <input v-model="sudoPwd" type="password" class="input-control"
            placeholder="Enter your sudo password…"
            style="text-align:center;margin-bottom:1rem"
            @keydown.enter="confirmSudo">
          <div style="display:flex;gap:0.5rem">
            <button class="btn btn-outline" style="flex:1" @click="sudoOpen=false">Cancel</button>
            <button class="btn btn-primary" style="flex:1;background:var(--red);border-color:var(--red)" @click="confirmSudo">Confirm</button>
          </div>
        </div>
      </div>
    </div>
  `,
  setup(props, { emit }) {
    const services = [
      { id: 'caddy',   label: 'Caddy Proxy',      apiId: 'caddy'   },
      { id: 'mariadb', label: 'MariaDB Server',    apiId: 'mariadb' },
      { id: 'php',     label: 'PHP-FPM Manager',   apiId: null      },
      { id: 'pma',     label: 'phpMyAdmin Client', apiId: null      },
    ];

    const extensions = ref([]), extSearch = ref('');
    const sudoOpen = ref(false), sudoPwd = ref(''), cachedPwd = ref('');
    let sudoResolve = null;

    const filteredExt = computed(() =>
      extensions.value.filter(e => e.name.includes(extSearch.value.toLowerCase()))
    );

    function svcInfo(id) {
      const s = props.status?.system || {};
      const map = { caddy: s.caddy, mariadb: s.mariadb, php: s.php_fpm, pma: s.phpmyadmin };
      return map[id] || {};
    }

    async function control(svc, action) {
      const r = await api('/service/control', 'POST', { service: svc, action });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      emit('reload');
    }

    async function install(id) {
      emit('toast', `Installing ${id}…`);
      const r = await api('/service/install', 'POST', { component: id });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      emit('reload');
    }

    async function loadExt() {
      const r = await api('/php/extensions');
      if (r.ok) extensions.value = r.extensions;
    }

    function openSudo() {
      sudoPwd.value = '';
      sudoOpen.value = true;
      return new Promise(res => sudoResolve = res);
    }

    function confirmSudo() {
      sudoOpen.value = false;
      if (sudoResolve) { sudoResolve(sudoPwd.value); sudoResolve = null; }
    }

    async function toggleExt(name, enable) {
      let r = await api('/php/extension/toggle', 'POST', { name, enable, sudo_password: cachedPwd.value });
      if (r.error === 'sudo_required') {
        const pwd = await openSudo();
        if (!pwd) return;
        cachedPwd.value = pwd;
        r = await api('/php/extension/toggle', 'POST', { name, enable, sudo_password: pwd });
      }
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) loadExt();
    }

    async function installExt(name) {
      let r = await api('/php/extension/install', 'POST', { name, sudo_password: cachedPwd.value });
      if (r.error === 'sudo_required') {
        const pwd = await openSudo();
        if (!pwd) return;
        cachedPwd.value = pwd;
        r = await api('/php/extension/install', 'POST', { name, sudo_password: pwd });
      }
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) loadExt();
    }

    onMounted(loadExt);
    return { services, extensions, extSearch, filteredExt, svcInfo, control, install, toggleExt, installExt, sudoOpen, sudoPwd, confirmSudo };
  }
};

// ── Tab: Settings ─────────────────────────────────────────────
const SettingsTab = {
  props: ['status'],
  emits: ['toast', 'reload', 'logout'],
  template: `
    <div class="fade-enter">
      <div class="page-header">
        <div class="page-title">⚙️ Settings</div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.25rem">

        <!-- Credentials -->
        <div class="panel" style="margin:0">
          <div class="panel-title">🔒 Update Credentials</div>
          <form @submit.prevent="saveCreds">
            <div class="form-group">
              <label>Username</label>
              <input v-model="newUser" class="input-control" required autocomplete="username" placeholder="admin">
            </div>
            <div class="form-group">
              <label>New Password</label>
              <input v-model="newPass" type="password" class="input-control" required autocomplete="new-password" placeholder="Min 4 characters">
            </div>
            <button type="submit" class="btn btn-primary" style="width:100%;padding:0.75rem">Save Credentials</button>
          </form>
        </div>

        <!-- LAN Info -->
        <div class="panel" style="margin:0">
          <div class="panel-title">🔗 Network Info</div>
          <div class="info-row"><span class="info-label">Domain</span><code class="info-value">https://{{ status.domain }}</code></div>
          <div class="info-row"><span class="info-label">LAN IP</span><code class="info-value">https://{{ status.lan_ip }}</code></div>
          <div class="info-row"><span class="info-label">Dashboard</span><code class="info-value">https://{{ status.domain }}/admin/</code></div>
          <div class="info-row"><span class="info-label">API Port</span><code class="info-value">9199</code></div>
        </div>

        <!-- SSL Certificate -->
        <div class="panel" style="margin:0;border-color:rgba(99,102,241,0.25)">
          <div class="panel-title">📱 Install SSL on Devices</div>
          <div style="display:flex;flex-direction:column;gap:0.85rem;font-size:0.875rem;color:var(--muted)">
            <p style="margin:0">Install <strong style="color:var(--text)">rootCA.pem</strong> on your phone or other LAN devices to trust HTTPS.</p>
            <div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:0.85rem;display:flex;flex-direction:column;gap:0.6rem">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <span style="font-weight:600;color:var(--text)">rootCA.pem</span>
                <span :class="['badge', status.rootca_available ? 'badge-ok' : 'badge-warn']">
                  {{ status.rootca_available ? '✓ Ready' : '⚠ Not exported' }}
                </span>
              </div>
              <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
                <a :href="status.rootca_available ? '/admin/rootCA.pem' : '#'" download="rootCA.pem"
                  class="btn btn-primary btn-sm" style="flex:1;min-width:140px;text-decoration:none">
                  ⬇️ Download rootCA.pem
                </a>
                <button class="btn btn-outline btn-sm" style="flex:1;min-width:120px" @click="exportCA">🔄 Re-export</button>
              </div>
            </div>
            <div style="background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.15);border-radius:8px;padding:0.85rem">
              <div style="font-weight:600;color:#a5b4fc;margin-bottom:0.4rem">📲 Android</div>
              <ol style="margin:0;padding-left:1.1rem;line-height:1.9;font-size:0.82rem">
                <li>Install <a href="https://play.google.com/store/apps/details?id=com.virtual_switch_hosts.app" target="_blank" style="color:var(--accent2);font-weight:600">Virtual Switch Hosts</a></li>
                <li>Add host: <code style="font-size:0.78rem">{{ status.lan_ip }}  {{ status.domain }}</code></li>
                <li>Enable the switch</li>
                <li>Download &amp; install rootCA.pem → Settings → Security → CA Certificate</li>
                <li>Open <code style="font-size:0.78rem">https://{{ status.domain }}/admin/</code> 🎉</li>
              </ol>
            </div>
            <div style="background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.15);border-radius:8px;padding:0.85rem">
              <div style="font-weight:600;color:var(--green);margin-bottom:0.4rem">🖥️ Linux / Mac on LAN</div>
              <pre style="font-size:0.72rem;color:#94a3b8;background:rgba(0,0,0,0.3);padding:0.65rem;border-radius:6px;overflow-x:auto;white-space:pre-wrap">{{ linuxInstructions }}</pre>
            </div>
          </div>
        </div>

        <!-- Guardian Panel -->
        <div class="panel" style="margin:0;border-color:rgba(99,102,241,0.2)">
          <div class="panel-header">
            <div class="panel-title">🛡️ Neo The Guardian</div>
            <span v-if="pendingList.length" class="badge badge-err" style="animation:pulse-badge 1.5s infinite">● {{ pendingList.length }} Pending</span>
          </div>

          <div style="font-size:0.78rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:0.5rem">Access Requests</div>
          <div v-if="pendingList.length===0" style="color:var(--muted);font-size:0.82rem;font-style:italic;margin-bottom:1rem">No pending requests</div>
          <div v-for="ip in pendingList" :key="ip" style="background:rgba(244,63,94,0.06);border:1px solid rgba(244,63,94,0.2);border-radius:8px;padding:0.75rem;margin-bottom:0.6rem">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
              <span style="color:#fda4af;font-weight:600;font-size:0.875rem">📱 {{ ip }}</span>
              <button class="btn btn-danger btn-xs" @click="reject(ip)">✕ Reject</button>
            </div>
            <div style="display:flex;gap:0.35rem;flex-wrap:wrap">
              <span style="font-size:0.72rem;color:var(--muted);align-self:center">Approve for:</span>
              <button class="btn btn-xs" style="background:rgba(16,185,129,0.12);color:var(--green);border-color:rgba(16,185,129,0.3)" @click="approve(ip,'1d')">1 Day</button>
              <button class="btn btn-xs" style="background:rgba(16,185,129,0.12);color:var(--green);border-color:rgba(16,185,129,0.3)" @click="approve(ip,'7d')">7 Days</button>
              <button class="btn btn-xs" style="background:rgba(16,185,129,0.12);color:var(--green);border-color:rgba(16,185,129,0.3)" @click="approve(ip,'30d')">30 Days</button>
              <button class="btn btn-primary btn-xs" @click="approve(ip,'permanent')">✓ Permanent</button>
            </div>
          </div>

          <div style="font-size:0.78rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:0.5rem">Approved Devices</div>
          <div v-for="dev in status.allowed_devices||[]" :key="dev.ip" class="guardian-device-row">
            <div style="display:flex;align-items:center;gap:0.5rem">
              <span style="color:var(--green);font-size:0.75rem">●</span>
              <code style="font-size:0.8rem">{{ dev.ip }}</code>
              <span v-if="isSystemIp(dev.ip)" style="font-size:0.65rem;color:var(--muted);background:rgba(255,255,255,0.04);padding:0.1rem 0.35rem;border-radius:4px">
                {{ dev.ip === status.lan_ip ? 'Host' : 'System' }}
              </span>
            </div>
            <div style="display:flex;align-items:center;gap:0.4rem">
              <span v-if="dev.expiry" style="font-size:0.72rem;color:var(--yellow)">⏱ {{ dev.remaining_days }}d</span>
              <span v-else style="font-size:0.72rem;color:var(--muted)">Permanent</span>
              <button v-if="!isSystemIp(dev.ip)" class="btn-ghost" style="font-size:0.85rem;color:var(--muted);padding:0"
                @mouseenter="$event.target.style.color='var(--red)'"
                @mouseleave="$event.target.style.color='var(--muted)'"
                @click="revoke(dev.ip)" title="Revoke">✕</button>
            </div>
          </div>
        </div>
      </div>

      <!-- System Logs -->
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">📋 System Logs</div>
          <div style="display:flex;gap:0.35rem;flex-wrap:wrap;align-items:center">
            <button class="btn btn-outline btn-sm" :class="{active: logType==='api'}"     @click="loadLogs('api')"    title="API server log">🔌 API</button>
            <button class="btn btn-outline btn-sm" :class="{active: logType==='access'}"  @click="loadLogs('access')" title="All incoming requests">📥 Caddy Access</button>
            <button class="btn btn-outline btn-sm" :class="{active: logType==='error'}"   @click="loadLogs('error')"  title="Proxy errors and 5xx responses">⚠️ Caddy Error</button>
            <div style="width:1px;height:18px;background:var(--border-light);margin:0 0.1rem"></div>
            <button class="btn btn-outline btn-sm" @click="loadLogs(logType)" title="Refresh">🔄</button>
            <button class="btn btn-danger btn-sm"  @click="clearLogs" :disabled="!logType" title="Clear current log file">🗑 Clear</button>
          </div>
        </div>
        <div style="font-size:0.72rem;color:var(--muted);margin-bottom:0.5rem;display:flex;justify-content:space-between">
          <span v-if="logType">
            <strong style="color:var(--text)">{{ logLabels[logType] }}</strong>
            — {{ logType==='api' ? 'API server events & errors' : logType==='access' ? 'All HTTP requests proxied through Caddy' : 'Caddy proxy errors (502, connection refused, etc.)' }}
          </span>
          <span v-else style="font-style:italic">Select a log type above</span>
          <span v-if="logLines>0" style="color:var(--accent2)">{{ logLines }} lines</span>
        </div>
        <div class="console" ref="consoleEl">{{ logContent }}</div>
      </div>
    </div>
  `,
  setup(props, { emit }) {
    const confirm = inject('confirm');
    const newUser = ref(''), newPass = ref('');
    const pendingList = ref([]);
    const logContent = ref('Select a log type above…'), logType = ref(''), logLines = ref(0);
    const consoleEl = ref(null);
    const logLabels = { api: '🔌 API Log', access: '📥 Caddy Access', error: '⚠️ Caddy Error' };

    const linuxInstructions = computed(() => {
      const ip = props.status?.lan_ip || '192.168.x.x';
      const domain = props.status?.domain || 'dev.local';
      return `# 1. Add host entry:\necho "${ip}  ${domain}" | sudo tee -a /etc/hosts\n\n# 2. Download & trust CA:\ncurl -k https://${ip}/admin/rootCA.pem -o rootCA.pem\nsudo cp rootCA.pem /usr/local/share/ca-certificates/neo-localdev.crt\nsudo update-ca-certificates\n\n# 3. Chrome/Firefox NSS:\ncertutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "Neo LocalDev CA" -i rootCA.pem`;
    });

    // Pre-fill username
    watch(() => props.status?.admin_username, v => { if (v && !newUser.value) newUser.value = v; }, { immediate: true });

    async function saveCreds(e) {
      if (newPass.value.length < 4) { emit('toast', 'Password must be at least 4 characters.', true); return; }
      const r = await api('/security/update', 'POST', { username: newUser.value, password: newPass.value });
      emit('toast', r.ok ? 'Credentials updated. Logging out…' : r.error, !r.ok);
      if (r.ok) { newPass.value = ''; localStorage.setItem('neold_current_user', newUser.value); setTimeout(() => emit('logout'), 1500); }
    }

    async function loadPending() {
      const r = await api('/guardian/pending');
      if (r.ok) pendingList.value = r.requests;
    }

    async function approve(ip, duration) {
      const r = await api('/guardian/approve', 'POST', { ip, duration });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      emit('reload'); loadPending();
    }

    async function reject(ip) {
      await api('/guardian/reject', 'POST', { ip });
      emit('toast', `Rejected ${ip}`); loadPending();
    }

    async function revoke(ip) {
      if (!await confirm('Revoke Access', `Revoke access for ${ip}?`)) return;
      const r = await api('/guardian/revoke', 'POST', { ip });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      emit('reload');
    }

    async function exportCA() {
      const r = await api('/certs/export');
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      emit('reload');
    }

    async function loadLogs(type) {
      logType.value = type;
      logContent.value = `Loading ${type} logs…`;
      logLines.value = 0;
      const r = await api(`/logs?type=${type}&lines=80`);
      if (r.ok) {
        logContent.value = r.lines.join('\n') || '(empty log)';
        logLines.value = r.count || 0;
        // Auto-scroll to bottom
        Vue.nextTick(() => { if (consoleEl.value) consoleEl.value.scrollTop = consoleEl.value.scrollHeight; });
      } else {
        logContent.value = 'Failed to load logs: ' + (r.error || 'unknown error');
      }
    }

    async function clearLogs() {
      if (!logType.value) return;
      if (!await confirm('Clear Logs', `Clear the ${logType.value} log file? This cannot be undone.`)) return;
      const r = await api('/logs/clear', 'POST', { type: logType.value });
      emit('toast', r.ok ? r.message : r.error, !r.ok);
      if (r.ok) { logContent.value = '(log cleared)'; logLines.value = 0; }
    }

    function isSystemIp(ip) {
      return ip === '127.0.0.1' || ip === '::1' || ip === props.status?.lan_ip;
    }

    onMounted(() => { loadPending(); loadLogs('api'); });

    return { newUser, newPass, pendingList, logContent, logType, logLines, logLabels, consoleEl, linuxInstructions, saveCreds, approve, reject, revoke, exportCA, loadLogs, clearLogs, isSystemIp };
  }
};

// ── Mount App ─────────────────────────────────────────────────
const app = createApp(App);

app.component('ToastList',    ToastList);
app.component('LoginScreen',  LoginScreen);
app.component('Dashboard',    Dashboard);
app.component('GuardianPopup',GuardianPopup);
app.component('ProxiesTab',   ProxiesTab);
app.component('LauncherTab',  LauncherTab);
app.component('DatabaseTab',  DatabaseTab);
app.component('ServicesTab',  ServicesTab);
app.component('SettingsTab',  SettingsTab);
app.component('CustomModal',  CustomModal);

// Register sub-components on their parents
Dashboard.components = { GuardianPopup };

app.mount('#app');
