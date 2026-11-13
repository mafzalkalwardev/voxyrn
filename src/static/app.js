/* INDUS TRANSPORTS LLC — Dispatch Agent Console — global JS */

(function () {
  'use strict';

  const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  if (csrfToken && window.fetch) {
    const originalFetch = window.fetch.bind(window);
    window.fetch = function (url, options) {
      const opts = options ? Object.assign({}, options) : {};
      const method = String(opts.method || 'GET').toUpperCase();
      if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
        const headers = new Headers(opts.headers || {});
        if (!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', csrfToken);
        opts.headers = headers;
      }
      return originalFetch(url, opts);
    };
  }

  // ── Topbar clock ─────────────────────────────────────────────────────────
  function startClock() {
    const el = document.getElementById('topbarClock');
    if (!el) return;
    function tick() {
      const now = new Date();
      el.textContent = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    tick();
    setInterval(tick, 1000);
  }

  // ── Global run-status badge ──────────────────────────────────────────────
  async function pollGlobalStatus() {
    try {
      const r = await fetch('/api/run/status');
      const state = await r.json();
      const badge = document.getElementById('globalRunBadge');
      const pill  = document.getElementById('globalStatusPill');

      if (badge) badge.style.display = state.is_running ? 'inline-block' : 'none';

      if (pill) {
        pill.textContent = state.status.toUpperCase();
        pill.className = 'status-pill';
        if (state.status === 'running') {
          pill.classList.add('status-pill--running');
        } else if (state.status === 'completed') {
          pill.classList.add('status-pill--completed');
        } else if (state.status === 'failed') {
          pill.classList.add('status-pill--failed');
        } else if (state.status === 'stopping') {
          pill.classList.add('status-pill--stopping');
        }
      }
    } catch (_) {
      // server might be starting — swallow
    }
    setTimeout(pollGlobalStatus, 4000);
  }

  // ── Live listen-in (Speakers unmute; recordings always keep both sides) ──
  function setListenInButton(enabled) {
    const btn = document.getElementById('listenInTopbarBtn');
    if (!btn) return;
    btn.textContent = enabled ? 'Listen-in: On' : 'Listen-in: Off';
    btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    btn.classList.toggle('status-pill--running', !!enabled);
  }

  async function refreshListenIn() {
    try {
      const r = await fetch('/api/audio/listen-in');
      const data = await r.json();
      setListenInButton(!!data.enabled);
      const runBox = document.getElementById('rListenIn');
      if (runBox) runBox.checked = !!data.enabled;
      const audioBox = document.getElementById('listenInToggle');
      if (audioBox) audioBox.checked = !!data.enabled;
    } catch (_) {
      // ignore while server starts
    }
  }

  window.voxyrnToggleListenIn = async function voxyrnToggleListenIn() {
    const btn = document.getElementById('listenInTopbarBtn');
    const currentlyOn = btn && btn.getAttribute('aria-pressed') === 'true';
    const enabled = !currentlyOn;
    try {
      const r = await fetch('/api/audio/listen-in', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const data = await r.json();
      setListenInButton(!!data.enabled);
      const runBox = document.getElementById('rListenIn');
      if (runBox) runBox.checked = !!data.enabled;
      const audioBox = document.getElementById('listenInToggle');
      if (audioBox) audioBox.checked = !!data.enabled;
    } catch (_) {
      // leave UI unchanged on failure
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    startClock();
    pollGlobalStatus();
    refreshListenIn();
  });
})();
