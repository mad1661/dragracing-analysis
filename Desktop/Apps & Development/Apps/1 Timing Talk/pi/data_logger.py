import sqlite3
import glob
import os
import csv
import threading
import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("timing-talk.logger")

DEFAULT_RETENTION_DAYS = 60


class DataLogger:
    def __init__(self, config: dict):
        self.db_path = config["logging"]["db_path"]
        self.raw_dir = config["logging"]["raw_dir"]
        self._retention_days = config["logging"].get("retention_days", DEFAULT_RETENTION_DAYS)
        self._lock = threading.Lock()
        self._init_db()
        self._start_cleanup_thread()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.raw_dir, exist_ok=True)
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    raw_data BLOB NOT NULL,
                    raw_text TEXT,
                    source_port TEXT,
                    synced INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_readings_synced
                ON readings (synced) WHERE synced = 0
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_readings_timestamp
                ON readings (timestamp)
            """)
            # ── Parsed race results ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS races (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    race_id TEXT UNIQUE NOT NULL,
                    event_id TEXT NOT NULL,
                    event_name TEXT,
                    track_name TEXT,
                    track_location TEXT,
                    event_start_date TEXT,
                    timestamp TEXT NOT NULL,
                    timestamp_ms INTEGER,
                    category TEXT,
                    category_num TEXT,
                    round TEXT,
                    winner TEXT,
                    margin TEXT,
                    left_name TEXT, left_car_num TEXT, left_class TEXT, left_dial TEXT,
                    left_rt TEXT, left_sixty TEXT, left_three30 TEXT,
                    left_six60 TEXT, left_six60_speed TEXT,
                    left_thousand TEXT, left_thousand_speed TEXT,
                    left_et TEXT, left_speed TEXT, left_mov TEXT,
                    right_name TEXT, right_car_num TEXT, right_class TEXT, right_dial TEXT,
                    right_rt TEXT, right_sixty TEXT, right_three30 TEXT,
                    right_six60 TEXT, right_six60_speed TEXT,
                    right_thousand TEXT, right_thousand_speed TEXT,
                    right_et TEXT, right_speed TEXT, right_mov TEXT,
                    synced INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_races_event ON races (event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_races_synced ON races (synced) WHERE synced = 0")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_races_ts ON races (timestamp_ms)")
            # ── Event metadata ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    event_name TEXT,
                    track_name TEXT,
                    track_location TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def log(self, raw_data: bytes, source_port: str = ""):
        now = datetime.now(timezone.utc).isoformat()
        raw_text = raw_data.decode("ascii", errors="replace").strip()
        if not raw_text:
            return

        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO readings (timestamp, raw_data, raw_text, source_port) VALUES (?, ?, ?, ?)",
                    (now, raw_data, raw_text, source_port),
                )
            self._append_raw_csv(now, raw_text, source_port)

        logger.debug("Logged: %s", raw_text[:80])

    def _append_raw_csv(self, timestamp: str, raw_text: str, source_port: str):
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        csv_path = os.path.join(self.raw_dir, f"{date_str}.csv")
        file_exists = os.path.exists(csv_path)
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "source_port", "raw_text"])
            writer.writerow([timestamp, source_port, raw_text])

    def get_unsynced(self, limit: int = 100) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, raw_text, source_port FROM readings WHERE synced = 0 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_synced(self, ids: list[int]):
        if not ids:
            return
        with self._lock:
            with self._get_conn() as conn:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE readings SET synced = 1 WHERE id IN ({placeholders})",
                    ids,
                )

    def get_stats(self) -> dict:
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            unsynced = conn.execute(
                "SELECT COUNT(*) FROM readings WHERE synced = 0"
            ).fetchone()[0]
            latest = conn.execute(
                "SELECT timestamp, raw_text FROM readings ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "total_readings": total,
            "unsynced": unsynced,
            "latest_timestamp": latest["timestamp"] if latest else None,
            "latest_data": latest["raw_text"] if latest else None,
            "retention_days": self._retention_days,
        }

    # ── Race Persistence ────────────────────────────────────────────────────

    def save_race(self, race: dict, event_id: str, meta: dict):
        """Persist a completed parsed race. Upserts on race_id."""
        ts = race.get("timestamp", "")
        cat = race.get("category", "")
        rnd = race.get("round", "")
        l = race.get("left", {})
        r = race.get("right", {})
        race_id = f"{ts}|{cat}|{rnd}|{l.get('car_num', '')}|{r.get('car_num', '')}"
        now = datetime.now(timezone.utc).isoformat()
        ts_ms = int(time.time() * 1000)

        with self._lock:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO races (
                        race_id, event_id, event_name, track_name, track_location,
                        event_start_date, timestamp, timestamp_ms,
                        category, category_num, round, winner, margin,
                        left_name, left_car_num, left_class, left_dial,
                        left_rt, left_sixty, left_three30,
                        left_six60, left_six60_speed, left_thousand, left_thousand_speed,
                        left_et, left_speed, left_mov,
                        right_name, right_car_num, right_class, right_dial,
                        right_rt, right_sixty, right_three30,
                        right_six60, right_six60_speed, right_thousand, right_thousand_speed,
                        right_et, right_speed, right_mov,
                        created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    race_id, event_id, meta.get("event_name", ""),
                    meta.get("track_name", ""), meta.get("track_location", ""),
                    meta.get("event_start_date", ""), ts, ts_ms,
                    cat, race.get("category_num", ""), rnd,
                    race.get("winner", ""), race.get("margin", ""),
                    l.get("name", ""), l.get("car_num", ""), l.get("class_name", ""), l.get("dial_in", ""),
                    l.get("rt", ""), l.get("sixty", ""), l.get("three30", ""),
                    l.get("six60", ""), l.get("six60_speed", ""), l.get("thousand", ""), l.get("thousand_speed", ""),
                    l.get("et", ""), l.get("speed", ""), l.get("mov", ""),
                    r.get("name", ""), r.get("car_num", ""), r.get("class_name", ""), r.get("dial_in", ""),
                    r.get("rt", ""), r.get("sixty", ""), r.get("three30", ""),
                    r.get("six60", ""), r.get("six60_speed", ""), r.get("thousand", ""), r.get("thousand_speed", ""),
                    r.get("et", ""), r.get("speed", ""), r.get("mov", ""),
                    now,
                ))
        logger.debug("Saved race: %s vs %s (%s)", l.get("name", "?"), r.get("name", "?"), cat)

    def save_event(self, event_id: str, meta: dict):
        """Create or update event metadata."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT INTO events (id, event_name, track_name, track_location, start_date, end_date, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        event_name=excluded.event_name, track_name=excluded.track_name,
                        track_location=excluded.track_location, start_date=excluded.start_date,
                        end_date=excluded.end_date, updated_at=excluded.updated_at
                """, (
                    event_id, meta.get("event_name", ""), meta.get("track_name", ""),
                    meta.get("track_location", ""), meta.get("start_date", ""),
                    meta.get("end_date", ""), now, now,
                ))

    def get_races_by_event(self, event_id: str, limit: int = 500) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM races WHERE event_id = ? ORDER BY timestamp_ms DESC LIMIT ?",
                (event_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_races(self, limit: int = 50) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM races ORDER BY timestamp_ms DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_events(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT e.*, COUNT(r.id) as race_count FROM events e "
                "LEFT JOIN races r ON r.event_id = e.id "
                "GROUP BY e.id ORDER BY e.updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def reassign_races(self, old_event_id: str, new_event_id: str, new_meta: dict):
        """Move all races from one event to another (retroactive assignment)."""
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE races SET event_id = ?, event_name = ?, track_name = ?, "
                    "track_location = ?, event_start_date = ?, synced = 0 "
                    "WHERE event_id = ?",
                    (new_event_id, new_meta.get("event_name", ""),
                     new_meta.get("track_name", ""), new_meta.get("track_location", ""),
                     new_meta.get("start_date", ""), old_event_id),
                )
                # Delete the old event entry
                conn.execute("DELETE FROM events WHERE id = ?", (old_event_id,))
        self.save_event(new_event_id, new_meta)
        logger.info("Reassigned races from %s to %s", old_event_id, new_event_id)

    def get_unsynced_races(self, limit: int = 100) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM races WHERE synced = 0 ORDER BY id LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_races_synced(self, ids: list[int]):
        if not ids:
            return
        with self._lock:
            with self._get_conn() as conn:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(f"UPDATE races SET synced = 1 WHERE id IN ({placeholders})", ids)

    # ── Data Retention ─────────────────────────────────────────────────────

    def _start_cleanup_thread(self):
        t = threading.Thread(target=self._cleanup_loop, daemon=True, name="data-cleanup")
        t.start()

    def _cleanup_loop(self):
        time.sleep(30)
        while True:
            try:
                self._purge_old_data()
            except Exception as e:
                logger.error("Cleanup error: %s", e)
            time.sleep(6 * 3600)

    def _purge_old_data(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention_days)).isoformat()
        cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=self._retention_days)).timestamp() * 1000)

        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    "DELETE FROM readings WHERE timestamp < ? AND synced = 1",
                    (cutoff,),
                )
                deleted = cursor.rowcount
                # Also purge old synced races
                rc = conn.execute(
                    "DELETE FROM races WHERE timestamp_ms < ? AND synced = 1",
                    (cutoff_ms,),
                )
                deleted_races = rc.rowcount

        if deleted > 0:
            logger.info("Purged %d readings older than %d days", deleted, self._retention_days)
        if deleted_races > 0:
            logger.info("Purged %d races older than %d days", deleted_races, self._retention_days)
        if deleted > 0 or deleted_races > 0:
            self._vacuum_db()

        self._purge_old_csv_files()

    def _vacuum_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("VACUUM")
            conn.close()
            logger.info("Database vacuumed to reclaim disk space")
        except Exception as e:
            logger.warning("Vacuum failed (non-critical): %s", e)

    def _purge_old_csv_files(self):
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        removed = 0
        for csv_file in glob.glob(os.path.join(self.raw_dir, "*.csv")):
            basename = os.path.basename(csv_file).replace(".csv", "")
            try:
                file_date = datetime.strptime(basename, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff_date:
                    os.remove(csv_file)
                    removed += 1
            except ValueError:
                continue
        if removed > 0:
            logger.info("Removed %d CSV files older than %d days", removed, self._retention_days)
