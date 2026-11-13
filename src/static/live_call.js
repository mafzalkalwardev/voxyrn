/**
 * Live call status poller for CRM / Live Run.
 * Shows realtime phase and holds the end result (voicemail, etc.) before clearing.
 */
(function () {
  const PHASE_LABELS = {
    idle: 'Idle',
    preparing: 'Preparing',
    dialing: 'Dialing',
    ringing: 'Ringing',
    connected: 'Connected — live',
    voicemail: 'Voicemail',
    result: 'Call result',
    ended: 'Ended',
    failed: 'Failed',
  };

  function el(id) {
    return document.getElementById(id);
  }

  function outcomeClass(outcome) {
    const o = String(outcome || '').toLowerCase();
    if (o.includes('voicemail')) return 'live-call--voicemail';
    if (o.includes('fail') || o.includes('dial_failed')) return 'live-call--failed';
    if (o.includes('no_answer') || o.includes('no-answer')) return 'live-call--noanswer';
    if (o.includes('connected') || o.includes('ended') || o.includes('interested')) return 'live-call--ok';
    if (o.includes('dry')) return 'live-call--dry';
    return 'live-call--active';
  }

  function render(data) {
    const panel = el('liveCallPanel');
    const resultBanner = el('liveCallResultBanner');
    if (!panel && !resultBanner) return;

    const phase = data.phase || 'idle';
    const outcome = data.outcome || '';
    const active = !!data.active || (phase !== 'idle' && phase !== 'result');
    const showingResult = phase === 'result' && outcome;

    if (panel) {
      panel.classList.toggle('live-call-panel--hidden', phase === 'idle' && !showingResult);
      panel.className = 'live-call-panel ' + outcomeClass(showingResult ? outcome : phase);
      if (phase === 'idle' && !showingResult) {
        panel.classList.add('live-call-panel--hidden');
      }
      const phaseEl = el('liveCallPhase');
      const whoEl = el('liveCallWho');
      const msgEl = el('liveCallMsg');
      if (phaseEl) phaseEl.textContent = showingResult
        ? ('RESULT: ' + String(outcome).toUpperCase())
        : (PHASE_LABELS[phase] || phase.toUpperCase());
      if (whoEl) {
        const who = [data.name, data.phone].filter(Boolean).join(' · ') || '—';
        whoEl.textContent = who;
      }
      if (msgEl) msgEl.textContent = data.message || '';
    }

    if (resultBanner) {
      if (showingResult) {
        resultBanner.style.display = 'flex';
        resultBanner.className = 'live-call-result-banner ' + outcomeClass(outcome);
        const title = el('liveCallResultTitle');
        const detail = el('liveCallResultDetail');
        if (title) title.textContent = String(outcome).replace(/_/g, ' ').toUpperCase();
        if (detail) {
          detail.textContent = [data.name, data.phone, data.message]
            .filter(Boolean)
            .join(' — ');
        }
      } else {
        resultBanner.style.display = 'none';
      }
    }

    // Sync run page state cards when present
    if (typeof highlightStateCard === 'function') {
      const map = {
        preparing: 'sc-idle',
        dialing: 'sc-dialing',
        ringing: 'sc-ringing',
        connected: 'sc-connected',
        voicemail: 'sc-voicemail',
        result: outcomeClass(outcome).includes('fail') ? 'sc-failed'
          : (String(outcome).toLowerCase().includes('voicemail') ? 'sc-voicemail' : 'sc-ended'),
        idle: 'sc-idle',
      };
      if (map[phase]) highlightStateCard(map[phase]);
    }

    const badge = el('globalLiveCallBadge');
    if (badge) {
      badge.style.display = (active || showingResult) ? 'inline-block' : 'none';
      badge.textContent = showingResult
        ? String(outcome).toUpperCase()
        : (PHASE_LABELS[phase] || 'LIVE');
      badge.className = 'run-badge ' + (showingResult ? 'run-badge--result' : '');
    }
  }

  async function poll() {
    try {
      const r = await fetch('/api/live-call');
      if (!r.ok) return;
      render(await r.json());
    } catch (_) { /* ignore */ }
  }

  function start() {
    poll();
    setInterval(poll, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.refreshLiveCallStatus = poll;
})();
