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

function relTime(ts) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60); if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60); if (h < 24) return h + 'h ago';
  const d = Math.floor(h / 24); if (d < 30) return d + 'd ago';
  const mo = Math.floor(d / 30); if (mo < 12) return mo + 'mo ago';
  return Math.floor(mo / 12) + 'y ago';
}

function formatDate(ts, format) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  const y = d.getFullYear(), mo = p(d.getMonth() + 1), day = p(d.getDate());
  const h24 = p(d.getHours()), mi = p(d.getMinutes());
  if (format === 'relative') return relTime(ts);
  if (format === 'us') {
    let h = d.getHours(); const ap = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return `${mo}/${day}/${y} ${h}:${mi} ${ap}`;
  }
  if (format === 'euro') return `${day}/${mo}/${y} ${h24}:${mi}`;
  return `${y}-${mo}-${day} ${h24}:${mi}`;
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
    case 'full': return ['media full', 'st-ok'];
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
$('openBrowser').onclick = openRootBrowser;
$('scrim').onclick = closeDrawers;
document.querySelectorAll('[data-close]').forEach((b) => {
  b.onclick = () => (b.dataset.close === 'browser' ? closeBrowser() : closeDrawers());
});
document.addEventListener('keydown', (e) => {
  // viewer is topmost: Esc closes it, arrow keys navigate (unless focus is in
  // a form control where arrows mean something else, like the zoom slider)
  if (!$('viewer').classList.contains('hidden')) {
    if (e.key === 'Escape') { closeViewer(); return; }
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT')) return;
    if (e.key === 'ArrowLeft') { viewerNav(-1); return; }
    if (e.key === 'ArrowRight') { viewerNav(1); return; }
    return;
  }
  if (e.key !== 'Escape') return;
  if (!$('browser').classList.contains('hidden')) closeBrowser();
  else closeDrawers();
});

/* ---------------- settings ---------------- */

function fillSettings() {
  const s = STATE.settings || {};
  $('s-root').value = s.root || '';
  $('s-board_dir').checked = !!s.board_dir;
  $('s-folder_mode').value = s.folder_mode || 'both';
  $('s-name_mode').value = s.name_mode || 'uploader';
  $('s-date_format').value = s.date_format || 'iso';
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
        date_format: $('s-date_format').value,
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

const PREVIEWS = { S: 64, M: 96, L: 144, XL: 200 };
let PREVIEW_SIZE = (function () {
  try { return localStorage.getItem('cr-preview') || 'M'; } catch (e) { return 'M'; }
})();

let BROWSER_PATH = '';
let LAST_ENTRIES = null;    // last folder listing, for re-layout on a size change
let FILES = [];             // ordered file entries (no dirs), for prev/next
const MARKED = new Set();   // paths marked for deletion in the current folder
const TILES = {};           // path -> tile element, for in-place mark updates

const VIEWER = { idx: -1, path: '' };
let ZOOM = 1.0;

let _mspan = null;
function measureText(s) {
  if (!_mspan) {
    _mspan = document.createElement('span');
    _mspan.style.cssText = 'position:absolute;left:-9999px;top:0;visibility:hidden;' +
      'white-space:nowrap;font:11.5px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;';
    document.body.appendChild(_mspan);
  }
  _mspan.textContent = s;
  return _mspan.offsetWidth;
}

function openBrowserPage() {
  closeDrawers();
  $('browser').classList.remove('hidden');
}

function closeBrowser() {
  closeViewer();
  $('browser').classList.add('hidden');
  MARKED.clear();
  LAST_ENTRIES = null;
  syncMarkUI();
}

async function openBrowser(board, no) {
  $('browser-title').textContent = `/${board}/${no}/`;
  openBrowserPage();
  const t = STATE.threads.find((x) => x.board === board && String(x.no) === no);
  let path = '';
  if (t && t.folder && STATE.settings.root && t.folder.startsWith(STATE.settings.root)) {
    path = t.folder.slice(STATE.settings.root.length).replace(/^[\\/]+/, '');
  }
  await browse(path);
}

// The "Browser" toolbar button: same files overlay, but it starts at the 4chan
// base folder (root/4chan) so deleted/untracked threads are still reachable.
async function openRootBrowser() {
  $('browser-title').textContent = 'Browser';
  openBrowserPage();
  if (!(await browse('4chan'))) {
    MARKED.clear();
    await browse('');          // no 4chan folder (board_dir off) -> show root
  }
}

async function browse(path) {
  try {
    const data = await api('/api/files?path=' + encodeURIComponent(path || ''));
    BROWSER_PATH = data.path || '';
    MARKED.clear();               // marks never outlive the folder you made them in
    renderCrumbs(data.path, data.parent);
    renderFiles(data.entries);
    return true;
  } catch (e) { toast('Failed: ' + e.message, true); return false; }
}

function setPreviewSize(size) {
  if (!PREVIEWS[size]) return;
  PREVIEW_SIZE = size;
  try { localStorage.setItem('cr-preview', size); } catch (e) { /* ignore */ }
  $('sizePick').querySelectorAll('[data-size]').forEach((b) => {
    b.classList.toggle('active', b.dataset.size === size);
  });
  if (LAST_ENTRIES) renderFiles(LAST_ENTRIES);
}

// Uniform tiles: width fits the widest name, height is the thumbnail preset
// plus a fixed meta block, so every box is the same size.
function applyGridSize(entries) {
  const th = PREVIEWS[PREVIEW_SIZE];
  let maxName = 0;
  (entries || []).forEach((f) => { maxName = Math.max(maxName, measureText(f.name)); });
  const tileW = Math.round(Math.min(Math.max(maxName + 18, 96), 320));
  $('files').style.setProperty('--thumb', th + 'px');
  $('files').style.setProperty('--tile-w', tileW + 'px');
}

function syncMarkUI() {
  const n = MARKED.size;
  const del = $('delMarked');
  del.disabled = n === 0;
  del.textContent = n ? `Delete marked files (${n})` : 'Delete marked files';
  const dl = $('dlMarked');
  dl.disabled = n === 0;
  dl.textContent = n ? `Download marked (${n})` : 'Download marked (.zip)';
  $('selcount').textContent = n ? `${n} marked` : '';
}

function setMarked(path, on) {
  if (on) MARKED.add(path); else MARKED.delete(path);
  const tile = TILES[path];
  if (tile) {
    tile.classList.toggle('marked', on);
    const box = tile.querySelector('.pick');
    if (box) box.checked = on;
  }
  if (!$('viewer').classList.contains('hidden') && VIEWER.path === path) {
    $('viewer-mark').checked = on;
  }
  syncMarkUI();
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
  LAST_ENTRIES = entries;
  FILES = entries.filter((f) => !f.dir);
  for (const k in TILES) delete TILES[k];

  if (!entries.length) {
    $('files').innerHTML = '<p class="muted small">empty</p>';
    syncMarkUI();
    return;
  }

  applyGridSize(entries);
  const fmt = (STATE.settings && STATE.settings.date_format) || 'iso';
  $('files').innerHTML = entries.map((f) => {
    const raw = '/api/files/raw?path=' + encodeURIComponent(f.path);
    let thumb = `<span>${esc(f.kind)}</span>`;
    if (f.kind === 'image') thumb = `<img loading="lazy" src="${esc(raw)}" alt="">`;
    const marked = !f.dir && MARKED.has(f.path);
    const pick = f.dir ? '' :
      `<input type="checkbox" class="pick" data-pick="${esc(f.path)}"` +
      ` title="Mark for deletion"${marked ? ' checked' : ''}>`;
    const date = f.mtime ? formatDate(f.mtime, fmt) : '';
    const meta = f.dir
      ? ('folder' + (date ? ' · ' + date : ''))
      : (human(f.size) + (date ? ' · ' + date : ''));
    return `<div class="file ${f.dir ? 'dir' : ''}${marked ? ' marked' : ''}" data-path="${esc(f.path)}">
      ${pick}
      <div class="thumb">${thumb}</div>
      <div class="meta">
        <span class="name">${esc(f.name)}</span>
        <span class="sz">${meta}</span>
      </div>
    </div>`;
  }).join('');

  $('files').querySelectorAll('.file').forEach((el) => { TILES[el.dataset.path] = el; });
  $('files').querySelectorAll('[data-pick]').forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();          // don't open the file when ticking the box
      setMarked(el.dataset.pick, el.checked);
    };
  });
  $('files').querySelectorAll('.file.dir').forEach((el) => {
    el.onclick = () => browse(el.dataset.path);
  });
  $('files').querySelectorAll('.file:not(.dir)').forEach((el) => {
    el.onclick = () => openViewer(el.dataset.path);
  });
  syncMarkUI();
}

/* ---------------- item preview overlay ---------------- */

function openViewer(path) {
  const idx = FILES.findIndex((f) => f.path === path);
  if (idx === -1) return;
  VIEWER.idx = idx;
  VIEWER.path = path;
  $('viewer').classList.remove('hidden');
  renderViewer();
}

function closeViewer() {
  $('viewer').classList.add('hidden');
  const v = $('viewer-video'); v.pause(); v.removeAttribute('src'); v.load();
  const a = $('viewer-audio'); a.pause(); a.removeAttribute('src'); a.load();
  $('viewer-img').removeAttribute('src');
  VIEWER.path = '';
}

function viewerNav(dir) {
  const ni = VIEWER.idx + dir;
  if (ni < 0 || ni >= FILES.length) return;
  VIEWER.idx = ni;
  renderViewer();
}

function renderViewer() {
  const f = FILES[VIEWER.idx];
  if (!f) { closeViewer(); return; }
  VIEWER.path = f.path;
  $('viewer-name').textContent = f.name;
  $('viewer-count').textContent = `${VIEWER.idx + 1} / ${FILES.length}`;
  $('viewer-mark').checked = MARKED.has(f.path);

  const raw = '/api/files/raw?path=' + encodeURIComponent(f.path);
  const dl = raw + '&download=true';

  ZOOM = 1.0;
  $('viewer-zoom').value = 100;
  $('viewer-zoom-label').textContent = '100%';
  $('viewer-img').hidden = true;
  $('viewer-video').hidden = true;
  $('viewer-audio').hidden = true;
  $('viewer-other').hidden = true;
  $('viewer-zoom').disabled = true;

  $('viewer-download').onclick = () => { window.location = dl; };
  $('viewer-prev').disabled = VIEWER.idx === 0;
  $('viewer-next').disabled = VIEWER.idx === FILES.length - 1;

  if (f.kind === 'image') {
    $('viewer-zoom').disabled = false;
    const img = $('viewer-img');
    img.hidden = false;
    img.onload = () => applyZoom();
    img.src = raw;
  } else if (f.kind === 'video') {
    const v = $('viewer-video');
    v.hidden = false;
    v.src = raw;
  } else if (f.kind === 'audio') {
    const a = $('viewer-audio');
    a.hidden = false;
    a.src = raw;
  } else {
    $('viewer-other').hidden = false;
    $('viewer-other-dl').onclick = () => { window.location = dl; };
  }
}

function applyZoom() {
  const img = $('viewer-img');
  if (img.hidden) return;
  const iw = img.naturalWidth, ih = img.naturalHeight;
  if (!iw || !ih) return;
  const stage = $('viewer-stage');
  const scale = Math.min(stage.clientWidth / iw, stage.clientHeight / ih);
  img.style.width = (iw * scale * ZOOM) + 'px';
  img.style.height = (ih * scale * ZOOM) + 'px';
}

function setZoom(z) {
  ZOOM = Math.min(6, Math.max(0.25, z));
  $('viewer-zoom').value = Math.round(ZOOM * 100);
  $('viewer-zoom-label').textContent = Math.round(ZOOM * 100) + '%';
  applyZoom();
}

/* ---------------- wiring ---------------- */

$('sizePick').querySelectorAll('[data-size]').forEach((b) => {
  b.classList.toggle('active', b.dataset.size === PREVIEW_SIZE);
  b.onclick = () => setPreviewSize(b.dataset.size);
});

$('markAll').onclick = () => {
  const boxes = [...document.querySelectorAll('#files [data-pick]')];
  if (!boxes.length) return;
  const all = boxes.every((b) => b.checked);
  boxes.forEach((b) => setMarked(b.dataset.pick, !all));
};

$('delMarked').onclick = async () => {
  const paths = [...MARKED];
  if (!paths.length) return;
  const noun = paths.length === 1 ? 'file' : 'files';
  if (!confirm(`Delete ${paths.length} marked ${noun} from disk?\n\nThis cannot be undone.`)) return;
  const btn = $('delMarked');
  btn.disabled = true;
  try {
    const res = await api('/api/files/delete', {
      method: 'POST', body: JSON.stringify({ paths }),
    });
    MARKED.clear();
    await browse(BROWSER_PATH);
    toast(`Deleted ${res.deleted.length} ${noun} (${human(res.bytes)})` +
          (res.failed.length ? ` · ${res.failed.length} failed` : ''));
    if (res.failed.length) console.warn('delete failures', res.failed);
  } catch (e) {
    toast('Delete failed: ' + e.message, true);
    syncMarkUI();
  }
};

$('dlMarked').onclick = async () => {
  const paths = [...MARKED];
  if (!paths.length) return;
  const btn = $('dlMarked');
  btn.disabled = true;
  try {
    const res = await fetch('/api/files/zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'chanripper-files.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast(`Zipped ${paths.length} file${paths.length === 1 ? '' : 's'}`);
  } catch (e) {
    toast('Zip failed: ' + e.message, true);
  } finally {
    syncMarkUI();
  }
};

$('viewer-close').onclick = closeViewer;
$('viewer-prev').onclick = () => viewerNav(-1);
$('viewer-next').onclick = () => viewerNav(1);
$('viewer-mark').onchange = () => {
  const f = FILES[VIEWER.idx];
  if (f) setMarked(f.path, $('viewer-mark').checked);
};
$('viewer-zoom').oninput = () => setZoom(Number($('viewer-zoom').value) / 100);
$('viewer-img').ondblclick = () => setZoom(1);
$('viewer-stage').addEventListener('wheel', (e) => {
  if ($('viewer').classList.contains('hidden')) return;
  const f = FILES[VIEWER.idx];
  if (!f || f.kind !== 'image') return;
  e.preventDefault();
  setZoom(ZOOM * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
}, { passive: false });
window.addEventListener('resize', () => {
  if (!$('viewer').classList.contains('hidden')) applyZoom();
});

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
