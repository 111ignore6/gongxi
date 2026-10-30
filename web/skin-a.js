// Skin A: keep the active topic tab visible on narrow screens (UI only).
// Re-binds whenever the authenticated shell is replaced (logout -> login).
(() => {
  const strip = () => document.querySelector('.topicstrip');
  let observed = null;
  let observer = null;

  function ensureActiveVisible(el) {
    const s = el || strip();
    if (!s || s.scrollWidth <= s.clientWidth) return;
    const on = s.querySelector('button.on, a.on');
    if (!on) return;
    const r = on.getBoundingClientRect();
    const sr = s.getBoundingClientRect();
    if (r.left < sr.left || r.right > sr.right) {
      on.scrollIntoView({ inline: 'center', block: 'nearest' });
    }
  }

  function attach(s) {
    if (observed === s) return;
    detach();
    observer = new MutationObserver(() => requestAnimationFrame(() => ensureActiveVisible(s)));
    observer.observe(s, { childList: true, subtree: true, attributes: true });
    observed = s;
    ensureActiveVisible(s);
  }

  function detach() {
    if (observer) observer.disconnect();
    observer = null;
    observed = null;
  }

  // Watch the document for the shell appearing, being replaced, or going away.
  const shellWatch = new MutationObserver(() => {
    const s = strip();
    if (s) attach(s); else detach();
  });
  shellWatch.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener('resize', () => ensureActiveVisible());
  const first = strip();
  if (first) attach(first);
})();
