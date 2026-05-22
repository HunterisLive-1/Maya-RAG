'use strict';

const { contextBridge, ipcRenderer } = require('electron');

const hudHost = process.env.BOILERMIND_HUD_HOST || '127.0.0.1';
const hudPort = process.env.BOILERMIND_HUD_WS_PORT || process.env.BOILERMIND_HUD_PORT || '7070';
const settingsPort = process.env.BOILERMIND_SETTINGS_PORT || '7071';

contextBridge.exposeInMainWorld('boilermindShell', {
  minimize: () => ipcRenderer.send('hud-minimize'),
  closeHud: () => ipcRenderer.send('hud-close'),
  openSettings: () => ipcRenderer.send('open-settings'),
  closeSettingsWindow: () => ipcRenderer.send('settings-window-close'),
  pickPdfFile: () => ipcRenderer.invoke('pick-pdf'),
  copyPdfToBooks: (srcPath) => ipcRenderer.invoke('copy-pdf-to-books', srcPath),
});

contextBridge.exposeInMainWorld('boilermindHud', {
  version: 3,
  hudWsUrl: () => `ws://${hudHost}:${hudPort}`,
  settingsApiOrigin: () => `http://127.0.0.1:${settingsPort}`,
});
