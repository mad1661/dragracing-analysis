import socket
import threading
import logging
import json
import time
from typing import Callable

logger = logging.getLogger("timing-talk.tcp")


class TCPReceiver:
    """Listens for timing data sent over TCP/IP (raw text or JSON)."""

    def __init__(self, config: dict):
        tcp_config = config.get("tcp", {})
        self._host = tcp_config.get("host", "0.0.0.0")
        self._port = tcp_config.get("port", 4000)
        self._enabled = tcp_config.get("enabled", True)
        self._running = False
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._callbacks: list[Callable[[bytes, str], None]] = []
        self._json_callbacks: list[Callable[[dict, str], None]] = []
        self._clients: list[socket.socket] = []
        self._client_count = 0

    def on_data(self, callback: Callable[[bytes, str], None]):
        """Same signature as SerialReader: callback(raw_bytes, source_name)."""
        self._callbacks.append(callback)

    def on_json(self, callback: Callable[[dict, str], None]):
        """For structured JSON: callback(parsed_dict, source_name)."""
        self._json_callbacks.append(callback)

    def _emit(self, data: bytes, source: str):
        for cb in self._callbacks:
            try:
                cb(data, source)
            except Exception as e:
                logger.error("TCP data callback error: %s", e)

    def _emit_json(self, parsed: dict, source: str):
        for cb in self._json_callbacks:
            try:
                cb(parsed, source)
            except Exception as e:
                logger.error("TCP JSON callback error: %s", e)

    def _handle_client(self, conn: socket.socket, addr: tuple):
        source = f"tcp:{addr[0]}:{addr[1]}"
        logger.info("TCP client connected: %s", source)
        self._clients.append(conn)
        self._client_count += 1
        buf = b""

        try:
            conn.settimeout(1.0)
            while self._running:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not chunk:
                    break

                buf += chunk

                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    self._emit(line + b"\n", source)

                    try:
                        parsed = json.loads(line)
                        if isinstance(parsed, dict):
                            self._emit_json(parsed, source)
                    except (json.JSONDecodeError, ValueError):
                        pass

                # Handle case where buffer has no newline but is very large
                # (malformed sender not using line delimiters)
                if len(buf) > 65536:
                    self._emit(buf, source)
                    buf = b""

        except Exception as e:
            logger.warning("TCP client %s error: %s", source, e)
        finally:
            self._client_count -= 1
            try:
                self._clients.remove(conn)
            except ValueError:
                pass
            try:
                conn.close()
            except Exception:
                pass
            logger.info("TCP client disconnected: %s", source)

    def _accept_loop(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self._server.bind((self._host, self._port))
            self._server.listen(5)
            self._server.settimeout(1.0)
            logger.info("TCP listener started on %s:%d", self._host, self._port)
        except OSError as e:
            logger.error("Failed to start TCP listener on port %d: %s", self._port, e)
            return

        while self._running:
            try:
                conn, addr = self._server.accept()
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                    name=f"tcp-client-{addr[0]}",
                )
                client_thread.start()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.warning("TCP accept error")
                break

    def start(self):
        if not self._enabled:
            logger.info("TCP receiver disabled in config")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="tcp-receiver")
        self._thread.start()
        logger.info("TCP receiver started")

    def stop(self):
        self._running = False
        for client in list(self._clients):
            try:
                client.close()
            except Exception:
                pass
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TCP receiver stopped")

    @property
    def is_connected(self) -> bool:
        return self._client_count > 0

    @property
    def client_count(self) -> int:
        return self._client_count

    @property
    def port(self) -> int:
        return self._port
