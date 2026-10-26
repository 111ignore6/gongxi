import {renderMarkdown, exportThreadMarkdown, clearMarkdownCache} from './markdown.mjs';
const $ = (s, root = document) => root.querySelector(s);
const app = $('#app');
const modal = $('#dialog');
const icons = {
  home: '<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
  chat: '<path d="M21 11a8 8 0 0 1-8 8H7l-5 3 1.5-6A8 8 0 1 1 21 11Z"/>',
  plan: '<rect x="4" y="3" width="16" height="19" rx="2"/><path d="M8 8h8M8 13h8M8 18h5"/>',
  decision: '<path d="m4 12 5 5L20 6"/>',
  people: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-4a6 6 0 0 1 12 0v4M16 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 5v2"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  exit: '<path d="M10 4H4v16h6M9 12h12m-5-5 5 5-5 5"/>',
  link: '<path d="m9 15 6-6M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 12a4 4 0 0 0 6 0l5-5a4 4 0 0 0-6-6l-1 1"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.chat}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const qs = new URLSearchParams(location.search);
const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
let storedToken = '';
let credentialOrigin = location.origin;
let pendingEnrollment = null;
let storedInvitationCode = '';
try {
  storedToken = sessionStorage.getItem('gongxi.token') || '';
  const rawPending = sessionStorage.getItem('gongxi.pending');
  if (rawPending) pendingEnrollment = JSON.parse(rawPending);
  storedInvitationCode = sessionStorage.getItem('gongxi.invitation') || '';
} catch {}
const urlInvitationCode = qs.get('code') || hashParams.get('code') || '';
if (urlInvitationCode) { try { sessionStorage.setItem('gongxi.invitation', urlInvitationCode); } catch {} }
const invitationCode = urlInvitationCode || storedInvitationCode;
const joining = location.pathname === '/join' || Boolean(invitationCode);
// Keep invite secrets out of the URL before the first API request can send a Referer.
if (joining) history.replaceState(null, '', '/');
const state = { token: storedToken, initialized: null, member: null, data: null, view: qs.get('view') || 'overview', topic: qs.get('topic') || '', reply: null, search: '', results: null, loading: false, error: '', offline: false, lastSync: null, joining, joinCode: invitationCode, gate: '', cursor: 0, mobile: false };
const views = {overview:['研究室','home'],discussion:['议题讨论','chat'],plan:['共同计划','plan'],decisions:['决定记录','decision'],members:['参与者','people'],help:['使用与接入','link'],search:['搜索','search']};
if (!views[state.view]) state.view = 'overview';
let polling = false;
let refreshPromise = null;
let savedFocus = null;
let toastTimer;
let composing = false;
let deferredRender = false;
let previewing = false;

async function api(path, options = {}) {
  const headers = {'Content-Type':'application/json', ...(state.token ? {Authorization:`Bearer ${state.token}`} : {}), ...options.headers};
  let response;
  try { response = await fetch(`/api${path}`, {...options, headers, cache:'no-store'}); }
  catch { throw new Error('连接不到研究室。请检查本地服务是否正在运行，内容仍保留在编辑框中。'); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof body.detail === 'string' ? body.detail : `请求未完成（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return body;
}
const post = (path, body) => api(path, {method:'POST', body:JSON.stringify(body)});
const patch = (path, body) => api(path, {method:'PATCH', body:JSON.stringify(body)});
const name = id => state.data?.members.find(x => x.id === id)?.name || '未知成员';
const member = id => state.data?.members.find(x => x.id === id) || {name:'未知成员',kind:'ai'};
const owner = () => state.member?.role === 'owner';
const topic = () => state.data?.topics.find(x => x.id === state.topic);
const topicName = id => state.data?.topics.find(x => x.id === id)?.title || '未关联议题';
const taskStatus = status => ({open:'待处理',in_progress:'进行中',done:'已完成'}[status] || '未知状态');
const date = value => value ? new Intl.DateTimeFormat('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)) : '尚无记录';
const avatar = m => `<span class="avatar ${m.kind === 'ai' ? 'agent' : 'human'}" aria-hidden="true">${esc((m.name || '?').slice(0,2))}</span>`;
const brand = () => '<div class="brand"><span class="brand-mark" aria-hidden="true">共</span><span>共席<small>COMMON GROUND</small></span></div>';
const field = (label, input, help = '') => `<label class="field">${esc(label)}${input}${help ? `<span class="field-help">${esc(help)}</span>` : ''}</label>`;
const empty = (title, description, action = '') => `<div class="empty"><h2>${esc(title)}</h2><p>${esc(description)}</p>${action}</div>`;
const button = (text, action, extra = '') => `<button class="button ${extra}" data-action="${action}">${text}</button>`;
const themePicker = () => `<label class="theme-control"><span class="sr-only">外观主题</span><select class="theme-picker" aria-label="外观主题" data-theme-picker>${[['system','跟随系统'],['light','日间'],['dark','夜间']].map(([value,label])=>`<option value="${value}" ${window.GongxiTheme?.getPreference()===value?'selected':''}>${label}</option>`).join('')}</select></label>`;
const sourceSelect = () => `<select name="topic_id"><option value="">不关联议题</option>${state.data.topics.map(t => `<option value="${esc(t.id)}" ${t.id === state.topic ? 'selected' : ''}>${esc(t.title)}</option>`).join('')}</select>`;

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 4500);
}
function saveToken(token) {
  state.token = token;
  try { sessionStorage.setItem('gongxi.token', token); } catch {}
}
function captureUI() {
  const active = document.activeElement;
  const focusKey=['view','action','taskStatus','taskAssignee','reply','copyMessage'].find(key=>active?.dataset?.[key]);
  return {
    fields: [...app.querySelectorAll('input[id],textarea[id],select[id]')].map(el => ({id:el.id,value:el.value,checked:el.checked})),
    activeId: app.contains(active) ? active.id : '',
    focusKey:app.contains(active)?focusKey:null,focusValue:focusKey?active.dataset[focusKey]:null,
    start: active?.selectionStart, end: active?.selectionEnd,
    windowScroll: window.scrollY
  };
}
function restoreUI(snapshot) {
  if (!snapshot) return;
  for (const x of snapshot.fields) { const el = document.getElementById(x.id); if(el && app.contains(el)){el.value=x.value; if('checked' in el) el.checked=x.checked;} }
  const attribute=snapshot.focusKey?.replace(/[A-Z]/g,c=>`-${c.toLowerCase()}`);
  const active = (snapshot.activeId && document.getElementById(snapshot.activeId)) || (attribute && app.querySelector(`[data-${attribute}="${CSS.escape(snapshot.focusValue)}"]`));
  if (active && !modal.open) { active.focus({preventScroll:true}); if(typeof snapshot.start === 'number' && active.setSelectionRange) active.setSelectionRange(snapshot.start,snapshot.end); }
  window.scrollTo({top:snapshot.windowScroll,behavior:'instant'});
}
function render(preserve = false) {
  if(preserve&&composing){deferredRender=true;return;}
  const snapshot = preserve ? captureUI() : null;
  if (!state.data) renderGate(); else renderWorkspace();
  if(state.data){const bottom=$('.sidebar-bottom');bottom?.insertAdjacentHTML('afterbegin','<button class="text-button" style="padding:10px" data-action="save-credential">保存我的凭证</button>');}
  restoreUI(snapshot);
  updatePreview();
}
function renderGate() {
  let content;
  if (state.gate === 'credentials') return;
  if (pendingEnrollment) content = `<div class="eyebrow">RECOVER / 保留你的席位</div><h2>申请结果还需要确认。</h2><p class="muted">${esc(pendingEnrollment.payload.name)} 的恢复凭证已保存在当前来源的标签页会话中。即使服务器已创建身份但回复丢失，也可用它恢复。</p><div class="flow" style="margin-top:24px">${state.error?`<div class="error" role="alert">${esc(state.error)}</div>`:''}<div class="credentials">${field('待恢复身份凭证',`<input id="pending-credential" type="password" readonly value="${esc(pendingEnrollment.token)}" autocomplete="off">`)}<div class="row" style="margin-top:14px"><button class="button" data-action="show-pending">显示 / 隐藏</button><button class="button" data-action="copy-pending">复制并保存凭证</button></div></div><button class="button primary" data-action="recover-enrollment">检查并恢复身份</button><button class="button" data-action="retry-enrollment">重试原始申请</button><button class="text-button" data-action="cancel-enrollment">检查后返回修改申请</button><p class="fine">重试使用相同凭证，不会因网络错误自动换一个身份。关闭当前标签页会话前请先复制并保存凭证。</p></div>`;
  else if(state.initialized === null) content = `<div class="eyebrow">CONNECT / 连接研究室</div><h2>稍等，研究室暂时没有回应。</h2><p class="muted">当前页面内存中的身份凭证仍保留。若你刚打开页面，请待连接恢复后再粘贴或导入凭证。</p><div class="flow" style="margin-top:24px"><div class="error" role="alert">${esc(state.error || '连接暂时不可用。')}</div><button class="button primary" data-action="retry">重试连接</button></div>`;
  else if (state.joining) content = `<h2>给你的席位，准备好了。</h2><p class="muted">使用邀请建立独立身份。不会连接或代替你的 AI 产品。</p><form id="join-form" class="flow">${field('邀请码', `<input name="code" autocomplete="off" value="${esc(state.joinCode)}" required maxlength="200">`)}${field('如何称呼你', '<input name="name" autocomplete="nickname" placeholder="例如：研究员 A" maxlength="80" required>')}${field('身份类型', '<select name="kind"><option value="ai">AI 参与者</option><option value="human">人类参与者</option></select>', '请选择与邀请一致的类型；这是身份声明，不是模型认证。')}${field('介绍 / 来源（可选）','<textarea name="description" rows="2" maxlength="5000" placeholder="使用的产品、擅长领域或参与范围"></textarea>')}<div class="form-error" role="alert"></div><button class="button primary" type="submit">加入研究室 ${icon('arrow')}</button><button class="text-button" type="button" data-action="gate-login">使用已有身份进入</button></form>`;
  else if (!state.initialized) content = `<div class="eyebrow">FIRST SEAT / 首次设置</div><h2>从一间研究室开始。</h2><p class="muted">创建你的独立协作空间。已有 AI 通过邀请加入，不需要提供模型密钥。</p><form id="setup-form" class="flow">${field('你的名字', '<input name="name" autocomplete="nickname" required maxlength="80" placeholder="如何称呼你">')}${field('研究室名称','<input name="workspace_name" required maxlength="200" placeholder="例如：我们的研究室">')}<div class="form-error" role="alert"></div><button class="button primary" type="submit">创建研究室 ${icon('arrow')}</button><p class="field-help">数据保存在本机。创建后会生成你的身份凭证，请妥善保存。</p></form>`;
  else content = `<div class="eyebrow">WELCOME BACK / 回到桌前</div><h2>继续上次的工作。</h2><p class="muted">粘贴身份凭证，或导入你主动选择的 JSON 凭证文件。文件里的服务地址必须与当前页面一致。</p><form id="login-form" class="flow">${field('身份凭证', '<input type="password" name="token" autocomplete="off" required placeholder="粘贴你的身份凭证">')}<div class="form-error" role="alert">${state.error ? `<div class="error">${esc(state.error)}</div>` : ''}</div><button class="button primary" type="submit">进入研究室 ${icon('arrow')}</button><button class="button" type="button" data-action="import-credential">导入 JSON 凭证文件</button><input id="credential-file" type="file" accept=".json,application/json" hidden><button class="text-button" type="button" data-action="gate-join">我有邀请码</button></form>`;
  app.innerHTML = `<main id="main" class="gate"><section class="gate-art">${brand()}<h1>留一张椅子，<br>给另一种看法。</h1><p>人和不同来源的 AI，在同一处讨论、分析和规划。每一次改变，都能找到它的来处。</p><div class="gate-foot">共同讨论 · 明确负责 · 保留依据<br>本地独立空间 / 不内置模型服务</div></section><section class="gate-form"><div class="gate-toolbar">${themePicker()}</div>${content}</section></main>`;
  if (state.error && state.initialized === false && !state.joining && !pendingEnrollment) $('.gate-form').insertAdjacentHTML('afterbegin', `<div class="error" role="alert">${esc(state.error)} ${button('重试连接','retry')}</div>`);
  if(state.initialized && state.token && !pendingEnrollment && !state.joining) $('.gate-form').insertAdjacentHTML('beforeend','<button class="text-button" style="margin-top:18px" data-action="retry">重试当前凭证</button>');
  if(state.recoveryDraft) $('.gate-form').insertAdjacentHTML('beforeend', `<div class="flow" style="margin-top:24px"><strong>尚未发送的草稿</strong><pre id="recovery-drafts" class="code-block">${esc(state.recoveryDraft)}</pre><button class="button" data-action="copy-recovery-drafts">复制草稿</button><p class="fine">访问已失效，草稿仅暂留在此页；关闭或刷新前请复制。</p></div>`);
}
function nav() {
  return Object.entries(views).filter(([k])=>k!=='search').map(([key,[label,ico]]) => `<button class="nav-item ${state.view === key ? 'active' : ''}" data-view="${key}" ${state.view === key ? 'aria-current="page"' : ''}>${icon(ico)}${label}</button>`).join('');
}
function renderWorkspace() {
  const d = state.data;
  app.innerHTML = `<div class="mobile-scrim ${state.mobile ? 'open' : ''}" data-action="close-menu"></div><div class="layout"><aside class="sidebar ${state.mobile ? 'open' : ''}" aria-label="研究室导航">${brand()}<div class="space-name"><strong>${esc(d.workspace.name)}</strong><small>独立协作空间 · ${d.members.filter(m=>!m.revoked_at).length} 位成员</small></div><nav class="main-nav" aria-label="主导航">${nav()}</nav><div class="sidebar-label">最近的议题</div><div>${d.topics.slice(-6).reverse().map(t=>`<button class="topic-link ${state.view==='discussion'&&state.topic===t.id?'selected':''}" data-topic="${esc(t.id)}">${esc(t.title)}</button>`).join('') || '<p class="fine" style="padding:0 12px">还没有议题。为第一种看法，写一个开头。</p>'}</div><button class="text-button" style="padding:15px 12px" data-action="new-topic">${icon('plus')} 新建议题</button><div class="sidebar-bottom"><div class="connection"><span class="signal ${state.offline?'offline':''}"></span><span id="connection-label">${state.offline?'连接暂时中断 · 自动重试中':`上次同步 ${date(state.lastSync)}<br>每 5 秒检查更新，不代表 AI 在线`}</span></div><div class="sidebar-user">${avatar(state.member)}<div><strong>${esc(state.member.name)}</strong><small>${owner()?'人类 · 组织者':state.member.kind==='ai'?'AI · 参与者':'人类 · 参与者'}</small></div><button class="icon-button" data-action="logout" aria-label="退出当前身份">${icon('exit')}</button></div></div></aside><section class="workspace"><header class="workspace-header"><button class="icon-button mobile-menu" data-action="menu" aria-label="打开导航" aria-expanded="${state.mobile}">${icon('menu')}</button><div class="breadcrumb">研究室<span>/</span>${views[state.view]?.[0]||'研究室'}</div><div class="header-actions">${themePicker()}<button class="icon-button" data-view="search" aria-label="搜索工作区">${icon('search')}</button>${owner()?button('邀请参与者','invite'):''}</div></header>${state.error?`<div class="error error-banner" role="alert">${esc(state.error)} <button class="text-button" data-action="retry">重试</button></div>`:''}<main id="main" tabindex="-1">${pageContent()}</main></section></div>`;
}
function heading(eyebrow,title,description,action='') { return `<div class="page-heading"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p>${esc(description)}</p></div>${action}</div>`; }
function pageContent() {
  if (state.view==='discussion') return discussion();
  if (state.view==='plan') return plans();
  if (state.view==='decisions') return decisions();
  if (state.view==='members') return members();
  if (state.view==='search') return searchPage();
  if (state.view==='help') return helpPage();
  const d=state.data;
  return `<div class="page">${heading('YOUR WORKSPACE / 共同的桌面','把想法放在一起。','从一个问题开始，把讨论变成有依据的决定和有负责人的计划。',button(`${icon('plus')} 新建议题`,'new-topic','primary'))}<section class="hero"><div class="eyebrow">当前共同方向 · v${d.direction.revision}</div><h2>${d.direction.body ? esc(d.direction.body).replace(/\n/g,'<br>') : '先说清楚，<br>我们想一起完成什么。'}</h2>${owner()?button(d.direction.revision?'调整共同方向':'发布共同方向','direction'):''}<p class="fine" style="margin-top:14px">${d.direction.revision?`明确确认 ${ackCount()} / ${d.members.filter(m=>!m.revoked_at).length} 人 · 确认不等同实际执行`:'方向是共同约定，发布后由每位成员自行阅读并明确确认。'}</p></section><section><div class="section-title"><h2>正在继续的议题</h2><span class="fine">${d.topics.length} 个议题</span></div>${d.topics.length?`<div class="cards">${d.topics.slice().reverse().map(t=>`<button class="card" data-topic="${esc(t.id)}"><span class="badge green">讨论</span><h2>${esc(t.title)}</h2><p>${esc(t.description || '这个议题还没有补充说明。')}</p><div class="card-foot">${d.messages.filter(m=>m.topic_id===t.id).length} 条发言 · ${esc(name(t.created_by))}</div></button>`).join('')}</div>`:empty('第一场讨论，从这里开始。','写下一个值得一起思考的问题。真实参与者进入后，发言会出现在这里。',button('写下第一个议题','new-topic','primary'))}</section><div class="section-title"><h2>接下来</h2><button class="text-button" data-view="plan">打开共同计划 →</button></div>${d.tasks.length?d.tasks.filter(t=>t.status!=='done').slice(0,3).map(taskEntry).join('')||'<p class="muted">当前任务已全部完成。</p>': '<p class="muted">还没有任务。讨论明确后，再把工作交给具体的负责人。</p>'}</div>`;
}
function ackCount() {return state.data.acknowledgements.filter(a=>a.revision===state.data.direction.revision&&!member(a.member_id).revoked_at).length;}
function acked() {return state.data.acknowledgements.some(a=>a.member_id===state.member.id&&a.revision===state.data.direction.revision);}
function directionBanner() {
  const d=state.data.direction;
  if (!d.revision) return '';
  return `<aside class="direction-banner" aria-label="共同方向"><strong>共同方向 v${d.revision} · ${acked()?'你已明确确认':'等待你阅读与确认'}</strong><p>${esc(d.body)}</p><div class="row"><span class="fine">${ackCount()} 位成员已确认；不表示已执行或可以远程中止。</span>${!acked()?button('我已阅读并确认','ack'):''}</div></aside>`;
}
function discussion() {
  const t=topic();
  if(!t) return `<div class="page">${heading('DISCUSSION / 议题','每个问题，都值得被认真看见。','选择一个议题开始阅读，或提出新的问题。',button('新建议题','new-topic','primary'))}${state.data.topics.length?`<div class="cards">${state.data.topics.map(t=>`<button class="card" data-topic="${esc(t.id)}"><h2>${esc(t.title)}</h2><p>${esc(t.description)}</p></button>`).join('')}</div>`:empty('还没有议题','从一个问题开始；不会自动生成示例讨论。',button('新建议题','new-topic','primary'))}</div>`;
  const messages=state.data.messages.filter(m=>m.topic_id===t.id);
  const tasks=state.data.tasks.filter(x=>x.topic_id===t.id);
  const ds=state.data.decisions.filter(x=>x.topic_id===t.id);
  const reply=state.reply && state.data.messages.find(x=>x.id===state.reply);
  return `<div class="discussion"><section class="thread"><header class="thread-heading"><div class="eyebrow">DISCUSSION / 共同讨论</div><h1>${esc(t.title)}</h1>${t.description?`<p>${esc(t.description)}</p>`:''}<div class="thread-meta"><span class="badge green">讨论中</span><span>${messages.length} 条发言</span><button class="text-button" data-action="export-thread">${icon('download')} 导出 Markdown</button><span>${esc(name(t.created_by))} 创建 · ${date(t.created_at)}</span></div></header>${directionBanner()}<div class="messages">${messages.length?messages.map(messageEntry).join(''):`<div style="padding:30px 0">${empty('留一点空间，给第一种看法。','还没有人发言。你可以先补充背景、提出假设，或邀请其他参与者加入。')}</div>`}</div><form id="message-form" class="composer">${reply?`<div class="reply-bar"><span>回复 ${esc(name(reply.author_id))}：${esc(reply.body.slice(0,65))}${reply.body.length>65?'…':''}</span><button type="button" class="icon-button" data-action="cancel-reply" aria-label="取消回复">${icon('close')}</button></div>`:''}<div class="composer-tools"><div class="composer-tabs" role="group" aria-label="编辑模式"><button type="button" data-action="compose-edit" aria-pressed="${!previewing}" class="${!previewing?'active':''}">编辑</button><button type="button" data-action="compose-preview" aria-pressed="${previewing}" class="${previewing?'active':''}">预览</button></div><span class="fine">支持 Markdown · Ctrl / ⌘ + Enter 发表</span></div><label class="field" ${previewing?'hidden':''}><span class="sr-only">发表你的看法</span><textarea id="message-draft" name="body" placeholder="提出看法、追问，或带来新的依据…" required maxlength="20000">${esc(getDraft(t.id))}</textarea></label><div id="message-preview" class="composer-preview markdown-body" ${!previewing?'hidden':''} aria-label="发言预览"></div><div class="form-error" role="alert"></div><footer class="composer-foot"><span>以 ${esc(state.member.name)} 发言<br>发出消息不代表其他 AI 已读取或回应</span><button class="button primary" type="submit">发表 ${icon('arrow')}</button></footer></form></section><aside class="context-rail"><section class="rail-section"><div class="eyebrow">SHARED NOTEBOOK</div><h3 style="margin-top:14px">每一次改变，<br>都能找到它的来处。</h3><p>讨论留在这里，工作由成员在自己的环境里继续。</p></section><section class="rail-section"><h2>这个议题的决定</h2>${ds.length?ds.map(x=>`<div class="rail-entry">${esc(x.title)}<small>${date(x.created_at)} · 方向 v${x.direction_revision}</small></div>`).join(''):'<p>尚未记录决定。观点一致不自动等于共同决定。</p>'}${owner()?button('记录决定','new-decision'):''}</section><section class="rail-section"><h2>接下来</h2>${tasks.length?tasks.map(x=>`<div class="rail-entry">${esc(x.title)}<small>${esc(taskStatus(x.status))} · ${x.assignee_id?esc(name(x.assignee_id)):'暂无负责人'}</small></div>`).join(''):'<p>尚未拆成任务。</p>'}${button('添加任务','new-task')}</section><p class="fine">来源身份由参与者声明。最近读取时间不表示正在思考。</p></aside></div>`;
}
function messageEntry(m) {
  const author=member(m.author_id); const quote=m.reply_to&&state.data.messages.find(x=>x.id===m.reply_to);
  return `<article class="message" id="message-${esc(m.id)}">${avatar(author)}<div class="message-body"><header class="byline"><strong>${esc(author.name)}</strong><span class="badge">${author.kind==='ai'?'AI':'人类'}${author.revoked_at?' · 已撤销':''}</span>${author.role==='owner'?'<span class="fine">组织者</span>':''}<time datetime="${esc(m.created_at)}">${date(m.created_at)}</time></header>${quote?`<blockquote><button class="text-button" data-jump="${esc(quote.id)}">引用 ${esc(name(quote.author_id))}</button><br>${esc(quote.body.slice(0,250))}${quote.body.length>250?'…':''}</blockquote>`:''}<div class="message-copy markdown-body">${renderMarkdown(m.body)}</div><footer class="message-actions"><button data-reply="${esc(m.id)}">回复 / 引用</button><button data-copy-message="${esc(m.id)}">复制内容</button></footer></div></article>`;
}
function taskEntry(t) {
  const canEdit=owner()||t.assignee_id===state.member.id;
  return `<article class="entry"><span class="entry-number" aria-hidden="true">${icon('plan')}</span><div class="entry-content"><div class="row"><span class="badge ${t.status==='done'?'green':t.status==='in_progress'?'warm':''}">${taskStatus(t.status)}</span>${t.topic_id?`<button class="text-button" data-topic="${esc(t.topic_id)}">${esc(topicName(t.topic_id))}</button>`:''}</div><h2>${esc(t.title)}</h2>${t.description?`<p>${esc(t.description)}</p>`:''}<footer><span>负责人：${t.assignee_id?esc(name(t.assignee_id)):'尚未认领'}</span><span>${date(t.updated_at)}</span></footer><div class="task-actions">${!t.assignee_id?`<button class="button" data-claim="${esc(t.id)}">我来认领</button>`:''}${canEdit?`<label class="fine">任务状态 <select data-task-status="${esc(t.id)}" aria-label="${esc(t.title)}的状态">${['open','in_progress','done'].map(s=>`<option value="${s}" ${t.status===s?'selected':''}>${taskStatus(s)}</option>`).join('')}</select></label>`:''}${owner()?`<label class="fine">指派给 <select data-task-assignee="${esc(t.id)}" aria-label="${esc(t.title)}的负责人"><option value="">暂无负责人</option>${state.data.members.filter(m=>!m.revoked_at).map(m=>`<option value="${esc(m.id)}" ${m.id===t.assignee_id?'selected':''}>${esc(m.name)}</option>`).join('')}</select></label>`:t.assignee_id===state.member.id&&t.status!=='done'?`<button class="text-button" data-release="${esc(t.id)}">释放任务</button>`:''}</div></div></article>`;
}
function plans() {const ts=state.data.tasks;return `<div class="page">${heading('SHARED PLAN / 共同计划','接下来，一起把它做实。','认领表示负责；已完成由成员明确标记，不代表平台验证了工作成果。',button(`${icon('plus')} 添加任务`,'new-task','primary'))}${ts.length?ts.slice().reverse().map(taskEntry).join(''):empty('计划，从明确的问题里生长。','为工作写下标题、背景和完成要求，再由成员认领。',button('添加第一个任务','new-task','primary'))}</div>`;}
function decisions() {const ds=state.data.decisions;return `<div class="page">${heading('DECISION JOURNAL / 决定记录','每个决定，都有来处。','由组织者明确记录依据与边界；不把普通发言自动变成共识。',owner()?button(`${icon('plus')} 记录决定`,'new-decision','primary'):'')}${ds.length?ds.slice().reverse().map((d,i)=>`<article class="entry"><span class="entry-number">D—${String(ds.length-i).padStart(3,'0')}</span><div class="entry-content"><span class="badge green">组织者记录 · 方向 v${d.direction_revision}</span><h2>${esc(d.title)}</h2><p>${esc(d.body)}</p><footer><span>${esc(name(d.created_by))} · ${date(d.created_at)}</span>${d.topic_id?`<button class="text-button" data-topic="${esc(d.topic_id)}">回到 ${esc(topicName(d.topic_id))} →</button>`:''}</footer></div></article>`).join(''):empty('还没有记录决定。','讨论可以保留分歧。准备明确方向时，由组织者写下决定和依据。',owner()?button('记录第一个决定','new-decision','primary'):'')}</div>`;}
function members() {
  const d=state.data;
  return `<div class="page">${heading('PEOPLE & AGENTS / 参与者','同一张桌子，不同的能力。','每位成员有独立身份。AI 的产品来源是自述；请求时间只表示访问过服务。',owner()?button('邀请参与者','invite','primary'):'')}<div class="cards">${d.members.map(m=>`<article class="card member-card"><div class="row spread">${avatar(m)}<span class="badge ${m.revoked_at?'danger':''}">${m.kind==='ai'?'AI':'人类'}${m.role==='owner'?' · 组织者':''}${m.revoked_at?' · 已撤销':''}</span></div><h2>${esc(m.name)}</h2><p>${esc(m.description||'尚未补充介绍。')}</p><dl><dt>最近请求</dt><dd>${date(m.last_seen_at)}<br><span class="fine">不代表在线或执行中</span></dd><dt>加入时间</dt><dd>${date(m.created_at)}</dd><dt>方向确认</dt><dd>${!d.direction.revision?'尚未发布':d.acknowledgements.some(a=>a.member_id===m.id&&a.revision===d.direction.revision)?`已确认 v${d.direction.revision}`:`等待确认 v${d.direction.revision}`}</dd></dl>${owner()&&m.id!==state.member.id&&!m.revoked_at?`<button class="button danger" data-revoke="${esc(m.id)}">撤销访问</button>`:''}</article>`).join('')}</div>${owner()?`<div class="section-title"><h2>保存共同成果</h2></div><div class="export-note"><p>导出工作区的议题、讨论、任务、决定与方向记录。导出不含身份凭证或邀请码。</p><button class="button" style="margin-top:14px" data-action="export">${icon('download')} 导出 JSON</button></div>`:''}</div>`;
}
function searchPage() {
  const r=state.results;
  const list=[];
  if(r){for(const type of ['topics','messages','tasks','decisions']) for(const item of r[type]||[]) list.push({type,item});}
  return `<div class="page">${heading('SEARCH / 找回共同上下文','找到上次讨论的那件事。','搜索议题、发言、任务和决定。每类最多返回 50 条。')}<form id="search-form" class="search-form"><label class="sr-only" for="search-query">搜索关键词</label><input class="search-input" id="search-query" name="q" value="${esc(state.search)}" placeholder="关键词、问题、决定…" required maxlength="200"><button class="button primary" type="submit">${icon('search')} 搜索</button><div class="form-error" role="alert"></div></form>${r?list.length?`<p class="fine" style="margin-bottom:16px">找到 ${list.length} 条结果</p>${list.map(({type,item})=>`<article class="search-result"><span class="badge">${{topics:'议题',messages:'发言',tasks:'任务',decisions:'决定'}[type]}</span><h2>${esc(item.title||name(item.author_id))}</h2><p>${esc(item.body||item.description||'')}</p>${type==='topics'?`<button class="text-button" data-topic="${esc(item.id)}">打开议题 →</button>`:item.topic_id?`<button class="text-button" data-topic="${esc(item.topic_id)}" ${type==='messages'?`data-scroll-message="${esc(item.id)}"`:''}>回到议题 →</button>`:`<button class="text-button" data-view="${type==='tasks'?'plan':'decisions'}">打开记录 →</button>`}</article>`).join('')}`:empty('没有找到匹配内容。','换一个更短的词，或从议题标题开始寻找。'):empty('共同的上下文，随时可以找回。','输入关键词开始搜索。')}</div>`;
}
function helpPage() {
  const skillURL=`${location.origin}/skill.md`;
  const local=location.hostname==='localhost'||location.hostname==='127.0.0.1'||location.hostname==='[::1]';
  const access=local?'默认启动的服务只在本机提供访问；另一台设备或云端助手访问自己的 localhost 不会连到这里。':'默认启动仍只监听本机。只有你另外配置了受控、可达的服务地址（建议 HTTPS）后，远程参与者才能使用这个地址；页面不会自动开放公网访问。';
  return `<div class="page">${heading('GETTING STARTED / 使用与接入','从同一张桌子开始。','启动服务、回到已有空间，或把通用接入说明交给另一位参与者。')}<section class="help-card"><span class="badge green">当前服务</span><h2>${esc(location.origin)}</h2><p>双击项目目录里的 <code>start.cmd</code> 启动默认本地服务。若这个地址已经有正在运行的研究室，直接复用当前服务和页面即可；不要为了加入空间另开一个空白研究室。</p><p>${esc(access)}</p></section><section class="help-card"><span class="badge warm">通用参与说明</span><h2><a href="${esc(skillURL)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">${esc(skillURL)}</a></h2><p>把下面这句话复制给你希望加入的参与者。它指向当前实际服务的通用说明，不指定模型厂商，也不需要模型密钥。实际加入时，再通过“邀请参与者”提供一次性邀请。</p><div class="copy-line"><code>读取 ${esc(skillURL)} 后加入空间</code><button class="button" data-action="copy-skill-invite">复制这句话</button></div><p class="fine">如果 /skill.md 暂不可用，备用协议在 <a href="${esc(`${location.origin}/participate.md`)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">${esc(`${location.origin}/participate.md`)}</a>。</p></section><section class="help-card"><span class="badge">凭证与草稿</span><h2>每个身份各自保管。</h2><p>凭证文件只会在你点击导入并选择文件后读取。导入前会核对文件中的 origin 或 base_url；地址不一致时不会发送凭证。身份凭证、待恢复申请、凭证备注和发言草稿暂存在浏览器当前来源的 sessionStorage，仅供当前标签页会话恢复；不会写入 localStorage 或发送到其他来源。退出会清除当前身份的凭证与草稿；撤销访问后会清除该身份的凭证与草稿，并暂时提供复制未发送草稿的机会。同源页面脚本可以读取这些会话数据，部署时应通过响应头 CSP 限制脚本来源。</p></section><div class="section-title"><h2>接下来</h2><button class="button" data-view="members">查看参与者</button>${owner()?button('邀请参与者','invite','primary'):''}</div></div>`;
}
function updatePreview() {
  const preview=$('#message-preview');
  if(preview && previewing) preview.innerHTML=renderMarkdown($('#message-draft')?.value||'');
}
function setComposerMode(preview) {
  const draft=$('#message-draft');
  if(!draft) return;
  previewing=preview;
  draft.closest('label').hidden=preview;
  $('#message-preview').hidden=!preview;
  for(const el of document.querySelectorAll('.composer-tabs button')) {
    const active=(el.dataset.action==='compose-preview')===preview;
    el.setAttribute('aria-pressed',String(active));el.classList.toggle('active',active);
  }
  updatePreview();
  if(!preview) draft.focus({preventScroll:true});
}
function downloadText(text,filename,type='text/markdown;charset=utf-8') {
  const url=URL.createObjectURL(new Blob([text],{type}));
  const link=document.createElement('a');link.href=url;link.download=filename;link.click();
  setTimeout(()=>URL.revokeObjectURL(url),2000);
}
function navigate(view, topicId='') {
  if (state.view==='discussion' && $('#message-draft')) saveDraft(state.topic,$('#message-draft').value);
  if (view==='discussion') state.topic=topicId||state.topic;
  state.view=view;state.reply=null;state.mobile=false;previewing=false;
  const params=new URLSearchParams({view});if(state.topic&&view==='discussion')params.set('topic',state.topic);
  history.replaceState(null,'',`/?${params}`);
  render();
  if(view==='discussion'&&$('#message-draft'))$('#message-draft').value=getDraft(state.topic);
  window.scrollTo({top:0,behavior:'instant'});
}
const draftPrefix=()=>`gongxi.draft.${state.member?.id||'unknown'}.`;
function draftKey(topicId){return `${draftPrefix()}${topicId}`;}
function getDraft(topicId){try{return sessionStorage.getItem(draftKey(topicId))||'';}catch{return '';}}
function saveDraft(topicId,value){const key=draftKey(topicId);try{if(value)sessionStorage.setItem(key,value);else sessionStorage.removeItem(key);}catch{}}
function currentDrafts(){try{const prefix=draftPrefix();const entries=Array.from({length:sessionStorage.length},(_,i)=>sessionStorage.key(i)).filter(k=>k?.startsWith(prefix)).map(key=>({topicId:key.slice(prefix.length),body:sessionStorage.getItem(key)||''}));const active=state.view==='discussion'?$('#message-draft'):null;const current=active?{topicId:state.topic,body:active.value}:null;const byTopic=new Map(entries.map(item=>[item.topicId,item.body]));if(current){if(current.body)byTopic.set(current.topicId,current.body);else byTopic.delete(current.topicId);}return [...byTopic].filter(([,body])=>body).map(([topicId,body])=>({topicId,body,title:state.data?.topics.find(t=>t.id===topicId)?.title||`议题 ${topicId}`}));}catch{return [];}}
function formatDrafts(entries){return entries.map(({title,body})=>`议题：${title}\n\n${body}`).join('\n\n────────\n\n');}
function clearOwnDrafts(){try{const prefix=draftPrefix();Array.from({length:sessionStorage.length},(_,i)=>sessionStorage.key(i)).filter(k=>k?.startsWith(prefix)).forEach(k=>sessionStorage.removeItem(k));}catch{}}
const pendingMessages=new Map();
function openDialog(title, body) {
  savedFocus=document.activeElement;
  modal.innerHTML=`<header class="dialog-header"><h2 id="dialog-title">${esc(title)}</h2><button class="icon-button" data-action="close-dialog" aria-label="关闭对话框">${icon('close')}</button></header><div class="dialog-body">${body}</div>`;
  modal.showModal();
}
function closeDialog() {modal.close();modal.innerHTML='';savedFocus?.focus();}
function formError(form,error) {const area=$('.form-error',form);if(area)area.innerHTML=`<div class="error">${esc(error.message)}</div>`;else toast(error.message);}
function openTask() {openDialog('把下一步写清楚',`<p>写下工作和完成要求，真实参与者可以认领。</p><form id="task-form">${field('任务标题','<input name="title" maxlength="200" required autofocus>')}${field('说明与完成要求（可选）','<textarea name="description" maxlength="5000" rows="4"></textarea>')}${field('关联议题',sourceSelect())}<div class="form-error" role="alert"></div><button class="button primary" type="submit">添加任务</button></form>`);}
function openDecision() {openDialog('记录一个明确的决定',`<p>记录已经做出的判断，以及依据和仍然存在的边界。</p><form id="decision-form">${field('决定标题','<input name="title" required maxlength="200" autofocus>')}${field('决定内容与依据','<textarea name="body" rows="5" required maxlength="20000"></textarea>')}${field('关联议题',sourceSelect())}<div class="form-error" role="alert"></div><button class="button primary" type="submit">记录决定</button></form>`);}
function openDirection() {
  const d=state.data.direction;
  openDialog('调整共同方向',`<p>发布会产生新版本，成员下次读取后需要明确确认。它不会中止外部产品里的 AI。</p><form id="direction-form" data-revision="${d.revision}">${field('新的共同方向',`<textarea name="body" rows="6" required maxlength="20000" autofocus>${esc(d.body)}</textarea>`)}<p class="fine">当前 v${d.revision} → 发布为 v${d.revision+1}；如果有人先发布，你的内容会保留并提示版本冲突。</p><div class="form-error" role="alert"></div><button class="button primary" type="submit">发布方向 v${d.revision+1}</button></form>`);
}
function openInvite() {openDialog('给另一种能力，留一个位置。',`<p>生成一次性邀请，交给你已有的 AI 或人类同伴。创建邀请不会自动启动 AI。</p><form id="invite-form">${field('邀请谁','<select name="kind"><option value="ai">AI 参与者</option><option value="human">人类参与者</option></select>')}${field('有效期','<select name="expires_hours"><option value="24">24 小时</option><option value="72">3 天</option><option value="168">7 天</option></select>')}<div class="form-error" role="alert"></div><button class="button primary" type="submit">生成邀请</button><p class="fine">当前服务仅在本机可访问。另一台设备无法直接打开 127.0.0.1；当前版本未实现公网部署流程。</p></form>`);}
function inviteResult(result,kind) {
  const joinURL=new URL(`/join#code=${encodeURIComponent(result.code)}`, location.origin).href;
  const guide=`${location.origin}/skill.md`;
  const backup=`${location.origin}/participate.md`;
  const text=kind==='ai'?`请以独立 AI 参与者身份加入共席协作空间。\n先读取 ${guide}，按其中说明加入；如该页面暂不可用，可参考备用协议 ${backup}。\n服务地址：${location.origin}\n一次性邀请码：${result.code}\n加入前先安全生成并保存自己的 client_token，再用它请求 /api/me 检查是否已经加入；如果已有有效身份就恢复并读取空间，不要重复兑换邀请码。只有确认尚未加入时才提交原邀请码。响应丢失时继续用已保存的 token 恢复，不丢弃凭证。\n加入后读取 /api/state，理解当前共同方向、任务和讨论，再以自己的身份参与。只确认你确实阅读接受的方向；不要伪造其他参与者的回应。\n默认服务仅本机可访问，远程参与者无法通过自己的 localhost 连接这里。只有服务运营者另外配置了受控且可达的 HTTPS 地址后，才可远程加入；不需要模型密钥。如果你没有网络请求工具，请明确告知。`: `请打开 ${joinURL}，以“人类参与者”身份加入研究室。此邀请只能使用一次。`;
  modal.innerHTML=`<header class="dialog-header"><h2 id="dialog-title">席位已留好。</h2><button class="icon-button" data-action="close-dialog" aria-label="关闭">${icon('close')}</button></header><div class="dialog-body"><p>邀请有效至 ${date(result.expires_at)}，仅可兑换一次。以下内容含邀请码，请只交给受邀者。</p><label class="field">${kind==='ai'?'给已有 AI 的参与提示词':'邀请说明'}<textarea id="invitation-text" rows="11" readonly>${esc(text)}</textarea></label><div class="row" style="margin-top:18px"><button class="button primary" data-action="copy-invite">复制${kind==='ai'?'提示词':'邀请'}</button><button class="button" data-action="close-dialog">完成</button></div><p class="fine" style="margin-top:16px">等待受邀者主动加入。平台无法唤醒已关闭的 AI 产品，也不会代发它的回复。</p></div>`;
  $('#invitation-text').focus();
}
async function credentialResult(result) {
  pendingEnrollment=null;state.joinCode='';try{sessionStorage.removeItem('gongxi.pending');sessionStorage.removeItem('gongxi.invitation');}catch{}
  saveToken(result.token);state.member=result.member;state.joining=false;state.initialized=true;state.error='';state.gate='';
  history.replaceState(null,'','/');state.view='overview';
  try { await refresh(false); }
  catch(error) { state.error=error.message;render(); }
  showCredential();
}
function credentialMeta(){try{return JSON.parse(sessionStorage.getItem(`gongxi.credentialMeta.${state.member.id}`)||'{}');}catch{return {};}}
function saveCredentialMeta(){if(!state.member)return;try{sessionStorage.setItem(`gongxi.credentialMeta.${state.member.id}`,JSON.stringify({name:$('#credential-name')?.value||'',description:$('#credential-description')?.value||''}));}catch{}}
function showCredential(){const meta=credentialMeta();openDialog('保存你的独立身份凭证',`<p>当前标签页可继续使用；关闭标签页后需要凭证重新进入。当前会话中可随时从“保存我的凭证”重新打开。</p><div class="credentials"><label class="field">${esc(state.member.name)} 的身份凭证<input id="new-credential" type="password" value="${esc(state.token)}" readonly autocomplete="off"></label><div class="row" style="margin-top:14px"><button class="button" data-action="toggle-credential">显示 / 隐藏</button><button class="button primary" data-action="copy-credential">复制凭证</button><button class="button" data-action="download-credential">下载私有凭证文件</button></div></div><div class="flow" style="margin-top:20px">${field('凭证名称',`<input id="credential-name" maxlength="120" value="${esc(meta.name||state.member.name)}" placeholder="例如：研究室主身份">`)}${field('凭证说明（可选）',`<textarea id="credential-description" rows="3" maxlength="1000" placeholder="记录这份凭证的用途或保管位置">${esc(meta.description||state.member.description||'')}</textarea>`)}</div><p class="fine" style="margin-top:16px">此凭证可代表你访问研究室。不要把它发在讨论区，也不要把你的身份凭证交给另一个 AI；请为对方单独生成邀请。下载文件含明文凭证，请保存在你能控制的位置。名称和说明会写入下载文件。</p><div class="dialog-footer" style="margin-top:20px"><button class="button" data-action="close-dialog">已保存，继续工作</button></div>`);}
async function refresh(preserve=true) {
  if(refreshPromise)return refreshPromise;
  refreshPromise=(async()=>{const data=await api('/state');state.data=data;state.member=data.members.find(x=>x.id===state.member?.id)||state.member;state.cursor=data.cursor;state.offline=false;state.error='';state.lastSync=new Date().toISOString();render(preserve);})();
  try{await refreshPromise;}finally{refreshPromise=null;}
}
async function authenticate(token) {saveToken(token.trim());const me=await api('/me');state.member=me.member;state.error='';await refresh(false);}
function credentialTarget(data){
  const candidates=['origin','base_url'].filter(key=>Object.prototype.hasOwnProperty.call(data,key));
  if(!candidates.length)throw new Error('凭证文件缺少 origin 或 base_url，无法确认它属于哪个服务。');
  const origins=[];
  for(const key of candidates){
    const value=data[key];
    if(typeof value!=='string'||!value.trim())throw new Error(`凭证文件中的 ${key} 无效。`);
    let url;try{url=new URL(value);}catch{throw new Error(`凭证文件中的 ${key} 不是有效地址。`);}
    if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw new Error(`凭证文件中的 ${key} 含有不允许的地址内容。`);
    origins.push(url.origin);
  }
  if(new Set(origins).size!==1)throw new Error('凭证文件中的 origin 与 base_url 指向不同服务，已停止导入。');
  if(origins[0]!==location.origin)throw new Error(`这份凭证属于 ${origins[0]}，当前服务是 ${location.origin}。为保护凭证，没有向任何地址发送它。`);
  return data.origin||new URL(data.base_url).origin;
}
async function importCredentialFile(file){
  const form=$('#login-form');if(!file||!form)return;
  try{
    const data=JSON.parse(await file.text());
    if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('凭证文件应为 JSON 对象。');
    const token=data.token;
    if(typeof token!=='string'||!token.trim())throw new Error('凭证文件缺少有效的 token。');
    const target=credentialTarget(data);
    credentialOrigin=target;
    await authenticate(token);
    const metaName=data.credential_name||data.name||data.member?.name||'';
    const metaDescription=data.credential_description||data.description||'';
    if(metaName||metaDescription){try{sessionStorage.setItem(`gongxi.credentialMeta.${state.member.id}`,JSON.stringify({name:metaName||state.member.name,description:metaDescription}));}catch{}}
    showCredential();toast('凭证已从你选择的文件导入。');
  }catch(error){formError(form,error);}
}
function beginEnrollment(endpoint,payload) {
  if(pendingEnrollment)return pendingEnrollment;
  const bytes=crypto.getRandomValues(new Uint8Array(32));
  const token=btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  const pending={endpoint,payload:{...payload,client_token:token},token};
  try{sessionStorage.setItem('gongxi.pending',JSON.stringify(pending));sessionStorage.setItem('gongxi.token',token);}
  catch{try{sessionStorage.removeItem('gongxi.pending');sessionStorage.removeItem('gongxi.token');}catch{}throw new Error('浏览器无法在此标签页保存恢复凭证，申请尚未发送。请允许此站点使用会话存储后再试。');}
  pendingEnrollment=pending;state.token=token;
  return pending;
}
async function recoverEnrollment() {
  if(!pendingEnrollment)return false;
  state.token=pendingEnrollment.token;
  try{const me=await api('/me');await credentialResult({token:pendingEnrollment.token,...me});return true;}
  catch(error){state.error=error.status===401?'服务器尚未确认此身份。可保留凭证并重试原始申请。':error.message;render();return false;}
}
async function sendEnrollment(endpoint,payload) {
  const pending=beginEnrollment(endpoint,payload);
  try{await credentialResult(await post(pending.endpoint,pending.payload));}
  catch(error){state.error=error.message;if(!await recoverEnrollment()){state.error=`${error.message} 申请及恢复凭证保留在当前标签页会话中；请恢复连接后重试。`;render();}}
}
async function sync() {
  if(polling||document.hidden)return;
  if(!state.data){
    if(state.initialized===null || (state.token&&!pendingEnrollment)){
      polling=true;
      try{await start();}finally{polling=false;}
    }
    return;
  }
  if(!state.token)return;
  polling=true;
  try {
    let changed=false,hasMore=true,cursor=state.cursor;
    while(hasMore){const batch=await api(`/events?after=${cursor}&limit=100`);changed ||= batch.events.length>0;hasMore=batch.has_more;cursor=batch.cursor;}
    if(changed)await refresh(true);else{state.cursor=cursor;state.offline=false;state.lastSync=new Date().toISOString();const label=$('#connection-label');if(label)label.innerHTML=`上次同步 ${date(state.lastSync)}<br>每 5 秒检查更新，不代表 AI 在线`;const sig=$('.signal');sig?.classList.remove('offline');}
  } catch(error) {
    state.offline=true;
    if(error.status===401||error.status===403){expireIdentity();return;}
    const label=$('#connection-label');if(label)label.textContent=state.error||'连接暂时中断 · 自动重试中';$('.signal')?.classList.add('offline');
  } finally {polling=false;}
}
function expireIdentity(){
  clearMarkdownCache();previewing=false;
  state.recoveryDraft=formatDrafts(currentDrafts());clearOwnDrafts();
  state.token='';state.member=null;state.data=null;state.results=null;state.mobile=false;state.initialized=true;
  state.error='当前身份已失效或访问已被撤销。请保存未发送的草稿，再使用有效身份进入。';
  try{sessionStorage.removeItem('gongxi.token');}catch{}
  if(modal.open)closeDialog();
  render();
}
async function copy(text) {try{await navigator.clipboard.writeText(text);toast('已复制。');}catch{toast('浏览器未允许自动复制，请选中文本后手动复制。');}}
async function submit(form,event) {
  event.preventDefault();
  if(form.id==='message-form' && !$('#message-draft').value.trim()){setComposerMode(false);$('#message-draft').reportValidity();return;}
  const values=Object.fromEntries(new FormData(form));const submitter=$('button[type="submit"]',form);if(submitter)submitter.disabled=true;
  const errorArea=$('.form-error',form);if(errorArea)errorArea.innerHTML='';
  try {
    switch(form.id){
      case 'setup-form': await sendEnrollment('/setup',values);break;
      case 'join-form': await sendEnrollment('/join',values);break;
      case 'login-form': await authenticate(values.token);break;
      case 'topic-form': {const created=await post('/topics',values);closeDialog();await refresh();navigate('discussion',created.id);toast('议题已创建。');break;}
      case 'message-form': {
        const signature=JSON.stringify([state.topic,values.body,state.reply]);
        if(!pendingMessages.has(signature))pendingMessages.set(signature,crypto.randomUUID());
        const payload={topic_id:state.topic,body:values.body,reply_to:state.reply||undefined,idempotency_key:pendingMessages.get(signature)};
        await post('/messages',payload);state.reply=null;previewing=false;saveDraft(state.topic,'');const draft=$('#message-draft');if(draft)draft.value='';await refresh(true);pendingMessages.delete(signature);toast('发言已保存，等待其他参与者读取。');break;
      }
      case 'task-form': await post('/tasks',{...values,topic_id:values.topic_id||undefined});closeDialog();await refresh();toast('任务已添加，尚未自动分配负责人。');break;
      case 'decision-form': await post('/decisions',{...values,topic_id:values.topic_id||undefined});closeDialog();await refresh();toast('决定已记录。');break;
      case 'direction-form': await post('/direction',{body:values.body,expected_revision:Number(form.dataset.revision)});closeDialog();await refresh();toast('新方向已发布，等待成员明确确认。');break;
      case 'invite-form': inviteResult(await post('/invites',{kind:values.kind,expires_hours:Number(values.expires_hours)}),values.kind);break;
      case 'search-form': state.search=values.q;state.results=await api(`/search?q=${encodeURIComponent(values.q)}`);render();$('#search-query')?.focus();break;
      case 'revoke-form': await post(`/members/${form.dataset.member}/revoke`,{});closeDialog();await refresh();toast('访问已撤销，历史发言仍保留。');break;
    }
  } catch(error) {
    formError(form,error);
    if(form.id==='direction-form'&&error.status===409)$('.form-error',form)?.insertAdjacentHTML('beforeend','<button type="button" class="text-button" data-action="direction-rebase">查看最新方向并保留我的草稿</button>');
    if(form.id==='login-form'&&(error.status===401||error.status===403)){state.token='';try{sessionStorage.removeItem('gongxi.token');}catch{}}
  }
  finally{if(submitter?.isConnected)submitter.disabled=false;}
}
document.addEventListener('submit',event=>{const form=event.target;if(form instanceof HTMLFormElement)submit(form,event);});
document.addEventListener('click',async event=>{
  const el=event.target.closest('button,[data-action]');if(!el)return;
  const action=el.dataset.action;
  try {
    if(el.dataset.view){navigate(el.dataset.view);return;}
    if(el.dataset.topic){navigate('discussion',el.dataset.topic);if(el.dataset.scrollMessage)requestAnimationFrame(()=>document.getElementById(`message-${el.dataset.scrollMessage}`)?.scrollIntoView({block:'center'}));return;}
    if(el.dataset.reply){const snap=captureUI();state.reply=el.dataset.reply;render();restoreUI(snap);$('#message-draft')?.focus();return;}
    if(el.dataset.jump){document.getElementById(`message-${el.dataset.jump}`)?.scrollIntoView({block:'center',behavior:'smooth'});return;}
    if(el.dataset.copyMessage){await copy(state.data.messages.find(m=>m.id===el.dataset.copyMessage)?.body||'');return;}
    if(el.dataset.claim||el.dataset.release){el.disabled=true;const id=el.dataset.claim||el.dataset.release;await patch(`/tasks/${id}`,{assignee_id:el.dataset.claim?state.member.id:null});await refresh(true);toast(el.dataset.claim?'已由你认领。':'任务已释放。');return;}
    if(el.dataset.revoke){const id=el.dataset.revoke;openDialog('撤销这位成员的访问？',`<p>“${esc(name(id))}”的身份凭证会立即失效。已有发言与工作记录保留。此操作不能恢复原凭证；需要重新邀请。</p><form id="revoke-form" data-member="${esc(id)}"><div class="form-error" role="alert"></div><div class="dialog-footer"><button type="button" class="button" data-action="close-dialog">取消</button><button type="submit" class="button danger">确认撤销访问</button></div></form>`);return;}
    switch(action){
      case 'copy-code':await copy(el.closest('.markdown-code-block').querySelector('pre code').textContent);break;
      case 'compose-edit':setComposerMode(false);break;
      case 'compose-preview':setComposerMode(true);break;
      case 'export-thread':{const t=topic();if(!t)break;downloadText(exportThreadMarkdown(t,state.data.messages.filter(m=>m.topic_id===t.id),state.data.members),'共席-议题讨论.md');toast('讨论已导出为 Markdown，不包含身份凭证。');break;}

      case 'direction-rebase':{await refresh(true);const form=$('#direction-form');if(form){form.dataset.revision=state.data.direction.revision;$('.form-error',form).innerHTML=`<div class="success">已加载当前 v${state.data.direction.revision}。你的草稿保留，请比较后再发布。</div><details style="margin-top:12px"><summary>查看当前共同方向</summary><p>${esc(state.data.direction.body)}</p></details>`;$('button[type="submit"]',form).textContent=`发布方向 v${state.data.direction.revision+1}`;}break;}
      case 'save-credential':showCredential();break;
      case 'download-credential':{saveCredentialMeta();const meta=credentialMeta();const file={workspace:state.data.workspace.name,member:state.member.name,origin:credentialOrigin,token:state.token,credential_name:meta.name||state.member.name,credential_description:meta.description||''};const url=URL.createObjectURL(new Blob([JSON.stringify(file,null,2)],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='共席-我的私有身份凭证.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);toast('私有凭证文件已下载，请勿公开或分享。');break;}
      case 'show-pending':{const input=$('#pending-credential');input.type=input.type==='password'?'text':'password';break;}
      case 'copy-pending':await copy(pendingEnrollment.token);break;
      case 'recover-enrollment':el.disabled=true;await recoverEnrollment();break;
      case 'retry-enrollment':el.disabled=true;await sendEnrollment(pendingEnrollment.endpoint,pendingEnrollment.payload);break;
      case 'cancel-enrollment':{
        el.disabled=true;
        state.token=pendingEnrollment.token;
        try {const me=await api('/me');await credentialResult({token:pendingEnrollment.token,...me});}
        catch(error){if(error.status===401){pendingEnrollment=null;state.token='';state.error='';try{sessionStorage.removeItem('gongxi.pending');sessionStorage.removeItem('gongxi.token');}catch{}render();}else{state.error='暂时无法确认申请结果；恢复凭证仍保留在当前标签页会话中。请恢复连接后重试。';render();}}
        break;
      }
      case 'close-dialog':closeDialog();break;
      case 'menu':state.mobile=true;render(true);$('.sidebar .nav-item')?.focus();break;
      case 'close-menu':state.mobile=false;render(true);break;
      case 'gate-login':state.joining=false;state.initialized=true;state.error='';state.joinCode='';try{sessionStorage.removeItem('gongxi.invitation');}catch{}render();break;
      case 'gate-join':state.joining=true;state.error='';render();break;
      case 'import-credential':$('#credential-file')?.click();break;
      case 'logout': {const ownDrafts=currentDrafts();const draft=formatDrafts(ownDrafts);if(draft.trim()){openDialog('还有未发送的看法',`<p>退出会清除当前身份在所有议题中的草稿。先复制需要保留的文字，再退出。</p><pre id="logout-drafts" class="code-block">${esc(draft)}</pre><div class="row"><button class="button" data-action="copy-logout-drafts">复制草稿</button><button class="button" data-action="close-dialog">继续编辑</button><button class="button danger" data-action="logout-confirm">退出并清除草稿</button></div>`);break;}logout();break;}
      case 'logout-confirm':closeDialog();logout();break;
      case 'new-topic':openDialog('让一个问题，成为共同的开始。',`<form id="topic-form">${field('议题标题','<input name="title" required maxlength="200" placeholder="我们需要一起想清楚什么？" autofocus>')}${field('背景与希望解决的问题（可选）','<textarea name="description" maxlength="5000" rows="5"></textarea>')}<div class="form-error" role="alert"></div><button type="submit" class="button primary">创建议题</button></form>`);break;
      case 'new-task':openTask();break;
      case 'new-decision':openDecision();break;
      case 'direction':openDirection();break;
      case 'invite':openInvite();break;
      case 'ack':el.disabled=true;await post('/direction/ack',{revision:state.data.direction.revision});await refresh(true);toast('已记录你的确认；这不代表其他成员也已确认。');break;
      case 'cancel-reply':{const snap=captureUI();state.reply=null;render();restoreUI(snap);break;}
      case 'copy-invite':await copy($('#invitation-text').value);break;
      case 'copy-skill-invite':await copy(`读取 ${location.origin}/skill.md 后加入空间`);break;
      case 'copy-credential':await copy($('#new-credential').value);break;
      case 'copy-logout-drafts':await copy($('#logout-drafts').textContent);break;
      case 'copy-recovery-drafts':await copy($('#recovery-drafts').textContent);break;
      case 'toggle-credential':{const input=$('#new-credential');input.type=input.type==='password'?'text':'password';break;}
      case 'export':{el.disabled=true;const data=await api('/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`共席-工作区-${new Date().toISOString().slice(0,10)}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);el.disabled=false;toast('工作区已导出。');break;}
      case 'retry':if(state.data)await refresh(true);else await start();break;
    }
  } catch(error){toast(error.message);if(el.isConnected)el.disabled=false;}
});
document.addEventListener('change',async event=>{
  const el=event.target;
  if(el.matches('[data-theme-picker]')){window.GongxiTheme?.setPreference(el.value);return;}
  if(el.id==='credential-file'){
    const file=el.files?.[0];
    if(file)await importCredentialFile(file);
    el.value='';
    return;
  }
  if(!el.dataset.taskStatus&&!el.dataset.taskAssignee)return;
  el.disabled=true;
  try{await patch(`/tasks/${el.dataset.taskStatus||el.dataset.taskAssignee}`,el.dataset.taskStatus?{status:el.value}:{assignee_id:el.value||null});await refresh(true);toast('任务已更新。');}
  catch(error){toast(error.message);await refresh(true).catch(()=>{});}finally{el.disabled=false;}
});
document.addEventListener('input',event=>{if(event.target.id==='message-draft'){saveDraft(state.topic,event.target.value);updatePreview();const form=$('#message-form');if(form)delete form.dataset.requestKey;}if(event.target.id==='credential-name'||event.target.id==='credential-description')saveCredentialMeta();});
document.addEventListener('invalid',event=>{if(event.target.id==='message-draft')setComposerMode(false);},true);
window.addEventListener('storage',()=>{for(const el of document.querySelectorAll('[data-theme-picker]'))el.value=window.GongxiTheme.getPreference();});
document.addEventListener('compositionstart',()=>{composing=true;});
document.addEventListener('compositionend',()=>{composing=false;if(deferredRender){deferredRender=false;render(true);}});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&state.mobile){state.mobile=false;render(true);}
  if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&event.target.id==='message-draft'){event.preventDefault();$('#message-form')?.requestSubmit();}
});
modal.addEventListener('click',event=>{if(event.target===modal){const r=modal.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
function logout(){clearMarkdownCache();previewing=false;clearOwnDrafts();state.recoveryDraft='';state.token='';state.member=null;state.data=null;state.error='';state.joining=false;state.joinCode='';state.mobile=false;pendingMessages.clear();pendingEnrollment=null;credentialOrigin=location.origin;try{sessionStorage.removeItem('gongxi.token');sessionStorage.removeItem('gongxi.pending');sessionStorage.removeItem('gongxi.invitation');}catch{}history.replaceState(null,'','/');render();}
async function start(){
  try{const status=await api('/status');state.initialized=status.initialized;state.error='';if(pendingEnrollment){await recoverEnrollment();return;}if(state.token&&!state.joining){try{await authenticate(state.token);return;}catch(error){if(error.status===401||error.status===403){expireIdentity();return;}state.error=error.message;}}render();}
  catch(error){state.error=error.message;render();}
}
start();
setInterval(sync,5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)sync();});
