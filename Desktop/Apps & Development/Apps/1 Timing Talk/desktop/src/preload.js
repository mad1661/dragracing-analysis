const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("timingtalk", {
  getConfig: () => ipcRenderer.invoke("get-config"),
  saveConfig: (config) => ipcRenderer.invoke("save-config", config),
  testConnection: () => ipcRenderer.invoke("test-connection"),
  login: (email, password) => ipcRenderer.invoke("login", email, password),
  getDevices: () => ipcRenderer.invoke("get-devices"),
  getTracks: () => ipcRenderer.invoke("get-tracks"),
  getOnlineTracks: () => ipcRenderer.invoke("get-online-tracks"),
  startBridge: (deviceId) => ipcRenderer.invoke("start-bridge", deviceId),
  stopBridge: () => ipcRenderer.invoke("stop-bridge"),
  getStatus: () => ipcRenderer.invoke("get-status"),
  quitApp: () => ipcRenderer.invoke("quit-app"),
  onSerialData: (callback) => {
    ipcRenderer.on("serial-data", (_, data) => callback(data));
  },
});
