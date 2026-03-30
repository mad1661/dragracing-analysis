const { initializeApp, deleteApp, getApps } = require("firebase/app");
const { getAuth, signInWithEmailAndPassword } = require("firebase/auth");
const { getDatabase, ref, onValue, off, goOffline } = require("firebase/database");
const {
  getFirestore, collection, query, where, getDocs,
  orderBy, limit, doc, getDoc, terminate,
} = require("firebase/firestore");

const FIREBASE_CONFIG = {
  apiKey: "AIzaSyCDfI8vVi4VjPYAmT7nNL4RvzB15livS54",
  authDomain: "nhra-timing-api.firebaseapp.com",
  databaseURL: "https://nhra-timing-api-default-rtdb.firebaseio.com",
  projectId: "nhra-timing-api",
  storageBucket: "nhra-timing-api.firebasestorage.app",
  messagingSenderId: "1043698542673",
  appId: "1:1043698542673:web:8d98acb05415ed51e1b070",
};

const API_BASE = "https://nhra-timing-api.web.app";

class FirebaseClient {
  constructor(store) {
    this._store = store;
    this._app = null;
    this._auth = null;
    this._rtdb = null;
    this._firestore = null;
    this._streamRef = null;
    this._streamListener = null;
    this._isLoggedIn = false;

    this._initApp();
  }

  _initApp() {
    const existing = getApps();
    this._app = existing.length > 0 ? existing[0] : initializeApp(FIREBASE_CONFIG);
    this._auth = getAuth(this._app);
    this._rtdb = getDatabase(this._app);
    this._firestore = getFirestore(this._app);
  }

  static async testConnection() {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      const res = await fetch(API_BASE, { signal: controller.signal });
      clearTimeout(timeout);
      return {
        reachable: res.status < 500,
        status: res.status,
        url: API_BASE,
      };
    } catch (err) {
      return {
        reachable: false,
        error: err.name === "AbortError" ? "Connection timed out" : err.message,
        url: API_BASE,
      };
    }
  }

  async login(email, password) {
    if (!this._auth) {
      this._initApp();
    }
    const cred = await signInWithEmailAndPassword(this._auth, email, password);
    this._isLoggedIn = true;
    return cred.user;
  }

  async getDevices() {
    if (!this._firestore || !this._auth.currentUser) return [];

    const q = query(
      collection(this._firestore, "devices"),
      where("owner", "==", this._auth.currentUser.uid)
    );
    const snapshot = await getDocs(q);
    return snapshot.docs.map((d) => ({ id: d.id, ...d.data() }));
  }

  async getTracks() {
    if (!this._firestore) return [];

    const q = query(
      collection(this._firestore, "tracks"),
      orderBy("name", "asc")
    );
    const snapshot = await getDocs(q);
    return snapshot.docs.map((d) => ({ id: d.id, ...d.data() }));
  }

  async getOnlineTracks() {
    if (!this._rtdb) return [];

    return new Promise((resolve) => {
      const devicesRef = ref(this._rtdb, "/devices");
      onValue(devicesRef, (snapshot) => {
        const val = snapshot.val();
        if (!val) { resolve([]); return; }

        const tracks = [];
        for (const [deviceId, device] of Object.entries(val)) {
          if (device.status && device.status.online) {
            tracks.push({
              deviceId,
              trackName: device.status.trackName || deviceId,
              trackLocation: device.status.trackLocation || "",
              online: true,
            });
          }
        }
        resolve(tracks);
      }, { onlyOnce: true });
    });
  }

  onStreamData(deviceId, callback) {
    if (!this._rtdb) return;

    this.stopStream();

    this._streamRefs = [];
    for (const channel of ["serial", "tcp"]) {
      const channelRef = ref(this._rtdb, `/devices/${deviceId}/stream/${channel}`);
      const listener = onValue(channelRef, (snapshot) => {
        const val = snapshot.val();
        if (val) callback({ ...val, source: channel });
      });
      this._streamRefs.push({ ref: channelRef, listener });
    }
  }

  stopStream() {
    if (this._streamRefs) {
      this._streamRefs.forEach(({ ref: r, listener }) => off(r, "value", listener));
      this._streamRefs = null;
    }
  }

  async destroy() {
    this.stopStream();
    try { if (this._rtdb) goOffline(this._rtdb); } catch {}
    try { if (this._firestore) await terminate(this._firestore); } catch {}
    try { if (this._app) await deleteApp(this._app); } catch {}
    this._app = null;
    this._auth = null;
    this._rtdb = null;
    this._firestore = null;
    this._isLoggedIn = false;
  }

  get isLoggedIn() {
    return this._isLoggedIn && this._auth && this._auth.currentUser !== null;
  }

}

module.exports = { FirebaseClient };
