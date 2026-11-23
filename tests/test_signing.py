"""Signing checks for official EduBridge course resources."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from signing import (
    create_teacher_key,
    fingerprint,
    load_ed25519_public_key,
    load_teacher_key,
    sign_file,
    verify_file,
)


class SigningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file_path = self.root / "notes.txt"
        self.file_path.write_text("lecture one", encoding="utf-8")
        self.private_path = self.root / "teacher_signing_key.pem"
        self.password = "passphrase-1"
        pem, self.key_fingerprint = create_teacher_key(self.private_path, self.password)
        self.public_key = load_ed25519_public_key(pem)
        self.private_key = load_teacher_key(self.private_path, self.password)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sign(self, path: Path | None = None) -> dict:
        return sign_file(path or self.file_path, "Lecture 1", "CS101", "2026", "Ada Teacher", self.private_key)

    def test_sign_and_verify(self) -> None:
        package = self.sign()
        self.assertEqual(package["schema"], 1)
        self.assertEqual(package["signer_fingerprint"], self.key_fingerprint)
        verify_file(self.file_path, package, self.public_key)

    def test_verify_without_extra_metadata(self) -> None:
        package = self.sign()
        verify_file(self.file_path, package, self.public_key)

    def test_wrong_passphrase(self) -> None:
        with self.assertRaises((ValueError, TypeError)):
            load_teacher_key(self.private_path, "wrong-pass")

    def test_tampered_file_hash(self) -> None:
        package = self.sign()
        self.file_path.write_text("lecture one changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "file contents do not match"):
            verify_file(self.file_path, package, self.public_key)

    def test_untrusted_fingerprint(self) -> None:
        package = self.sign()
        other = Ed25519PrivateKey.generate().public_key()
        self.assertNotEqual(fingerprint(other), package["signer_fingerprint"])
        with self.assertRaisesRegex(ValueError, "fingerprint does not match"):
            verify_file(self.file_path, package, other)

    def test_metadata_mismatch(self) -> None:
        package = self.sign()
        metadata = {
            "title": "Wrong title",
            "course_code": "CS101",
            "academic_year": "2026",
            "sha256": package["file_sha256"],
        }
        with self.assertRaisesRegex(ValueError, "course details"):
            verify_file(self.file_path, package, self.public_key, metadata)


if __name__ == "__main__":
    unittest.main()
