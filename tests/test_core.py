"""Core library checks that do not need a window or browser."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import EduBridgeCore, default_app_dir


class PathTests(unittest.TestCase):
    def test_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"EDUBRIDGE_HOME": folder}):
                self.assertEqual(default_app_dir(), Path(folder))


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.core = EduBridgeCore(app_dir=Path(self.temp.name), port=45991, discovery_port=45992)

    def tearDown(self) -> None:
        self.core.close()
        self.temp.cleanup()

    def test_save_profile_and_add_file(self) -> None:
        teacher = self.core.register("ada", "password12", "Ada Teacher", "T1", "CS", "Teacher")
        self.core.save_profile("Ada Teacher", "T1", "CS", "Teacher", teacher)
        result = self.core.add_uploaded_file("notes.txt", b"hello", "Lecture 1", "CS101", "2026", teacher)
        self.assertEqual(result["file_name"], "notes.txt")
        rows = self.core.list_resources()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Lecture 1")
        self.assertEqual(rows[0]["signature"], "Unsigned")

    def test_edit_clears_signature_json_when_unsigned_stays(self) -> None:
        teacher = self.core.register("ada", "password12", "Ada Teacher", "T1", "CS", "Teacher")
        self.core.add_uploaded_file("notes.txt", b"hello", "Lecture 1", "CS101", "2026", teacher)
        resource_id = self.core.list_resources(user_id=teacher)[0]["id"]
        changed = self.core.update_resource(resource_id, "Lecture 1b", "CS101", "2026", teacher)
        self.assertFalse(changed["cleared_signature"])
        self.assertEqual(self.core.list_resources(user_id=teacher)[0]["title"], "Lecture 1b")

    def test_send_to_another_account(self) -> None:
        teacher = self.core.register("ada", "password12", "Ada Teacher", "T1", "CS", "Teacher")
        student = self.core.register("sam", "password12", "Sam Student", "S1", "CS", "Student")
        self.core.add_uploaded_file("notes.txt", b"official notes", "Lecture 1", "CS101", "2026", teacher)
        resource_id = self.core.list_resources(user_id=teacher)[0]["id"]
        key = self.core.create_signing_key("passphrase-1", "passphrase-1", user_id=teacher)
        self.core.sign_resource(resource_id, "passphrase-1", teacher)
        self.core.import_trusted_key(key["public_key_pem"].encode(), "Ada Teacher", True, student)
        self.core.send_to_username(resource_id, "sam", teacher)
        waiting = self.core.inbox(student)
        self.assertEqual(len(waiting), 1)
        self.core.decide_share(waiting[0]["id"], True, student)
        rows = self.core.list_resources(user_id=student)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["signature"], "Signed")

    def test_send_signing_key_requires_confirmation(self) -> None:
        teacher = self.core.register("ada", "password12", "Ada Teacher", "T1", "CS", "Teacher")
        student = self.core.register("sam", "password12", "Sam Student", "S1", "CS", "Student")
        key = self.core.create_signing_key("passphrase-1", "passphrase-1", user_id=teacher)
        sent = self.core.send_signing_key("sam", teacher)
        self.assertEqual(sent["fingerprint"], key["fingerprint"])
        waiting = self.core.key_inbox(student)
        self.assertEqual(waiting[0]["fingerprint"], key["fingerprint"])
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.core.decide_key_offer(waiting[0]["id"], True, False, student)
        self.assertEqual(self.core.trusted_instructors(student), [])
        self.core.decide_key_offer(waiting[0]["id"], True, True, student)
        self.assertEqual(self.core.trusted_instructors(student)[0]["fingerprint"], key["fingerprint"])
        self.assertEqual(self.core.key_inbox(student), [])

    def test_change_sign_in(self) -> None:
        user = self.core.register("ada", "password12", "Ada Teacher", "T1", "CS", "Teacher")
        self.core.register("sam", "password12", "Sam Student", "S1", "CS", "Student")
        updated = self.core.change_sign_in("password12", "ada_t", "new-password", "new-password", user)
        self.assertEqual(updated["username"], "ada_t")
        self.assertEqual(self.core.login("ada_t", "new-password")["id"], user)
        with self.assertRaises(ValueError):
            self.core.login("ada", "password12")
        with self.assertRaisesRegex(ValueError, "incorrect"):
            self.core.change_sign_in("password12", "ada_t", "", "", user)
        with self.assertRaisesRegex(ValueError, "8 characters"):
            self.core.change_sign_in("new-password", "ada_t", "short", "short", user)
        with self.assertRaisesRegex(ValueError, "taken"):
            self.core.change_sign_in("new-password", "sam", "", "", user)

    def test_phone_url_is_reported(self) -> None:
        self.core.phone_url = "http://192.168.137.1:8080"
        self.assertEqual(self.core.share_status()["phone_url"], "http://192.168.137.1:8080")

    def test_search_other_accounts(self) -> None:
        teacher = self.core.register("ada", "password12", "Ada Teacher", "T1", "CS", "Teacher")
        self.core.register("sam", "password12", "Sam Student", "S1", "CS", "Student")
        matches = self.core.search_users("sam", teacher)
        self.assertEqual([item["username"] for item in matches], ["sam"])
        self.assertEqual(self.core.search_users("ada", teacher), [])
        self.assertEqual(self.core.search_users("s", teacher), [])


if __name__ == "__main__":
    unittest.main()
