"""LAN discovery and file-transfer helper for the EduBridge prototype."""
from __future__ import annotations

import hashlib
import json
import queue
import socket
import threading
import time
from pathlib import Path

PORT = 45872
DISCOVERY_PORT = 45873
MAX_BYTES = 1024 * 1024 * 1024
CHUNK = 64 * 1024


def parse_peer_address(value: str, default_port: int) -> tuple[str, int]:
    text = value.strip()
    if not text:
        raise ValueError("Enter a receiver address.")
    if ":" in text:
        host, port_text = text.rsplit(":", 1)
        if host and port_text.isdigit():
            port = int(port_text)
            if 1 <= port <= 65535:
                return host, port
        raise ValueError("Use an address such as 127.0.0.1:45882.")
    return text, default_port


def _read_line(sock: socket.socket, limit: int = 8192) -> bytes:
    data = bytearray()
    while len(data) <= limit:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("Disconnected while reading a control message.")
        if byte == b"\n":
            return bytes(data)
        data.extend(byte)
    raise ValueError("Control message is too long.")


def _read_json_line(sock: socket.socket, limit: int = 8192) -> dict:
    line = _read_line(sock, limit)
    return json.loads(line.decode("utf-8"))


class LanShare:
    def __init__(self, received_dir: Path, port: int = PORT, discovery_port: int = DISCOVERY_PORT) -> None:
        self.received_dir = received_dir
        self.port = port
        self.discovery_port = discovery_port
        self.events: queue.Queue = queue.Queue()
        self.server: socket.socket | None = None
        self.discovery_socket: socket.socket | None = None
        self.beacon_socket: socket.socket | None = None
        self.running = False
        self.worker: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._active_send: socket.socket | None = None
        self._active_cancel: threading.Event | None = None
        self._send_running = False

    @staticmethod
    def local_ip() -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 80))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            probe.close()

    def start(self) -> None:
        if self.running:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        beacon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.port))
            server.listen(5)
            server.settimeout(0.5)
            discovery.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            discovery.bind(("0.0.0.0", self.discovery_port))
            discovery.settimeout(0.5)
            beacon.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            server.close()
            discovery.close()
            beacon.close()
            raise
        self.server, self.discovery_socket, self.beacon_socket = server, discovery, beacon
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._discovery_loop, daemon=True).start()
        threading.Thread(target=self._beacon_loop, daemon=True).start()

    def stop(self) -> None:
        self.running = False
        for name in ("server", "discovery_socket", "beacon_socket"):
            sock = getattr(self, name)
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, name, None)

    def _beacon_loop(self) -> None:
        payload = json.dumps({"type": "edubridge_peer", "name": socket.gethostname(), "port": self.port}).encode("utf-8")
        while self.running and self.beacon_socket:
            try:
                self.beacon_socket.sendto(payload, ("255.255.255.255", self.discovery_port))
            except OSError as exc:
                if self.running:
                    self.events.put(("error", f"Nearby discovery could not broadcast: {exc}"))
                return
            time.sleep(2)

    def _discovery_loop(self) -> None:
        while self.running and self.discovery_socket:
            try:
                packet, address = self.discovery_socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                message = json.loads(packet.decode("utf-8"))
                if message.get("type") == "edubridge_peer" and address[0] != self.local_ip():
                    self.events.put(("peer", {"ip": address[0], "name": str(message.get("name", "EduBridge computer")), "port": int(message.get("port", self.port))}))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                continue

    def _accept_loop(self) -> None:
        while self.running and self.server:
            try:
                connection, address = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._receive, args=(connection, address), daemon=True).start()

    def _receive(self, connection: socket.socket, address: tuple) -> None:
        temp_path: Path | None = None
        try:
            connection.settimeout(30)
            request = _read_json_line(connection)
            if request.get("type") != "file" or not isinstance(request.get("size"), int):
                raise ValueError("Unsupported transfer request.")
            size = request["size"]
            if size < 0 or size > MAX_BYTES:
                raise ValueError("File is too large (maximum 1 GB).")
            name = Path(str(request.get("name", "resource"))).name
            if not name or name in (".", ".."):
                raise ValueError("Invalid file name.")
            approval = threading.Event()
            decision = {"approved": False}
            self.events.put(("incoming", {"request": request, "address": address[0], "approval": approval, "decision": decision}))
            if not approval.wait(120):
                raise TimeoutError("The receiver did not answer the transfer request.")
            if not decision["approved"]:
                connection.sendall(b'{"accepted":false}\n')
                return
            connection.sendall(b'{"accepted":true}\n')
            self.received_dir.mkdir(parents=True, exist_ok=True)
            final_path = self._unique_path(name)
            temp_path = final_path.with_suffix(final_path.suffix + ".part")
            digest = hashlib.sha256()
            remaining = size
            last_report = time.monotonic()
            with temp_path.open("wb") as output:
                while remaining:
                    block = connection.recv(min(CHUNK, remaining))
                    if not block:
                        raise ConnectionError("The sender disconnected before the file finished.")
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
                    now = time.monotonic()
                    percent = 100 if size == 0 else round((size - remaining) * 100 / size)
                    if now - last_report >= 0.25 or remaining == 0:
                        self.events.put(("progress", {"direction": "Receiving", "name": name, "percent": percent}))
                        last_report = now
            if digest.hexdigest().lower() != str(request.get("sha256", "")).lower():
                raise ValueError("File verification failed. The received file was discarded.")
            temp_path.replace(final_path)
            temp_path = None
            connection.sendall(b'{"complete":true}\n')
            self.events.put(("received", {"path": str(final_path), "request": request, "address": address[0]}))
        except Exception as exc:
            self.events.put(("error", f"Transfer from {address[0]} failed: {exc}"))
            try:
                connection.sendall(json.dumps({"error": str(exc)}).encode() + b"\n")
            except OSError:
                pass
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                connection.close()
            except OSError:
                pass

    def _unique_path(self, name: str) -> Path:
        candidate = self.received_dir / name
        stem, suffix = candidate.stem, candidate.suffix
        index = 1
        while candidate.exists():
            candidate = self.received_dir / f"{stem} ({index}){suffix}"
            index += 1
        return candidate

    def cancel_send(self) -> None:
        with self._send_lock:
            cancel = self._active_cancel
            sock = self._active_send
        if cancel is not None:
            cancel.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def send_file(self, host: str, path: Path, title: str, course: str, year: str, signature: dict, dest_port: int | None = None) -> None:
        port = self.port if dest_port is None else dest_port
        with self._send_lock:
            if self._send_running:
                self.events.put(("error", "A transfer is already in progress."))
                return
            self._send_running = True
            self._active_cancel = threading.Event()
            cancel = self._active_cancel
        threading.Thread(target=self._send, args=(host, path, title, course, year, signature, cancel, port), daemon=True).start()

    def _send(self, host: str, path: Path, title: str, course: str, year: str, signature: dict, cancel: threading.Event, dest_port: int) -> None:
        try:
            try:
                size = path.stat().st_size
                if size > MAX_BYTES:
                    raise ValueError("File is larger than the 1 GB prototype limit.")
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(CHUNK), b""):
                        digest.update(block)
                request = {"type": "file", "name": path.name, "title": title, "course": course, "year": year, "size": size, "sha256": digest.hexdigest(), "signature": signature}
                with socket.create_connection((host, dest_port), timeout=15) as connection:
                    with self._send_lock:
                        self._active_send = connection
                    try:
                        connection.settimeout(60)
                        if cancel.is_set():
                            raise ConnectionError("cancelled")
                        connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
                        reply = _read_json_line(connection)
                        if cancel.is_set():
                            raise ConnectionError("cancelled")
                        if not reply.get("accepted"):
                            raise PermissionError("The receiver declined the transfer.")
                        sent_bytes = 0
                        last_report = time.monotonic()
                        with path.open("rb") as source:
                            for block in iter(lambda: source.read(CHUNK), b""):
                                if cancel.is_set():
                                    raise ConnectionError("cancelled")
                                connection.sendall(block)
                                sent_bytes += len(block)
                                now = time.monotonic()
                                percent = 100 if size == 0 else round(sent_bytes * 100 / size)
                                if now - last_report >= 0.25 or sent_bytes == size:
                                    self.events.put(("progress", {"direction": "Sending", "name": path.name, "percent": percent}))
                                    last_report = now
                        reply = _read_json_line(connection)
                        if not reply.get("complete"):
                            raise RuntimeError(reply.get("error", "The receiver did not confirm completion."))
                    finally:
                        with self._send_lock:
                            if self._active_send is connection:
                                self._active_send = None
                self.events.put(("sent", f"Sent {path.name} to {host}:{dest_port}."))
            except Exception as exc:
                if cancel.is_set():
                    self.events.put(("error", f"Cancelled sending {path.name}."))
                else:
                    self.events.put(("error", f"Could not send {path.name}: {exc}"))
        finally:
            with self._send_lock:
                self._send_running = False
                if self._active_cancel is cancel:
                    self._active_cancel = None
                    self._active_send = None







