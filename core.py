"""Shared EduBridge library, profile, signing, and transfer logic."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from lan_share import DISCOVERY_PORT, PORT, LanShare, parse_peer_address
from signing import (
    create_teacher_key,
    fingerprint,
    load_ed25519_public_key,
    load_teacher_key,
    sign_file,
    verify_file,
    verify_signature_claim,
)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


def default_app_dir() -> Path:
    override = os.environ.get("EDUBRIDGE_HOME", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EduBridge"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "EduBridge"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "EduBridge"


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return salt, digest


class EduBridgeCore:
    def __init__(self, app_dir: Path | None = None, port: int = PORT, discovery_port: int = DISCOVERY_PORT) -> None:
        self.app_dir = app_dir or default_app_dir()
        self.app_dir.mkdir(parents=True, exist_ok=True)
        (self.app_dir / "Library").mkdir(exist_ok=True)
        (self.app_dir / "Received").mkdir(exist_ok=True)
        (self.app_dir / "Shares").mkdir(exist_ok=True)
        (self.app_dir / "keys").mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.app_dir / "edubridge.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.network = LanShare(self.app_dir / "Received", port=port, discovery_port=discovery_port)
        self.discovered_peers: dict[str, tuple] = {}
        self.status_by_user: dict[int, str] = {}
        self.alerts_by_user: dict[int, list] = {}
        self.progress = 0
        self.transfer_busy = False
        self.pending_incoming: list[dict] = []
        self.public_url = ""
        self.public_url_error = ""
        self.public_requested = False
        self.public_stable = False
        self.create_tables()

    def create_tables(self) -> None:
        with self._lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                academic_id TEXT NOT NULL,
                department TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Student', 'Teacher'))
            )""")
            user_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(users)")}
            if "signing_fingerprint" not in user_columns:
                self.db.execute("ALTER TABLE users ADD COLUMN signing_fingerprint TEXT")
            self.db.execute("""CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                full_name TEXT NOT NULL,
                academic_id TEXT NOT NULL,
                department TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Student', 'Teacher'))
            )""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                course_code TEXT NOT NULL,
                academic_year TEXT NOT NULL,
                file_path TEXT NOT NULL UNIQUE,
                added_at TEXT NOT NULL
            )""")
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(resources)")}
            if "signature_json" not in columns:
                self.db.execute("ALTER TABLE resources ADD COLUMN signature_json TEXT")
            if "user_id" not in columns:
                self.db.execute("ALTER TABLE resources ADD COLUMN user_id INTEGER")
            self.db.execute("""CREATE TABLE IF NOT EXISTS trusted_instructors (
                fingerprint TEXT PRIMARY KEY,
                instructor_name TEXT NOT NULL,
                public_key_pem BLOB NOT NULL,
                added_at TEXT NOT NULL
            )""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS trusted_keys (
                user_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                instructor_name TEXT NOT NULL,
                public_key_pem BLOB NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY(user_id, fingerprint)
            )""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS key_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                instructor_name TEXT NOT NULL,
                public_key_pem BLOB NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'declined')),
                created_at TEXT NOT NULL
            )""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                course_code TEXT NOT NULL,
                academic_year TEXT NOT NULL,
                file_path TEXT NOT NULL,
                signature_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'declined')),
                created_at TEXT NOT NULL
            )""")
        self._migrate_legacy_user()

    def _migrate_legacy_user(self) -> None:
        with self._lock:
            users = self.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            profile = self.db.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if users or not profile:
            return
        user_id = self.register("local", "changeme-local", profile["full_name"], profile["academic_id"], profile["department"], profile["role"])
        with self._lock, self.db:
            self.db.execute("UPDATE resources SET user_id = ? WHERE user_id IS NULL", (user_id,))
            for row in self.db.execute("SELECT * FROM trusted_instructors"):
                self.db.execute(
                    "INSERT OR REPLACE INTO trusted_keys(user_id, fingerprint, instructor_name, public_key_pem, added_at) VALUES(?, ?, ?, ?, ?)",
                    (user_id, row["fingerprint"], row["instructor_name"], row["public_key_pem"], row["added_at"]),
                )

    def close(self) -> None:
        self.network.cancel_send()
        self.network.stop()
        with self._lock:
            self.db.close()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _set_status(self, user_id: int | None, message: str) -> None:
        if user_id is None:
            return
        self.status_by_user[user_id] = message

    def _alert(self, user_id: int | None, level: str, title: str, message: str) -> None:
        if user_id is None:
            return
        self.alerts_by_user.setdefault(user_id, []).append({"level": level, "title": title, "message": message})

    def _unique_path(self, folder: Path, name: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        candidate = folder / Path(name).name
        stem, suffix = candidate.stem, candidate.suffix
        index = 1
        while candidate.exists():
            candidate = folder / f"{stem} ({index}){suffix}"
            index += 1
        return candidate

    def _uid(self, user_id: int | None) -> int:
        if user_id is not None:
            return int(user_id)
        with self._lock:
            rows = self.db.execute("SELECT id FROM users ORDER BY id").fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
        if not rows:
            return self.register("local", "changeme-local", "Local User", "local", "General", "Student")
        raise ValueError("Sign in first.")

    def register(self, username: str, password: str, full_name: str, academic_id: str, department: str, role: str) -> int:
        username = username.strip().lower()
        if not USERNAME_RE.match(username):
            raise ValueError("Username must be 3-32 letters, numbers, or underscores.")
        if not password or len(password) < 8:
            raise ValueError("Use a password with at least 8 characters.")
        values = [full_name.strip(), academic_id.strip(), department.strip()]
        if not all(values):
            raise ValueError("Enter your name, academic ID, and department.")
        if role not in ("Student", "Teacher"):
            raise ValueError("Role must be Student or Teacher.")
        salt, digest = _hash_password(password)
        try:
            with self._lock, self.db:
                cursor = self.db.execute(
                    """INSERT INTO users(username, password_salt, password_hash, full_name, academic_id, department, role)
                    VALUES(?, ?, ?, ?, ?, ?, ?)""",
                    (username, salt, digest, *values, role),
                )
                user_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            raise ValueError("That username is already taken.") from None
        self._set_status(user_id, "Account created. You can use EduBridge from any network.")
        return user_id

    def login(self, username: str, password: str) -> dict:
        username = username.strip().lower()
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise ValueError("Incorrect username or password.")
        _, digest = _hash_password(password, row["password_salt"])
        if not secrets.compare_digest(digest, row["password_hash"]):
            raise ValueError("Incorrect username or password.")
        return self.user_public(row["id"])

    def change_sign_in(self, current_password: str, username: str = "", new_password: str = "", confirmation: str = "", user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("Sign in first.")
        _, digest = _hash_password(current_password or "", row["password_salt"])
        if not secrets.compare_digest(digest, row["password_hash"]):
            raise ValueError("Current password is incorrect.")
        next_username = (username or row["username"]).strip().lower()
        if not USERNAME_RE.match(next_username):
            raise ValueError("Username must be 3-32 letters, numbers, or underscores.")
        if new_password or confirmation:
            if len(new_password) < 8:
                raise ValueError("Use a password with at least 8 characters.")
            if new_password != confirmation:
                raise ValueError("The new passwords do not match.")
            salt, new_digest = _hash_password(new_password)
        else:
            salt, new_digest = row["password_salt"], row["password_hash"]
        try:
            with self._lock, self.db:
                self.db.execute(
                    "UPDATE users SET username = ?, password_salt = ?, password_hash = ? WHERE id = ?",
                    (next_username, salt, new_digest, uid),
                )
        except sqlite3.IntegrityError:
            raise ValueError("That username is already taken.") from None
        self._set_status(uid, "Sign-in details updated.")
        return self.user_public(uid)

    def user_public(self, user_id: int) -> dict:
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("Sign in first.")
        return {
            "id": row["id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "academic_id": row["academic_id"],
            "department": row["department"],
            "role": row["role"],
        }

    def find_user(self, username: str):
        with self._lock:
            return self.db.execute("SELECT * FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()

    def search_users(self, query: str, user_id: int | None = None) -> list[dict]:
        uid = self._uid(user_id)
        query = query.strip().lower()
        if len(query) < 2:
            return []
        like = f"%{query}%"
        with self._lock:
            rows = self.db.execute(
                """SELECT username, full_name, role FROM users
                WHERE id != ? AND (username LIKE ? OR lower(full_name) LIKE ?)
                ORDER BY username LIMIT 8""",
                (uid, like, like),
            ).fetchall()
        return [{"username": row["username"], "full_name": row["full_name"], "role": row["role"]} for row in rows]

    def profile(self, user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        return self.user_public(uid)

    def save_profile(self, full_name: str, academic_id: str, department: str, role: str, user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        values = [full_name.strip(), academic_id.strip(), department.strip()]
        if not all(values):
            raise ValueError("Enter your name, academic ID, and department.")
        if role not in ("Student", "Teacher"):
            raise ValueError("Role must be Student or Teacher.")
        with self._lock, self.db:
            self.db.execute(
                "UPDATE users SET full_name = ?, academic_id = ?, department = ?, role = ? WHERE id = ?",
                (*values, role, uid),
            )
        self._set_status(uid, "Profile saved. You can open this account from any device.")
        return self.profile(uid)

    def resource_signature_view(self, row) -> tuple[str, str]:
        raw = row["signature_json"]
        if not raw:
            return "Unsigned", ""
        try:
            package = json.loads(raw)
        except json.JSONDecodeError:
            return "Invalid", ""
        return "Signed", str(package.get("signer_name", ""))

    def list_resources(self, query: str = "", user_id: int | None = None) -> list[dict]:
        uid = self._uid(user_id)
        query = query.strip()
        with self._lock:
            rows = self.db.execute(
                """SELECT * FROM resources WHERE user_id = ? AND (title LIKE ? OR course_code LIKE ? OR academic_year LIKE ?)
                ORDER BY course_code, title""",
                (uid, f"%{query}%", f"%{query}%", f"%{query}%"),
            ).fetchall()
        items = []
        for row in rows:
            status, signer = self.resource_signature_view(row)
            items.append({
                "id": row["id"],
                "title": row["title"],
                "course_code": row["course_code"],
                "academic_year": row["academic_year"],
                "file_name": Path(row["file_path"]).name,
                "signature": status,
                "signer": signer,
            })
        return items

    def get_resource(self, resource_id: int, user_id: int | None = None):
        uid = self._uid(user_id)
        with self._lock:
            return self.db.execute("SELECT * FROM resources WHERE id = ? AND user_id = ?", (resource_id, uid)).fetchone()

    def add_uploaded_file(self, filename: str, data: bytes, title: str, course: str, year: str, user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        title, course, year = title.strip(), course.strip(), year.strip()
        if not all((title, course, year)):
            raise ValueError("Enter a title, course code, and academic year.")
        name = Path(filename or "resource").name
        if not name or name in (".", ".."):
            raise ValueError("Invalid file name.")
        dest = self._unique_path(self.app_dir / "Library" / str(uid), name)
        dest.write_bytes(data)
        try:
            with self._lock, self.db:
                self.db.execute(
                    "INSERT INTO resources(user_id, title, course_code, academic_year, file_path, added_at) VALUES(?, ?, ?, ?, ?, ?)",
                    (uid, title, course, year, str(dest), self._now()),
                )
        except sqlite3.IntegrityError:
            raise ValueError("That file is already listed in your library.") from None
        self._set_status(uid, f"Added {dest.name} to your library.")
        return {"ok": True, "file_name": dest.name}

    def update_resource(self, resource_id: int, title: str, course: str, year: str, user_id: int | None = None) -> dict:
        row = self.get_resource(resource_id, user_id)
        if not row:
            raise ValueError("Choose a resource from the list first.")
        title, course, year = title.strip(), course.strip(), year.strip()
        if not all((title, course, year)):
            raise ValueError("Enter a title, course code, and academic year.")
        if (title, course, year) == (row["title"], row["course_code"], row["academic_year"]):
            self._set_status(row["user_id"], "Course details are unchanged.")
            return {"cleared_signature": False}
        signed = bool(row["signature_json"])
        with self._lock, self.db:
            if signed:
                self.db.execute(
                    "UPDATE resources SET title = ?, course_code = ?, academic_year = ?, signature_json = NULL WHERE id = ?",
                    (title, course, year, row["id"]),
                )
            else:
                self.db.execute(
                    "UPDATE resources SET title = ?, course_code = ?, academic_year = ? WHERE id = ?",
                    (title, course, year, row["id"]),
                )
        if signed:
            self._set_status(row["user_id"], "Course details saved. The instructor signature was cleared because those details are part of the signature.")
        else:
            self._set_status(row["user_id"], "Course details saved.")
        return {"cleared_signature": signed}

    def remove_resource(self, resource_id: int, user_id: int | None = None) -> None:
        row = self.get_resource(resource_id, user_id)
        if not row:
            raise ValueError("Choose a resource from the list first.")
        with self._lock, self.db:
            self.db.execute("DELETE FROM resources WHERE id = ?", (row["id"],))
        self._set_status(row["user_id"], "Resource removed from the library.")

    def resource_path(self, resource_id: int, user_id: int | None = None) -> Path:
        row = self.get_resource(resource_id, user_id)
        if not row:
            raise ValueError("Choose a resource from the list first.")
        path = Path(row["file_path"])
        if not path.is_file():
            raise FileNotFoundError("The selected resource file is not available at its saved location.")
        return path

    def verify_official_file(self, path: Path, package: dict, title: str, course_code: str, academic_year: str, user_id: int | None = None) -> None:
        trusted = self.trusted_key_for_signature(package, user_id)
        if not trusted:
            raise ValueError("This account does not trust the instructor key that signed this resource.")
        _trusted_row, public_key = trusted
        metadata = {"title": title, "course_code": course_code, "academic_year": academic_year, "sha256": str(package.get("file_sha256", ""))}
        verify_file(path, package, public_key, metadata)

    def trusted_instructors(self, user_id: int | None = None) -> list[dict]:
        uid = self._uid(user_id)
        with self._lock:
            rows = self.db.execute("SELECT fingerprint, instructor_name FROM trusted_keys WHERE user_id = ? ORDER BY instructor_name", (uid,)).fetchall()
        return [{"fingerprint": row["fingerprint"], "instructor_name": row["instructor_name"]} for row in rows]

    def trusted_key_for_signature(self, signature_package: dict, user_id: int | None = None):
        uid = self._uid(user_id)
        key_fingerprint = str(signature_package.get("signer_fingerprint", "")).lower()
        with self._lock:
            row = self.db.execute("SELECT * FROM trusted_keys WHERE user_id = ? AND fingerprint = ?", (uid, key_fingerprint)).fetchone()
        if not row:
            return None
        return row, load_ed25519_public_key(bytes(row["public_key_pem"]))

    def _key_path(self, user_id: int) -> Path:
        return self.app_dir / "keys" / f"teacher_{user_id}.pem"

    def create_signing_key(self, password: str, confirmation: str, replace: bool = False, user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        profile = self.profile(uid)
        if profile["role"] != "Teacher":
            raise ValueError("Set your profile role to Teacher before creating an instructor signing key.")
        name = profile["full_name"].strip()
        if not name:
            raise ValueError("Save your instructor name in My profile first.")
        if not password or len(password) < 8:
            raise ValueError("Use a passphrase with at least 8 characters.")
        if confirmation != password:
            raise ValueError("The passphrases do not match.")
        private_path = self._key_path(uid)
        if private_path.exists() and not replace:
            raise FileExistsError("A signing key already exists. Confirm replacement to continue.")
        pem, key_fingerprint = create_teacher_key(private_path, password)
        with self._lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO trusted_keys(user_id, fingerprint, instructor_name, public_key_pem, added_at) VALUES(?, ?, ?, ?, ?)",
                (uid, key_fingerprint, name, pem, self._now()),
            )
            self.db.execute("UPDATE users SET signing_fingerprint = ? WHERE id = ?", (key_fingerprint, uid))
        self._set_status(uid, "Signing key created. Share only the public key.")
        return {"fingerprint": key_fingerprint, "public_key_pem": pem.decode("utf-8"), "instructor_name": name}

    def sign_resource(self, resource_id: int, password: str, user_id: int | None = None) -> None:
        uid = self._uid(user_id)
        profile = self.profile(uid)
        if profile["role"] != "Teacher":
            raise ValueError("Only an instructor profile can sign official resources.")
        row = self.get_resource(resource_id, uid)
        if not row:
            raise ValueError("Choose a resource from the list first.")
        path = Path(row["file_path"])
        if not path.is_file():
            raise FileNotFoundError("The selected resource file is not available at its saved location.")
        if not password:
            raise ValueError("Enter your private key passphrase.")
        private_key = load_teacher_key(self._key_path(uid), password)
        package = sign_file(path, row["title"], row["course_code"], row["academic_year"], profile["full_name"].strip(), private_key)
        with self._lock, self.db:
            self.db.execute("UPDATE resources SET signature_json = ? WHERE id = ?", (json.dumps(package), row["id"]))
        self._set_status(uid, f"Signed {path.name} as official course material.")

    def preview_trusted_key(self, pem: bytes) -> dict:
        key = load_ed25519_public_key(pem)
        return {"fingerprint": fingerprint(key)}

    def import_trusted_key(self, pem: bytes, instructor_name: str, confirmed: bool, user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        if not confirmed:
            raise ValueError("Compare the fingerprint with the instructor before trusting this key.")
        name = instructor_name.strip()
        if not name:
            raise ValueError("Enter the instructor's name.")
        info = self.preview_trusted_key(pem)
        with self._lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO trusted_keys(user_id, fingerprint, instructor_name, public_key_pem, added_at) VALUES(?, ?, ?, ?, ?)",
                (uid, info["fingerprint"], name, pem, self._now()),
            )
        self._set_status(uid, f"{name} can now send files signed with this key.")
        return {"fingerprint": info["fingerprint"], "instructor_name": name}

    def _visible_signing_fingerprint(self, user_id: int) -> str:
        try:
            fingerprint_value, _name, _pem = self._signing_public_key(user_id)
        except ValueError:
            return ""
        return fingerprint_value

    def _signing_public_key(self, user_id: int) -> tuple[str, str, bytes]:
        profile = self.profile(user_id)
        if profile["role"] != "Teacher":
            raise ValueError("Set your profile role to Teacher before sharing a signing key.")
        with self._lock:
            row = self.db.execute("SELECT signing_fingerprint FROM users WHERE id = ?", (user_id,)).fetchone()
        fingerprint_value = (row["signing_fingerprint"] or "") if row else ""
        if not fingerprint_value and self._key_path(user_id).is_file():
            with self._lock:
                keys = self.db.execute("SELECT fingerprint, instructor_name FROM trusted_keys WHERE user_id = ?", (user_id,)).fetchall()
            named = [item for item in keys if item["instructor_name"] == profile["full_name"]]
            chosen = named[0] if len(named) == 1 else (keys[0] if len(keys) == 1 else None)
            if chosen:
                fingerprint_value = chosen["fingerprint"]
                with self._lock, self.db:
                    self.db.execute("UPDATE users SET signing_fingerprint = ? WHERE id = ?", (fingerprint_value, user_id))
        if not fingerprint_value:
            raise ValueError("Create a signing key before sending it.")
        with self._lock:
            key = self.db.execute(
                "SELECT instructor_name, public_key_pem FROM trusted_keys WHERE user_id = ? AND fingerprint = ?",
                (user_id, fingerprint_value),
            ).fetchone()
        if not key:
            raise ValueError("Create a signing key before sending it.")
        pem = key["public_key_pem"]
        if isinstance(pem, str):
            pem = pem.encode("utf-8")
        return fingerprint_value, key["instructor_name"], bytes(pem)

    def send_signing_key(self, recipient_username: str, user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        recipient = self.find_user(recipient_username)
        if not recipient:
            raise ValueError("No EduBridge account has that username.")
        if recipient["id"] == uid:
            raise ValueError("Choose another person's username.")
        fingerprint_value, name, pem = self._signing_public_key(uid)
        with self._lock, self.db:
            existing = self.db.execute(
                "SELECT id FROM key_offers WHERE sender_id = ? AND recipient_id = ? AND status = 'pending'",
                (uid, recipient["id"]),
            ).fetchone()
            if existing:
                self.db.execute(
                    "UPDATE key_offers SET fingerprint = ?, instructor_name = ?, public_key_pem = ?, created_at = ? WHERE id = ?",
                    (fingerprint_value, name, pem, self._now(), existing["id"]),
                )
            else:
                self.db.execute(
                    """INSERT INTO key_offers(sender_id, recipient_id, fingerprint, instructor_name, public_key_pem, status, created_at)
                    VALUES(?, ?, ?, ?, ?, 'pending', ?)""",
                    (uid, recipient["id"], fingerprint_value, name, pem, self._now()),
                )
        self._set_status(uid, f"Sent your public key to {recipient['username']}. They must compare the fingerprint before trusting it.")
        self._alert(recipient["id"], "info", "Instructor key waiting", f"{self.profile(uid)['full_name']} sent a public key. Compare the fingerprint before trusting it.")
        return {"ok": True, "recipient": recipient["username"], "fingerprint": fingerprint_value}

    def key_inbox(self, user_id: int | None = None) -> list[dict]:
        uid = self._uid(user_id)
        with self._lock:
            rows = self.db.execute(
                """SELECT key_offers.id, key_offers.fingerprint, key_offers.instructor_name,
                users.username AS sender_username, users.full_name AS sender_name
                FROM key_offers JOIN users ON users.id = key_offers.sender_id
                WHERE recipient_id = ? AND status = 'pending' ORDER BY key_offers.id DESC""",
                (uid,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "fingerprint": row["fingerprint"],
                "instructor_name": row["instructor_name"],
                "sender_username": row["sender_username"],
                "sender_name": row["sender_name"],
            }
            for row in rows
        ]

    def decide_key_offer(self, offer_id: int, approved: bool, confirmed: bool, user_id: int | None = None) -> None:
        uid = self._uid(user_id)
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM key_offers WHERE id = ? AND recipient_id = ? AND status = 'pending'",
                (offer_id, uid),
            ).fetchone()
        if not row:
            raise ValueError("There is no instructor key waiting for approval.")
        if not approved:
            with self._lock, self.db:
                self.db.execute("UPDATE key_offers SET status = 'declined' WHERE id = ?", (offer_id,))
            self._set_status(uid, "Instructor key declined.")
            return
        if not confirmed:
            raise ValueError("Compare the fingerprint with the instructor before trusting this key.")
        pem = row["public_key_pem"]
        if isinstance(pem, str):
            pem = pem.encode("utf-8")
        self.import_trusted_key(bytes(pem), row["instructor_name"], True, uid)
        with self._lock, self.db:
            self.db.execute("UPDATE key_offers SET status = 'accepted' WHERE id = ?", (offer_id,))

    def remove_trusted_key(self, fingerprint_value: str, user_id: int | None = None) -> None:
        uid = self._uid(user_id)
        with self._lock, self.db:
            self.db.execute("DELETE FROM trusted_keys WHERE user_id = ? AND fingerprint = ?", (uid, fingerprint_value))
        self._set_status(uid, "Stopped trusting that instructor key.")

    def toggle_sharing(self) -> dict:
        if self.network.running:
            self.network.stop()
            return self.share_status()
        self.network.start()
        return self.share_status()

    def share_status(self) -> dict:
        return {
            "running": self.network.running,
            "local_ip": self.network.local_ip(),
            "port": self.network.port,
            "discovery_port": self.network.discovery_port,
            "public_url": self.public_url,
            "public_error": self.public_url_error,
            "public_pending": self.public_requested and not self.public_url and not self.public_url_error,
            "public_stable": self.public_stable,
        }

    def send_resource(self, resource_id: int, address: str, user_id: int | None = None) -> None:
        uid = self._uid(user_id)
        row = self.get_resource(resource_id, uid)
        if not row:
            raise ValueError("Choose a resource from the list first.")
        if not row["signature_json"]:
            raise ValueError("Only resources signed by an instructor can be shared as official course materials.")
        host, dest_port = parse_peer_address(address, self.network.port)
        path = Path(row["file_path"])
        if not path.is_file():
            raise FileNotFoundError("The selected resource file is not available at its saved location.")
        package = json.loads(row["signature_json"])
        self.verify_official_file(path, package, row["title"], row["course_code"], row["academic_year"], uid)
        self.progress = 0
        self.transfer_busy = True
        self.network.send_file(host, path, row["title"], row["course_code"], row["academic_year"], package, dest_port=dest_port)
        self._set_status(uid, f"Connecting to {host}:{dest_port}...")

    def send_to_username(self, resource_id: int, recipient_username: str, user_id: int | None = None) -> dict:
        uid = self._uid(user_id)
        recipient = self.find_user(recipient_username)
        if not recipient:
            raise ValueError("No EduBridge account has that username.")
        if recipient["id"] == uid:
            raise ValueError("Choose another person's username.")
        row = self.get_resource(resource_id, uid)
        if not row:
            raise ValueError("Choose a resource from the list first.")
        if not row["signature_json"]:
            raise ValueError("Only resources signed by an instructor can be shared as official course materials.")
        path = Path(row["file_path"])
        if not path.is_file():
            raise FileNotFoundError("The selected resource file is not available at its saved location.")
        package = json.loads(row["signature_json"])
        self.verify_official_file(path, package, row["title"], row["course_code"], row["academic_year"], uid)
        dest = self._unique_path(self.app_dir / "Shares", path.name)
        shutil.copy2(path, dest)
        with self._lock, self.db:
            self.db.execute(
                """INSERT INTO shares(sender_id, recipient_id, title, course_code, academic_year, file_path, signature_json, status, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (uid, recipient["id"], row["title"], row["course_code"], row["academic_year"], str(dest), json.dumps(package), self._now()),
            )
        self._set_status(uid, f"Sent {path.name} to {recipient['username']}. They can open it from any network.")
        self._alert(recipient["id"], "info", "Official file waiting", f"{self.profile(uid)['full_name']} sent {row['title']}.")
        return {"ok": True, "recipient": recipient["username"]}

    def inbox(self, user_id: int | None = None) -> list[dict]:
        uid = self._uid(user_id)
        with self._lock:
            rows = self.db.execute(
                """SELECT shares.*, users.username AS sender_username, users.full_name AS sender_name
                FROM shares JOIN users ON users.id = shares.sender_id
                WHERE recipient_id = ? AND status = 'pending' ORDER BY id DESC""",
                (uid,),
            ).fetchall()
        items = []
        for row in rows:
            package = json.loads(row["signature_json"])
            items.append({
                "id": row["id"],
                "title": row["title"],
                "course": row["course_code"],
                "year": row["academic_year"],
                "name": Path(row["file_path"]).name,
                "sender_username": row["sender_username"],
                "sender_name": row["sender_name"],
                "instructor_name": package.get("signer_name", row["sender_name"]),
                "size_mb": round(Path(row["file_path"]).stat().st_size / (1024 * 1024), 1) if Path(row["file_path"]).is_file() else 0,
            })
        return items

    def decide_share(self, share_id: int, approved: bool, user_id: int | None = None) -> None:
        uid = self._uid(user_id)
        with self._lock:
            row = self.db.execute("SELECT * FROM shares WHERE id = ? AND recipient_id = ? AND status = 'pending'", (share_id, uid)).fetchone()
        if not row:
            raise ValueError("There is no transfer waiting for approval.")
        if not approved:
            with self._lock, self.db:
                self.db.execute("UPDATE shares SET status = 'declined' WHERE id = ?", (share_id,))
            self._set_status(uid, "Incoming file declined.")
            return
        saved = Path(row["file_path"])
        package = json.loads(row["signature_json"])
        try:
            self.verify_official_file(saved, package, row["title"], row["course_code"], row["academic_year"], uid)
        except Exception as exc:
            with self._lock, self.db:
                self.db.execute("UPDATE shares SET status = 'declined' WHERE id = ?", (share_id,))
            raise ValueError(f"The received file was rejected. {exc}") from exc
        dest = self._unique_path(self.app_dir / "Library" / str(uid), saved.name)
        shutil.copy2(saved, dest)
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO resources(user_id, title, course_code, academic_year, file_path, added_at, signature_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (uid, row["title"], row["course_code"], row["academic_year"], str(dest), self._now(), row["signature_json"]),
            )
            self.db.execute("UPDATE shares SET status = 'accepted' WHERE id = ?", (share_id,))
        self._set_status(uid, f"Received verified course file {dest.name}.")

    def cancel_send(self) -> None:
        self.network.cancel_send()

    def decide_incoming(self, approved: bool) -> None:
        if not self.pending_incoming:
            raise ValueError("There is no transfer waiting for approval.")
        item = self.pending_incoming.pop(0)
        item["decision"]["approved"] = bool(approved)
        item["approval"].set()

    def _refresh_peers(self) -> list[dict]:
        now = time.time()
        self.discovered_peers = {key: entry for key, entry in self.discovered_peers.items() if now - entry[1] < 7}
        peers = []
        for key, (name, _seen, ip, port) in sorted(self.discovered_peers.items()):
            address = ip if port == self.network.port else f"{ip}:{port}"
            peers.append({"id": key, "name": name, "address": address})
        return peers

    def drain_events(self) -> None:
        while True:
            try:
                kind, data = self.network.events.get_nowait()
            except queue.Empty:
                return
            if kind == "incoming":
                self._handle_incoming(data)
            elif kind == "progress":
                self.progress = data["percent"]
            elif kind == "peer":
                port = int(data.get("port") or self.network.port)
                self.discovered_peers[f"{data['ip']}:{port}"] = (data["name"], time.time(), data["ip"], port)
            elif kind == "received":
                self._handle_received(data)
            elif kind == "sent":
                self.transfer_busy = False
                self.progress = 100
            elif kind == "error":
                self.transfer_busy = False

    def _handle_incoming(self, data: dict) -> None:
        request = data["request"]
        package = request.get("signature")
        try:
            user_id = self._uid(None)
        except ValueError:
            data["decision"]["approved"] = False
            data["approval"].set()
            return
        trusted = self.trusted_key_for_signature(package, user_id) if isinstance(package, dict) else None
        if not trusted:
            data["decision"]["approved"] = False
            data["approval"].set()
            self._alert(user_id, "warning", "Untrusted instructor", "This file was rejected because its instructor signing key is not trusted.")
            return
        trusted_row, public_key = trusted
        signed_metadata = {
            "title": request.get("title", ""),
            "course_code": request.get("course", ""),
            "academic_year": request.get("year", ""),
            "sha256": request.get("sha256", ""),
        }
        try:
            verify_signature_claim(signed_metadata, package, public_key)
        except Exception as exc:
            data["decision"]["approved"] = False
            data["approval"].set()
            self._alert(user_id, "error", "Invalid instructor signature", str(exc))
            return
        size_mb = request["size"] / (1024 * 1024)
        self.pending_incoming.append({
            "request": request,
            "address": data.get("address", ""),
            "approval": data["approval"],
            "decision": data["decision"],
            "instructor_name": trusted_row["instructor_name"],
            "size_mb": round(size_mb, 1),
            "user_id": user_id,
        })

    def _handle_received(self, data: dict) -> None:
        request = data["request"]
        saved = Path(data["path"])
        package = request.get("signature")
        try:
            user_id = self._uid(None)
            if not isinstance(package, dict):
                raise ValueError("The transfer did not include an instructor signature.")
            self.verify_official_file(saved, package, str(request.get("title", "")), str(request.get("course", "")), str(request.get("year", "")), user_id)
        except Exception as exc:
            try:
                saved.unlink(missing_ok=True)
            except OSError:
                pass
            self.transfer_busy = False
            self.progress = 0
            self._alert(None, "error", "Verification failed", str(exc))
            return
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO resources(user_id, title, course_code, academic_year, file_path, added_at, signature_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (user_id, str(request.get("title", saved.stem)), str(request.get("course", "Uncategorized")), str(request.get("year", "")), data["path"], self._now(), json.dumps(package)),
            )
        self.transfer_busy = False
        self.progress = 100
        self._set_status(user_id, f"Received verified course file {saved.name} from {data['address']}.")

    def snapshot(self, query: str = "", user_id: int | None = None) -> dict:
        self.drain_events()
        uid = self._uid(user_id)
        alerts = self.alerts_by_user.get(uid, [])
        self.alerts_by_user[uid] = []
        pending = None
        inbox = self.inbox(uid)
        if self.pending_incoming:
            item = self.pending_incoming[0]
            request = item["request"]
            pending = {
                "id": None,
                "title": request.get("title", ""),
                "course": request.get("course", ""),
                "year": request.get("year", ""),
                "name": request.get("name", ""),
                "instructor_name": item["instructor_name"],
                "size_mb": item["size_mb"],
                "sender_username": item.get("address", ""),
                "sender_name": item.get("address", ""),
            }
        return {
            "user": self.profile(uid),
            "profile": self.profile(uid),
            "resources": self.list_resources(query, uid),
            "trusted": self.trusted_instructors(uid),
            "key_inbox": self.key_inbox(uid),
            "signing_fingerprint": self._visible_signing_fingerprint(uid),
            "peers": self._refresh_peers(),
            "inbox": inbox,
            "share": self.share_status(),
            "status": self.status_by_user.get(uid, "Ready - open EduBridge from any network with this account."),
            "progress": self.progress,
            "transfer_busy": self.transfer_busy,
            "pending_incoming": pending,
            "alerts": alerts,
            "platform": sys.platform,
        }
