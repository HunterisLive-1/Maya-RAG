'use strict';

const { app, BrowserWindow, dialog, globalShortcut, ipcMain, screen } = require('electron');
const fs = require('fs');
const path = require('path');

let mainWindow = null;
let settingsWindow = null;
let visible = true;

/** Resolved once: same BoilerMind.ico as PyInstaller exe (bundled under assets/). */
function resolveHudIconPath() {
  const forced = process.env.BOILERMIND_ICON_ICO;
  try {
    if (forced && String(forced).trim() && fs.existsSync(forced)) {
      return path.resolve(forced);
    }
  } catch (_e) {
    /* noop */
  }
  /** Installed layout: exeDir/books plus exeDir/assets/icon.ico */
  const fromBooks = process.env.BOILERMIND_BOOKS_DIR;
  const cands = [];
  if (fromBooks && String(fromBooks).trim()) {
    const root = path.resolve(path.dirname(String(fromBooks)));
    cands.push(path.join(root, 'assets', 'icon.ico'));
  }
  /** Frozen: …/_MEIPASS/hud_electron · Dev: repo/hud_electron */
  cands.push(path.resolve(__dirname, '..', 'assets', 'icon.ico'));
  for (const p of cands) {
    try {
      if (fs.existsSync(p)) return p;
    } catch (_e) {
      /* noop */
    }
  }
  return undefined;
}

const HUD_WINDOW_ICON = resolveHudIconPath();

/** Settings HTTP port mirrored from Python `hud_config` (authoritative once WS connects). */
let settingsPortFromWs = null;

const HUD_W = 340;
const HUD_H = 680;
const HUD_MARGIN = 20;

const SETTINGS_W = 520;
const SETTINGS_H = 700;

function killPythonParentIfAny() {
  const raw = process.env.PYTHON_PID;
  if (!raw) return;
  const pid = parseInt(String(raw).trim(), 10);
  if (!Number.isFinite(pid) || pid <= 0) return;
  try {
    process.kill(pid);
  } catch (_e) {
    /* noop */
  }
}

function booksDirFallback() {
  const fromEnv = process.env.BOILERMIND_BOOKS_DIR;
  if (fromEnv) return path.resolve(fromEnv);
  return path.join(__dirname, '..', '..', 'books');
}

function createWindow() {
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;

  const winOpts = {
    width: HUD_W,
    height: HUD_H,
    x: Math.max(0, sw - HUD_W - HUD_MARGIN),
    y: Math.max(0, sh - HUD_H - HUD_MARGIN),
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable: true,
    minWidth: 280,
    minHeight: 420,
    hasShadow: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  };

  /** Native edge/corner resize on frameless Windows (thin transparent windows). */
  if (process.platform === 'win32') {
    winOpts.thickFrame = true;
  }

  if (HUD_WINDOW_ICON) {
    winOpts.icon = HUD_WINDOW_ICON;
  }

  mainWindow = new BrowserWindow(winOpts);

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.setAlwaysOnTop(true, 'floating');
    try {
      mainWindow.moveTop();
    } catch (_e) {
      /* noop */
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.on('close', () => {
    killPythonParentIfAny();
    app.quit();
  });
}

function createSettingsWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;

  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.focus();
    return;
  }

  const bounds = screen.getPrimaryDisplay().workArea;
  const x = Math.round(bounds.x + (bounds.width - SETTINGS_W) / 2);
  const y = Math.round(bounds.y + (bounds.height - SETTINGS_H) / 2);

  settingsWindow = new BrowserWindow({
    width: SETTINGS_W,
    height: SETTINGS_H,
    x,
    y,
    parent: mainWindow,
    modal: false,
    frame: false,
    transparent: false,
    backgroundColor: '#0b1622',
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    show: false,
    ...(HUD_WINDOW_ICON ? { icon: HUD_WINDOW_ICON } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  settingsWindow.loadFile(path.join(__dirname, 'renderer', 'settings.html'));

  settingsWindow.once('ready-to-show', () => {
    settingsWindow.show();
  });

  settingsWindow.on('closed', () => {
    settingsWindow = null;
  });
}

function toggleHud() {
  if (!mainWindow) {
    createWindow();
    visible = true;
    return;
  }
  visible = !visible;
  if (visible) mainWindow.show();
  else mainWindow.hide();
}

ipcMain.on('hud-minimize', () => {
  try {
    mainWindow?.minimize();
  } catch (_e) {
    /* noop */
  }
});

ipcMain.on('hud-close', () => {
  try {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
  } catch (_e) {
    /* noop */
  }
});

ipcMain.on('open-settings', () => createSettingsWindow());

ipcMain.on('boilermind-settings-port', (_event, port) => {
  const n = parseInt(String(port), 10);
  if (Number.isFinite(n) && n >= 1 && n <= 65535) {
    settingsPortFromWs = n;
  }
});

ipcMain.handle('get-settings-api-origin', () => {
  let p = settingsPortFromWs;
  if (p == null) {
    p = parseInt(String(process.env.BOILERMIND_SETTINGS_PORT || '7071'), 10);
  }
  if (!Number.isFinite(p) || p < 1 || p > 65535) {
    p = 7071;
  }
  return `http://127.0.0.1:${p}`;
});

ipcMain.on('settings-window-close', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) win.close();
});

ipcMain.handle('pick-pdf', async () => {
  const owner = BrowserWindow.getFocusedWindow() || mainWindow;
  const r = await dialog.showOpenDialog(owner, {
    title: 'Select PDF',
    properties: ['openFile'],
    filters: [{ name: 'PDF', extensions: ['pdf'] }],
  });
  if (r.canceled || !r.filePaths.length) return null;
  return r.filePaths[0];
});

ipcMain.handle('copy-pdf-to-books', async (_evt, srcPath) => {
  const src = String(srcPath || '');
  if (!src || !fs.existsSync(src)) return { ok: false, error: 'Source file missing' };
  const dstDir = booksDirFallback();
  fs.mkdirSync(dstDir, { recursive: true });
  const base = path.basename(src);
  const dst = path.join(dstDir, base);
  fs.copyFileSync(src, dst);
  return { ok: true, path: dst };
});

app.whenReady().then(() => {
  createWindow();
  globalShortcut.register('CommandOrControl+Shift+B', toggleHud);
});

app.on('will-quit', () => globalShortcut.unregisterAll());

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
