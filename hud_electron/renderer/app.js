/* global boilermindShell, boilermindHud */
'use strict';

const WS_URL = (() => {
  try {
    const g = typeof globalThis !== 'undefined' ? globalThis : window;
    if (g.__WS_URL) return String(g.__WS_URL);
    if (g.boilermindHud && typeof g.boilermindHud.hudWsUrl === 'function') return g.boilermindHud.hudWsUrl();
  } catch (_e) {
    /* noop */
  }
  return 'ws://127.0.0.1:7070';
})();

function wireTitlebarButtons() {
  const shell = typeof window !== 'undefined' ? window.boilermindShell : undefined;
  document.getElementById('btnSettings')?.addEventListener(
    'click',
    () => shell?.openSettings?.(),
    { passive: true },
  );
  document.getElementById('btnMin')?.addEventListener(
    'click',
    () => shell?.minimize?.(),
    { passive: true },
  );
  document.getElementById('btnClose')?.addEventListener(
    'click',
    () => shell?.closeHud?.(),
    { passive: true },
  );
}

const statusIndicator = document.getElementById('statusIndicator');
const mainStateLabel = document.getElementById('mainStateLabel');
const subStateDots = document.getElementById('subStateDots');
const booksList = document.getElementById('booksList');
const transcript = document.getElementById('transcript');
const sysOnlineLed = document.getElementById('sysOnlineLed');
const sysOnlineText = document.getElementById('sysOnlineText');
const sysModel = document.getElementById('sysModel');
const sysWs = document.getElementById('sysWs');

/** @type {{role:string,text:string,ts:string}[]} */
const transcriptLines = [];
const MAX_MESSAGES = 8;

/** @type {string | null} */
let lastActivityState = null;
let liveModelDisplay = '—';

function pad2(n) {
  return String(n).padStart(2, '0');
}

function timeNow() {
  const d = new Date();
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function setSystemOnline(online) {
  if (online) {
    sysOnlineLed.classList.remove('led-red');
    sysOnlineLed.classList.add('led-green');
    sysOnlineText.textContent = 'ONLINE';
    sysOnlineText.classList.remove('dim');
  } else {
    sysOnlineLed.classList.remove('led-green');
    sysOnlineLed.classList.add('led-red');
    sysOnlineText.textContent = 'OFFLINE';
    sysOnlineText.classList.add('dim');
  }
}

function setWsLabel(connected) {
  if (connected) {
    sysWs.textContent = 'WS: CONNECTED';
    sysWs.style.color = 'var(--accent-cyan)';
  } else {
    sysWs.textContent = 'WS: DISCONNECTED';
    sysWs.style.color = 'var(--accent-red)';
  }
}

function flashStatus() {
  statusIndicator.classList.remove('state-flash');
  void statusIndicator.offsetWidth;
  statusIndicator.classList.add('state-flash');
  setTimeout(() => statusIndicator.classList.remove('state-flash'), 320);
}

function setActivityState(stateRaw) {
  const s = String(stateRaw || 'idle').toLowerCase().replace(/\s+/g, '_');
  const norm = s.replace(/[^\w_-]/g, '') || 'idle';

  if (norm !== lastActivityState) {
    if (lastActivityState !== null) flashStatus();
    lastActivityState = norm;
  }

  statusIndicator.classList.remove(
    'state-idle',
    'state-listening',
    'state-responding',
    'state-tool_running',
  );

  if (norm === 'listening') {
    statusIndicator.classList.add('state-listening');
    mainStateLabel.textContent = 'LISTENING';
    subStateDots.textContent = '';
  } else if (norm === 'responding') {
    statusIndicator.classList.add('state-responding');
    mainStateLabel.textContent = 'RESPONDING';
    subStateDots.textContent = '';
  } else if (norm === 'tool_running') {
    statusIndicator.classList.add('state-tool_running');
    mainStateLabel.textContent = 'SEARCHING BOOKS...';
    subStateDots.textContent = '⋯';
  } else {
    statusIndicator.classList.add('state-idle');
    mainStateLabel.textContent = 'STANDBY';
    subStateDots.textContent = '';
  }
}

function fmtBackendStatus(msg) {
  const m = String(msg || '').toUpperCase();
  if (m === 'ONLINE') {
    setSystemOnline(true);
    if (lastActivityState === 'idle' || lastActivityState === null) {
      mainStateLabel.textContent = 'STANDBY';
    }
    return;
  }
  if (m === 'OFFLINE') {
    setSystemOnline(false);
    return;
  }
  if (!m || m === 'CONNECTING') {
    setSystemOnline(false);
    sysOnlineText.textContent = 'CONNECTING';
    sysOnlineText.classList.add('dim');
  }
}

function renderBooks(bookArr, count) {
  booksList.innerHTML = '';
  if (!count || !bookArr || !bookArr.length) {
    const row = document.createElement('div');
    row.className = 'book-row book-warn';
    row.innerHTML =
      '<div class="book-row-title">⚠ NO BOOKS LOADED</div><div class="book-row-meta"><span class="coverage-track"><span class="coverage-fill" style="width:0%"></span></span></div>';
    booksList.appendChild(row);
    return;
  }

  const maxChunks = Math.max(...bookArr.map((b) => Number(b.total_chunks) || 0), 1);

  for (const b of bookArr) {
    const chunks = Number(b.total_chunks) || 0;
    const pct = Math.round(Math.min(100, (chunks / maxChunks) * 100));

    const row = document.createElement('div');
    row.className = 'book-row';
    row.innerHTML = `
      <div class="book-row-title">${escapeHtml(b.book_name || b.book_id || 'Book')}</div>
      <div class="book-row-meta">
        <span>${chunks} chunks</span>
        <span class="coverage-track" title="Relative size vs loaded books"><span class="coverage-fill" style="width:${pct}%"></span></span>
      </div>`;
    booksList.appendChild(row);
  }
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (ch) => {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return map[ch];
  });
}

function renderTranscript() {
  transcript.innerHTML = '';
  const n = transcriptLines.length;

  transcriptLines.forEach((row, i) => {
    const isAi = row.role !== 'user';
    const card = document.createElement('div');
    card.className = `msg-card ${isAi ? 'msg-ai' : 'msg-user'}`;
    if (n > 4 && i < n - 4) card.classList.add('msg-old');
    card.innerHTML = `
      <span class="msg-tag">${isAi ? 'BOILERMIND' : 'YOU'}</span>
      <div class="msg-body">${escapeHtml(row.text)}</div>
      <span class="msg-ts">${row.ts}</span>`;
    transcript.appendChild(card);
  });

  transcript.scrollTop = transcript.scrollHeight;
}

function appendTranscript(role, text) {
  transcriptLines.push({
    role,
    text: String(text || ''),
    ts: timeNow(),
  });
  while (transcriptLines.length > MAX_MESSAGES) transcriptLines.shift();
  renderTranscript();
}

function handleHudConfig(payload) {
  const lm = (payload.live_model || '').trim();
  const dn = (payload.display_name || 'BOILERMIND').trim();
  liveModelDisplay = (lm || dn).toUpperCase().replace(/\s+/g, ' ');
  sysModel.textContent = liveModelDisplay;
}

function connectWsLoop() {
  let sock = null;
  let reconnectTimer = null;

  function dial() {
    if (sock) {
      try {
        sock.close();
      } catch (_e) {
        /* noop */
      }
    }

    clearTimeout(reconnectTimer);

    sysWs.textContent = 'WS: CONNECTING';
    sysWs.style.color = 'var(--accent-amber)';

    sock = new WebSocket(WS_URL);

    sock.addEventListener('open', () => {
      setWsLabel(true);
    });

    sock.addEventListener('message', (evt) => {
      let payload;
      try {
        payload = JSON.parse(evt.data);
      } catch (_e) {
        return;
      }

      const t = payload.type;
      if (t === 'activity') {
        setActivityState(payload.state);
      } else if (t === 'status') {
        fmtBackendStatus(payload.message);
      } else if (t === 'transcript') {
        appendTranscript(payload.role, payload.text);
      } else if (t === 'books_status') {
        renderBooks(payload.books || [], payload.count || 0);
      } else if (t === 'hud_config') {
        handleHudConfig(payload);
      }
    });

    sock.addEventListener('close', () => {
      setWsLabel(false);
      sysOnlineText.textContent = 'RECONNECT…';
      sysOnlineLed.classList.remove('led-green');
      sysOnlineLed.classList.add('led-red');
      reconnectTimer = setTimeout(dial, 3000);
    });

    sock.addEventListener('error', () => {
      try {
        sock.close();
      } catch (_e2) {
        /* noop */
      }
    });
  }

  sysModel.textContent = liveModelDisplay;
  setSystemOnline(false);
  sysWs.textContent = 'WS: …';
  setActivityState('idle');
  dial();
}

wireTitlebarButtons();
void boilermindHud;
connectWsLoop();
