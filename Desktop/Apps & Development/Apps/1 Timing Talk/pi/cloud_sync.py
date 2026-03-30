import threading
import logging
import time
import os
import json
from datetime import datetime, timezone
from remote_support import execute_support_action, iso_now, support_snapshot

logger = logging.getLogger("timing-talk.cloud")


class CloudSync:
    def __init__(self, config: dict, data_logger):
        self.config = config["cloud"]
        self.device_id = config.get("device_id", "unknown")
        self.track_name = config.get("track_name", "")
        self.track_location = config.get("track_location", "")
        self.full_config = config
        self.data_logger = data_logger
        self._db = None  # Firebase Realtime DB reference
        self._firestore = None
        self._running = False
        self._connected = False
        self._sync_thread: threading.Thread | None = None
        self._batch_size = self.config.get("sync_batch_size", 100)
        self._heartbeat_interval = self.config.get("heartbeat_interval_sec", 30)
        self._support_poll_interval = self.config.get("support_poll_interval_sec", 5)

    def _current_event_date(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _effective_race_name(self) -> str:
        event_name = (self.full_config.get("event_name") or "").strip()
        current_date = self._current_event_date()
        if event_name:
            return f"{event_name} - {current_date}"
        return current_date

    def _init_firebase(self) -> bool:
        creds_path = self.config.get("firebase_credentials", "")
        if not creds_path or not os.path.exists(creds_path):
            logger.warning("Firebase credentials not found at %s", creds_path)
            return False

        try:
            import firebase_admin
            from firebase_admin import credentials, db, firestore

            if not firebase_admin._apps:
                cred = credentials.Certificate(creds_path)
                firebase_admin.initialize_app(cred, {
                    "databaseURL": self.config.get("database_url", ""),
                })

            self._db = db.reference(f"/devices/{self.device_id}")
            self._firestore = firestore.client()
            self._connected = True
            logger.info("Firebase initialized for device %s", self.device_id)
            self._register_device()
            return True
        except Exception as e:
            logger.error("Firebase init failed: %s", e)
            self._connected = False
            return False

    def _register_device(self):
        """Register or update device in Firestore and the public tracks directory."""
        if not self._firestore:
            return
        try:
            track_name = self.full_config.get("track_name", "")
            track_location = self.full_config.get("track_location", "")
            race_name = self._effective_race_name()
            remote_access = support_snapshot(self.full_config)

            self._firestore.collection("devices").document(self.device_id).set({
                "trackName": track_name,
                "trackLocation": track_location,
                "raceName": race_name,
                "eventName": (self.full_config.get("event_name") or "").strip(),
                "deviceId": self.device_id,
                "remoteAccess": remote_access["tailscale"],
                "supportCapabilities": remote_access["capabilities"],
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }, merge=True)

            if track_name:
                self._firestore.collection("tracks").document(self.device_id).set({
                    "name": track_name,
                    "location": track_location,
                    "raceName": race_name,
                    "eventName": (self.full_config.get("event_name") or "").strip(),
                    "deviceId": self.device_id,
                    "online": True,
                    "remoteAccess": remote_access["tailscale"],
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }, merge=True)
                logger.info("Registered track: %s (%s) - %s", track_name, track_location, race_name)
        except Exception as e:
            logger.error("Device registration failed: %s", e)

    def push_live(self, raw_text: str, source_port: str = ""):
        """Push serial data to its own channel in Realtime DB."""
        if not self._connected or not self._db:
            return
        try:
            channel = "tcp" if source_port.startswith("tcp:") else "serial"
            payload = {
                "raw": raw_text,
                "source_port": source_port,
                "timestamp": {".sv": "timestamp"},
            }
            self._db.child(f"stream/{channel}").set(payload)
        except Exception as e:
            logger.debug("Live push failed (will retry via sync): %s", e)

    def push_live_json(self, parsed: dict, source_port: str = ""):
        """Push structured TCP/JSON data to the tcp channel with parsed fields."""
        if not self._connected or not self._db:
            return
        try:
            self._db.child("stream/tcp").set({
                "raw": json.dumps(parsed),
                "parsed": parsed,
                "source_port": source_port,
                "source_type": "tcp_json",
                "timestamp": {".sv": "timestamp"},
            })
        except Exception as e:
            logger.debug("Live JSON push failed: %s", e)

    def _sync_backlog(self):
        """Upload unsynced records from SQLite to Firestore."""
        if not self._connected or not self._firestore:
            return 0

        unsynced = self.data_logger.get_unsynced(limit=self._batch_size)
        if not unsynced:
            return 0

        try:
            device_ref = self._firestore.collection("devices").document(self.device_id)
            batch = self._firestore.batch()
            synced_ids = []

            for record in unsynced:
                reading_ref = device_ref.collection("readings").document()
                batch.set(reading_ref, {
                    "timestamp": record["timestamp"],
                    "rawData": record["raw_text"],
                    "sourcePort": record["source_port"],
                    "syncedAt": datetime.now(timezone.utc).isoformat(),
                })
                synced_ids.append(record["id"])

            batch.commit()
            self.data_logger.mark_synced(synced_ids)
            logger.info("Synced %d records to Firestore", len(synced_ids))
            return len(synced_ids)
        except Exception as e:
            logger.error("Backlog sync failed: %s", e)
            return 0

    def _send_heartbeat(self):
        if not self._connected or not self._db:
            return
        try:
            track_name = self.full_config.get("track_name", "")
            race_name = self._effective_race_name()
            remote_access = support_snapshot(self.full_config)
            self._db.child("status").set({
                "online": True,
                "lastSeen": {".sv": "timestamp"},
                "deviceName": self.device_id[:8],
                "trackName": track_name,
                "trackLocation": self.full_config.get("track_location", ""),
                "raceName": race_name,
                "eventName": (self.full_config.get("event_name") or "").strip(),
                "remoteAccess": remote_access["tailscale"],
                "supportCapabilities": remote_access["capabilities"],
            })
            self._db.child("support/meta").update({
                "capabilities": remote_access["capabilities"],
                "tailscale": remote_access["tailscale"],
                "updatedAt": iso_now(),
            })
            self._register_device()
        except Exception as e:
            logger.debug("Heartbeat failed: %s", e)

    def _process_support_commands(self):
        if not self._connected or not self._db:
            return

        try:
            commands = self._db.child("support/commands").get() or {}
            if not isinstance(commands, dict):
                return

            pending = []
            for command_id, command in commands.items():
                if not isinstance(command, dict):
                    continue
                if command.get("status") != "pending":
                    continue
                pending.append((command_id, command))

            pending.sort(key=lambda item: item[1].get("requestedAt", 0))

            for command_id, command in pending[:5]:
                action = str(command.get("action") or "").strip()
                command_ref = self._db.child(f"support/commands/{command_id}")
                command_ref.update({
                    "status": "in_progress",
                    "startedAt": iso_now(),
                })
                try:
                    result = execute_support_action(action, command.get("payload") or {}, self.full_config)
                    command_ref.update({
                        "status": "completed",
                        "completedAt": iso_now(),
                        "ok": bool(result.get("ok", True)),
                        "message": result.get("message", ""),
                        "result": result,
                    })
                    self._db.child("support/meta").update({
                        "lastCommandAt": iso_now(),
                        "lastCommandAction": action,
                        "lastCommandStatus": "completed",
                    })
                except Exception as exc:
                    logger.exception("Support command failed: %s", action)
                    command_ref.update({
                        "status": "failed",
                        "completedAt": iso_now(),
                        "ok": False,
                        "message": str(exc),
                    })
                    self._db.child("support/meta").update({
                        "lastCommandAt": iso_now(),
                        "lastCommandAction": action,
                        "lastCommandStatus": "failed",
                    })
        except Exception as e:
            logger.debug("Support command poll failed: %s", e)

    def _set_offline(self):
        if not self._connected or not self._db:
            return
        try:
            self._db.child("status/online").set(False)
        except Exception:
            pass

    def _sync_loop(self):
        last_heartbeat = 0
        last_support_poll = 0
        retry_delay = 5

        while self._running:
            if not self._connected:
                if self._init_firebase():
                    retry_delay = 5
                else:
                    logger.debug("Firebase connection retry in %ds", retry_delay)
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)
                    continue

            now = time.time()
            if now - last_heartbeat > self._heartbeat_interval:
                self._send_heartbeat()
                last_heartbeat = now

            if now - last_support_poll > self._support_poll_interval:
                self._process_support_commands()
                last_support_poll = now

            synced = self._sync_backlog()
            sleep_time = 2 if synced > 0 else 10
            time.sleep(sleep_time)

    def start(self):
        if self._running:
            return
        self._running = True
        self._init_firebase()
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True, name="cloud-sync")
        self._sync_thread.start()
        logger.info("Cloud sync started")

    def stop(self):
        self._running = False
        self._set_offline()
        if self._sync_thread:
            self._sync_thread.join(timeout=10)
        logger.info("Cloud sync stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected
