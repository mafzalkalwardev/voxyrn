(function () {
  'use strict';

  const storageKey = 'voxyrn_theme';
  const legacyKey = 'indus_theme';

  function applyTheme(theme) {
    const html = document.documentElement;
    if (theme === 'dark') html.setAttribute('data-theme', 'dark');
    else html.removeAttribute('data-theme');
  }

  function readStoredTheme() {
    try {
      let theme = localStorage.getItem(storageKey);
      if (theme !== 'light' && theme !== 'dark') {
        const legacy = localStorage.getItem(legacyKey);
        if (legacy === 'light' || legacy === 'dark') {
          theme = legacy;
          localStorage.setItem(storageKey, legacy);
          localStorage.removeItem(legacyKey);
        }
      }
      return theme === 'light' || theme === 'dark' ? theme : null;
    } catch (_) {
      return null;
    }
  }

  function getPreferredTheme() {
    const theme = readStoredTheme();
    if (theme) return theme;
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    return prefersDark ? 'dark' : 'light';
  }

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function hookToggleButtons() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;

    function refreshLabel() {
      const isDark = currentTheme() === 'dark';
      btn.textContent = isDark ? 'Light' : 'Dark';
      btn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    }

    btn.addEventListener('click', () => {
      const next = currentTheme() === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(storageKey, next);
      } catch (_) {}
      applyTheme(next);
      refreshLabel();
    });

    refreshLabel();
  }

  applyTheme(getPreferredTheme());

  document.addEventListener('DOMContentLoaded', () => {
    hookToggleButtons();
  });
})();
