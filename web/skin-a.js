// Skin A: keep the active topic tab visible on narrow screens (UI only).
const strip = () => document.querySelector('.topicstrip');
function ensureActiveVisible() {
  const s = strip();
  if (!s || s.scrollWidth <= s.clientWidth) return;
  const on = s.querySelector('button.on, a.on');
  if (!on) return;
  const r = on.getBoundingClientRect();
  const sr = s.getBoundingClientRect();
  if (r.left < sr.left || r.right > sr.right) {
    on.scrollIntoView({ inline: 'center', block: 'nearest' });
  }
}
const mo = new MutationObserver(() => requestAnimationFrame(ensureActiveVisible));
function boot() {
  const s = strip();
  if (!s) return setTimeout(boot, 200);
  mo.observe(s, { childList: true, subtree: true, attributes: true });
  ensureActiveVisible();
}
window.addEventListener('resize', () => requestAnimationFrame(ensureActiveVisible));
boot();
