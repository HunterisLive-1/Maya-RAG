/* global boilermindShell, boilermindHud */
'use strict';

const TOPK_CHOICES = [3, 5, 7, 10];

/** Last resolved settings API origin (for error messages). */
let lastResolvedOrigin = 'http://127.0.0.1:7071';

/** True once we've received the real port from the WS hud_config message. */
let _portConfirmed = false;
/** Resolved port number pushed from the WS bridge (may differ from env var). */
let _confirmedPort = null;

async function apiBase() {
  try {
    const shell = typeof window !== 'undefined' ? window.boilermindShell : undefined;
    if (shell && typeof shell.getSettingsApiOrigin === 'function') {
      const o = await shell.getSettingsApiOrigin();
      if (o && typeof o === 'string' && o.startsWith('http')) {
        lastResolvedOrigin = o;
        return o;
      }
    }
  } catch (_e) {
    /* noop */
  }
  try {
    if (window.boilermindHud && typeof window.boilermindHud.settingsApiOrigin === 'function') {
      const o = window.boilermindHud.settingsApiOrigin();
      if (o && typeof o === 'string') {
        lastResolvedOrigin = o;
        return o;
      }
    }
  } catch (_e2) {
    /* noop */
  }
  lastResolvedOrigin = 'http://127.0.0.1:7071';
  return lastResolvedOrigin;
}

let selectedTopK = 5;
let pickedPdfPath = null;

function $(id) {
  return document.getElementById(id);
}

function setSaveBanner(msg, ok) {
  const el = $('saveBanner');
  el.textContent = msg;
  el.classList.remove('hidden');
  el.style.background = ok ? 'rgba(0, 255, 136, 0.12)' : 'rgba(255, 51, 51, 0.12)';
  el.style.color = ok ? 'var(--accent-green)' : 'var(--accent-red)';
}

function buildTopK() {
  const row = $('topkRow');
  row.innerHTML = '';
  for (const n of TOPK_CHOICES) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'topk-btn' + (n === selectedTopK ? ' active' : '');
    b.textContent = String(n);
    b.addEventListener('click', () => {
      selectedTopK = n;
      buildTopK();
      $('topkCaption').textContent = `Currently: ${selectedTopK} chunks`;
    });
    row.appendChild(b);
  }
}

async function loadAll() {
  let base;
  try {
    base = await apiBase();
    const r = await fetch(`${base}/settings/load`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    selectedTopK = Number(d.top_k) || 5;
    buildTopK();
    $('topkCaption').textContent = `Currently: ${selectedTopK} chunks`;
    $('inpHudPort').value = String(d.hud_port ?? 7070);
    $('selVoice').value = d.voice || 'Laomedeia';
    const keyInp = $('inpApiKey');
    keyInp.value = '';
    keyInp.placeholder = d.has_api_key ? (d.api_key_masked || '********') : 'Paste API key';
    $('apiTestStatus').textContent = 'Status: —';
    renderBooks(d.loaded_books);
    return true;
  } catch (e) {
    try {
      base = await apiBase();
    } catch (_e2) {
      base = lastResolvedOrigin;
    }
    $('apiTestStatus').textContent =
      `Cannot reach settings API at ${base} (${e.message || 'Failed to fetch'}). Is BoilerMind running? Check firewall.`;
    return false;
  }
}

/**
 * Wait for the real settings-API port to be confirmed via the WS hud_config
 * message before making the first load call.  Falls back to an immediate call
 * if we don't hear anything within ~2 s so the window is never blank.
 */
async function loadAllWhenReady() {
  const MAX_POLLS = 20;        // 20 × 100 ms = 2 s
  const POLL_MS  = 100;

  // Poll until the IPC handle returns a port that was pushed from the WS bridge.
  for (let i = 0; i < MAX_POLLS; i++) {
    try {
      const shell = typeof window !== 'undefined' ? window.boilermindShell : undefined;
      if (shell && typeof shell.getSettingsApiOrigin === 'function') {
        const origin = await shell.getSettingsApiOrigin();
        // main.js returns the env-var default until settingsPortFromWs is set.
        // We consider the port "confirmed" once we successfully load settings.
        if (origin && origin.startsWith('http')) {
          lastResolvedOrigin = origin;
          const ok = await loadAll();
          if (ok) return;   // success — done
        }
      }
    } catch (_e) {
      /* noop – keep polling */
    }
    await new Promise((res) => setTimeout(res, POLL_MS));
  }

  // Fallback: try one more time regardless
  await loadAll();

  // If it still failed, schedule a retry in 2 s in case Python is still starting.
  setTimeout(async () => {
    const ok = await loadAll();
    if (!ok) setTimeout(() => loadAll(), 3000);
  }, 2000);
}

function renderBooks(payload) {
  const wrap = $('booksWrap');
  wrap.innerHTML = '';
  const books = (payload && payload.books) || [];
  if (!books.length) {
    const d = document.createElement('div');
    d.className = 'st-muted';
    d.style.padding = '10px';
    d.textContent = 'No books loaded.';
    wrap.appendChild(d);
    return;
  }
  for (const b of books) {
    const line = document.createElement('div');
    line.className = 'book-line';
    const left = document.createElement('div');
    left.innerHTML = `<div>📘 ${escapeHtml(b.book_name || b.book_id)}</div>
      <div class="book-meta">${Number(b.total_chunks) || 0} chunks</div>`;
    const rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'btn-mini';
    rm.textContent = '🗑 Remove';
    rm.addEventListener('click', () => removeBook(b.book_id));
    line.appendChild(left);
    line.appendChild(rm);
    wrap.appendChild(line);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) => {
    const m = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return m[ch];
  });
}

async function removeBook(bookId) {
  if (!bookId || !confirm('Remove this book from the knowledge base?')) return;
  try {
    const base = await apiBase();
    const r = await fetch(`${base}/settings/book/${encodeURIComponent(bookId)}`, {
      method: 'DELETE',
    });
    const j = await r.json();
    if (!j.success) throw new Error(j.error || 'remove failed');
    await loadAll();
  } catch (e) {
    alert(String(e));
  }
}

async function testApi() {
  const key = $('inpApiKey').value.trim();
  if (!key) {
    $('apiTestStatus').textContent = 'Status: enter an API key to test';
    $('apiTestStatus').style.color = 'var(--accent-amber)';
    return;
  }
  $('apiTestStatus').textContent = 'Status: testing…';
  $('apiTestStatus').style.color = 'var(--text-secondary)';
  try {
    const base = await apiBase();
    const r = await fetch(`${base}/settings/test-api`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key }),
    });
    const j = await r.json();
    if (j.success) {
      $('apiTestStatus').textContent = 'Status: ✓ API key valid';
      $('apiTestStatus').style.color = 'var(--accent-green)';
    } else {
      $('apiTestStatus').textContent = 'Status: ✗ ' + (j.error || 'Invalid');
      $('apiTestStatus').style.color = 'var(--accent-red)';
    }
  } catch (e) {
    const base =
      typeof lastResolvedOrigin === 'string'
        ? lastResolvedOrigin
        : 'http://127.0.0.1:7071';
    $('apiTestStatus').textContent =
      `Status: ✗ Failed to fetch (${base}). Is BoilerMind / Settings API running?`;
    $('apiTestStatus').style.color = 'var(--accent-red)';
  }
}

async function saveSettings(closeAfterMs) {
  const body = {};
  const k = $('inpApiKey').value.trim();
  if (k) body.api_key = k;
  body.top_k = selectedTopK;
  const hp = parseInt(String($('inpHudPort').value || '').trim(), 10);
  if (Number.isFinite(hp)) body.hud_port = hp;
  body.voice = $('selVoice').value.trim() || 'Laomedeia';

  try {
    const base = await apiBase();
    const r = await fetch(`${base}/settings/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok || !j.success) throw new Error(j.detail || j.message || 'save failed');
    const msg =
      j.message || '✓ Settings saved. Restart BoilerMind to apply API / port changes.';
    setSaveBanner(msg, true);
    if (closeAfterMs) {
      setTimeout(() => window.boilermindShell?.closeSettingsWindow?.(), closeAfterMs);
    }
    await loadAll();
  } catch (e) {
    const hint =
      e && String(e.message || e).includes('fetch')
        ? ` Failed to reach ${lastResolvedOrigin}.`
        : '';
    setSaveBanner(String(e) + hint, false);
  }
}

async function ingestStream() {
  if (!pickedPdfPath) {
    $('ingestStatus').textContent = 'Select and copy a PDF first (ADD BOOK).';
    return;
  }
  const name = $('inpDisplayName').value.trim();
  $('ingestStatus').textContent = '';
  $('ingestProgressBar').style.width = '0%';
  try {
    const base = await apiBase();
    const res = await fetch(`${base}/settings/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pdf_path: pickedPdfPath,
        book_name: name || undefined,
      }),
    });
    if (!res.ok || !res.body) {
      $('ingestStatus').textContent = `Ingest request failed (${res.status}) at ${base}.`;
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() || '';
      for (const block of parts) {
        const line = block.trim();
        if (!line.startsWith('data:')) continue;
        let payload;
        try {
          payload = JSON.parse(line.slice(5).trim());
        } catch (_e2) {
          continue;
        }
        if (payload.type === 'progress') {
          const cur = payload.current_page || 0;
          const tot = payload.total_pages || 1;
          const pct = Math.min(100, Math.round((cur / tot) * 100));
          $('ingestProgressBar').style.width = `${pct}%`;
          $('ingestStatus').textContent = `${payload.status || ''} (${cur}/${tot})`;
        }
        if (payload.type === 'error') {
          $('ingestStatus').textContent = '✗ ' + (payload.error || 'error');
          return;
        }
        if (payload.type === 'complete') {
          $('ingestProgressBar').style.width = '100%';
          $('ingestStatus').textContent = `✓ Done! ${payload.chunks_added || 0} chunks added.`;
          await loadAll();
          return;
        }
      }
    }
  } catch (e) {
    $('ingestStatus').textContent =
      '✗ Failed to fetch ' + lastResolvedOrigin + ' — ' + String(e);
  }
}

function wire() {
  $('btnStClose')?.addEventListener('click', () => window.boilermindShell?.closeSettingsWindow?.());
  $('btnShowKey')?.addEventListener('click', () => {
    const el = $('inpApiKey');
    el.type = el.type === 'password' ? 'text' : 'password';
  });
  $('btnTestApi')?.addEventListener('click', () => testApi());

  $('btnAddBook')?.addEventListener('click', async () => {
    const src = await window.boilermindShell?.pickPdfFile?.();
    if (!src) return;
    const cp = await window.boilermindShell?.copyPdfToBooks?.(src);
    if (!cp || !cp.ok) {
      alert(cp?.error || 'copy failed');
      return;
    }
    pickedPdfPath = cp.path;
    $('pickedPath').textContent = cp.path;
    $('inpDisplayName').value = '';
  });

  $('btnIngest')?.addEventListener('click', () => ingestStream());

  $('btnSaveSettings')?.addEventListener('click', () => saveSettings(1500));

  buildTopK();
}

wire();
void loadAllWhenReady();
