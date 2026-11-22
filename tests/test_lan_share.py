"""Loopback transfer checks for EduBridge LAN sharing."""
from __future__ import annotations

import socket
import tempfile
import time
import unittest
from pathlib import Path

from lan_share import LanShare


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class LanShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.network = LanShare(self.root / "Received", port=_free_port(), discovery_port=_free_port())
        self.network.start()

    def tearDown(self) -> None:
        self.network.cancel_send()
        self.network.stop()
        self.temp.cleanup()

    def _wait_event(self, kind: str, timeout: float = 8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                event_kind, data = self.network.events.get(timeout=remaining)
            except Exception:
                break
            if event_kind == kind:
                return data
            if event_kind == "error" and kind != "error":
                self.fail(f"Unexpected transfer error: {data}")
        self.fail(f"Timed out waiting for {kind} event")

    def test_loopback_signed_file_transfer(self) -> None:
        source = self.root / "course.txt"
        source.write_text("official notes", encoding="utf-8")
        signature = {"schema": 1, "signer_name": "Ada Teacher"}
        self.network.send_file("127.0.0.1", source, "Lecture 1", "CS101", "2026", signature)
        incoming = self._wait_event("incoming")
        incoming["decision"]["approved"] = True
        incoming["approval"].set()
        received = self._wait_event("received")
        saved = Path(received["path"])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_text(encoding="utf-8"), "official notes")
        self.assertEqual(received["request"]["title"], "Lecture 1")
        self.assertEqual(received["request"]["signature"]["signer_name"], "Ada Teacher")
        sent = self._wait_event("sent")
        self.assertIn("course.txt", sent)

    def test_cancel_before_receiver_accepts(self) -> None:
        source = self.root / "large.bin"
        source.write_bytes(b"x" * (256 * 1024))
        self.network.send_file("127.0.0.1", source, "Lecture 1", "CS101", "2026", {"schema": 1})
        incoming = self._wait_event("incoming")
        self.network.cancel_send()
        incoming["decision"]["approved"] = False
        incoming["approval"].set()
        error = self._wait_event("error")
        self.assertIn("Cancelled", error)


if __name__ == "__main__":
    unittest.main()
