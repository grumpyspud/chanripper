/* ChanRipper web UI — no build step, no framework. */
'use strict';

let STATE = { threads: [], settings: {}, monitoring: false };
let SETTINGS_OPEN = false;

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function human(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' B';
  const u = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return n.toFixed(1) + ' ' + u[i];
}

function hhmmss(unix) {
  if (!unix) return '—';
  const d = new Date(unix * 1000);
  return d.toLocaleTimeString();
}

function toast(msg, bad) {
  const t = $('toast');
  t.textContent = msg;
  t.style.borderColor = bad ? 'var(--bad)' : 'var(--line)';
  t.classList.add('on');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('on'), 4000);
}

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* ---------------- table ---------------- */

function statusCell(t) {
  if (t.checking) return ['checking…', 'st-wait'];
  switch (t.status) {
    case 'ok':
    case 'unchanged': return ['up to date', 'st-ok'];
    case 'dead': return ['DEAD (404)', 'st-bad'];
    case 'error': return ['error: ' + (t.error || '').slice(0, 40), 'st-bad'];
    default: return [t.status || 'idle', 'st-wait'];
  }
}

function renderRows() {
  const rows = $('rows');
  const threads = STATE.threads;
  $('empty').classList.toggle('hidden', threads.length > 0);
  rows.innerHTML = threads.map((t) => {
    const [label, cls] = statusCell(t);
    // While a check is in flight there is no next-check time yet, so show the
    // idle placeholder instead of "now": the STATUS column already says
    // "checking…", and a wider word here resized the column on every tick.
    let next = '—';
    if (!t.checking && t.next_in != null) next = Math.max(0, Math.round(t.next_in)) + 's';
    return `<tr data-k="${esc(t.board)}|${t.no}">
      <td>/${esc(t.board)}/</td>
      <td class="num">${t.no}</td>
      <td class="subject" title="${esc(t.subject)}">${esc(t.subject)}</td>
      <td class="num">${t.post_count || 0}</td>
      <td class="num">${t.media_count || 0}</td>
      <td class="num">${human(t.media_bytes)}</td>
      <td class="num">${hhmmss(t.last_check)}</td>
      <td class="num next" data-next="${!t.checking && t.next_in != null ? Math.max(0, Math.round(t.next_in)) : ''}">${next}</td>
      <td><input type="number" class="delay" min="2" step="1" value="${Math.round(t.delay || 10)}"></td>
      <td><input type="checkbox" class="mon" ${t.monitor ? 'checked' : ''}></td>
      <td><input type="checkbox" class="com" ${t.comments ? 'checked' : ''}></td>
      <td class="${cls}">${esc(label)}</td>
      <td class="acts">
        <button class="mini" data-act="files">Files</button>
        <button class="mini" data-act="check">Check</button>
        <button class="mini" data-act="zip">Zip</button>
        <button class="mini danger" data-act="del">✕</button>
      </td>
    </tr>`;
  }).join('');
}

function summary() {
  const n = STATE.threads.filter((t) => t.monitor).length;
  const media = STATE.threads.reduce((a, t) => a + (t.media_count || 0), 0);
  $('summary').textContent =
    `${STATE.threads.length} thread(s) · ${n} monitored · ${media} media · ` +
    (STATE.monitoring ? 'monitor running' : 'monitor stopped');
}

function applyThread(t) {
  const i = STATE.threads.findIndex((x) => x.board === t.board && x.no === t.no);
  if (i === -1) STATE.threads.push(t);
  else STATE.threads[i] = Object.assign({}, STATE.threads[i], t);
}

async function loadState() {
  STATE = await api('/api/state');
  $('version').textContent = 'v' + STATE.version;
  renderRows();
  summary();
  if (SETTINGS_OPEN) fillSettings();
}

/* ---------------- live events ---------------- */

function connect() {
  const es = new EventSource('/api/events');
  es.onopen = () => {
    $('conn').textContent = 'live';
    $('conn').className = 'pill pill-ok';
  };
  es.onerror = () => {
    $('conn').textContent = 'reconnecting…';
    $('conn').className = 'pill pill-warn';
  };
  es.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'sync' && msg.thread) {
      applyThread(msg.thread);
      renderRows();
      summary();
      if (msg.new_posts || msg.new_media) {
        toast(`/${msg.board}/${msg.no}: +${msg.new_posts} posts, +${msg.new_media} media`);
      }
      if (msg.status === 'dead') toast(`/${msg.board}/${msg.no} is dead (404)`, true);
    }
  };
}

/* ---------------- actions ---------------- */

$('add').onclick = async () => {
  const url = $('url').value.trim();
  if (!url) return;
  $('busy').textContent = 'fetching…';
  try {
    const res = await api('/api/threads', {
      method: 'POST',
      body: JSON.stringify({
        url,
        monitor: $('monitor').checked,
        comments: $('comments').checked,
      }),
    });
    applyThread(res.thread);
    renderRows();
    summary();
    $('url').value = '';
    toast(`saved /${res.board}/${res.no} — +${res.new_posts} posts, +${res.new_media} media`);
  } catch (e) {
    toast('Failed: ' + e.message, true);
  } finally {
    $('busy').textContent = '';
  }
};

$('url').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('add').click(); });

$('rows').addEventListener('change', async (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  const [board, no] = tr.dataset.k.split('|');
  const body = {};
  if (e.target.classList.contains('delay')) body.delay = Number(e.target.value);
  if (e.target.classList.contains('mon')) body.monitor = e.target.checked;
  if (e.target.classList.contains('com')) body.comments = e.target.checked;
  if (!Object.keys(body).length) return;
  try {
    applyThread(await api(`/api/threads/${board}/${no}`, {
      method: 'PATCH', body: JSON.stringify(body),
    }));
    renderRows();
    summary();
  } catch (err) { toast('Failed: ' + err.message, true); }
});

$('rows').addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const tr = btn.closest('tr');
  const [board, no] = tr.dataset.k.split('|');
  const act = btn.dataset.act;
  if (act === 'files') return openBrowser(board, no);
  if (act === 'zip') { window.location = `/api/threads/${board}/${no}/zip`; return; }
  if (act === 'check') {
    $('busy').textContent = `checking /${board}/${no}…`;
    try {
      const res = await api(`/api/threads/${board}/${no}/check`, { method: 'POST' });
      applyThread(res.thread); renderRows();
      toast(`/${board}/${no}: +${res.new_posts} posts, +${res.new_media} media`);
    } catch (err) { toast('Failed: ' + err.message, true); }
    finally { $('busy').textContent = ''; }
    return;
  }
  if (act === 'del') {
    if (!confirm(`Remove /${board}/${no} from the list?\n\nFiles already downloaded stay on disk.`)) return;
    try {
      await api(`/api/threads/${board}/${no}`, { method: 'DELETE' });
      STATE.threads = STATE.threads.filter((t) => !(t.board === board && String(t.no) === no));
      renderRows(); summary();
    } catch (err) { toast('Failed: ' + err.message, true); }
  }
});

$('startAll').onclick = async () => {
  await api('/api/monitor/start', { method: 'POST' });
  await loadState();
  toast('monitoring started');
};
$('stopAll').onclick = async () => {
  await api('/api/monitor/stop', { method: 'POST' });
  await loadState();
  toast('monitoring stopped');
};
$('refresh').onclick = () => loadState();

/* ---------------- drawers ---------------- */

function openDrawer(id) {
  $(id).classList.add('on');
  $('scrim').classList.add('on');
  if (id === 'settings') { SETTINGS_OPEN = true; fillSettings(); }
}
function closeDrawers() {
  document.querySelectorAll('.drawer').forEach((d) => d.classList.remove('on'));
  $('scrim').classList.remove('on');
  SETTINGS_OPEN = false;
}
$('openSettings').onclick = () => openDrawer('settings');
$('scrim').onclick = closeDrawers;
document.querySelectorAll('[data-close]').forEach((b) => { b.onclick = closeDrawers; });

/* ---------------- settings ---------------- */

function fillSettings() {
  const s = STATE.settings || {};
  $('s-root').value = s.root || '';
  $('s-board_dir').checked = !!s.board_dir;
  $('s-folder_mode').value = s.folder_mode || 'both';
  $('s-name_mode').value = s.name_mode || 'uploader';
  $('s-default_delay').value = Math.round(s.default_delay || 10);
  $('s-default_comments').checked = !!s.default_comments;
  $('s-max_workers').value = s.max_workers || 3;
  $('s-media_workers').value = s.media_workers || 4;
  $('s-request_interval').value = s.request_interval || 1;
  delayNote();
}

function delayNote() {
  const v = Number($('s-default_delay').value);
  const floor = STATE.delay_floor || 2;
  const warn = STATE.delay_warn_below || 10;
  $('s-delay-note').textContent = v < warn
    ? `Below ${warn}s you are polling faster than 4chan's own refresh. Floor is ${floor}s.`
    : '';
}
$('s-default_delay').addEventListener('input', delayNote);

$('saveSettings').onclick = async () => {
  try {
    STATE.settings = await api('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({
        root: $('s-root').value.trim(),
        board_dir: $('s-board_dir').checked,
        folder_mode: $('s-folder_mode').value,
        name_mode: $('s-name_mode').value,
        default_delay: Number($('s-default_delay').value),
        default_comments: $('s-default_comments').checked,
        max_workers: Number($('s-max_workers').value),
        media_workers: Number($('s-media_workers').value),
        request_interval: Number($('s-request_interval').value),
      }),
    });
    fillSettings();
    toast('settings saved');
  } catch (e) { toast('Failed: ' + e.message, true); }
};

/* ---------------- file browser ---------------- */

let BROWSER_PATH = '';

async function openBrowser(board, no) {
  $('browser-title').textContent = `/${board}/${no}/`;
  openDrawer('browser');
  const t = STATE.threads.find((x) => x.board === board && String(x.no) === no);
  let path = '';
  if (t && t.folder && STATE.settings.root && t.folder.startsWith(STATE.settings.root)) {
    path = t.folder.slice(STATE.settings.root.length).replace(/^[\\/]+/, '');
  }
  await browse(path);
}

async function browse(path) {
  try {
    const data = await api('/api/files?path=' + encodeURIComponent(path || ''));
    BROWSER_PATH = data.path || '';
    renderCrumbs(data.path, data.parent);
    renderFiles(data.entries);
  } catch (e) { toast('Failed: ' + e.message, true); }
}

function renderCrumbs(path, parent) {
  const parts = (path || '').split('/').filter(Boolean);
  let html = `<a data-go="">root</a>`;
  let acc = '';
  parts.forEach((p) => {
    acc = acc ? acc + '/' + p : p;
    html += ` / <a data-go="${esc(acc)}">${esc(p)}</a>`;
  });
  if (parent !== null && parent !== undefined) {
    html = `<a data-go="${esc(parent)}">↑ up</a> &nbsp;` + html;
  }
  $('crumbs').innerHTML = html;
  $('crumbs').querySelectorAll('[data-go]').forEach((a) => {
    a.onclick = () => browse(a.dataset.go);
  });
}

function renderFiles(entries) {
  if (!entries.length) { $('files').innerHTML = '<p class="muted small">empty</p>'; return; }
  $('files').innerHTML = entries.map((f) => {
    const raw = '/api/files/raw?path=' + encodeURIComponent(f.path);
    let thumb = `<span>${esc(f.kind)}</span>`;
    if (f.kind === 'image') thumb = `<img loading="lazy" src="${esc(raw)}" alt="">`;
    const click = f.dir ? `data-dir="${esc(f.path)}"` : `data-open="${esc(raw)}"`;
    return `<div class="file ${f.dir ? 'dir' : ''}" ${click}>
      <div class="thumb">${thumb}</div>
      <div class="meta">
        <span class="name">${esc(f.name)}</span>
        <span class="sz">${f.dir ? 'folder' : human(f.size)}</span>
      </div>
    </div>`;
  }).join('');
  $('files').querySelectorAll('[data-dir]').forEach((el) => {
    el.onclick = () => browse(el.dataset.dir);
  });
  $('files').querySelectorAll('[data-open]').forEach((el) => {
    el.onclick = () => window.open(el.dataset.open, '_blank');
  });
}

/* ---------------- boot ---------------- */

loadState().catch((e) => toast('Load failed: ' + e.message, true));
connect();

// Local countdown for the "Next" column only — re-rendering the whole table
// every second would steal focus from the delay inputs while you type.
setInterval(() => {
  const byKey = {};
  STATE.threads.forEach((t) => { byKey[t.board + '|' + t.no] = t; });
  document.querySelectorAll('#rows tr').forEach((tr) => {
    const t = byKey[tr.dataset.k];
    const cell = tr.querySelector('.next');
    if (!t || !cell) return;
    if (t.checking) { cell.textContent = '—'; return; }
    if (t.next_in != null) {
      t.next_in = Math.max(0, t.next_in - 1);
      cell.textContent = Math.round(t.next_in) + 's';
    }
  });
}, 1000);

// periodic resync as a safety net behind the SSE stream
setInterval(() => loadState().catch(() => {}), 15000);
