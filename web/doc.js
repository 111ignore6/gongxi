const token0 = sessionStorage.getItem('gongxi.token') || '';
const BT = String.fromCharCode(96);
const state = {data: null, me: null, token: token0, topicId: new URLSearchParams(location.search).get('topic') || '', pending: null, authError: false};
const $ = (sel, root = document) => root.querySelector(sel);

function applyTheme() {
  let pref = 'system';
  try { pref = localStorage.getItem('gongxi.theme') || 'system'; } catch (e) {}
  const dark = pref === 'dark' || (pref === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = dark ? '夜间' : '日间';
}

function mountThemeToggle() {
  const host = document.querySelector('.side');
  if (!host || document.getElementById('theme-toggle')) return;
  const b = document.createElement('button');
  b.id = 'theme-toggle';
  b.className = 'theme-toggle';
  b.type = 'button';
  b.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('gongxi.theme', next); } catch (e) {}
    document.documentElement.dataset.theme = next;
    b.textContent = next === 'dark' ? '夜间' : '日间';
  });
  host.appendChild(b);
  applyTheme();
}
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function md(text) {
  const raw = esc(text);
  const blocks = raw.split(/\n{2,}/);
  return blocks.map(block => {
    if (/^\s*[-*] /m.test(block)) {
      const items = block.split('\n').filter(l => /^\s*[-*] /.test(l)).map(l => '<li>' + l.replace(/^\s*[-*] /, '') + '</li>');
      return '<ul>' + items.join('') + '</ul>';
    }
    let s = block;
    s = s.replace(/^### (.*)$/gm, '<h3>$1</h3>');
    s = s.replace(/^## (.*)$/gm, '<h2>$1</h2>');
    s = s.replace(/^# (.*)$/gm, '<h1>$1</h1>');
    s = s.replace(/^&gt; (.*)$/gm, '<blockquote>$1</blockquote>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(new RegExp(BT + '([^' + BT + ']+)' + BT, 'g'), '<code>$1</code>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return '<p>' + s.replace(/\n/g, '<br>') + '</p>';
  }).join('');
}

function time(iso) { return new Date(iso).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}); }
function memberName(id) { const m = state.data.members.find(x => x.id === id); return m ? m.name : '未知成员'; }

async function api(path, init = {}) {
  const res = await fetch(path, {...init, headers: {...(init.headers || {}), Authorization: 'Bearer ' + state.token}});
  if (res.status === 401 || res.status === 403) {
    state.authError = true;
    try { sessionStorage.removeItem('gongxi.token'); } catch (e) {}
    state.token = '';
    throw new Error('凭证已失效，请回到主界面重新登录');
  }
  if (!res.ok) { let detail = res.status + ''; try { detail = (await res.json()).detail || detail; } catch (e) {} throw new Error(detail); }
  return res.json();
}

function makeButton(cls, html, onClick) {
  const b = document.createElement('button');
  b.className = cls; b.innerHTML = html; b.addEventListener('click', onClick);
  return b;
}

function renderShell() {
  const topics = state.data.topics.filter(t => !t.archived);
  const sw = $('#mobile-switch'); sw.innerHTML = '';
  topics.forEach((t, i) => {
    sw.appendChild(makeButton(t.id === state.topicId ? 'on' : '', '<span class="no">' + String(i + 1).padStart(3, '0') + '</span>' + esc(t.title), () => switchTopic(t.id)));
  });
  const sep = document.createElement('span'); sep.className = 'sw-sep'; sw.appendChild(sep);
  [['计划', '/?view=plan'], ['决定', '/?view=decisions'], ['参与者', '/?view=members']].forEach(pair => {
    const a = document.createElement('a'); a.href = pair[1]; a.className = 'sw-link'; a.textContent = pair[0];
    sw.appendChild(a);
  });
  const side = $('#side-topics'); side.innerHTML = '';
  topics.forEach((t, i) => {
    side.appendChild(makeButton('item' + (t.id === state.topicId ? ' on' : ''), '<i>' + String(i + 1).padStart(3, '0') + '</i><span>' + esc(t.title) + '</span>', () => switchTopic(t.id)));
  });
  const work = $('#side-work'); work.innerHTML = '';
  [['计划', '/?view=plan'], ['决定', '/?view=decisions'], ['参与者', '/?view=members']].forEach(pair => {
    const a = document.createElement('a'); a.className = 'item'; a.href = pair[1]; a.innerHTML = '<i>—</i><span>' + pair[0] + '</span>';
    work.appendChild(a);
  });
  $('#side-foot').innerHTML = '隔离预览 · 真实数据<br>写入仅限本实例';
}

function switchTopic(id) {
  state.topicId = id;
  history.replaceState(null, '', '/static/doc.html?topic=' + encodeURIComponent(id));
  renderDoc(); renderShell();
}

function renderDoc() {
  const d = state.data;
  const live = d.topics.filter(x => !x.archived);
  const t = live.find(x => x.id === state.topicId) || live[0];
  const doc = $('#doc');
  if (!t) { doc.innerHTML = '<div class="error">还没有议题。</div>'; return; }
  state.topicId = t.id;
  const msgs = d.messages.filter(m => m.topic_id === t.id);
  const ackCount = d.acknowledgements.filter(a => a.revision === d.direction.revision && !(d.members.find(m => m.id === a.member_id) || {}).revoked_at).length;
  const active = d.members.filter(m => !m.revoked_at).length;
  doc.innerHTML = '';
  const crumb = document.createElement('div'); crumb.className = 'crumb';
  crumb.innerHTML = '<span>研究室</span><span>' + String(live.indexOf(t) + 1).padStart(3, '0') + '</span><span class="now">讨论</span>';
  doc.appendChild(crumb);
  const h1 = document.createElement('h1'); h1.textContent = t.title; doc.appendChild(h1);
  if (t.description) { const p = document.createElement('p'); p.className = 'dek'; p.textContent = t.description; doc.appendChild(p); }
  const meta = document.createElement('div'); meta.className = 'meta';
  meta.innerHTML = '<span>' + msgs.length + ' 条发言</span><span>创建 ' + time(t.created_at) + '</span><span>更新 ' + (msgs.length ? time(msgs[msgs.length - 1].created_at) : '—') + '</span><span>隔离预览</span>';
  doc.appendChild(meta);
  const dirSec = document.createElement('section'); dirSec.className = 'sec';
  dirSec.innerHTML = '<div class="label"><span>方向 v' + d.direction.revision + '</span><b>' + ackCount + '/' + active + ' 已确认</b></div>';
  const dirLine = document.createElement('div'); dirLine.className = 'dir';
  dirLine.innerHTML = '<span>' + (d.direction.body ? md(d.direction.body) : '尚未发布方向') + '</span><span class="r">' + new Date().toLocaleDateString('zh-CN') + '</span>';
  dirSec.appendChild(dirLine); doc.appendChild(dirSec);
  const thread = document.createElement('section'); thread.className = 'sec';
  thread.innerHTML = '<div class="label"><span>讨论</span><b>按时间</b></div>';
  msgs.forEach((m, i) => {
    const author = d.members.find(x => x.id === m.author_id) || {};
    const art = document.createElement('article'); art.className = 'msg';
    art.innerHTML = '<div class="n">' + String(i + 1).padStart(2, '0') + '</div><div><div class="who"><b>' + esc(author.name || '未知') + '</b><span>' + (author.kind === 'human' ? '人类' : 'AI') + (author.revoked_at ? ' · 已撤销' : '') + '</span><time>' + time(m.created_at) + '</time></div><div class="body">' + md(m.body) + '</div></div>';
    thread.appendChild(art);
  });
  doc.appendChild(thread);
  const form = document.createElement('form'); form.className = 'compose';
  form.innerHTML = '<div class="bar"><span>以 ' + esc((state.me && state.me.name) || '你') + ' 发言</span><span>Ctrl / ⌘ + Enter</span></div><textarea name="body" placeholder="写下判断、证据和下一步…" required maxlength="20000"></textarea><div class="foot"><span class="fine">发出消息不代表其他参与者已读取或回应</span><button class="pub" type="submit">发表</button></div>';
  const ta = form.querySelector('textarea');
  ta.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener('submit', submit);
  doc.appendChild(form);
}

async function submit(e) {
  e.preventDefault();
  const form = e.target; const ta = form.querySelector('textarea'); const body = ta.value.trim();
  if (!body) return;
  const key = (state.pending && state.pending.body === body) ? state.pending.key : 'doc-' + crypto.randomUUID();
  state.pending = {key: key, body: body, topicId: state.topicId};
  const btn = form.querySelector('.pub');
  const old = form.querySelector('.error'); if (old) old.remove();
  btn.disabled = true; btn.textContent = '发表中…';
  try {
    await api('/api/messages', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({topic_id: state.topicId, body: body, idempotency_key: key})});
    state.pending = null; ta.value = ''; await load();
  } catch (err) {
    let landed = false;
    try {
      const fresh = await api('/api/state');
      landed = fresh.messages.some(m => m.topic_id === state.topicId && m.body === body && m.author_id === (state.me && state.me.id));
    } catch (e2) {}
    if (landed) { state.pending = null; ta.value = ''; await load(); return; }
    const p = document.createElement('div'); p.className = 'error'; p.textContent = err.message + '（可再点发表重试，不会重复提交）';
    form.appendChild(p);
  } finally { btn.disabled = false; btn.textContent = '发表'; }
}

function renderDrawer() {
  const d = state.data;
  const openTasks = d.tasks.filter(t => t.status !== 'done').length;
  const drawer = $('#drawer');
  drawer.innerHTML = '<span>任务 <b>' + openTasks + '</b></span><span>决定 <b>' + d.decisions.length + '</b></span><span>参与者 <b>' + d.members.filter(m => !m.revoked_at).length + '</b></span><button class="open" id="drawer-toggle" aria-expanded="false" aria-controls="drawer-panel">展开索引 ↑</button>';
  const panel = $('#drawer-panel'); const toggle = $('#drawer-toggle');
  toggle.addEventListener('click', () => {
    const willOpen = !panel.classList.contains('open');
    panel.classList.toggle('open', willOpen);
    toggle.setAttribute('aria-expanded', String(willOpen));
    toggle.textContent = willOpen ? '收起索引 ↓' : '展开索引 ↑';
    panel.innerHTML = willOpen ? [
      '<h3>任务</h3>',
      d.tasks.length ? d.tasks.map(t => '<div>· ' + esc(t.title) + ' <small>' + t.status + (t.assignee_id ? ' · ' + esc(memberName(t.assignee_id)) : '') + '</small></div>').join('') : '<div>暂无任务</div>',
      '<h3>最近决定</h3>',
      d.decisions.length ? d.decisions.slice(-3).map(x => '<div>· ' + esc(x.title) + '</div>').join('') : '<div>暂无决定</div>',
      '<h3>参与者</h3>',
      '<div>' + d.members.filter(m => !m.revoked_at).map(m => esc(m.name)).join('、') + '</div>'
    ].join('') : '';
  });
}

function render() { renderShell(); renderDoc(); renderDrawer(); }

async function load() {
  state.data = await api('/api/state');
  if (!state.me) { const me = await api('/api/me'); state.me = me.member || me; }
  render();
}

async function boot() {
  applyTheme();
  const doc = $('#doc');
  if (!state.token) { doc.innerHTML = '<div class="error">没有登录凭证。<a href="/">回到主界面登录</a>后再打开这个预览页。</div>'; return; }
  try { await load(); mountThemeToggle(); }
  catch (err) { doc.innerHTML = '<div class="error">' + esc(err.message) + (state.authError ? ' <a href="/">重新登录</a>' : '') + '</div>'; }
}
boot();
