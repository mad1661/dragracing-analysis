const { onRequest } = require("firebase-functions/v2/https");
const admin = require("firebase-admin");
const express = require("express");
const cors = require("cors");
const crypto = require("crypto");

admin.initializeApp();

const db = admin.firestore();
const rtdb = admin.database();
const app = express();

app.use((req, _res, next) => {
  if (req.url === "/api" || req.url.startsWith("/api/")) {
    req.url = req.url.slice(4) || "/";
  }
  next();
});

app.use(cors({ origin: true }));
app.use(express.json());
app.use((req, res, next) => {
  res.set("Cache-Control", "no-store");
  next();
});

const SUPER_ADMINS = ["mdawson@nhra.com"];
const SUPPORT_ACTIONS = new Set([
  "diagnostics",
  "fetch_logs",
  "start_kiosk",
  "stop_kiosk",
  "restart_kiosk",
  "restart_app",
]);

async function authenticateAdmin(req, res, next) {
  const authHeader = req.headers.authorization;
  const queryToken = req.query.token;
  let rawToken = null;

  if (authHeader && authHeader.startsWith("Bearer ")) {
    rawToken = authHeader.split("Bearer ")[1];
  } else if (queryToken) {
    rawToken = queryToken;
  }

  if (!rawToken) {
    return res.status(401).json({ error: "Admin authentication required" });
  }
  try {
    req.user = await admin.auth().verifyIdToken(rawToken);
    if (!SUPER_ADMINS.includes(req.user.email)) {
      return res.status(403).json({ error: "Super admin access required" });
    }
    req.isAdmin = true;
    next();
  } catch (err) {
    return res.status(401).json({ error: "Invalid token" });
  }
}

// ─── API Key Authentication ──────────────────────────────────────────────────
// Web developers get an API key from the dashboard. Simpler than Firebase Auth
// tokens for website embedding. Keys are stored in Firestore `api_keys` collection.

async function authenticateApiKey(req, res, next) {
  const apiKey = req.headers["x-api-key"] || req.query.key || req.query.api_key;
  if (!apiKey) {
    return res.status(401).json({
      error: "API key required. Pass via X-API-Key header or ?key= query param.",
      docs: "https://nhra-timing-api.web.app/docs.html",
    });
  }

  try {
    const hash = crypto.createHash("sha256").update(apiKey).digest("hex");
    const snapshot = await db.collection("api_keys").where("keyHash", "==", hash).limit(1).get();
    if (snapshot.empty) {
      return res.status(401).json({ error: "Invalid API key" });
    }

    const keyDoc = snapshot.docs[0];
    const keyData = keyDoc.data();

    if (keyData.disabled) {
      return res.status(403).json({ error: "API key has been disabled" });
    }

    req.apiKeyOwner = keyData.owner;
    req.apiKeyId = keyDoc.id;

    db.collection("api_keys").doc(keyDoc.id).update({
      lastUsed: admin.firestore.FieldValue.serverTimestamp(),
      requestCount: admin.firestore.FieldValue.increment(1),
    }).catch(() => {});

    next();
  } catch (err) {
    return res.status(500).json({ error: "Auth failed" });
  }
}

async function authenticateFirebase(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith("Bearer ")) {
    return res.status(401).json({ error: "Missing or invalid authorization header" });
  }
  try {
    const token = authHeader.split("Bearer ")[1];
    req.user = await admin.auth().verifyIdToken(token);
    next();
  } catch (err) {
    return res.status(401).json({ error: "Invalid token" });
  }
}

// Accepts either API key or Firebase token
async function authenticateAny(req, res, next) {
  const apiKey = req.headers["x-api-key"] || req.query.key || req.query.api_key;
  const authHeader = req.headers.authorization;

  if (apiKey) {
    return authenticateApiKey(req, res, next);
  }
  if (authHeader && authHeader.startsWith("Bearer ")) {
    return authenticateFirebase(req, res, next);
  }

  return res.status(401).json({
    error: "Authentication required. Use x-api-key header or Bearer token.",
  });
}

let _piLocalTime = null;
function setLocalTime(t) { _piLocalTime = t || null; }
function currentClockTime() {
  if (_piLocalTime) return _piLocalTime;
  return new Date().toISOString().slice(11, 19);
}

function emptyLane() {
  return {
    name: "",
    car_num: "",
    class_name: "",
    member_num: "",
    qual: "",
    dial_in: "",
    rt: "",
    sixty: "",
    three30: "",
    six60: "",
    six60_speed: "",
    thousand: "",
    thousand_speed: "",
    et: "",
    speed: "",
    mov: "",
  };
}

function emptyRace() {
  return {
    category: "",
    category_num: "",
    round: "",
    left: emptyLane(),
    right: emptyLane(),
    lane3: emptyLane(),
    lane4: emptyLane(),
    margin: "",
    winner: "",
    placements: {},
    complete: false,
    timestamp: "",
  };
}

function deepCopyRace(race) {
  return {
    category: race.category || "",
    category_num: race.category_num || "",
    round: race.round || "",
    left: { ...emptyLane(), ...(race.left || {}) },
    right: { ...emptyLane(), ...(race.right || {}) },
    lane3: { ...emptyLane(), ...(race.lane3 || {}) },
    lane4: { ...emptyLane(), ...(race.lane4 || {}) },
    margin: race.margin || "",
    winner: race.winner || "",
    placements: race.placements ? { ...race.placements } : {},
    complete: Boolean(race.complete),
    timestamp: race.timestamp || "",
  };
}

function isBlankDial(value) {
  const cleaned = String(value || "").trim().replace(/[.\-\s]/g, "");
  return cleaned.length === 0;
}

function hasRaceData(race) {
  return Boolean(
    race?.category ||
    race?.left?.name ||
    race?.right?.name ||
    race?.lane3?.name ||
    race?.lane4?.name ||
    race?.left?.rt ||
    race?.right?.rt ||
    race?.lane3?.rt ||
    race?.lane4?.rt
  );
}

function readXmlTag(xml, tag) {
  const match = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, "i"));
  return match ? match[1].trim() : "";
}

function readXmlSection(xml, tag) {
  const match = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, "i"));
  return match ? match[1] : "";
}

class RaceParser {
  constructor(state = {}) {
    this.current = deepCopyRace(state.current || emptyRace());
    this.completed = Array.isArray(state.completed)
      ? state.completed.slice(-50).map((race) => deepCopyRace(race))
      : [];
    this.xmlLines = Array.isArray(state.xmlLines) ? [...state.xmlLines] : [];
    this.inXml = Boolean(state.inXml);
  }

  serialize() {
    return {
      current: deepCopyRace(this.current),
      completed: this.completed.slice(-50).map((race) => deepCopyRace(race)),
      xmlLines: [...this.xmlLines],
      inXml: this.inXml,
    };
  }

  getState() {
    return deepCopyRace(this.current);
  }

  getCompleted() {
    return this.completed.slice(-50).map((race) => deepCopyRace(race));
  }

  finishCurrent() {
    if (!hasRaceData(this.current) || this.current.complete) {
      return;
    }
    this.current.complete = true;
    this.current.timestamp = this.current.timestamp || currentClockTime();
    this.completed.push(deepCopyRace(this.current));
    if (this.completed.length > 50) {
      this.completed = this.completed.slice(-50);
    }
  }

  feedLine(line) {
    const text = String(line || "").trim();
    if (!text) return;

    if (text.includes("<TimeSlip>")) {
      this.inXml = true;
      this.xmlLines = [text];
      return;
    }

    if (this.inXml) {
      this.xmlLines.push(text);
      if (text.includes("</TimeSlip>")) {
        this.parseXml(this.xmlLines.join("\n"));
        this.inXml = false;
        this.xmlLines = [];
      }
      return;
    }

    this.parseSerial(text);
  }

  parseSerial(text) {
    if (text.startsWith("S5:D")) {
      const value = text.slice(4).trim();
      if (isBlankDial(value)) {
        this.finishCurrent();
        this.current = emptyRace();
        this.current.timestamp = currentClockTime();
      } else {
        this.current.left.dial_in = value;
      }
      return;
    }

    if (text.startsWith("S6:D")) {
      const value = text.slice(4).trim();
      if (!isBlankDial(value)) {
        this.current.right.dial_in = value;
      }
      return;
    }

    // 4-wide dial-ins (future protocol placeholders)
    if (text.startsWith("S7:D")) {
      const value = text.slice(4).trim();
      if (!isBlankDial(value)) {
        this.current.lane3.dial_in = value;
      }
      return;
    }
    if (text.startsWith("S8:D")) {
      const value = text.slice(4).trim();
      if (!isBlankDial(value)) {
        this.current.lane4.dial_in = value;
      }
      return;
    }

    if (text.startsWith("L+") || text.startsWith("R+")) {
      return;
    }

    if (
      text.length >= 2 &&
      text[0] === "C" &&
      (/\d/.test(text[1]) || (text.length > 2 && text[1] === " " && /\d/.test(text[2])))
    ) {
      this.finishCurrent();
      this.current = emptyRace();
      const parts = text.split(",", 3);
      this.current.category_num = parts[0].slice(1).trim();
      if (parts[1]) this.current.category = parts[1].trim();
      if (parts[2]) this.current.round = parts[2].trim();
      this.current.timestamp = currentClockTime();
      return;
    }

    // Car numbers: c1=left, c2=right, c3=lane3, c4=lane4
    const carNumMap = { "c1": "left", "c2": "right", "c3": "lane3", "c4": "lane4" };
    const carPrefix = text.slice(0, 2);
    if (carNumMap[carPrefix]) {
      this.current[carNumMap[carPrefix]].car_num = text.slice(2).trim();
      return;
    }

    // Class names: b1=left, b2=right, b3=lane3, b4=lane4
    const classMap = { "b1": "left", "b2": "right", "b3": "lane3", "b4": "lane4" };
    if (classMap[carPrefix]) {
      this.current[classMap[carPrefix]].class_name = text.slice(2).trim();
      return;
    }

    // Driver names: n1=left, n2=right, n3=lane3, n4=lane4
    const nameMap = { "n1": "left", "n2": "right", "n3": "lane3", "n4": "lane4" };
    if (nameMap[carPrefix]) {
      this.current[nameMap[carPrefix]].name = text.slice(2).trim();
      return;
    }

    // Member numbers: m1=left, m2=right, m3=lane3, m4=lane4
    const memMap = { "m1": "left", "m2": "right", "m3": "lane3", "m4": "lane4" };
    if (memMap[carPrefix]) {
      this.current[memMap[carPrefix]].member_num = text.slice(2).trim();
      return;
    }
    if (text.startsWith("m-")) {
      this.current.margin = text.slice(2).trim();
      return;
    }
    if (text.startsWith("q1")) {
      this.current.left.qual = text.slice(2).trim();
      return;
    }
    if (text.startsWith("q2")) {
      this.current.right.qual = text.slice(2).trim();
      return;
    }
    if (text.startsWith("q3")) {
      this.current.lane3.qual = text.slice(2).trim();
      return;
    }
    if (text.startsWith("q4")) {
      this.current.lane4.qual = text.slice(2).trim();
      return;
    }

    // Timing data: L=left, R=right, A=lane3, B=lane4 (A/B are placeholders for future 4-wide protocol)
    if (text.length >= 2 && ["L", "R", "A", "B"].includes(text[0]) && /\d/.test(text[1])) {
      const sideMap = { "L": "left", "R": "right", "A": "lane3", "B": "lane4" };
      const side = sideMap[text[0]];
      const fieldMap = {
        "0": "rt",
        "1": "sixty",
        "2": "three30",
        "3": "six60",
        "4": "six60_speed",
        "5": "thousand",
        "6": "et",
        "7": "speed",
      };
      const field = fieldMap[text[1]];
      if (field && side) {
        this.current[side][field] = text.slice(2).trim();
      }
      return;
    }

    if (text.startsWith("w-")) {
      this.current.winner = text.slice(2).trim().toUpperCase();
      return;
    }

    const lower = text.toLowerCase();
    if (lower.startsWith("fl") && text.length > 2 && (/[\d .]/.test(text[2]))) {
      this.current.left.mov = text.slice(2).trim();
      // Only complete if not 4-wide (4-wide completes on last lane finish)
      if (!this.is4WideRace()) {
        this.current.complete = true;
        this.current.timestamp = this.current.timestamp || currentClockTime();
        this.completed.push(deepCopyRace(this.current));
        this.completed = this.completed.slice(-50);
      }
      return;
    }
    if (lower.startsWith("fr") && text.length > 2 && (/[\d .]/.test(text[2]))) {
      this.current.right.mov = text.slice(2).trim();
      if (!this.is4WideRace()) {
        this.current.complete = true;
        this.current.timestamp = this.current.timestamp || currentClockTime();
        this.completed.push(deepCopyRace(this.current));
        this.completed = this.completed.slice(-50);
      }
      return;
    }
    // 4-wide MOV finishes (future protocol placeholders)
    if (lower.startsWith("fa") && text.length > 2 && (/[\d .]/.test(text[2]))) {
      this.current.lane3.mov = text.slice(2).trim();
      return;
    }
    if (lower.startsWith("fb") && text.length > 2 && (/[\d .]/.test(text[2]))) {
      this.current.lane4.mov = text.slice(2).trim();
      // Last lane finish completes the 4-wide race
      if (this.is4WideRace()) {
        this.current.complete = true;
        this.current.timestamp = this.current.timestamp || currentClockTime();
        this.completed.push(deepCopyRace(this.current));
        this.completed = this.completed.slice(-50);
      }
      return;
    }
  }

  is4WideRace() {
    return !!(this.current.lane3?.name || this.current.lane4?.name || this.current.lane3?.rt || this.current.lane4?.rt);
  }

  parseXml(xmlText) {
    const race = emptyRace();
    race.category = readXmlTag(xmlText, "Category");
    race.round = readXmlTag(xmlText, "Rnd");
    race.timestamp = readXmlTag(xmlText, "TimeStamp") || currentClockTime();

    for (const [section, key] of [["Left", "left"], ["Right", "right"], ["Lane3", "lane3"], ["Lane4", "lane4"]]) {
      const xml = readXmlSection(xmlText, section);
      if (!xml) continue;
      race[key].name = readXmlTag(xml, "Name");
      race[key].car_num = readXmlTag(xml, "CarNumber");
      race[key].class_name = readXmlTag(xml, "Class");
      race[key].member_num = readXmlTag(xml, "MemberNum");
      race[key].dial_in = readXmlTag(xml, "DialIn");
      race[key].qual = readXmlTag(xml, "QualPos");
      race[key].rt = readXmlTag(xml, "RT");
      race[key].sixty = readXmlTag(xml, "ft60");
      race[key].three30 = readXmlTag(xml, "ft330");
      race[key].six60 = readXmlTag(xml, "ft660");
      race[key].six60_speed = readXmlTag(xml, "mph660");
      race[key].thousand = readXmlTag(xml, "ft1000");
      race[key].thousand_speed = readXmlTag(xml, "mph1000");
      race[key].et = readXmlTag(xml, "ft1320");
      race[key].speed = readXmlTag(xml, "mph1320");
      if (readXmlTag(xml, "Win").toUpperCase() === "W") {
        race.winner = section.toUpperCase();
      }
    }

    race.complete = true;
    this.current = race;
    this.completed.push(deepCopyRace(race));
    this.completed = this.completed.slice(-50);
  }
}

async function loadParser(deviceId) {
  const snapshot = await rtdb.ref(`/devices/${deviceId}/parserState`).once("value");
  return new RaceParser(snapshot.val() || {});
}

async function saveParser(deviceId, parser) {
  await rtdb.ref(`/devices/${deviceId}/parserState`).set(parser.serialize());
}

function toJsonIfPossible(line) {
  try {
    return JSON.parse(line);
  } catch {
    return null;
  }
}

function toParsedPayload(parser) {
  const state = parser.getState();
  return hasRaceData(state) ? state : null;
}

function getPreferredTrackLabel(source = {}, fallback = "") {
  return (
    source.trackName ||
    source.promoter ||
    source.raceName ||
    fallback
  );
}

async function updateTrackStatus(deviceId, status) {
  if (!deviceId) return;
  const trackName = getPreferredTrackLabel(status, "");
  const trackLocation = status.trackLocation || "";

  await rtdb.ref(`/devices/${deviceId}/status`).update(status);

  if (trackName) {
    await db.collection("tracks").doc(deviceId).set({
      name: trackName,
      trackName: status.trackName || "",
      promoter: status.promoter || "",
      raceName: status.raceName || "",
      location: trackLocation,
      deviceId,
      online: Boolean(status.online),
      lastSeen: status.lastSeen || Date.now(),
    }, { merge: true });
  }

  // Auto-create event entry from heartbeat data
  const raceName = status.raceName || "";
  const promoter = status.promoter || "";
  // Only auto-create events if the Pi explicitly sends a race name
  if (raceName && raceName.trim()) {
    const owner = (promoter || trackName || "local").toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const nameKey = (raceName || new Date().toISOString().slice(0, 10)).toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const dateKey = new Date().toISOString().slice(0, 10);
    const eventId = `${owner}-${nameKey}-${dateKey}`.replace(/-+/g, "-").slice(0, 120);
    await db.collection("events").doc(eventId).set({
      event_name: raceName,
      name: raceName || dateKey,
      track_name: status.trackName || "",
      promoter: promoter,
      device_id: deviceId,
      start_date: dateKey,
      end_date: dateKey,
      status: "active",
      updated_at: new Date().toISOString(),
    }, { merge: true });
  }
}


// ═════════════════════════════════════════════════════════════════════════════
//  PUBLIC ENDPOINTS (no auth required)
// ═════════════════════════════════════════════════════════════════════════════

app.post("/heartbeat", async (req, res) => {
  try {
    const deviceId = String(req.body.deviceId || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    const status = {
      online: true,
      lastSeen: Date.now(),
      deviceId,
      hostname: req.body.hostname || "",
      ip: req.body.ip || "",
      serialPort: req.body.serialPort || "",
      serialStatus: req.body.serialStatus || "",
      serialConnected: /listen|open|idle|test/i.test(req.body.serialStatus || ""),
      tcpHost: req.body.tcpHost || "",
      tcpPort: req.body.tcpPort || "",
      tcpStatus: req.body.tcpStatus || "",
      tcpConnected: /listen|conn|connected|idle/i.test(req.body.tcpStatus || ""),
      baudRate: req.body.baudRate || "",
      uptime: req.body.uptime || "",
      platform: req.body.platform || "",
      trackName: req.body.trackName || "",
      raceName: req.body.raceName || "",
      promoter: req.body.promoter || "",
      logFiles: req.body.logFiles || 0,
      logLines: req.body.logLines || 0,
      logSizeKB: req.body.logSizeKB || 0,
      queuedBatches: req.body.queuedBatches || 0,
    };

    // Push current device settings to /devices/{deviceId}/config in RTDB
    // so the settings page always has the latest config to display
    if (req.body.settings) {
      await rtdb.ref(`/devices/${deviceId}/config`).set({
        event: req.body.settings.event || {},
        device: req.body.settings.device || {},
        has_remote_password: req.body.settings.has_remote_password || false,
        updatedAt: new Date().toISOString(),
      });
    }

    await updateTrackStatus(deviceId, status);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/ingest", async (req, res) => {
  try {
    const deviceId = String(req.body.deviceId || "").trim();
    const rawBatch = String(req.body.data || "");
    const localDate = String(req.body.local_date || "").trim() || new Date().toISOString().slice(0, 10);
    const localTime = String(req.body.local_time || "").trim();
    if (localTime) setLocalTime(localTime);
    if (!deviceId || !rawBatch.trim()) {
      if (localTime) setLocalTime(null);
      return res.status(400).json({ error: "deviceId and data are required" });
    }

    const parser = await loadParser(deviceId);
    const timestamp = Date.now();
    const lines = rawBatch
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    const latest = {};
    const historyWrites = [];

    for (const line of lines) {
      const parsedJson = toJsonIfPossible(line);
      const channel = parsedJson ? "tcp" : "serial";
      let parsed = parsedJson;

      if (!parsedJson) {
        parser.feedLine(line);
        parsed = toParsedPayload(parser);
      }

      latest[channel] = {
        raw: line,
        timestamp,
        source: "ingest",
        parsed: parsed || null,
      };

      historyWrites.push({
        raw: line,
        rawData: line,
        timestamp: admin.firestore.FieldValue.serverTimestamp(),
        timestampMs: timestamp,
        channel,
        sourcePort: channel === "tcp" ? "tcp:ingest" : "serial:ingest",
        parsed: parsed || null,
      });
    }

    const updates = {};
    for (const [channel, payload] of Object.entries(latest)) {
      updates[`/devices/${deviceId}/stream/${channel}`] = payload;
    }
    const raceState = parser.getState();
    // Only include lane3/lane4 in RTDB if they have actual data
    if (raceState.lane3 && !raceState.lane3.name && !raceState.lane3.rt && !raceState.lane3.et) {
      delete raceState.lane3;
    }
    if (raceState.lane4 && !raceState.lane4.name && !raceState.lane4.rt && !raceState.lane4.et) {
      delete raceState.lane4;
    }
    if (raceState.placements && Object.keys(raceState.placements).length === 0) {
      delete raceState.placements;
    }
    updates[`/devices/${deviceId}/raceState/current`] = raceState;
    updates[`/devices/${deviceId}/raceState/completed`] = parser.getCompleted();

    // Also push each line to a feed log so the web Feed page gets every line
    for (const line of lines) {
      const parsedJson = toJsonIfPossible(line);
      const channel = parsedJson ? "tcp" : "serial";
      const feedRef = rtdb.ref(`/devices/${deviceId}/feedLog`).push();
      updates[feedRef.toString().replace(rtdb.ref().toString(), "")] = {
        raw: line,
        channel,
        ts: Date.now(),
      };
    }

    await rtdb.ref().update(updates);

    // Trim feedLog to last 500 entries (async, don't block response)
    rtdb.ref(`/devices/${deviceId}/feedLog`).orderByKey().limitToFirst(1).once("value").then(snap => {
      const count = snap.numChildren();
      // Only trim if we have data - do a count check
      rtdb.ref(`/devices/${deviceId}/feedLog`).once("value").then(allSnap => {
        const total = allSnap.numChildren();
        if (total > 500) {
          const toDelete = total - 500;
          rtdb.ref(`/devices/${deviceId}/feedLog`).orderByKey().limitToFirst(toDelete).once("value").then(oldSnap => {
            const deletes = {};
            oldSnap.forEach(child => { deletes[child.key] = null; });
            rtdb.ref(`/devices/${deviceId}/feedLog`).update(deletes).catch(() => {});
          });
        }
      });
    }).catch(() => {});
    await saveParser(deviceId, parser);

    const batch = db.batch();
    for (const reading of historyWrites) {
      const ref = db.collection("devices").doc(deviceId).collection("readings").doc();
      batch.set(ref, reading);
    }

    // Save completed races to Firestore races subcollection
    const completed = parser.getCompleted() || [];
    // Read status ONCE outside the loop
    const statusSnap = await rtdb.ref(`/devices/${deviceId}/status`).once("value");
    const st = statusSnap.val() || {};
    const owner = (st.promoter || st.trackName || "local").toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const nameKey = (st.raceName || localDate).toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const eventId = `${owner}-${nameKey}-${localDate}`.replace(/-+/g, "-").slice(0, 120);

    const raceBatch = db.batch();
    let raceWriteCount = 0;
    for (const race of completed) {
      if (!race || !race.complete) continue;
      const ts = race.timestamp || "";
      const cat = race.category || "";
      const rnd = race.round || "";
      const lNum = (race.left && race.left.car_num) || "";
      const rNum = (race.right && race.right.car_num) || "";
      const l3Num = (race.lane3 && race.lane3.car_num) || "";
      const l4Num = (race.lane4 && race.lane4.car_num) || "";
      const raceKeyParts = [ts, cat, rnd, lNum, rNum];
      if (l3Num || l4Num) { raceKeyParts.push(l3Num, l4Num); }
      const raceKey = raceKeyParts.join("_").replace(/[\/\.#\$\[\]|:]/g, "_");

      const raceDoc = {
        race_id: raceKey,
        event_id: eventId,
        event_name: st.raceName || localDate,
        track_name: st.trackName || "",
        promoter: st.promoter || "",
        timestamp: ts,
        timestamp_ms: Date.now(),
        created_date: race.created_date || localDate,
        device_id: deviceId,
        category: cat,
        category_num: race.category_num || "",
        round: rnd,
        winner: race.winner || "",
        margin: race.margin || "",
        left_name: (race.left && race.left.name) || "",
        left_car_num: lNum,
        left_class: (race.left && race.left.class_name) || "",
        left_dial: (race.left && race.left.dial_in) || "",
        left_rt: (race.left && race.left.rt) || "",
        left_sixty: (race.left && race.left.sixty) || "",
        left_three30: (race.left && race.left.three30) || "",
        left_six60: (race.left && race.left.six60) || "",
        left_six60_speed: (race.left && race.left.six60_speed) || "",
        left_thousand: (race.left && race.left.thousand) || "",
        left_thousand_speed: (race.left && race.left.thousand_speed) || "",
        left_et: (race.left && race.left.et) || "",
        left_speed: (race.left && race.left.speed) || "",
        left_mov: (race.left && race.left.mov) || "",
        right_name: (race.right && race.right.name) || "",
        right_car_num: rNum,
        right_class: (race.right && race.right.class_name) || "",
        right_dial: (race.right && race.right.dial_in) || "",
        right_rt: (race.right && race.right.rt) || "",
        right_sixty: (race.right && race.right.sixty) || "",
        right_three30: (race.right && race.right.three30) || "",
        right_six60: (race.right && race.right.six60) || "",
        right_six60_speed: (race.right && race.right.six60_speed) || "",
        right_thousand: (race.right && race.right.thousand) || "",
        right_thousand_speed: (race.right && race.right.thousand_speed) || "",
        right_et: (race.right && race.right.et) || "",
        right_speed: (race.right && race.right.speed) || "",
        right_mov: (race.right && race.right.mov) || "",
        left_member_num: (race.left && race.left.member_num) || "",
        right_member_num: (race.right && race.right.member_num) || "",
        // Lane 3 fields
        lane3_name: (race.lane3 && race.lane3.name) || "",
        lane3_car_num: (race.lane3 && race.lane3.car_num) || "",
        lane3_class: (race.lane3 && race.lane3.class_name) || "",
        lane3_dial: (race.lane3 && race.lane3.dial_in) || "",
        lane3_rt: (race.lane3 && race.lane3.rt) || "",
        lane3_sixty: (race.lane3 && race.lane3.sixty) || "",
        lane3_three30: (race.lane3 && race.lane3.three30) || "",
        lane3_six60: (race.lane3 && race.lane3.six60) || "",
        lane3_six60_speed: (race.lane3 && race.lane3.six60_speed) || "",
        lane3_thousand: (race.lane3 && race.lane3.thousand) || "",
        lane3_thousand_speed: (race.lane3 && race.lane3.thousand_speed) || "",
        lane3_et: (race.lane3 && race.lane3.et) || "",
        lane3_speed: (race.lane3 && race.lane3.speed) || "",
        lane3_mov: (race.lane3 && race.lane3.mov) || "",
        lane3_member_num: (race.lane3 && race.lane3.member_num) || "",
        // Lane 4 fields
        lane4_name: (race.lane4 && race.lane4.name) || "",
        lane4_car_num: (race.lane4 && race.lane4.car_num) || "",
        lane4_class: (race.lane4 && race.lane4.class_name) || "",
        lane4_dial: (race.lane4 && race.lane4.dial_in) || "",
        lane4_rt: (race.lane4 && race.lane4.rt) || "",
        lane4_sixty: (race.lane4 && race.lane4.sixty) || "",
        lane4_three30: (race.lane4 && race.lane4.three30) || "",
        lane4_six60: (race.lane4 && race.lane4.six60) || "",
        lane4_six60_speed: (race.lane4 && race.lane4.six60_speed) || "",
        lane4_thousand: (race.lane4 && race.lane4.thousand) || "",
        lane4_thousand_speed: (race.lane4 && race.lane4.thousand_speed) || "",
        lane4_et: (race.lane4 && race.lane4.et) || "",
        lane4_speed: (race.lane4 && race.lane4.speed) || "",
        lane4_mov: (race.lane4 && race.lane4.mov) || "",
        lane4_member_num: (race.lane4 && race.lane4.member_num) || "",
        // Placement results per lane (for 4-wide: W, R, 3, 4)
        left_result: (race.placements && race.placements.left) || "",
        right_result: (race.placements && race.placements.right) || "",
        lane3_result: (race.placements && race.placements.lane3) || "",
        lane4_result: (race.placements && race.placements.lane4) || "",
        synced_at: new Date().toISOString(),
      };

      const raceRef = db.collection("devices").doc(deviceId).collection("races").doc(raceKey);
      raceBatch.set(raceRef, raceDoc, { merge: true });
      raceWriteCount++;
    }

    await batch.commit();
    if (raceWriteCount > 0) await raceBatch.commit();

    // Event race count is computed dynamically by the /events endpoint

    res.json({
      ok: true,
      deviceId,
      lineCount: lines.length,
      channels: Object.keys(latest),
      racesStored: completed.filter(r => r && r.complete).length,
    });
    setLocalTime(null);
  } catch (err) {
    setLocalTime(null);
    res.status(500).json({ error: err.message });
  }
});

// Browse all registered tracks
app.get("/tracks", async (req, res) => {
  try {
    const searchQuery = (req.query.q || "").toLowerCase();
    const onlyOnline = req.query.online === "true";

    const snapshot = await db.collection("tracks").orderBy("name").get();
    const trackDocsByDeviceId = new Map();

    let tracks = [];
    snapshot.forEach((doc) => {
      const track = { id: doc.id, ...doc.data() };
      tracks.push(track);
      trackDocsByDeviceId.set(track.deviceId || doc.id, track);
    });

    if (onlyOnline) {
      const devicesRef = rtdb.ref("/devices");
      const liveSnapshot = await devicesRef.once("value");
      const liveDevices = liveSnapshot.val() || {};

      tracks = [];
      for (const [deviceId, device] of Object.entries(liveDevices)) {
        if (!device?.status?.online) {
          continue;
        }

        const existing = trackDocsByDeviceId.get(deviceId) || {};
        const displayName = getPreferredTrackLabel(
          device.status,
          existing.name || existing.trackName || deviceId
        );
        const trackLocation = device.status.trackLocation || existing.location || "";

        tracks.push({
          id: existing.id || deviceId,
          ...existing,
          deviceId,
          name: displayName,
          trackName: displayName,
          promoter: device.status.promoter || existing.promoter || "",
          raceName: device.status.raceName || existing.raceName || "",
          location: trackLocation,
          trackLocation,
          lastSeen: device.status.lastSeen || existing.lastSeen || null,
          online: true,
        });
      }
    }

    if (searchQuery) {
      tracks = tracks.filter((t) =>
        (t.name || "").toLowerCase().includes(searchQuery) ||
        (t.promoter || "").toLowerCase().includes(searchQuery) ||
        (t.location || "").toLowerCase().includes(searchQuery)
      );
    }

    if (onlyOnline) {
      tracks = tracks.filter((t) => t.online === true);
    }

    res.json({ tracks, count: tracks.length });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Single track details with live status
app.get("/tracks/:trackId", async (req, res, next) => {
  if (req.params.trackId === "live") {
    return next();
  }
  try {
    const doc = await db.collection("tracks").doc(req.params.trackId).get();
    if (!doc.exists) {
      return res.status(404).json({ error: "Track not found" });
    }
    const trackData = doc.data();

    let status = { online: false };
    try {
      const statusRef = rtdb.ref(`/devices/${trackData.deviceId}/status`);
      const statusSnap = await statusRef.once("value");
      status = statusSnap.val() || { online: false };
    } catch {}

    res.json({ id: doc.id, ...trackData, status });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Currently online tracks only
app.get("/tracks/live", async (req, res) => {
  try {
    const devicesRef = rtdb.ref("/devices");
    const snapshot = await devicesRef.once("value");
    const val = snapshot.val() || {};

    const liveTracks = [];
    const now = Date.now();
    const STALE_MS = 5 * 60 * 1000; // 5 minutes — if not heard from in 5 min, consider offline
    for (const [deviceId, device] of Object.entries(val)) {
      if (!device.status) continue;
      const lastSeen = device.status.lastSeen || 0;
      const isRecent = (now - lastSeen) < STALE_MS;
      if (device.status.online && isRecent) {
        const displayName = getPreferredTrackLabel(device.status, deviceId);
        liveTracks.push({
          deviceId,
          name: displayName,
          trackName: displayName,
          promoter: device.status.promoter || "",
          raceName: device.status.raceName || "",
          trackLocation: device.status.trackLocation || "",
          lastSeen,
          online: true,
        });
      }
    }

    liveTracks.sort((a, b) => (a.trackName || "").localeCompare(b.trackName || ""));
    res.json({ tracks: liveTracks, count: liveTracks.length });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/pairs/:deviceId", async (req, res) => {
  try {
    const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 100, 1), 500);
    const deviceId = req.params.deviceId;

    const [statusSnap, completedSnap] = await Promise.all([
      rtdb.ref(`/devices/${deviceId}/status`).once("value"),
      rtdb.ref(`/devices/${deviceId}/raceState/completed`).once("value"),
    ]);

    const status = statusSnap.val() || {};
    const completed = completedSnap.val() || [];
    const races = Array.isArray(completed)
      ? completed.filter(Boolean)
      : Object.values(completed).filter(Boolean);

    const ordered = races
      .slice(-limit)
      .reverse()
      .map((race, index) => ({
        id: `${deviceId}-${index}`,
        timestamp: race.timestamp || "",
        category: race.category || "",
        round: race.round || "",
        winner: race.winner || "",
        margin: race.margin || "",
        left: {
          name: race.left?.name || "",
          car_num: race.left?.car_num || "",
          class_name: race.left?.class_name || "",
          dial_in: race.left?.dial_in || "",
          rt: race.left?.rt || "",
          et: race.left?.et || "",
          speed: race.left?.speed || "",
          mov: race.left?.mov || "",
        },
        right: {
          name: race.right?.name || "",
          car_num: race.right?.car_num || "",
          class_name: race.right?.class_name || "",
          dial_in: race.right?.dial_in || "",
          rt: race.right?.rt || "",
          et: race.right?.et || "",
          speed: race.right?.speed || "",
          mov: race.right?.mov || "",
        },
      }));

    res.json({
      deviceId,
      name: getPreferredTrackLabel(status, deviceId),
      trackName: status.trackName || "",
      promoter: status.promoter || "",
      raceName: status.raceName || "",
      trackLocation: status.trackLocation || "",
      runs: ordered,
      count: ordered.length,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

function isTimingRaceState(value) {
  return Boolean(
    value &&
    typeof value === "object" &&
    (value.left || value.right) &&
    Object.prototype.hasOwnProperty.call(value, "category") &&
    Object.prototype.hasOwnProperty.call(value, "round")
  );
}

function isRaceCompleteState(race) {
  return Boolean(
    race &&
    (
      race.complete ||
      race.winner ||
      race.left?.mov ||
      race.right?.mov ||
      (race.left?.et && race.right?.et)
    )
  );
}

function parseNumber(value) {
  if (value === null || value === undefined) return null;
  const trimmed = String(value).trim();
  if (!trimmed) return null;
  const normalized = trimmed.replace(/,/g, "");
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeWinnerSide(winner) {
  const upper = String(winner || "").trim().toUpperCase();
  if (upper === "LEFT" || upper === "L") return "L";
  if (upper === "RIGHT" || upper === "R") return "R";
  return null;
}

function hasLaneData(lane) {
  return Boolean(
    lane &&
    (
      lane.name ||
      lane.car_num ||
      lane.rt ||
      lane.et ||
      lane.speed ||
      lane.dial_in
    )
  );
}

function formatTimestamp12Hour(timestampMs) {
  if (!timestampMs) return "";
  const date = new Date(timestampMs);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const year = date.getFullYear();
  let hours = date.getHours();
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  if (hours === 0) hours = 12;
  if (hours > 12) hours -= 12;
  return `${month}/${day}/${year} ${hours}:${minutes}:${seconds}`;
}

function formatIsoDate(timestampMs) {
  if (!timestampMs) return "";
  const date = new Date(timestampMs);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function buildRaceDedupKey(race) {
  return [
    race.timestamp || "",
    race.category || "",
    race.round || "",
    race.left?.car_num || race.left?.name || "",
    race.right?.car_num || race.right?.name || "",
  ].join("|");
}

function buildRunRow(race, side, laneKey, laneCode, timestampMs, meta) {
  const winnerSide = normalizeWinnerSide(race.winner);
  const pairedLane = laneCode === "L" ? race.right : race.left;
  const eventName = meta.raceName || meta.trackName || meta.promoter || meta.deviceId;
  const timestamp = formatTimestamp12Hour(timestampMs);
  const dateIso = formatIsoDate(timestampMs);

  return {
    id: `${meta.deviceId}-${meta.raceId}-${laneCode}`,
    pair_id: meta.raceId,
    timestamp,
    timestamp_ms: timestampMs,
    round: race.round || null,
    qual_pos: parseNumber(side.qual),
    car_number: side.car_num || null,
    name: side.name || null,
    class_index: side.class_name || null,
    rt: parseNumber(side.rt),
    ft60: parseNumber(side.sixty),
    ft330: parseNumber(side.three30),
    ft660: parseNumber(side.six60),
    mph_660: parseNumber(side.six60_speed),
    ft1000: parseNumber(side.thousand),
    mph_1000: parseNumber(side.thousand_speed),
    ft1320: parseNumber(side.et),
    mph_1320: parseNumber(side.speed),
    mov: parseNumber(side.mov || race.margin),
    is_winner: winnerSide === laneCode ? 1 : 0,
    is_dq: 0,
    place: null,
    category: race.category || null,
    lane: laneCode,
    dial_in: parseNumber(side.dial_in),
    event_code: meta.deviceId,
    event_name: eventName,
    event_type: "raw",
    season: dateIso ? dateIso.slice(0, 4) : null,
    start_date: dateIso || null,
    promoter: meta.promoter || null,
    track_name: meta.trackName || null,
    race_name: meta.raceName || null,
    track_location: meta.trackLocation || null,
    opponents: hasLaneData(pairedLane)
      ? [{
          name: pairedLane.name || null,
          car_number: pairedLane.car_num || null,
          rt: parseNumber(pairedLane.rt),
          ft60: parseNumber(pairedLane.sixty),
          ft330: parseNumber(pairedLane.three30),
          ft660: parseNumber(pairedLane.six60),
          mph_660: parseNumber(pairedLane.six60_speed),
          ft1000: parseNumber(pairedLane.thousand),
          mph_1000: parseNumber(pairedLane.thousand_speed),
          ft1320: parseNumber(pairedLane.et),
          mph_1320: parseNumber(pairedLane.speed),
          mov: parseNumber(pairedLane.mov || race.margin),
          is_winner: winnerSide === (laneCode === "L" ? "R" : "L") ? 1 : 0,
          is_dq: 0,
          lane: laneCode === "L" ? "R" : "L",
          dial_in: parseNumber(pairedLane.dial_in),
        }]
      : [],
  };
}

async function loadDeviceMeta(deviceId) {
  const [trackDoc, statusSnap] = await Promise.all([
    db.collection("tracks").doc(deviceId).get(),
    rtdb.ref(`/devices/${deviceId}/status`).once("value"),
  ]);

  const track = trackDoc.exists ? trackDoc.data() : {};
  const status = statusSnap.val() || {};

  return {
    deviceId,
    trackName: status.trackName || track.trackName || track.name || "",
    promoter: status.promoter || track.promoter || "",
    raceName: status.raceName || track.raceName || "",
    trackLocation: status.trackLocation || track.trackLocation || track.location || "",
    online: Boolean(status.online),
    lastSeen: status.lastSeen || track.lastSeen || null,
  };
}

async function loadDeviceRawRuns(deviceId, options = {}) {
  const limit = Math.min(Math.max(parseInt(options.limit, 10) || 5000, 1), 10000);
  const snapshot = await db
    .collection("devices")
    .doc(deviceId)
    .collection("readings")
    .orderBy("timestamp", "desc")
    .limit(limit)
    .get();

  const meta = await loadDeviceMeta(deviceId);
  const byRace = new Map();

  snapshot.docs
    .map((doc) => doc.data())
    .reverse()
    .forEach((reading) => {
      if (!isTimingRaceState(reading.parsed) || !isRaceCompleteState(reading.parsed)) {
        return;
      }

      const race = deepCopyRace(reading.parsed);
      const timestampMs =
        reading.timestampMs ||
        (reading.timestamp && typeof reading.timestamp.toMillis === "function"
          ? reading.timestamp.toMillis()
          : null);
      const dedupKey = buildRaceDedupKey(race);
      const existing = byRace.get(dedupKey);

      if (!existing || (timestampMs && timestampMs > existing.timestampMs)) {
        byRace.set(dedupKey, { race, timestampMs: timestampMs || Date.now() });
      }
    });

  const races = Array.from(byRace.values()).sort((a, b) => a.timestampMs - b.timestampMs);
  const runs = [];

  races.forEach((entry, index) => {
    const raceId = `${deviceId}-${entry.timestampMs}-${index}`;
    if (hasLaneData(entry.race.left)) {
      runs.push(buildRunRow(entry.race, entry.race.left, "left", "L", entry.timestampMs, { ...meta, raceId }));
    }
    if (hasLaneData(entry.race.right)) {
      runs.push(buildRunRow(entry.race, entry.race.right, "right", "R", entry.timestampMs, { ...meta, raceId }));
    }
  });

  return { runs, meta };
}

function getRawFilters(runs) {
  const categories = new Set();
  const rounds = new Set();
  const classes = new Set();

  runs.forEach((run) => {
    if (run.category) categories.add(run.category);
    if (run.round) rounds.add(run.round);
    if (run.class_index) classes.add(run.class_index);
  });

  return {
    categories: Array.from(categories).sort(),
    rounds: Array.from(rounds).sort(),
    classes: Array.from(classes).sort(),
  };
}

function parseRawTimestamp(ts) {
  if (!ts) return null;
  const [datePart, timePart] = String(ts).split(" ");
  if (!datePart || !timePart) return null;
  const [month, day, year] = datePart.split("/").map((part) => parseInt(part, 10));
  let [hours, minutes, seconds] = timePart.split(":").map((part) => parseInt(part, 10));
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day) || !Number.isFinite(hours)) {
    return null;
  }
  if (hours >= 1 && hours <= 6) hours += 12;
  return new Date(year, month - 1, day, hours, minutes || 0, seconds || 0);
}

function sortRuns(runs, sortBy = "timestamp", sortDir = "DESC") {
  const direction = String(sortDir).toUpperCase() === "ASC" ? 1 : -1;
  return [...runs].sort((a, b) => {
    if (sortBy === "timestamp") {
      return ((a.timestamp_ms || 0) - (b.timestamp_ms || 0)) * direction;
    }
    const left = a[sortBy];
    const right = b[sortBy];
    if (left == null && right == null) return 0;
    if (left == null) return 1;
    if (right == null) return -1;
    if (typeof left === "number" && typeof right === "number") {
      return (left - right) * direction;
    }
    return String(left).localeCompare(String(right)) * direction;
  });
}

function detectRawNoShows(elimRuns, category) {
  const rounds = [...new Set(elimRuns.map((r) => r.round).filter(Boolean))].sort();
  const noShows = [];

  for (let index = 0; index < rounds.length - 1; index++) {
    const currentRound = rounds[index];
    const nextRound = rounds[index + 1];
    const currentRuns = elimRuns.filter((run) => run.round === currentRound);
    const nextRuns = elimRuns.filter((run) => run.round === nextRound);
    if (nextRuns.length === 0) continue;

    const nextCars = new Set(nextRuns.map((run) => String(run.car_number || "").trim()).filter(Boolean));
    currentRuns
      .filter((run) => run.is_winner === 1 && run.car_number)
      .forEach((winner) => {
        const carNumber = String(winner.car_number || "").trim();
        if (!nextCars.has(carNumber)) {
          noShows.push({
            name: winner.name || "",
            car_number: carNumber,
            category,
            wonRound: currentRound,
            missedRound: nextRound,
          });
        }
      });
  }

  return noShows;
}

function getRawDidNotRace(runs) {
  const elimCarsByCategory = new Map();
  const qualifyingByCategory = new Map();

  runs.forEach((run) => {
    if (!run.car_number || !run.category || !run.round) return;
    const carNumber = String(run.car_number).trim();
    if (!carNumber) return;

    if (run.round.startsWith("E")) {
      const set = elimCarsByCategory.get(run.category) || new Set();
      set.add(carNumber);
      elimCarsByCategory.set(run.category, set);
      return;
    }

    if (run.round.startsWith("Q") || run.round.startsWith("T")) {
      const map = qualifyingByCategory.get(run.category) || new Map();
      const existing = map.get(carNumber);
      if (!existing || String(run.round).localeCompare(existing.lastRound) > 0) {
        map.set(carNumber, {
          name: run.name || "",
          car_number: carNumber,
          category: run.category,
          lastRound: run.round,
        });
      }
      qualifyingByCategory.set(run.category, map);
    }
  });

  const results = [];
  qualifyingByCategory.forEach((qualMap, category) => {
    const elimCars = elimCarsByCategory.get(category) || new Set();
    if (elimCars.size === 0) return;
    qualMap.forEach((entry, carNumber) => {
      if (!elimCars.has(carNumber)) {
        results.push(entry);
      }
    });
  });

  return results.sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
}

function getRawSchedule(runs) {
  const grouped = new Map();

  runs.forEach((run) => {
    if (!run.category || !run.round || !run.timestamp) return;
    const key = `${run.category}|||${run.round}`;
    const entry = grouped.get(key) || { timestamps: new Map() };
    const bucket = entry.timestamps.get(run.timestamp) || [];
    bucket.push(run);
    entry.timestamps.set(run.timestamp, bucket);
    grouped.set(key, entry);
  });

  const sessions = [];
  const maxGapMin = 10;

  grouped.forEach((entry, key) => {
    const [category, round] = key.split("|||");
    const sortedTimestamps = Array.from(entry.timestamps.keys()).sort((a, b) => {
      const left = parseRawTimestamp(a);
      const right = parseRawTimestamp(b);
      return (left?.getTime() || 0) - (right?.getTime() || 0);
    });

    let segment = [];
    for (const timestamp of sortedTimestamps) {
      if (segment.length === 0) {
        segment.push(timestamp);
        continue;
      }

      const previous = parseRawTimestamp(segment[segment.length - 1]);
      const current = parseRawTimestamp(timestamp);
      const gapMin = previous && current ? (current.getTime() - previous.getTime()) / 60000 : 0;
      if (gapMin >= maxGapMin) {
        sessions.push({ category, round, timestamps: [...segment], entry });
        segment = [timestamp];
      } else {
        segment.push(timestamp);
      }
    }

    if (segment.length > 0) {
      sessions.push({ category, round, timestamps: [...segment], entry });
    }
  });

  return sessions
    .map((session) => {
      const firstTimestamp = session.timestamps[0];
      const lastTimestamp = session.timestamps[session.timestamps.length - 1];
      const firstDate = parseRawTimestamp(firstTimestamp);
      const lastDate = parseRawTimestamp(lastTimestamp);
      const totalRuns = session.timestamps.reduce((sum, ts) => sum + (session.entry.timestamps.get(ts)?.length || 0), 0);
      const durationMinutes = firstDate && lastDate
        ? Math.max(0, Math.round((lastDate.getTime() - firstDate.getTime()) / 60000))
        : 0;

      return {
        category: session.category,
        round: session.round,
        firstTimestamp,
        lastTimestamp,
        totalRuns,
        pairCount: session.timestamps.length,
        durationMinutes,
      };
    })
    .sort((a, b) => {
      const left = parseRawTimestamp(a.firstTimestamp);
      const right = parseRawTimestamp(b.firstTimestamp);
      return (left?.getTime() || 0) - (right?.getTime() || 0);
    });
}

async function listRawDevices() {
  const [trackSnapshot, devicesSnapshot] = await Promise.all([
    db.collection("tracks").orderBy("name").get(),
    rtdb.ref("/devices").once("value"),
  ]);

  const liveDevices = devicesSnapshot.val() || {};
  const merged = new Map();

  trackSnapshot.forEach((doc) => {
    const data = doc.data() || {};
    merged.set(doc.id, {
      deviceId: data.deviceId || doc.id,
      id: doc.id,
      name: data.name || data.trackName || doc.id,
      trackName: data.trackName || data.name || "",
      promoter: data.promoter || "",
      raceName: data.raceName || "",
      trackLocation: data.trackLocation || data.location || "",
      online: false,
      lastSeen: data.lastSeen || null,
    });
  });

  Object.entries(liveDevices).forEach(([deviceId, device]) => {
    const status = device?.status || {};
    const existing = merged.get(deviceId) || { deviceId, id: deviceId };
    merged.set(deviceId, {
      ...existing,
      deviceId,
      id: existing.id || deviceId,
      name: getPreferredTrackLabel(status, existing.name || deviceId),
      trackName: status.trackName || existing.trackName || existing.name || "",
      promoter: status.promoter || existing.promoter || "",
      raceName: status.raceName || existing.raceName || "",
      trackLocation: status.trackLocation || existing.trackLocation || "",
      online: Boolean(status.online),
      lastSeen: status.lastSeen || existing.lastSeen || null,
    });
  });

  return Array.from(merged.values()).sort((a, b) => (a.name || "").localeCompare(b.name || ""));
}

app.get("/raw/devices", async (req, res) => {
  try {
    const search = String(req.query.q || "").trim().toLowerCase();
    let devices = await listRawDevices();

    if (search) {
      devices = devices.filter((device) =>
        [device.name, device.trackName, device.promoter, device.raceName, device.trackLocation]
          .filter(Boolean)
          .some((field) => String(field).toLowerCase().includes(search))
      );
    }

    res.json({ devices, count: devices.length });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/raw/runs", async (req, res) => {
  try {
    const deviceId = String(req.query.deviceId || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    const { runs, meta } = await loadDeviceRawRuns(deviceId);
    let filtered = runs;

    if (req.query.category) filtered = filtered.filter((run) => run.category === req.query.category);
    if (req.query.round) filtered = filtered.filter((run) => run.round === req.query.round);
    if (req.query.class_index) filtered = filtered.filter((run) => run.class_index === req.query.class_index);
    if (req.query.name) {
      const search = String(req.query.name).trim().toLowerCase();
      filtered = filtered.filter((run) => String(run.name || "").toLowerCase().includes(search));
    }

    const filters = getRawFilters(runs);
    const total = filtered.length;
    const sorted = sortRuns(filtered, req.query.sort_by || "timestamp", req.query.sort_dir || "DESC");
    const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 100, 1), 1000);
    const offset = Math.max(parseInt(req.query.offset, 10) || 0, 0);

    res.json({
      runs: sorted.slice(offset, offset + limit),
      total,
      filters,
      device: meta,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/raw/racers", async (req, res) => {
  try {
    const deviceId = String(req.query.deviceId || "").trim();
    const search = String(req.query.search || "").trim().toLowerCase();
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    const { runs } = await loadDeviceRawRuns(deviceId);
    const seen = new Map();
    runs.forEach((run) => {
      if (!run.name) return;
      if (!search) return;
      if (
        String(run.name).toLowerCase().includes(search) ||
        String(run.car_number || "").toLowerCase().includes(search)
      ) {
        if (!seen.has(run.name)) {
          seen.set(run.name, { name: run.name, car_number: run.car_number || "" });
        }
      }
    });

    res.json({ racers: Array.from(seen.values()).slice(0, 50) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/raw/racer", async (req, res) => {
  try {
    const deviceId = String(req.query.deviceId || "").trim();
    const name = String(req.query.name || "").trim();
    if (!deviceId || !name) {
      return res.status(400).json({ error: "deviceId and name are required" });
    }

    const { runs, meta } = await loadDeviceRawRuns(deviceId);
    const racerRuns = runs
      .filter((run) => run.name === name)
      .sort((a, b) => (b.timestamp_ms || 0) - (a.timestamp_ms || 0));

    res.json({ name, runs: racerRuns, totalRuns: racerRuns.length, device: meta });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/raw/latest-pair", async (req, res) => {
  try {
    const deviceId = String(req.query.deviceId || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    const { runs, meta } = await loadDeviceRawRuns(deviceId);
    const sorted = sortRuns(runs, "timestamp", "DESC");
    const latest = sorted[0];
    const pair = latest ? sorted.filter((run) => run.pair_id === latest.pair_id) : [];

    res.json({ pair, device: meta });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/raw/schedule", async (req, res) => {
  try {
    const deviceId = String(req.query.deviceId || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    const { runs, meta } = await loadDeviceRawRuns(deviceId);
    const schedule = getRawSchedule(runs);
    res.json({ schedule, device: meta });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/raw/noshows", async (req, res) => {
  try {
    const deviceId = String(req.query.deviceId || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    const { runs, meta } = await loadDeviceRawRuns(deviceId);
    const elimRuns = runs.filter((run) => String(run.round || "").startsWith("E"));
    const categories = [...new Set(elimRuns.map((run) => run.category).filter(Boolean))];

    const noShows = [];
    let activeCategory = null;
    let latestTimestampMs = 0;

    categories.forEach((category) => {
      const categoryRuns = elimRuns
        .filter((run) => run.category === category)
        .sort((a, b) => (a.timestamp_ms || 0) - (b.timestamp_ms || 0));

      noShows.push(...detectRawNoShows(categoryRuns, category));

      categoryRuns.forEach((run) => {
        if ((run.timestamp_ms || 0) > latestTimestampMs) {
          latestTimestampMs = run.timestamp_ms || 0;
          activeCategory = category;
        }
      });
    });

    res.json({
      noShows: noShows.sort((a, b) => a.category.localeCompare(b.category) || a.missedRound.localeCompare(b.missedRound)),
      activeCategory,
      device: meta,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/raw/didnotrace", async (req, res) => {
  try {
    const deviceId = String(req.query.deviceId || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    const { runs, meta } = await loadDeviceRawRuns(deviceId);
    res.json({ didNotRace: getRawDidNotRace(runs), device: meta });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ═════════════════════════════════════════════════════════════════════════════
//  API-KEY AUTHENTICATED ENDPOINTS (for web developers embedding on sites)
// ═════════════════════════════════════════════════════════════════════════════

// Live SSE stream for a track -- the primary endpoint for website embedding
// ?source=serial  - serial data only
// ?source=tcp     - TCP/JSON data only
// (no param)      - both channels
app.get("/stream/:deviceId", authenticateApiKey, async (req, res) => {
  const { deviceId } = req.params;
  const source = (req.query.source || "").toLowerCase();

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });

  const statusRef = rtdb.ref(`/devices/${deviceId}/status`);
  const statusSnap = await statusRef.once("value");
  const status = statusSnap.val() || {};

  res.write(`data: ${JSON.stringify({
    type: "connected",
    trackName: status.trackName || "",
    online: status.online || false,
    sources: source || "all",
  })}\n\n`);

  const listeners = [];

  function attachChannel(channel) {
    const ref = rtdb.ref(`/devices/${deviceId}/stream/${channel}`);
    const listener = ref.on("value", (snapshot) => {
      const val = snapshot.val();
      if (val) {
        res.write(`data: ${JSON.stringify({ type: "data", source: channel, ...val })}\n\n`);
      }
    });
    listeners.push({ ref, listener });
  }

  if (source === "serial" || !source) attachChannel("serial");
  if (source === "tcp" || !source) attachChannel("tcp");

  req.on("close", () => {
    listeners.forEach(({ ref, listener }) => ref.off("value", listener));
  });
});

// Latest data snapshot (polling alternative to SSE)
// ?source=serial  - serial data only
// ?source=tcp     - TCP/JSON data only
// (no param)      - both channels
app.get("/latest/:deviceId", authenticateApiKey, async (req, res) => {
  try {
    const source = (req.query.source || "").toLowerCase();
    const basePath = `/devices/${req.params.deviceId}/stream`;

    let data = {};
    if (source === "serial" || !source) {
      const snap = await rtdb.ref(`${basePath}/serial`).once("value");
      if (snap.val()) data.serial = snap.val();
    }
    if (source === "tcp" || !source) {
      const snap = await rtdb.ref(`${basePath}/tcp`).once("value");
      if (snap.val()) data.tcp = snap.val();
    }

    const statusRef = rtdb.ref(`/devices/${req.params.deviceId}/status`);
    const statusSnap = await statusRef.once("value");
    const status = statusSnap.val() || { online: false };

    res.json({
      data: source ? (data[source] || null) : data,
      status,
      deviceId: req.params.deviceId,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Historical data with pagination
app.get("/history/:deviceId", authenticateApiKey, async (req, res) => {
  try {
    const queryLimit = Math.min(parseInt(req.query.limit) || 50, 500);
    const after = req.query.after || null;

    let q = db
      .collection("devices")
      .doc(req.params.deviceId)
      .collection("readings")
      .orderBy("timestamp", "desc")
      .limit(queryLimit);

    if (after) {
      q = q.where("timestamp", ">", after);
    }

    const snapshot = await q.get();
    const readings = [];
    snapshot.forEach((doc) => {
      readings.push({ id: doc.id, ...doc.data() });
    });

    res.json({ readings, count: readings.length, limit: queryLimit });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ═════════════════════════════════════════════════════════════════════════════
//  API KEY MANAGEMENT (Firebase Auth required)
// ═════════════════════════════════════════════════════════════════════════════

app.post("/keys", authenticateFirebase, async (req, res) => {
  try {
    const label = req.body.label || "My API Key";
    const rawKey = `tt_${crypto.randomBytes(24).toString("hex")}`;
    const keyHash = crypto.createHash("sha256").update(rawKey).digest("hex");

    const docRef = await db.collection("api_keys").add({
      keyHash,
      keyPrefix: rawKey.substring(0, 8),
      label,
      owner: req.user.uid,
      disabled: false,
      requestCount: 0,
      createdAt: admin.firestore.FieldValue.serverTimestamp(),
    });

    res.status(201).json({
      id: docRef.id,
      apiKey: rawKey,
      label,
      message: "Save this key -- it won't be shown again.",
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/keys", authenticateFirebase, async (req, res) => {
  try {
    const snapshot = await db.collection("api_keys")
      .where("owner", "==", req.user.uid)
      .get();

    const keys = [];
    snapshot.forEach((doc) => {
      const d = doc.data();
      keys.push({
        id: doc.id,
        label: d.label,
        keyPrefix: d.keyPrefix,
        disabled: d.disabled,
        requestCount: d.requestCount,
        createdAt: d.createdAt,
        lastUsed: d.lastUsed,
      });
    });
    res.json({ keys });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete("/keys/:keyId", authenticateFirebase, async (req, res) => {
  try {
    const doc = await db.collection("api_keys").doc(req.params.keyId).get();
    if (!doc.exists || doc.data().owner !== req.user.uid) {
      return res.status(404).json({ error: "Key not found" });
    }
    await db.collection("api_keys").doc(req.params.keyId).delete();
    res.json({ message: "API key deleted" });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});


// ═════════════════════════════════════════════════════════════════════════════
//  DEVICE MANAGEMENT (Firebase Auth -- used by Pi devices and desktop app)
// ═════════════════════════════════════════════════════════════════════════════

app.get("/devices", authenticateFirebase, async (req, res) => {
  try {
    const snapshot = await db.collection("devices")
      .where("owner", "==", req.user.uid)
      .get();

    const devices = [];
    snapshot.forEach((doc) => {
      devices.push({ id: doc.id, ...doc.data() });
    });
    res.json({ devices });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/devices/:deviceId", authenticateFirebase, async (req, res) => {
  try {
    const doc = await db.collection("devices").doc(req.params.deviceId).get();
    if (!doc.exists) {
      return res.status(404).json({ error: "Device not found" });
    }
    res.json({ id: doc.id, ...doc.data() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/devices", authenticateFirebase, async (req, res) => {
  try {
    const { deviceId, name, trackName, trackLocation } = req.body;
    if (!deviceId) {
      return res.status(400).json({ error: "deviceId is required" });
    }

    await db.collection("devices").doc(deviceId).set({
      name: name || trackName || "Unnamed Device",
      trackName: trackName || "",
      trackLocation: trackLocation || "",
      owner: req.user.uid,
      authorized_users: [],
      createdAt: admin.firestore.FieldValue.serverTimestamp(),
    });

    if (trackName) {
      await db.collection("tracks").doc(deviceId).set({
        name: trackName,
        location: trackLocation || "",
        deviceId,
        online: false,
        registeredBy: req.user.uid,
        createdAt: admin.firestore.FieldValue.serverTimestamp(),
      });
    }

    res.status(201).json({ id: deviceId, message: "Device registered" });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/devices/:deviceId/config", authenticateFirebase, async (req, res) => {
  try {
    const { deviceId } = req.params;
    const doc = await db.collection("devices").doc(deviceId).get();
    if (!doc.exists) {
      return res.status(404).json({ error: "Device not found" });
    }
    if (doc.data().owner !== req.user.uid) {
      return res.status(403).json({ error: "Only device owner can update config" });
    }

    const allowedFields = ["name", "trackName", "trackLocation", "authorized_users"];
    const updates = {};
    for (const field of allowedFields) {
      if (req.body[field] !== undefined) {
        updates[field] = req.body[field];
      }
    }
    await db.collection("devices").doc(deviceId).update(updates);

    if (updates.trackName !== undefined) {
      await db.collection("tracks").doc(deviceId).set({
        name: updates.trackName,
        location: updates.trackLocation || doc.data().trackLocation || "",
        deviceId,
      }, { merge: true });
    }

    res.json({ message: "Config updated" });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/devices/:deviceId/status", authenticateAny, async (req, res) => {
  try {
    const statusRef = rtdb.ref(`/devices/${req.params.deviceId}/status`);
    const snapshot = await statusRef.once("value");
    res.json(snapshot.val() || { online: false });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ═════════════════════════════════════════════════════════════════════════════
//  SUPER ADMIN ENDPOINTS (mdawson@nhra.com only)
// ═════════════════════════════════════════════════════════════════════════════

app.get("/admin/tracks", authenticateAdmin, async (req, res) => {
  try {
    const snapshot = await db.collection("tracks").orderBy("name").get();
    const tracks = [];
    snapshot.forEach((doc) => tracks.push({ id: doc.id, ...doc.data() }));

    const devicesRef = rtdb.ref("/devices");
    const devSnap = await devicesRef.once("value");
    const liveDevices = devSnap.val() || {};

    tracks.forEach((t) => {
      const live = liveDevices[t.deviceId];
      t.online = live && live.status && live.status.online || false;
      t.lastSeen = live && live.status && live.status.lastSeen || null;
    });

    res.json({ tracks, count: tracks.length });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/admin/devices", authenticateAdmin, async (req, res) => {
  try {
    const snapshot = await db.collection("devices").get();
    const devices = [];
    snapshot.forEach((doc) => devices.push({ id: doc.id, ...doc.data() }));
    res.json({ devices, count: devices.length });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/admin/support/:deviceId", authenticateAdmin, async (req, res) => {
  try {
    const { deviceId } = req.params;
    const [statusSnap, supportSnap] = await Promise.all([
      rtdb.ref(`/devices/${deviceId}/status`).once("value"),
      rtdb.ref(`/devices/${deviceId}/support`).once("value"),
    ]);

    const status = statusSnap.val() || { online: false };
    const support = supportSnap.val() || {};
    const commands = Object.entries(support.commands || {})
      .map(([id, value]) => ({ id, ...(value || {}) }))
      .sort((a, b) => (b.requestedAt || 0) - (a.requestedAt || 0))
      .slice(0, 10);

    res.json({
      deviceId,
      status,
      remoteAccess: status.remoteAccess || support.meta?.tailscale || {},
      support: {
        meta: support.meta || {},
        capabilities: support.meta?.capabilities || status.supportCapabilities || [],
        commands,
      },
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/admin/support/:deviceId/commands", authenticateAdmin, async (req, res) => {
  try {
    const { deviceId } = req.params;
    const action = String(req.body.action || "").trim();
    if (!SUPPORT_ACTIONS.has(action)) {
      return res.status(400).json({ error: "Unsupported support action" });
    }

    const payload = {};
    if (action === "fetch_logs") {
      const lines = Math.min(Math.max(parseInt(req.body.lines, 10) || 120, 20), 300);
      payload.lines = lines;
    }

    const command = {
      action,
      payload,
      status: "pending",
      requestedAt: Date.now(),
      requestedBy: req.user.email || "",
      deviceId,
    };

    const commandRef = rtdb.ref(`/devices/${deviceId}/support/commands`).push();
    await commandRef.set(command);

    res.status(202).json({
      ok: true,
      commandId: commandRef.key,
      command,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/admin/stream/:deviceId", authenticateAdmin, async (req, res) => {
  const { deviceId } = req.params;
  const source = (req.query.source || "").toLowerCase();

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });

  const statusRef = rtdb.ref(`/devices/${deviceId}/status`);
  const statusSnap = await statusRef.once("value");
  const status = statusSnap.val() || {};

  res.write(`data: ${JSON.stringify({
    type: "connected",
    trackName: status.trackName || "",
    online: status.online || false,
  })}\n\n`);

  const listeners = [];

  function attachChannel(channel) {
    const ref = rtdb.ref(`/devices/${deviceId}/stream/${channel}`);
    const listener = ref.on("value", (snapshot) => {
      const val = snapshot.val();
      if (val) {
        res.write(`data: ${JSON.stringify({ type: "data", source: channel, ...val })}\n\n`);
      }
    });
    listeners.push({ ref, listener });
  }

  if (source === "serial" || !source) attachChannel("serial");
  if (source === "tcp" || !source) attachChannel("tcp");

  req.on("close", () => {
    listeners.forEach(({ ref, listener }) => ref.off("value", listener));
  });
});

app.get("/admin/latest/:deviceId", authenticateAdmin, async (req, res) => {
  try {
    const basePath = `/devices/${req.params.deviceId}/stream`;
    const serialSnap = await rtdb.ref(`${basePath}/serial`).once("value");
    const tcpSnap = await rtdb.ref(`${basePath}/tcp`).once("value");
    const statusSnap = await rtdb.ref(`/devices/${req.params.deviceId}/status`).once("value");

    res.json({
      serial: serialSnap.val() || null,
      tcp: tcpSnap.val() || null,
      status: statusSnap.val() || { online: false },
      deviceId: req.params.deviceId,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/admin/history/:deviceId", authenticateAdmin, async (req, res) => {
  try {
    const queryLimit = Math.min(parseInt(req.query.limit) || 100, 1000);
    const q = db
      .collection("devices")
      .doc(req.params.deviceId)
      .collection("readings")
      .orderBy("timestamp", "desc")
      .limit(queryLimit);

    const snapshot = await q.get();
    const readings = [];
    snapshot.forEach((doc) => readings.push({ id: doc.id, ...doc.data() }));

    res.json({ readings, count: readings.length });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── Events & Results Endpoints ─────────────────────────────────────────────

// Helper: count races for an event by date range
async function countEventRaces(deviceId, startDate, endDate) {
  if (!deviceId) return 0;
  try {
    let q = db.collection("devices").doc(deviceId).collection("races");
    if (startDate) q = q.where("created_date", ">=", startDate);
    if (endDate) q = q.where("created_date", "<=", endDate);
    const snap = await q.get();
    return snap.size;
  } catch {
    return 0;
  }
}

app.get("/events", async (req, res) => {
  try {
    const deviceId = req.query.device_id;
    let query = db.collection("events").orderBy("updated_at", "desc").limit(50);
    if (deviceId) {
      query = db.collection("events").where("device_id", "==", deviceId).orderBy("updated_at", "desc").limit(50);
    }
    const snapshot = await query.get();
    const events = [];
    snapshot.forEach((doc) => events.push({ id: doc.id, ...doc.data() }));

    // Dynamically compute race_count for each event
    await Promise.all(events.map(async (evt) => {
      evt.race_count = await countEventRaces(evt.device_id, evt.start_date, evt.end_date);
    }));

    res.json({ events });
  } catch (err) {
    // Fallback: if Firestore index not ready, try without ordering
    try {
      const deviceId = req.query.device_id;
      if (deviceId) {
        const snapshot = await db.collection("events").where("device_id", "==", deviceId).limit(50).get();
        const events = [];
        snapshot.forEach((doc) => events.push({ id: doc.id, ...doc.data() }));
        await Promise.all(events.map(async (evt) => {
          evt.race_count = await countEventRaces(evt.device_id, evt.start_date, evt.end_date);
        }));
        res.json({ events });
      } else {
        res.json({ events: [], note: "Provide device_id parameter" });
      }
    } catch (err2) {
      res.status(500).json({ error: err2.message });
    }
  }
});

app.get("/events/:eventId", async (req, res) => {
  try {
    const doc = await db.collection("events").doc(req.params.eventId).get();
    if (!doc.exists) return res.status(404).json({ error: "Event not found" });
    const eventData = { id: doc.id, ...doc.data() };
    // Compute race_count dynamically from device races by date range
    eventData.race_count = await countEventRaces(eventData.device_id, eventData.start_date, eventData.end_date);
    res.json(eventData);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/events/:eventId/results", async (req, res) => {
  try {
    const limit = Math.min(parseInt(req.query.limit) || 500, 2000);

    // Fetch event doc to get device_id, start_date, end_date
    const eventDoc = await db.collection("events").doc(req.params.eventId).get();
    if (!eventDoc.exists) {
      return res.status(404).json({ error: "Event not found" });
    }
    const evt = eventDoc.data();
    const deviceId = evt.device_id;
    if (!deviceId) {
      return res.json({ races: [], count: 0, note: "Event has no device_id" });
    }

    // If event has date range, query by created_date; otherwise fall back to event_id
    if (evt.start_date && evt.end_date) {
      let q = db.collection("devices").doc(deviceId).collection("races");
      q = q.where("created_date", ">=", evt.start_date);
      q = q.where("created_date", "<=", evt.end_date);
      q = q.orderBy("created_date").orderBy("timestamp_ms", "desc").limit(limit);

      const racesSnap = await q.get();
      const races = [];
      racesSnap.forEach((doc) => races.push({ id: doc.id, ...doc.data() }));
      races.sort((a, b) => (b.timestamp_ms || 0) - (a.timestamp_ms || 0));
      res.json({ races, count: races.length, event_name: evt.event_name || evt.name || "", start_date: evt.start_date, end_date: evt.end_date });
    } else {
      // Legacy: query by event_id for events without dates
      const racesSnap = await db.collectionGroup("races")
        .where("event_id", "==", req.params.eventId)
        .orderBy("timestamp_ms", "desc")
        .limit(limit)
        .get();
      const races = [];
      racesSnap.forEach((doc) => races.push({ id: doc.id, ...doc.data() }));
      res.json({ races, count: races.length, event_name: evt.event_name || evt.name || "" });
    }
  } catch (err) {
    // If index not ready, fall back to event_id query
    try {
      const limit = Math.min(parseInt(req.query.limit) || 500, 2000);
      const racesSnap = await db.collectionGroup("races")
        .where("event_id", "==", req.params.eventId)
        .orderBy("timestamp_ms", "desc")
        .limit(limit)
        .get();
      const races = [];
      racesSnap.forEach((doc) => races.push({ id: doc.id, ...doc.data() }));
      res.json({ races, count: races.length, note: "Used legacy event_id query: " + err.message });
    } catch (err2) {
      res.json({ races: [], count: 0, note: "Index may still be building: " + err2.message });
    }
  }
});

// ─── Runs endpoint (no auth required) ────────────────────────────────────────

app.get("/runs", async (req, res) => {
  try {
    const deviceId = String(req.query.device_id || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "device_id is required" });
    }
    const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 500, 1), 2000);
    const startDate = req.query.start_date || null;
    const endDate = req.query.end_date || null;

    let q = db.collection("devices").doc(deviceId).collection("races");
    if (startDate || endDate) {
      // Use created_date filtering
      if (startDate) q = q.where("created_date", ">=", startDate);
      if (endDate) q = q.where("created_date", "<=", endDate);
      q = q.orderBy("created_date").orderBy("timestamp_ms", "desc").limit(limit);
    } else {
      // No date filter — just get most recent by timestamp_ms
      q = q.orderBy("timestamp_ms", "desc").limit(limit);
    }

    const snapshot = await q.get();
    const races = [];
    snapshot.forEach((doc) => races.push({ id: doc.id, ...doc.data() }));
    races.sort((a, b) => (b.timestamp_ms || 0) - (a.timestamp_ms || 0));
    res.json({ races, count: races.length, total_count: snapshot.size });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── Event CRUD (Firebase Auth required) ─────────────────────────────────────

app.post("/events", async (req, res) => {
  try {
    const { name, device_id, start_date, end_date, track_name, promoter, status } = req.body;
    if (!name) {
      return res.status(400).json({ error: "name is required" });
    }

    const now = new Date().toISOString();
    const eventData = {
      name: name,
      event_name: name,
      device_id: device_id || "",
      start_date: start_date || "",
      end_date: end_date || "",
      track_name: track_name || "",
      promoter: promoter || "",
      status: status || "active",
      created_at: now,
      updated_at: now,
    };

    const docRef = await db.collection("events").add(eventData);
    res.status(201).json({ id: docRef.id, ...eventData });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put("/events/:eventId", async (req, res) => {
  try {
    const doc = await db.collection("events").doc(req.params.eventId).get();
    if (!doc.exists) {
      return res.status(404).json({ error: "Event not found" });
    }

    const allowedFields = ["event_name", "name", "device_id", "start_date", "end_date", "track_name", "promoter", "status"];
    const updates = {};
    for (const field of allowedFields) {
      if (req.body[field] !== undefined) {
        // Normalize "name" to "event_name" for consistency
        const key = field === "name" ? "event_name" : field;
        updates[key] = req.body[field];
      }
    }
    updates.updated_at = new Date().toISOString();

    await db.collection("events").doc(req.params.eventId).update(updates);
    res.json({ id: req.params.eventId, ...updates, message: "Event updated" });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete("/events/:eventId", authenticateFirebase, async (req, res) => {
  try {
    const doc = await db.collection("events").doc(req.params.eventId).get();
    if (!doc.exists) {
      return res.status(404).json({ error: "Event not found" });
    }
    await db.collection("events").doc(req.params.eventId).delete();
    res.json({ message: "Event deleted. Associated runs were not modified." });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Recount all event race_counts (admin utility)
app.post("/events/recount", async (req, res) => {
  try {
    const eventsSnap = await db.collection("events").get();
    let updated = 0;
    for (const doc of eventsSnap.docs) {
      const eventId = doc.id;
      const evt = doc.data();
      const deviceId = evt.device_id;
      if (!deviceId) continue;
      const raceCount = await countEventRaces(deviceId, evt.start_date, evt.end_date);
      await doc.ref.update({ race_count: raceCount });
      updated++;
    }
    res.json({ ok: true, eventsUpdated: updated });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── Admin: Batch update created_date on races ───────────────────────────────
app.post("/admin/update-dates", async (req, res) => {
  try {
    const deviceId = String(req.body.device_id || "").trim();
    const raceIds = req.body.race_ids || [];
    const newDate = String(req.body.new_date || "").trim();
    if (!deviceId || !raceIds.length || !newDate) {
      return res.status(400).json({ error: "device_id, race_ids[], and new_date required" });
    }
    const batch = db.batch();
    let count = 0;
    for (const id of raceIds) {
      const ref = db.collection("devices").doc(deviceId).collection("races").doc(id);
      batch.update(ref, { created_date: newDate });
      count++;
      if (count >= 450) break; // batch limit safety
    }
    await batch.commit();
    res.json({ ok: true, updated: count });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ─── Admin: Reset parser state ────────────────────────────────────────────────
app.post("/admin/reset-parser", async (req, res) => {
  const deviceId = String(req.body.device_id || "").trim();
  if (!deviceId) return res.status(400).json({ error: "device_id required" });
  await rtdb.ref(`/devices/${deviceId}/parserState`).set({ current: {}, completed: [] });
  res.json({ ok: true, message: "Parser state reset for " + deviceId });
});

// ─── Admin: Test ingest debug ─────────────────────────────────────────────────
app.post("/admin/test-ingest", async (req, res) => {
  try {
    const deviceId = String(req.body.device_id || "").trim();
    const parser = await loadParser(deviceId);
    const beforeCount = parser.getCompleted().length;
    const lines = String(req.body.data || "").split(/\r?\n/).filter(Boolean);
    for (const line of lines) {
      parser.feedLine(line);
    }
    const afterCount = parser.getCompleted().length;
    const last = parser.getCompleted().slice(-1)[0];
    res.json({
      ok: true,
      beforeCompleted: beforeCount,
      afterCompleted: afterCount,
      newRaces: afterCount - beforeCount,
      lastRace: last ? { ts: last.timestamp, cat: last.category, left: last.left?.name, complete: last.complete } : null,
      currentState: { cat: parser.current?.category, left_name: parser.current?.left?.name, complete: parser.current?.complete },
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ─── Admin: Backfill created_date on races ───────────────────────────────────

app.post("/admin/backfill-dates", authenticateFirebase, async (req, res) => {
  try {
    const deviceId = String(req.body.device_id || "").trim();
    if (!deviceId) {
      return res.status(400).json({ error: "device_id is required" });
    }

    // Query all races missing created_date
    const racesSnap = await db.collection("devices").doc(deviceId).collection("races").get();
    let updatedCount = 0;
    const batchSize = 500;
    let batch = db.batch();
    let batchCount = 0;

    for (const doc of racesSnap.docs) {
      const data = doc.data();
      if (data.created_date) continue; // already has it

      // Derive created_date from synced_at (ISO string) or timestamp_ms (epoch)
      let derivedDate = null;
      if (data.synced_at) {
        // synced_at is ISO string like "2026-03-25T12:34:56.789Z"
        derivedDate = String(data.synced_at).slice(0, 10);
      } else if (data.timestamp_ms) {
        derivedDate = new Date(data.timestamp_ms).toISOString().slice(0, 10);
      }

      if (!derivedDate) continue;

      batch.update(doc.ref, {
        created_date: derivedDate,
        device_id: deviceId,
      });
      updatedCount++;
      batchCount++;

      if (batchCount >= batchSize) {
        await batch.commit();
        batch = db.batch();
        batchCount = 0;
      }
    }

    if (batchCount > 0) {
      await batch.commit();
    }

    res.json({ ok: true, device_id: deviceId, updated: updatedCount, total: racesSnap.size });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── Remote Device Settings (via RTDB command queue) ─────────────────────────

// Get current settings for a device (from RTDB /devices/{deviceId}/config)
app.get("/devices/:deviceId/settings", async (req, res) => {
  try {
    const deviceId = req.params.deviceId;
    const snap = await rtdb.ref(`/devices/${deviceId}/config`).once("value");
    const config = snap.val();
    if (!config) {
      // Fall back to status data
      const statusSnap = await rtdb.ref(`/devices/${deviceId}/status`).once("value");
      const st = statusSnap.val() || {};
      return res.json({
        ok: true,
        source: "status",
        event: { track: st.trackName || "", promoter: st.promoter || "", race_base: st.raceName || "" },
        device: { port: st.serialPort || "", baud: st.baudRate || 9600, tcp_port: st.tcpPort || "", input_mode: "both", kiosk_autostart: true },
      });
    }
    res.json({ ok: true, source: "device", ...config });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Push settings to a device (written to RTDB config node, Pi polls every 5s)
app.post("/devices/:deviceId/settings", async (req, res) => {
  try {
    const deviceId = req.params.deviceId;
    const { event, device, password } = req.body;
    await rtdb.ref(`/devices/${deviceId}/config`).set({
      event: event || {},
      device: device || {},
      password: password || "",
      updatedAt: new Date().toISOString(),
    });
    res.json({ ok: true, message: "Settings written to config. Pi will apply within 5 seconds." });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Legacy device settings endpoints removed — using RTDB pendingSettings approach above

// ─── Schedule Plans ────────────────────────────────────────────────────────
// GET /schedule-plans?device_id=X          → all plans for device
// GET /schedule-plans/:date?device_id=X    → plan for specific date
// POST /schedule-plans                     → create/update plan
// DELETE /schedule-plans/:date?device_id=X → delete plan

app.get("/schedule-plans/:date", async (req, res) => {
  const deviceId = req.query.device_id;
  const date = req.params.date;
  if (!deviceId) return res.status(400).json({ error: "device_id required" });
  if (!date) return res.status(400).json({ error: "date required" });

  try {
    const doc = await db.collection("devices").doc(deviceId)
      .collection("schedule_plans").doc(date).get();
    if (doc.exists) {
      return res.json({ plan: doc.data() });
    }
    return res.json({ plan: null });
  } catch (err) {
    console.error("Schedule plan GET error:", err);
    return res.status(500).json({ error: "Failed to load plan" });
  }
});

app.get("/schedule-plans", async (req, res) => {
  const deviceId = req.query.device_id;
  if (!deviceId) return res.status(400).json({ error: "device_id required" });

  try {
    const snap = await db.collection("devices").doc(deviceId)
      .collection("schedule_plans").orderBy("date").get();
    const plans = [];
    snap.forEach((doc) => plans.push(doc.data()));
    return res.json({ plans });
  } catch (err) {
    console.error("Schedule plans list error:", err);
    return res.status(500).json({ error: "Failed to load plans" });
  }
});

app.post("/schedule-plans", async (req, res) => {
  try {
    const { device_id, date, startTime, delayMinutes, entries } = req.body;
    if (!device_id || !date) {
      return res.status(400).json({ error: "device_id and date required" });
    }

    await db.collection("devices").doc(device_id)
      .collection("schedule_plans").doc(date).set({
        date: date,
        startTime: startTime || "9:00 AM",
        delayMinutes: Math.max(0, Number(delayMinutes || 0)),
        entries: entries || [],
        updatedAt: new Date().toISOString(),
      }, { merge: true });

    return res.json({ ok: true });
  } catch (err) {
    console.error("Schedule plan POST error:", err);
    return res.status(500).json({ error: "Failed to save plan" });
  }
});

app.delete("/schedule-plans/:date", async (req, res) => {
  const deviceId = req.query.device_id;
  const date = req.params.date;
  if (!deviceId) return res.status(400).json({ error: "device_id required" });
  if (!date) return res.status(400).json({ error: "date required" });

  try {
    await db.collection("devices").doc(deviceId)
      .collection("schedule_plans").doc(date).delete();
    return res.json({ ok: true });
  } catch (err) {
    console.error("Schedule plan DELETE error:", err);
    return res.status(500).json({ error: "Failed to delete plan" });
  }
});

// ─── Race Classes (global, stored in Firestore) ─────────────────────────────

const DEFAULT_RACE_CLASSES = [
  {name:"TOP FUEL",code:"TF",perPairSec:225,isRacing:true,fieldSize:16},
  {name:"FUNNY CAR",code:"FC",perPairSec:225,isRacing:true,fieldSize:16},
  {name:"PRO STOCK",code:"PS",perPairSec:150,isRacing:true,fieldSize:16},
  {name:"PRO STOCK MOTORCYCLE",code:"PSM",perPairSec:135,isRacing:true,fieldSize:16},
  {name:"Pro ET",code:"PRO",perPairSec:55,isRacing:true},
  {name:"Super Pro",code:"SPRO",perPairSec:65,isRacing:true},
  {name:"Super Comp",code:"SC",perPairSec:65,isRacing:true,fieldSize:32},
  {name:"Super Gas",code:"SG",perPairSec:55,isRacing:true,fieldSize:32},
  {name:"Super Stock",code:"SS",perPairSec:55,isRacing:true,fieldSize:32},
  {name:"Super Street",code:"SST",perPairSec:55,isRacing:true,fieldSize:32},
  {name:"Stock Eliminator",code:"STK",perPairSec:55,isRacing:true,fieldSize:32},
  {name:"Sportsman",code:"SPTM",perPairSec:55,isRacing:true},
  {name:"Sportsman Motorcycle",code:"SMC",perPairSec:55,isRacing:true},
  {name:"Street Legal",code:"SL",perPairSec:55,isRacing:true},
  {name:"Snowmobile",code:"SM",perPairSec:55,isRacing:true},
  {name:"Heads Up",code:"HU",perPairSec:65,isRacing:true},
  {name:"Jr Dragster",code:"JR",perPairSec:60,isRacing:true},
  {name:"Jr Street",code:"JS",perPairSec:50,isRacing:true},
  {name:"Top Alcohol Dragster",code:"TAD",perPairSec:200,isRacing:true,fieldSize:16},
  {name:"Top Alcohol Funny Car",code:"TAFC",perPairSec:200,isRacing:true,fieldSize:16},
  {name:"Pro Mod",code:"PM",perPairSec:150,isRacing:true,fieldSize:16},
  {name:"Top Dragster",code:"TD",perPairSec:105,isRacing:true,fieldSize:32},
  {name:"Top Sportsman",code:"TS",perPairSec:135,isRacing:true,fieldSize:32},
  {name:"Competition Eliminator",code:"COMP",perPairSec:125,isRacing:true,fieldSize:32},
  {name:"Factory Stock Showdown",code:"FSS",perPairSec:80,isRacing:true,fieldSize:16},
  {name:"Mountain Motor Pro Stock",code:"MMPS",perPairSec:150,isRacing:true,fieldSize:16},
  {name:"Hemi Challenge",code:"HC",perPairSec:65,isRacing:true},
  {name:"Top Fuel Motorcycle",code:"TFM",perPairSec:150,isRacing:true,fieldSize:16},
  {name:"Sponsor Race",code:"SR",perPairSec:75,isRacing:true},
  {name:"Drag & Drive",code:"DND",perPairSec:70,isRacing:true},
  {name:"Sportsman Finals",code:"SF",perPairSec:150,isRacing:true},
  {name:"Sportsman Semi Finals",code:"SSF",perPairSec:120,isRacing:true},
  {name:"Nostalgia Exhibition",code:"NS",perPairSec:120,isRacing:true},
  {name:"Nostalgia Top Fuel",code:"NTF",perPairSec:210,isRacing:true},
  {name:"Nostalgia Funny Car",code:"NFC",perPairSec:210,isRacing:true},
  {name:"Legacy Nitro Funny Car",code:"NFC",perPairSec:210,isRacing:true},
  {name:"Nostalgia Pro Stock",code:"NPS",perPairSec:150,isRacing:true},
  {name:"Legends Match Race",code:"LMR",perPairSec:60,isRacing:true},
  {name:"Exhibition",code:"EXH",perPairSec:150,isRacing:true},
  {name:"Gassers",code:"GS",perPairSec:90,isRacing:true},
  {name:"Jet Dragster",code:"JET",perPairSec:480,isRacing:true},
  {name:"Wheelstander",code:"WS",perPairSec:300,isRacing:true},
  {name:"Cacklefest",code:"CF",perPairSec:900,isRacing:false},
  {name:"Top the Cops",code:"TTC",perPairSec:50,isRacing:true},
  {name:"DeeCell Comp Clash",code:"DCC",perPairSec:125,isRacing:true},
  {name:"Summit Pro ET",code:"PROET",perPairSec:55,isRacing:true},
  {name:"Summit Sportsman ET",code:"SPTM",perPairSec:55,isRacing:true},
  {name:"Summit Super Pro ET",code:"SPRO",perPairSec:65,isRacing:true},
  {name:"Summit ET Motorcycle",code:"ETM",perPairSec:55,isRacing:true},
  {name:"Summit JDRL Shootout",code:"JDRL",perPairSec:60,isRacing:true},
  {name:"Summit Street Legal EV",code:"SLEV",perPairSec:50,isRacing:true},
  {name:"Track Prep",code:"TP",perPairSec:420,isRacing:false},
  {name:"Pre-Race Ceremonies",code:"PRC",perPairSec:3600,isRacing:false},
  {name:"Driver Intros",code:"DI",perPairSec:900,isRacing:false},
  {name:"Marketing Activity",code:"MA",perPairSec:300,isRacing:false},
  {name:"SealMaster Track Walk",code:"TW",perPairSec:600,isRacing:false},
  {name:"Miscellaneous",code:"MISC",perPairSec:600,isRacing:false},
  {name:"Parade of Champions",code:"PC",perPairSec:1500,isRacing:false},
  {name:"Invocation / National Anthem",code:"MKT",perPairSec:300,isRacing:false},
  {name:"Start Engines",code:"",perPairSec:1800,isRacing:false},
  {name:"Secure",code:"X",perPairSec:0,isRacing:false},
];

app.get("/race-classes", async (req, res) => {
  try {
    const doc = await db.collection("race_classes").doc("default").get();
    if (doc.exists && doc.data().classes) {
      return res.json({ classes: doc.data().classes });
    }
    // Doc doesn't exist yet — return defaults
    return res.json({ classes: DEFAULT_RACE_CLASSES });
  } catch (err) {
    console.error("Race classes GET error:", err);
    return res.status(500).json({ error: "Failed to load race classes" });
  }
});

app.post("/race-classes", async (req, res) => {
  try {
    const classes = req.body.classes;
    if (!Array.isArray(classes)) {
      return res.status(400).json({ error: "classes array required" });
    }
    await db.collection("race_classes").doc("default").set({ classes, updatedAt: admin.firestore.FieldValue.serverTimestamp() });
    return res.json({ ok: true, count: classes.length });
  } catch (err) {
    console.error("Race classes POST error:", err);
    return res.status(500).json({ error: "Failed to save race classes" });
  }
});

// ─── Tech Cards ─────────────────────────────────────────────────────────────

app.post("/tech-cards/upload", async (req, res) => {
  try {
    const rows = req.body.rows;
    if (!Array.isArray(rows) || rows.length === 0) {
      return res.status(400).json({ error: "rows array required" });
    }

    const sourceFile = req.body.source_file || "upload";
    let created = 0;
    let updated = 0;

    // Firestore batches max 500 ops — process in chunks
    const chunks = [];
    for (let i = 0; i < rows.length; i += 250) {
      chunks.push(rows.slice(i, i + 250));
    }

    for (const chunk of chunks) {
      const batch = db.batch();

      for (const row of chunk) {
        const memberNum = String(row.member_num || "").trim();
        if (!memberNum) continue;

        const entry = {
          category: row.category || "",
          class: row.class || "",
          car_number: row.car_number || "",
          owner: row.owner || "",
          crew_chief: row.crew_chief || "",
          engine_make: row.engine_make || "",
          engine_year: row.engine_year || "",
          body_type: row.body_type || "",
          body_year: row.body_year || "",
          cui_cc: row.cui_cc || "",
          hp: row.hp || "",
          factored_hp: row.factored_hp || "",
        };

        const ref = db.collection("tech_cards").doc(memberNum);
        const existing = await ref.get();

        if (existing.exists) {
          const data = existing.data();
          const entries = data.entries || [];
          const idx = entries.findIndex((e) => e.category === entry.category && entry.category !== "");
          if (idx >= 0) {
            entries[idx] = entry;
          } else {
            entries.push(entry);
          }
          batch.update(ref, {
            first_name: row.first_name || data.first_name || "",
            last_name: row.last_name || data.last_name || "",
            car_number: row.car_number || data.car_number || "",
            city: row.city || data.city || "",
            state: row.state || data.state || "",
            occupation: row.occupation || data.occupation || "",
            license_num: row.license_num || data.license_num || "",
            license_expiry: row.license_expiry || data.license_expiry || "",
            home_division: row.home_division || data.home_division || "",
            member_expiry: row.member_expiry || data.member_expiry || "",
            line1: row.line1 || data.line1 || "",
            line2: row.line2 || data.line2 || "",
            line3: row.line3 || data.line3 || "",
            line4: row.line4 || data.line4 || "",
            line5: row.line5 || data.line5 || "",
            line6: row.line6 || data.line6 || "",
            entries,
            updated_at: new Date().toISOString(),
            source_file: sourceFile,
          });
          updated++;
        } else {
          batch.set(ref, {
            member_num: memberNum,
            first_name: row.first_name || "",
            last_name: row.last_name || "",
            car_number: row.car_number || "",
            city: row.city || "",
            state: row.state || "",
            occupation: row.occupation || "",
            license_num: row.license_num || "",
            license_expiry: row.license_expiry || "",
            home_division: row.home_division || "",
            member_expiry: row.member_expiry || "",
            line1: row.line1 || "",
            line2: row.line2 || "",
            line3: row.line3 || "",
            line4: row.line4 || "",
            line5: row.line5 || "",
            line6: row.line6 || "",
            entries: [entry],
            updated_at: new Date().toISOString(),
            source_file: sourceFile,
          });
          created++;
        }
      }

      await batch.commit();
    }

    res.json({ ok: true, created, updated, total: created + updated });
  } catch (err) {
    console.error("Tech cards upload error:", err);
    res.status(500).json({ error: err.message });
  }
});

app.get("/tech-cards", async (req, res) => {
  try {
    const q = (req.query.q || "").trim().toLowerCase();
    const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 50, 1), 500);

    let snapshot;
    if (!q) {
      snapshot = await db.collection("tech_cards").orderBy("last_name").limit(limit).get();
    } else {
      // Fetch a broader set and filter client-side for multi-field search
      snapshot = await db.collection("tech_cards").limit(2000).get();
    }

    let cards = [];
    snapshot.forEach((doc) => cards.push({ id: doc.id, ...doc.data() }));

    if (q) {
      cards = cards.filter((c) => {
        const haystack = [c.first_name, c.last_name, c.car_number, c.member_num]
          .join(" ")
          .toLowerCase();
        return haystack.includes(q);
      });
      cards = cards.slice(0, limit);
    }

    res.json({ cards });
  } catch (err) {
    console.error("Tech cards GET error:", err);
    res.status(500).json({ error: err.message });
  }
});

app.patch("/tech-cards/:memberId", async (req, res) => {
  try {
    const memberId = req.params.memberId;
    const docRef = db.collection("tech_cards").doc(memberId);
    const doc = await docRef.get();
    if (!doc.exists) {
      return res.status(404).json({ error: "Tech card not found" });
    }

    // Only allow updating specific safe fields
    const allowedFields = ["photo_url", "announcer_notes"];
    const updates = {};
    for (const field of allowedFields) {
      if (req.body[field] !== undefined) {
        updates[field] = req.body[field];
      }
    }

    if (Object.keys(updates).length === 0) {
      return res.status(400).json({ error: "No valid fields to update" });
    }

    updates.updated_at = new Date().toISOString();
    await docRef.update(updates);

    const updated = await docRef.get();
    res.json({ id: updated.id, ...updated.data() });
  } catch (err) {
    console.error("Tech card PATCH error:", err);
    res.status(500).json({ error: err.message });
  }
});

app.get("/tech-cards/:memberId", async (req, res) => {
  try {
    const doc = await db.collection("tech_cards").doc(req.params.memberId).get();
    if (!doc.exists) {
      return res.status(404).json({ error: "Tech card not found" });
    }
    res.json({ id: doc.id, ...doc.data() });
  } catch (err) {
    console.error("Tech card detail error:", err);
    res.status(500).json({ error: err.message });
  }
});

app.get("/tech-cards/:memberId/runs", async (req, res) => {
  try {
    const memberId = req.params.memberId;
    const deviceId = req.query.device_id;
    if (!deviceId) {
      return res.status(400).json({ error: "device_id query param required" });
    }

    const racesRef = db.collection("devices").doc(deviceId).collection("races");
    const [leftSnap, rightSnap] = await Promise.all([
      racesRef.where("left_member_num", "==", memberId).orderBy("timestamp_ms", "desc").limit(100).get(),
      racesRef.where("right_member_num", "==", memberId).orderBy("timestamp_ms", "desc").limit(100).get(),
    ]);

    const seen = new Set();
    const runs = [];

    const addRuns = (snap, side) => {
      snap.forEach((doc) => {
        if (seen.has(doc.id)) return;
        seen.add(doc.id);
        runs.push({ id: doc.id, side, ...doc.data() });
      });
    };

    addRuns(leftSnap, "left");
    addRuns(rightSnap, "right");

    runs.sort((a, b) => (b.timestamp_ms || 0) - (a.timestamp_ms || 0));

    res.json({ runs });
  } catch (err) {
    console.error("Tech card runs error:", err);
    res.status(500).json({ error: err.message });
  }
});

exports.api = onRequest(app);
