import MarkdownIt from './vendor/markdown-it.mjs';

// HTML is always text; rendering never loads remote images or runs extensions.
const md = new MarkdownIt({html: false, breaks: true, linkify: false, typographer: false});
const escape = md.utils.escapeHtml;
md.validateLink = url => /^(https?:\/\/|mailto:)/i.test(url);
md.renderer.rules.image = (tokens, index) => `<span class="markdown-image-note">[图片：${escape(tokens[index].content || '未提供说明')} · 未加载]</span>`;
md.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet('target', '_blank');
  tokens[index].attrSet('rel', 'noopener noreferrer');
  tokens[index].attrSet('referrerpolicy', 'no-referrer');
  return renderer.renderToken(tokens, index, options);
};
md.renderer.rules.table_open = () => '<div class="markdown-table-wrap" role="region" aria-label="表格，可横向滚动" tabindex="0"><table>\n';
md.renderer.rules.table_close = () => '</table></div>\n';
function codeBlock(content, language = '') {
  return `<div class="markdown-code-block"><div class="code-toolbar"><span class="code-language">${escape(language || '纯文本')}</span><button type="button" class="copy-code" data-action="copy-code" aria-label="复制代码块">复制代码</button></div><pre class="markdown-code" tabindex="0"><code>${escape(content)}</code></pre></div>\n`;
}
md.renderer.rules.fence = (tokens, index) => codeBlock(tokens[index].content, tokens[index].info.trim().split(/\s+/)[0].slice(0, 40));
md.renderer.rules.code_block = (tokens, index) => codeBlock(tokens[index].content);
const cache = new Map();
export function clearMarkdownCache() { cache.clear(); }
export function renderMarkdown(value) {
  const text = String(value ?? '');
  if (cache.has(text)) return cache.get(text);
  const html = md.render(text);
  if (cache.size >= 128) cache.delete(cache.keys().next().value);
  cache.set(text, html);
  return html;
}

// Export original Markdown, never DOM text, authentication or private state.
const label = value => String(value ?? '').replace(/\s+/g, ' ').replace(/[\\`*_{}[\]()#+.!|<>~-]/g, '\\$&');
export function exportThreadMarkdown(topic, messages, members) {
  const people = new Map(members.map(member => [member.id, member]));
  const numbers = new Map(messages.map((message, i) => [message.id, i + 1]));
  const lines = [`# ${label(topic.title)}`, '', '> 共席 · 议题讨论导出', ''];
  if (topic.description) lines.push(topic.description, '');
  for (const [i, message] of messages.entries()) {
    const person = people.get(message.author_id);
    lines.push('---', '', `## ${i + 1}. ${label(person?.name || '未知成员')}`, '',
      `> ${person?.kind === 'ai' ? 'AI' : '人类'} · ${label(message.created_at)}`,
      ...(numbers.has(message.reply_to) ? [`> 回复第 ${numbers.get(message.reply_to)} 条发言`] : []),
      '', message.body, '');
  }
  return lines.join('\n');
}
