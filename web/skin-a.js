// Skin A: keep the active topic tab visible on narrow screens (UI only).
(() => {
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
  function bind() {
    const s = strip();
    if (!s) return false;
    const mo = new MutationObserver(() => requestAnimationFrame(ensureActiveVisible));
    mo.observe(s, { childList: true, subtree: true, attributes: true });
    window.addEventListener('resize', () => requestAnimationFrame(ensureActiveVisible));
    ensureActiveVisible();
    return true;
  }
  if (bind()) return;
  // Observe until the authenticated shell mounts; disconnect as soon as we bind.
  // No timer: on the login page this only reacts to DOM mutations, it does not poll.
  const wait = new MutationObserver(() => {
    if (bind()) wait.disconnect();
  });
  wait.observe(document.documentElement, { childList: true, subtree: true });
})();
