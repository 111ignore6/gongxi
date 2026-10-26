(() => {
  'use strict';

  const STORAGE_KEY = 'gongxi.theme';
  const validPreferences = new Set(['system', 'light', 'dark']);
  const root = document.documentElement;
  const colorMeta = document.querySelector('meta[name="theme-color"]');
  const schemeMeta = document.querySelector('meta[name="color-scheme"]');
  const media = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;
  let preference = 'system';

  function readPreference() {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      return validPreferences.has(saved) ? saved : 'system';
    } catch (_) {
      return 'system';
    }
  }

  function getResolved() {
    return preference === 'system'
      ? (media && media.matches ? 'dark' : 'light')
      : preference;
  }

  function apply() {
    const resolved = getResolved();
    root.dataset.theme = resolved;
    if (colorMeta) colorMeta.setAttribute('content', resolved === 'dark' ? '#201f1b' : '#f3f1eb');
    if (schemeMeta) schemeMeta.setAttribute('content', resolved);
  }

  function setPreference(value) {
    if (!validPreferences.has(value)) return false;
    preference = value;
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (_) {
      // Theme changes still apply when storage is unavailable.
    }
    apply();
    return true;
  }

  preference = readPreference();
  apply();

  if (media) {
    const onSystemChange = () => {
      if (preference === 'system') apply();
    };
    if (typeof media.addEventListener === 'function') media.addEventListener('change', onSystemChange);
    else if (typeof media.addListener === 'function') media.addListener(onSystemChange);
  }

  window.addEventListener('storage', (event) => {
    if (event.key !== null && event.key !== STORAGE_KEY) return;
    preference = validPreferences.has(event.newValue) ? event.newValue : 'system';
    apply();
  });

  window.GongxiTheme = Object.freeze({
    getPreference: () => preference,
    setPreference,
    getResolved,
  });
})();
