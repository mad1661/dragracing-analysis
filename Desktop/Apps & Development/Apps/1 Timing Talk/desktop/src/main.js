const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage } = require("electron");
const path = require("path");
const Store = require("electron-store");
const { SerialBridge } = require("./serial-bridge");
const { FirebaseClient } = require("./firebase-client");

app.disableHardwareAcceleration();

const store = new Store();
let mainWindow = null;
let tray = null;
let firebaseClient = null;
let serialBridge = null;

function cleanup() {
  try { if (serialBridge) serialBridge.stop(); } catch {}
  try { if (firebaseClient) firebaseClient.stopStream(); } catch {}
  serialBridge = null;
}

function forceQuit() {
  cleanup();
  process.exit(0);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 520,
    height: 760,
    resizable: true,
    title: "Timing Talk",
    backgroundColor: "#0f172a",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));

  mainWindow.on("close", (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

function createTray() {
  const iconPath = path.join(__dirname, "..", "assets", "tray-icon.png");
  let trayIcon;
  try {
    trayIcon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 });
  } catch {
    trayIcon = nativeImage.createEmpty();
  }

  tray = new Tray(trayIcon);
  updateTrayMenu();
  tray.setToolTip("Timing Talk");
  tray.on("click", () => {
    if (mainWindow) {
      mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
    }
  });
}

function updateTrayMenu(status = "Disconnected", trackName = "") {
  const comPort = serialBridge ? serialBridge.portName : "None";
  const items = [
    { label: `Status: ${status}`, enabled: false },
    { label: `COM Port: ${comPort}`, enabled: false },
  ];
  if (trackName) {
    items.push({ label: `Track: ${trackName}`, enabled: false });
  }
  items.push(
    { type: "separator" },
    { label: "Show Window", click: () => mainWindow && mainWindow.show() },
    { type: "separator" },
    { label: "Quit", click: () => forceQuit() }
  );
  tray.setContextMenu(Menu.buildFromTemplate(items));
}

// IPC handlers
ipcMain.handle("get-config", () => {
  return {
    comPortName: store.get("comPortName", ""),
    email: store.get("email", ""),
    lastDeviceId: store.get("deviceId", ""),
  };
});

ipcMain.handle("save-config", (_, config) => {
  for (const [key, value] of Object.entries(config)) {
    store.set(key, value);
  }
  return true;
});

ipcMain.handle("test-connection", async () => {
  try {
    return await FirebaseClient.testConnection();
  } catch (err) {
    return { reachable: false, error: err.message };
  }
});

ipcMain.handle("login", async (_, email, password) => {
  try {
    if (!firebaseClient) {
      firebaseClient = new FirebaseClient(store);
    }
    await firebaseClient.login(email, password);
    store.set("email", email);
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle("get-devices", async () => {
  if (!firebaseClient) return [];
  try {
    return await firebaseClient.getDevices();
  } catch {
    return [];
  }
});

ipcMain.handle("get-tracks", async () => {
  if (!firebaseClient) {
    firebaseClient = new FirebaseClient(store);
  }
  try {
    return await firebaseClient.getTracks();
  } catch {
    return [];
  }
});

ipcMain.handle("get-online-tracks", async () => {
  if (!firebaseClient) {
    firebaseClient = new FirebaseClient(store);
  }
  try {
    return await firebaseClient.getOnlineTracks();
  } catch {
    return [];
  }
});

ipcMain.handle("start-bridge", async (_, deviceId) => {
  try {
    if (serialBridge) {
      serialBridge.stop();
    }
    serialBridge = new SerialBridge(store.get("comPortName", ""));
    await serialBridge.start();

    let trackName = "";
    if (firebaseClient) {
      firebaseClient.onStreamData(deviceId, (data) => {
        if (serialBridge && data.raw) {
          serialBridge.write(data.raw);
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send("serial-data", data);
          }
        }
      });
    }

    store.set("deviceId", deviceId);
    updateTrayMenu("Connected", trackName);
    return {
      success: true,
      port: serialBridge.portName,
      devicePath: serialBridge.devicePath,
      tcpPort: serialBridge.tcpPort,
    };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle("stop-bridge", () => {
  if (serialBridge) {
    serialBridge.stop();
    serialBridge = null;
  }
  if (firebaseClient) {
    firebaseClient.stopStream();
  }
  updateTrayMenu("Disconnected");
  return true;
});

ipcMain.handle("get-status", () => {
  return {
    bridgeActive: serialBridge !== null && serialBridge.isRunning,
    comPort: serialBridge ? serialBridge.portName : "None",
    devicePath: serialBridge ? serialBridge.devicePath : "",
    tcpPort: serialBridge ? serialBridge.tcpPort : 0,
    firebaseConnected: firebaseClient ? firebaseClient.isLoggedIn : false,
  };
});

ipcMain.handle("quit-app", () => {
  forceQuit();
});

app.whenReady().then(() => {
  createWindow();
  createTray();
});

app.on("window-all-closed", () => {});

app.on("before-quit", () => {
  app.isQuitting = true;
  cleanup();
});

app.on("activate", () => {
  if (mainWindow) mainWindow.show();
});
