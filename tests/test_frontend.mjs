import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { renderMarkdown, exportThreadMarkdown } from '../web/markdown.mjs';

test('Markdown renders common readable structures and copyable fenced code', () => {
  const html = renderMarkdown([
    '# Heading',
    '',
    '**bold** and *italic*',
    '',
    '- first item',
    '- second item',
    '',
    '> a quoted thought',
    '',
    '| Name | Count |',
    '| --- | ---: |',
    '| apples | 3 |',
    '',
    '```js',
    'const answer = 42;',
    '```',
  ].join('\n'));

  assert.match(html, /<h1>Heading<\/h1>/);
  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /<em>italic<\/em>/);
  assert.match(html, /<ul>[\s\S]*<li>[\s\S]*first item[\s\S]*<\/ul>/);
  assert.match(html, /<blockquote>/);
  assert.match(html, /<div class="markdown-table-wrap"[^>]*>[\s\S]*<table>/);
  assert.match(html, /class="code-language">js<\/span>/);
  assert.match(html, /data-action="copy-code"/);
  assert.match(html, /aria-label="复制代码块"/);
  assert.match(html, /<code>const answer = 42;\n<\/code>/);
});

test('Markdown treats HTML and SVG as text and never renders images', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> <svg onload="alert(2)"><script>x</script></svg>\n\n![remote](https://tracker.invalid/pixel.png "title")');

  assert.doesNotMatch(html, /<img\b/i);
  assert.doesNotMatch(html, /<(?:svg|script)\b/i);
  assert.doesNotMatch(html, /<[^>]*\son(?:error|load)\s*=/i);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;svg onload=/);
  assert.match(html, /markdown-image-note/);
  assert.match(html, /[图片：remote · 未加载]/);
  assert.doesNotMatch(html, /tracker\.invalid/);
});

test('Markdown allows only HTTP(S) and mailto links and adds safe new-tab attributes', () => {
  const html = renderMarkdown([
    '[secure](https://example.test/path)',
    '[email](mailto:person@example.test)',
    '[js](javascript:alert(1))',
    '[vb](vbscript:msgbox(1))',
    '[data](data:text/html,hi)',
    '[file](file:///secret)',
    '[relative](/private)',
  ].join(' '));

  assert.match(html, /href="https:\/\/example\.test\/path" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"/);
  assert.match(html, /href="mailto:person@example\.test" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"/);
  assert.doesNotMatch(html, /href="(?:javascript|vbscript|data|file):/i);
  assert.doesNotMatch(html, /href="\/private"/);
  assert.match(html, /\[js\]\(javascript:alert\(1\)\)/);
  assert.match(html, /\[relative\]\(\/private\)/);
  assert.doesNotMatch(html, /<a[^>]*>\[(?:js|vb|data|file|relative)\]/);
});

test('malicious fenced-code info is text, not injected markup or attributes', () => {
  const html = renderMarkdown('```js" onclick="alert(1) <img src=x onerror=alert(2)>\nhello\n```');

  assert.doesNotMatch(html, /onclick\s*=|<img\b/i);
  assert.match(html, /class="code-language">js&amp;quot;|class="code-language">js&quot;/);
  assert.match(html, /<code>hello\n<\/code>/);
});

test('thread Markdown export preserves original bodies, authors, order and reply links without member secrets', () => {
  const topic = { id: 'topic-1', title: 'Review plan', description: 'Background from the topic.' };
  const messages = [
    { id: 'm1', topic_id: 'topic-1', author_id: 'human', created_at: '2026-10-27T09:00:00Z', body: 'Original **Markdown** with `code`.' },
    { id: 'm2', topic_id: 'topic-1', author_id: 'assistant', created_at: '2026-10-27T09:05:00Z', reply_to: 'm1', body: 'A reply preserving [the source](https://example.test).' },
    { id: 'm3', topic_id: 'topic-1', author_id: 'missing', created_at: '2026-10-27T09:06:00Z', reply_to: 'unknown', body: 'Unknown member stays readable.' },
  ];
  const members = [
    { id: 'human', name: 'Ada', kind: 'human', token: 'member-token-secret', credential: 'credential-secret', api_key: 'api-key-secret' },
    { id: 'assistant', name: 'Helper', kind: 'ai', client_token: 'client-token-secret', authorization: 'Bearer-secret', private_key: 'private-key-secret' },
  ];

  const exported = exportThreadMarkdown(topic, messages, members);
  assert.match(exported, /^# Review plan/m);
  assert.match(exported, /Background from the topic\./);
  assert.ok(exported.indexOf('Original **Markdown** with `code`.') < exported.indexOf('A reply preserving'));
  assert.match(exported, /## 1\. Ada[\s\S]*> 人类 · 2026\\-10\\-27T09:00:00Z/);
  assert.match(exported, /## 2\. Helper[\s\S]*> AI · 2026\\-10\\-27T09:05:00Z[\s\S]*> 回复第 1 条发言/);
  assert.match(exported, /## 3\. 未知成员/);
  assert.doesNotMatch(exported, /member-token-secret|credential-secret|api-key-secret|client-token-secret|Bearer-secret|private-key-secret/);
});

async function loadThemeHarness({ storedValue, mediaMatches = false, storageUnavailable = false } = {}) {
  const script = await readFile(new URL('../web/theme.js', import.meta.url), 'utf8');
  const attributes = new Map();
  const root = { dataset: {} };
  const metas = new Map([
    ['theme-color', { setAttribute: (key, value) => attributes.set(`theme-color:${key}`, value) }],
    ['color-scheme', { setAttribute: (key, value) => attributes.set(`color-scheme:${key}`, value) }],
  ]);
  const writes = [];
  const storageListeners = [];
  const mediaListeners = [];
  const localStorage = {
    getItem(key) {
      if (storageUnavailable) throw new Error('storage denied');
      assert.equal(key, 'gongxi.theme');
      return storedValue ?? null;
    },
    setItem(key, value) {
      if (storageUnavailable) throw new Error('storage denied');
      writes.push([key, value]);
    },
  };
  const media = {
    matches: mediaMatches,
    addEventListener(type, callback) { assert.equal(type, 'change'); mediaListeners.push(callback); },
  };
  const window = {
    localStorage,
    matchMedia(query) { assert.equal(query, '(prefers-color-scheme: dark)'); return media; },
    addEventListener(type, callback) { if (type === 'storage') storageListeners.push(callback); },
  };
  const document = {
    documentElement: root,
    querySelector(selector) {
      if (selector === 'meta[name="theme-color"]') return metas.get('theme-color');
      if (selector === 'meta[name="color-scheme"]') return metas.get('color-scheme');
      return null;
    },
  };
  vm.runInNewContext(script, { window, document });
  return { theme: window.GongxiTheme, root, attributes, writes, storageListeners, media, mediaListeners };
}

test('theme defaults to system and tracks media changes with matching meta colors', async () => {
  const h = await loadThemeHarness({ mediaMatches: false });
  assert.equal(h.theme.getPreference(), 'system');
  assert.equal(h.theme.getResolved(), 'light');
  assert.equal(h.root.dataset.theme, 'light');
  assert.equal(h.attributes.get('theme-color:content'), '#f3f1eb');
  assert.equal(h.attributes.get('color-scheme:content'), 'light');

  h.media.matches = true;
  h.mediaListeners[0]();
  assert.equal(h.theme.getResolved(), 'dark');
  assert.equal(h.root.dataset.theme, 'dark');
  assert.equal(h.attributes.get('theme-color:content'), '#201f1b');
  h.media.matches = false;
  h.mediaListeners[0]();
  assert.equal(h.root.dataset.theme, 'light');
  assert.deepEqual(h.writes, []);
});

test('an explicit dark preference ignores system changes and stores only gongxi.theme', async () => {
  const h = await loadThemeHarness({ storedValue: 'dark', mediaMatches: false });
  assert.equal(h.theme.getPreference(), 'dark');
  assert.equal(h.root.dataset.theme, 'dark');
  h.media.matches = true;
  h.mediaListeners[0]();
  h.media.matches = false;
  h.mediaListeners[0]();
  assert.equal(h.root.dataset.theme, 'dark');
  assert.equal(h.theme.setPreference('light'), true);
  assert.equal(h.root.dataset.theme, 'light');
  assert.deepEqual(h.writes, [['gongxi.theme', 'light']]);
  assert.equal(h.theme.setPreference('auto'), false);
  assert.deepEqual(h.writes, [['gongxi.theme', 'light']]);
});

test('theme storage sync adopts valid values, falls back on invalid values, and ignores other keys', async () => {
  const h = await loadThemeHarness({ storedValue: 'light' });
  h.storageListeners[0]({ key: 'unrelated', newValue: 'dark' });
  assert.equal(h.theme.getPreference(), 'light');
  assert.equal(h.root.dataset.theme, 'light');
  h.storageListeners[0]({ key: 'gongxi.theme', newValue: 'dark' });
  assert.equal(h.theme.getPreference(), 'dark');
  assert.equal(h.root.dataset.theme, 'dark');
  h.storageListeners[0]({ key: 'gongxi.theme', newValue: 'invalid' });
  assert.equal(h.theme.getPreference(), 'system');
  h.storageListeners[0]({ key: null, newValue: null });
  assert.equal(h.theme.getPreference(), 'system');
  assert.deepEqual(h.writes, []);
});

test('theme remains usable when localStorage is unavailable', async () => {
  const h = await loadThemeHarness({ storageUnavailable: true, mediaMatches: true });
  assert.equal(h.theme.getPreference(), 'system');
  assert.equal(h.root.dataset.theme, 'dark');
  assert.equal(h.theme.setPreference('light'), true);
  assert.equal(h.root.dataset.theme, 'light');
  assert.deepEqual(h.writes, []);
});
