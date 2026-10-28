const token = sessionStorage.getItem('gongxi.token') || '';
const state = {data: null, me: null, topicId: new URLSearchParams(location.search).get('topic') || '', error: ''};

function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function time(iso) { const d = new Date(iso); return d.toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}); }
function name(id) { const m = state.data.members.find(x => x.id === id); return m ? m.name : '未知成员'; }

async function api(path, init = {}) {
  const res = await fetch(path, {...init, headers: {...(init.headers || {}), Authorization: 'Bearer ' + token}});
  if (!res.ok) { let detail = res.status + ''; try { detail = (await res.json()).detail || detail; } catch {} throw new Error(detail); }
  return res.json();
}

function topicList(target, prefix) {
  target.innerHTML = '';
  const topics = state.data.topics.filter(t => !t.archived).sort((a, b) => a.created_at.localeCompare(b.created_at));
  topics.forEach((t, i) => {
    const b = el('button', 'item' + (t.id === state.topicId ? ' on' : ''));
    b.innerHTML = '<i>' + prefix + String(i + 1).padStart(3, '0') + '</i><span>' + esc(t.title) + '</span>';
    b.addEventListener('click', () => { state.topicId = t.id; history.replaceState(null, '', '/doc.html?topic=' + encodeURIComponent(t.id)); render(); });
    target.appendChild(b);
  });
}

function renderShell() {
  const d = state.data;
  const sw = document.getElementById('mobile-switch');
  sw.innerHTML = '';
  d.topics.filter(t => !t.archived).forEach((t, i) => {
    const b = el('button', t.id === state.topicId ? 'on' : '');
    b.innerHTML = '<span class="no">' + String(i + 1).padStart(3, '0') + '</span>' + esc(t.title);
    b.addEventListener('click', () => { state.topicId = t.id; history.replaceState(null, '', '/doc.html?topic=' + encodeURIComponent(t.id)); render(); });
    sw.appendChild(b);
  });
  topicList(document.getElementById('side-topics'), '');
  const work = document.getElementById('side-work');
  work.innerHTML = '';
  [['计划', '/?view=plan'], ['决定', '/?view=decisions'], ['参与者', '/?view=members']].forEach(([label, href]) => {
    const a = el('button', 'item'); a.innerHTML = '<i>—</i><span>' + label + '</span>';
    a.addEventListener('click', () => { location.href = href; });
    work.appendChild(a);
  });
  document.getElementById('side-foot').innerHTML = '预览页 · 真实数据<br>只读除发言外';
}

function renderDoc() {
  const d = state.data;
  const live = d.topics.filter(x => !x.archived); const t = live.find(x => x.id === state.topicId) || live[0];
  if (!t) { document.getElementById('doc').innerHTML = '<div class="error">还没有议题。</div>'; return; }
  state.topicId = t.id;
  const msgs = d.messages.filter(m => m.topic_id === t.id);
  const ackCount = d.acknowledgements.filter(a => a.revision === d.direction.revision && !(d.members.find(m => m.id === a.member_id) || {}).revoked_at).length;
  const active = d.members.filter(m => !m.revoked_at).length;
  const doc = document.getElementById('doc');
  doc.innerHTML = '';
  const crumb = el('div', 'crumb'); crumb.innerHTML = '<span>研究室</span><span>' + String(d.topics.indexOf(t) + 1).padStart(3, '0') + '</span><span class="now">讨论</span>';
  doc.appendChild(crumb);
  const h1 = el('h1', null, t.title); doc.appendChild(h1);
  if (t.description) doc.appendChild(el('p', 'dek', t.description));
  const meta = el('div', 'meta');
  meta.innerHTML = '<span>' + msgs.length + ' 条发言</span><span>创建 ' + time(t.created_at) + '</span><span>更新 ' + (msgs.length ? time(msgs[msgs.length - 1].created_at) : '—') + '</span><span>真实数据</span>';
  doc.appendChild(meta);

  const dirSec = el('section', 'sec');
  dirSec.innerHTML = '<div class="label"><span>方向 v' + d.direction.revision + '</span><b>' + ackCount + '/' + active + ' 已确认</b></div>';
  const dirLine = el('div', 'dir');
  dirLine.innerHTML = '<span>' + esc(d.direction.body || '尚未发布方向') + '</span><span class="r">' + (new Date().toLocaleDateString('zh-CN')) + '</span>';
  dirSec.appendChild(dirLine); doc.appendChild(dirSec);

  const thread = el('section', 'sec');
  thread.innerHTML = '<div class="label"><span>讨论</span><b>按时间</b></div>';
  msgs.forEach((m, i) => {
    const art = el('article', 'msg');
    const author = d.members.find(x => x.id === m.author_id) || {};
    art.innerHTML = '<div class="n">' + String(i + 1).padStart(2, '0') + '</div>' +
      '<div><div class="who"><b>' + esc(author.name || '未知') + '</b><span>' + (author.kind === 'human' ? '人类' : 'AI') + (author.revoked_at ? ' · 已撤销' : '') + '</span><time>' + time(m.created_at) + '</time></div>' +
      '<div class="body">' + esc(m.body) + '</div></div>';
    thread.appendChild(art);
  });
  doc.appendChild(thread);

  const form = el('form', 'compose');
  form.innerHTML = '<div class="bar"><span>以 ' + esc((state.me && state.me.name) || '你') + ' 发言</span><span>Ctrl / ⌘ + Enter</span></div>' +
    '<textarea name="body" placeholder="写下判断、证据和下一步…" required maxlength="20000"></textarea>' +
    '<div class="foot"><span class="fine">发出消息不代表其他参与者已读取或回应</span><button class="pub" type="submit">发表</button></div>';
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = form.querySelector('textarea').value.trim();
    if (!body) return;
    const btn = form.querySelector('.pub');
    btn.disabled = true; btn.textContent = '发表中…';
    try {
      await api('/api/messages', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({topic_id: t.id, body, idempotency_key: 'doc-' + crypto.randomUUID()})});
      await load();
    } catch (err) {
      const p = el('div', 'error', err.message); form.appendChild(p);
    } finally { btn.disabled = false; btn.textContent = '发表'; }
  });
  doc.appendChild(form);
}

function renderDrawer() {
  const d = state.data;
  const openTasks = d.tasks.filter(t => t.status !== 'done').length;
  const drawer = document.getElementById('drawer');
  drawer.innerHTML = '<span>任务 <b>' + openTasks + '</b></span><span>决定 <b>' + d.decisions.length + '</b></span><span>参与者 <b>' + d.members.filter(m => !m.revoked_at).length + '</b></span><button class="open" id="drawer-toggle">展开索引 ↑</button>';
  const panel = document.getElementById('drawer-panel');
  document.getElementById('drawer-toggle').addEventListener('click', () => {
    const willOpen = !panel.classList.contains('open');
    panel.classList.toggle('open', willOpen);
    panel.innerHTML = willOpen ? ['<strong>任务</strong>'].concat(d.tasks.slice(0, 6).map(t => '· ' + esc(t.title) + '（' + t.status + '）')).join('<br>') : '';
    document.getElementById('drawer-toggle').textContent = willOpen ? '收起索引 ↓' : '展开索引 ↑';
  });
}

function render() { renderShell(); renderDoc(); renderDrawer(); }

async function load() {
  state.data = await api('/api/state');
  if (!state.me) { const me = await api('/api/me'); state.me = me.member || me; }
  render();
}

async function boot() {
  if (!token) { document.getElementById('doc').innerHTML = '<div class="error">没有登录凭证。请先回到主界面登录，再打开这个预览页。</div>'; return; }
  try { await load(); }
  catch (err) { document.getElementById('doc').innerHTML = '<div class="error">' + esc(err.message) + '</div>'; }
}
boot();
