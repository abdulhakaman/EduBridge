"""Small LAN file-transfer helper used by the EduBridge desktop prototype."""
from __future__ import annotations

import hashlib
import json
import queue
import socket
import threading
from pathlib import Path

PORT = 45872
MAX_BYTES = 1024 * 1024 * 1024
CHUNK = 64 * 1024


class LanShare:
    def __init__(self, received_dir: Path) -> None:
        self.received_dir = received_dir
        self.received_dir = received_dir
        self.port = PORT
        self.events: queue.Queue = queue.Queue()
        self.server: socket.socket | None = None
        self.running = False
        self.worker: threading.Thread | None = None

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
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", PORT))
        server.listen(5)
        server.settimeout(0.5)
        self.server = server
        self.running = True
        self.worker = threading.Thread(target=self._accept_loop, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.running = False
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
            self.server = None

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
            reader = connection.makefile("rb")
            line = reader.readline(8193)
            if len(line) > 8192 or not line.endswith(b"\n"):
                raise ValueError("Invalid transfer request.")
            request = json.loads(line.decode("utf-8"))
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
            with temp_path.open("wb") as output:
                while remaining:
                    block = connection.recv(min(CHUNK, remaining))
                    if not block:
                        raise ConnectionError("The sender disconnected before the file finished.")
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
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

    def send_file(self, host: str, path: Path, title: str, course: str, year: str) -> None:
        threading.Thread(target=self._send, args=(host, path, title, course, year), daemon=True).start()

    def _send(self, host: str, path: Path, title: str, course: str, year: str) -> None:
        try:
            size = path.stat().st_size
            if size > MAX_BYTES:
                raise ValueError("File is larger than the 1 GB prototype limit.")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(CHUNK), b""):
                    digest.update(block)
            request = {"type": "file", "name": path.name, "title": title, "course": course, "year": year, "size": size, "sha256": digest.hexdigest()}
            with socket.create_connection((host, PORT), timeout=15) as connection:
                connection.settimeout(60)
                connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
                reader = connection.makefile("rb")
                reply = json.loads(reader.readline(8193).decode("utf-8"))
                if not reply.get("accepted"):
                    raise PermissionError("The receiver declined the transfer.")
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(CHUNK), b""):
                        connection.sendall(block)
                reply = json.loads(reader.readline(8193).decode("utf-8"))
                if not reply.get("complete"):
                    raise RuntimeError(reply.get("error", "The receiver did not confirm completion."))
            self.events.put(("sent", f"Sent {path.name} to {host}."))
        except Exception as exc:
            self.events.put(("error", f"Could not send {path.name}: {exc}"))
