/* global exports -- imported as script, functions exposed globally */
const FIREBASE_CONFIG = {
  apiKey: "AIzaSyCDfI8vVi4VjPYAmT7nNL4RvzB15livS54",
  authDomain: "nhra-timing-api.firebaseapp.com",
  databaseURL: "https://nhra-timing-api-default-rtdb.firebaseio.com",
  projectId: "nhra-timing-api",
  storageBucket: "nhra-timing-api.firebasestorage.app",
  messagingSenderId: "1043698542673",
  appId: "1:1043698542673:web:8d98acb05415ed51e1b070",
};

const API_BASE = "/api";

let _app, _auth, _currentUser = null;

async function initFirebase() {
  const { initializeApp } = await import("https://www.gstatic.com/firebasejs/11.1.0/firebase-app.js");
  const { getAuth, onAuthStateChanged } = await import("https://www.gstatic.com/firebasejs/11.1.0/firebase-auth.js");

  _app = initializeApp(FIREBASE_CONFIG);
  _auth = getAuth(_app);

  return new Promise((resolve) => {
    onAuthStateChanged(_auth, (user) => {
      _currentUser = user;
      updateAuthUI(user);
      resolve(user);
    });
  });
}

async function signUp(email, password) {
  const { createUserWithEmailAndPassword } = await import("https://www.gstatic.com/firebasejs/11.1.0/firebase-auth.js");
  return createUserWithEmailAndPassword(_auth, email, password);
}

async function signIn(email, password) {
  const { signInWithEmailAndPassword } = await import("https://www.gstatic.com/firebasejs/11.1.0/firebase-auth.js");
  return signInWithEmailAndPassword(_auth, email, password);
}

async function signOut() {
  const { signOut: fbSignOut } = await import("https://www.gstatic.com/firebasejs/11.1.0/firebase-auth.js");
  await fbSignOut(_auth);
}

async function getToken() {
  if (!_currentUser) return null;
  return _currentUser.getIdToken();
}

async function apiFetch(path, opts = {}) {
  const token = await getToken();
  const headers = { ...(opts.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (opts.body && typeof opts.body === "object") {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(API_BASE + path, { ...opts, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || "Request failed");
  }
  return res.json();
}

function updateAuthUI(user) {
  const userEl = document.getElementById("nav-user");
  const loginLink = document.getElementById("nav-login");
  const logoutBtn = document.getElementById("nav-logout");
  const authRequired = document.querySelectorAll(".auth-required");
  const authHide = document.querySelectorAll(".auth-hide");

  if (user) {
    if (userEl) { userEl.textContent = user.email; userEl.classList.remove("hidden"); }
    if (loginLink) loginLink.classList.add("hidden");
    if (logoutBtn) logoutBtn.classList.remove("hidden");
    authRequired.forEach(el => el.classList.remove("hidden"));
    authHide.forEach(el => el.classList.add("hidden"));
  } else {
    if (userEl) userEl.classList.add("hidden");
    if (loginLink) { loginLink.classList.remove("hidden"); }
    if (logoutBtn) logoutBtn.classList.add("hidden");
    authRequired.forEach(el => el.classList.add("hidden"));
    authHide.forEach(el => el.classList.remove("hidden"));
  }
}

function isLoggedIn() {
  return _currentUser !== null;
}

function getCurrentUser() {
  return _currentUser;
}
