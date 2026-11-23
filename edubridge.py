"""Windows desktop EduBridge app with local storage and signed LAN transfers."""
from __future__ import annotations

import argparse
import json
import os
import queue
import sqlite3
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from signing import create_teacher_key, fingerprint, load_ed25519_public_key, load_teacher_key, sign_file, verify_file, verify_signature_claim

from core import EduBridgeCore, default_app_dir
from lan_share import DISCOVERY_PORT, PORT, LanShare, parse_peer_address


class EduBridge:
    def __init__(self, root: tk.Tk, app_dir: Path | None = None, port: int = PORT, discovery_port: int = DISCOVERY_PORT, window_title: str = "EduBridge - Offline Course Library") -> None:
        self.root = root
        self.app_dir = app_dir or default_app_dir()
        self.root.title(window_title)
        self.root.geometry("1020x680")
        self.root.minsize(780, 520)
        self.root.configure(bg="#f4f7f6")
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.app_dir / "edubridge.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.network = LanShare(self.app_dir / "Received", port=port, discovery_port=discovery_port)
        self.discovered_peers = {}
        self.create_tables()
        self.configure_style()
        self.build_ui()
        self.load_profile()
        self.refresh_resources()
        self.root.after(150, self.process_network_events)

    def create_tables(self) -> None:
        with self.db:
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

        resource_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(resources)")}
        if "signature_json" not in resource_columns:
            self.db.execute("ALTER TABLE resources ADD COLUMN signature_json TEXT")
        self.db.execute("""CREATE TABLE IF NOT EXISTS trusted_instructors (
            fingerprint TEXT PRIMARY KEY,
            instructor_name TEXT NOT NULL,
            public_key_pem BLOB NOT NULL,
            added_at TEXT NOT NULL
        )""")
    def configure_style(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background="#f4f7f6")
        style.configure("Header.TLabel", background="#f4f7f6", foreground="#145c50", font=("Segoe UI", 22, "bold"))
        style.configure("Sub.TLabel", background="#f4f7f6", foreground="#475569", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(10, 6))
        style.configure("Treeview", rowheight=32, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    def build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(24, 18, 24, 8))
        header.pack(fill="x")
        ttk.Label(header, text="EduBridge", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text="Your offline course library", style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(8, 12))
        self.library_tab = ttk.Frame(self.tabs, padding=18)
        self.profile_tab = ttk.Frame(self.tabs, padding=18)
        self.sharing_tab = ttk.Frame(self.tabs, padding=18)
        self.signing_tab = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(self.library_tab, text="  Course library  ")
        self.tabs.add(self.profile_tab, text="  My profile  ")
        self.tabs.add(self.sharing_tab, text="  Share over Wi-Fi  ")
        self.tabs.add(self.signing_tab, text="  Signatures  ")
        self.build_library()
        self.build_profile()
        self.build_sharing()
        self.build_signing()

        self.status = tk.StringVar(value="Ready - files and profile are saved on this computer.")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=(10, 5)).pack(fill="x", side="bottom")
        self.progress_value = tk.DoubleVar(value=0)
        ttk.Progressbar(self.root, variable=self.progress_value, maximum=100).pack(fill="x", side="bottom", padx=8, pady=(0, 5))

    def build_library(self) -> None:
        top = ttk.Frame(self.library_tab)
        top.pack(fill="x", pady=(0, 14))
        ttk.Label(top, text="Course materials", font=("Segoe UI", 17, "bold")).pack(side="left")
        self.add_button = ttk.Button(top, text="＋ Add a file", command=self.add_resource)
        self.add_button.pack(side="right")

        controls = ttk.Frame(self.library_tab)
        controls.pack(fill="x", pady=(0, 12))
        ttk.Label(controls, text="Search").pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        search = ttk.Entry(controls, textvariable=self.search_var, width=42)
        search.pack(side="left", fill="x", expand=True)
        search.bind("<KeyRelease>", lambda _event: self.refresh_resources())
        ttk.Button(controls, text="Clear", command=self.clear_search).pack(side="left", padx=(8, 0))

        columns = ("title", "course", "year", "file", "signature", "signer")
        self.resource_table = ttk.Treeview(self.library_tab, columns=columns, show="headings", selectmode="browse")
        for col, label, width in [("title", "Resource title", 210), ("course", "Course", 90), ("year", "Academic year", 110), ("file", "File name", 170), ("signature", "Status", 90), ("signer", "Instructor", 150)]:
            self.resource_table.heading(col, text=label)
            self.resource_table.column(col, width=width, anchor="w")
        self.resource_table.pack(fill="both", expand=True)
        self.resource_table.bind("<Double-1>", lambda _event: self.open_selected())

        actions = ttk.Frame(self.library_tab)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Open selected file", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text="Edit details", command=self.edit_selected).pack(side="left", padx=8)
        ttk.Button(actions, text="Remove from library", command=self.remove_selected).pack(side="left")
        self.count_label = ttk.Label(actions, text="0 resources", style="Sub.TLabel")
        self.count_label.pack(side="right")

        self.notice = ttk.Label(self.library_tab, text="Files marked Signed have an instructor signature; only trusted signed files can be shared as official material.", style="Sub.TLabel", wraplength=850)
        self.notice.pack(anchor="w", pady=(14, 0))

    def build_profile(self) -> None:
        ttk.Label(self.profile_tab, text="Your profile", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(self.profile_tab, text="Profile details are stored locally on this computer.", style="Sub.TLabel").pack(anchor="w", pady=(0, 18))
        form = ttk.Frame(self.profile_tab)
        form.pack(anchor="nw", fill="x")
        self.name_var = tk.StringVar()
        self.id_var = tk.StringVar()
        self.department_var = tk.StringVar()
        self.role_var = tk.StringVar(value="Student")
        fields = [("Full name", self.name_var), ("Academic ID", self.id_var), ("Department", self.department_var)]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=8, padx=(0, 16))
            ttk.Entry(form, textvariable=variable, width=48).grid(row=row, column=1, sticky="ew", pady=8)
        ttk.Label(form, text="Role").grid(row=3, column=0, sticky="w", pady=8, padx=(0, 16))
        ttk.Combobox(form, textvariable=self.role_var, values=("Student", "Teacher"), state="readonly", width=45).grid(row=3, column=1, sticky="w", pady=8)
        form.columnconfigure(1, weight=1)
        ttk.Button(self.profile_tab, text="Save profile", command=self.save_profile).pack(anchor="w", pady=(18, 0))

    def build_sharing(self) -> None:
        ttk.Label(self.sharing_tab, text="Nearby EduBridge computers", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(self.sharing_tab, text="Start sharing on both computers. They should appear here when connected to the same Wi-Fi. On one computer, open run-teacher.bat and run-student.bat, then send to 127.0.0.1:45882.", style="Sub.TLabel", wraplength=850).pack(anchor="w", pady=(0, 16))
        self.network_status = tk.StringVar(value="Sharing and discovery are stopped.")
        ttk.Label(self.sharing_tab, textvariable=self.network_status, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.ip_label = ttk.Label(self.sharing_tab, text=f"This computer's Wi-Fi address: {self.network.local_ip()}   |   Port: {self.network.port}", style="Sub.TLabel")
        self.ip_label.pack(anchor="w", pady=(0, 12))
        self.server_button = ttk.Button(self.sharing_tab, text="Start sharing and discovery", command=self.toggle_sharing)
        self.server_button.pack(anchor="w")
        self.peer_table = ttk.Treeview(self.sharing_tab, columns=("name", "ip"), show="headings", height=5, selectmode="browse")
        self.peer_table.heading("name", text="Computer")
        self.peer_table.heading("ip", text="Wi-Fi address")
        self.peer_table.column("name", width=400)
        self.peer_table.column("ip", width=250)
        self.peer_table.pack(fill="x", pady=(16, 4))
        self.peer_table.bind("<Double-1>", self.choose_peer)
        ttk.Label(self.sharing_tab, text="Double-click a computer to select it. You can also enter an address such as 192.168.1.20 or 127.0.0.1:45882.", style="Sub.TLabel").pack(anchor="w", pady=(0, 16))
        ttk.Separator(self.sharing_tab).pack(fill="x", pady=(0, 16))
        ttk.Label(self.sharing_tab, text="Send a library file", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 10))
        send_row = ttk.Frame(self.sharing_tab)
        send_row.pack(fill="x")
        ttk.Label(send_row, text="Receiver address").pack(side="left", padx=(0, 8))
        self.peer_ip_var = tk.StringVar()
        ttk.Entry(send_row, textvariable=self.peer_ip_var, width=25).pack(side="left")
        ttk.Button(send_row, text="Send selected library file", command=self.send_selected).pack(side="left", padx=10)
        self.cancel_button = ttk.Button(send_row, text="Cancel transfer", command=self.cancel_transfer, state="disabled")
        self.cancel_button.pack(side="left")
        ttk.Label(self.sharing_tab, text="Select a file in Course library first. The receiving computer will ask before accepting it.", style="Sub.TLabel", wraplength=820).pack(anchor="w", pady=(12, 0))
        ttk.Label(self.sharing_tab, text="Use a trusted private Wi-Fi network. Official transfers require a trusted instructor signature; SHA-256 detects transfer corruption.", wraplength=850, foreground="#8a4b08").pack(anchor="w", pady=(20, 0))

    def build_signing(self) -> None:
        ttk.Label(self.signing_tab, text="Official instructor materials", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(self.signing_tab, text="Teachers sign course files with a private key. Students trust a teacher by importing their public key and checking its fingerprint with that teacher.", style="Sub.TLabel", wraplength=850).pack(anchor="w", pady=(0, 16))
        ttk.Label(self.signing_tab, text="Teacher actions", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        teacher_actions = ttk.Frame(self.signing_tab)
        teacher_actions.pack(anchor="w")
        ttk.Button(teacher_actions, text="Create signing key and export public key", command=self.create_signing_key).pack(side="left")
        ttk.Button(teacher_actions, text="Sign selected library file", command=self.sign_selected_resource).pack(side="left", padx=8)
        ttk.Label(self.signing_tab, text="The private key is encrypted with your passphrase and stays in your local EduBridge data folder. Keep a secure backup; never share the private key.", style="Sub.TLabel", wraplength=850).pack(anchor="w", pady=(10, 18))
        ttk.Separator(self.signing_tab).pack(fill="x", pady=12)
        ttk.Label(self.signing_tab, text="Trusted instructors", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        trust_actions = ttk.Frame(self.signing_tab)
        trust_actions.pack(anchor="w", pady=(0, 10))
        ttk.Button(trust_actions, text="Import instructor public key", command=self.import_trusted_key).pack(side="left")
        ttk.Button(trust_actions, text="Remove selected instructor", command=self.remove_trusted_key).pack(side="left", padx=8)
        self.trusted_table = ttk.Treeview(self.signing_tab, columns=("name", "fingerprint"), show="headings", height=6, selectmode="browse")
        self.trusted_table.heading("name", text="Instructor")
        self.trusted_table.heading("fingerprint", text="SHA-256 key fingerprint")
        self.trusted_table.column("name", width=220)
        self.trusted_table.column("fingerprint", width=600)
        self.trusted_table.pack(fill="both", expand=True)
        ttk.Label(self.signing_tab, text="Always compare the fingerprint with the instructor through a separate trusted channel before importing the key. A signature only proves the file was signed by the matching key.", foreground="#8a4b08", wraplength=850).pack(anchor="w", pady=(12, 0))
        self.refresh_trusted_keys()

    def refresh_trusted_keys(self) -> None:
        if not hasattr(self, "trusted_table"):
            return
        for item in self.trusted_table.get_children():
            self.trusted_table.delete(item)
        for row in self.db.execute("SELECT * FROM trusted_instructors ORDER BY instructor_name"):
            self.trusted_table.insert("", "end", iid=row["fingerprint"], values=(row["instructor_name"], row["fingerprint"]))

    def create_signing_key(self) -> None:
        if self.role_var.get() != "Teacher":
            messagebox.showwarning("Teacher profile required", "Set your profile role to Teacher before creating an instructor signing key.")
            return
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Profile required", "Save your instructor name in My profile first.")
            return
        public_path = filedialog.asksaveasfilename(title="Save instructor public key", defaultextension=".pem", initialfile="edubridge_instructor_public.pem", filetypes=[("PEM public key", "*.pem")])
        if not public_path:
            return
        private_path = self.app_dir / "teacher_signing_key.pem"
        if private_path.exists() and not messagebox.askyesno("Replace signing key?", "Replacing this key means students will need to trust the new public key. Continue?"):
            return
        password = simpledialog.askstring("Protect private key", "Choose a passphrase for your encrypted private signing key:", show="*")
        if not password or len(password) < 8:
            messagebox.showwarning("Passphrase required", "Use a passphrase with at least 8 characters.")
            return
        confirmation = simpledialog.askstring("Confirm passphrase", "Enter the same passphrase again:", show="*")
        if confirmation != password:
            messagebox.showerror("Passphrases do not match", "The signing key was not created.")
            return
        try:
            pem, key_fingerprint = create_teacher_key(private_path, password)
            Path(public_path).write_bytes(pem)
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO trusted_instructors(fingerprint, instructor_name, public_key_pem, added_at) VALUES(?, ?, ?, ?)", (key_fingerprint, name, pem, datetime.now().isoformat(timespec="seconds")))
            self.refresh_trusted_keys()
            messagebox.showinfo("Signing key created", f"Public key saved to:\n{public_path}\n\nFingerprint:\n{key_fingerprint}\n\nShare only the public key. Students must compare the fingerprint with you before trusting it.")
        except Exception as exc:
            messagebox.showerror("Could not create key", str(exc))

    def sign_selected_resource(self) -> None:
        if self.role_var.get() != "Teacher":
            messagebox.showwarning("Teacher profile required", "Only an instructor profile can sign official resources.")
            return
        row = self.selected_row()
        if not row:
            return
        path = Path(row["file_path"])
        if not path.is_file():
            messagebox.showerror("File not found", "The selected resource file is not available at its saved location.")
            return
        password = simpledialog.askstring("Unlock signing key", "Enter your private key passphrase:", show="*")
        if not password:
            return
        try:
            private_key = load_teacher_key(self.app_dir / "teacher_signing_key.pem", password)
            package = sign_file(path, row["title"], row["course_code"], row["academic_year"], self.name_var.get().strip(), private_key)
            with self.db:
                self.db.execute("UPDATE resources SET signature_json = ? WHERE id = ?", (json.dumps(package), row["id"]))
            self.refresh_resources()
            self.status.set(f"Signed {path.name} as official course material.")
            messagebox.showinfo("Resource signed", "The file is signed. Students will need your public key imported and trusted to verify it.")
        except Exception as exc:
            messagebox.showerror("Could not sign resource", f"Check your passphrase and signing key.\n\n{exc}")

    def import_trusted_key(self) -> None:
        path = filedialog.askopenfilename(title="Choose instructor public key", filetypes=[("PEM public key", "*.pem"), ("All files", "*.*")])
        if not path:
            return
        try:
            pem = Path(path).read_bytes()
            key = load_ed25519_public_key(pem)
            key_fingerprint = fingerprint(key)
        except Exception as exc:
            messagebox.showerror("Invalid public key", str(exc))
            return
        if not messagebox.askyesno("Verify instructor key", f"Key fingerprint:\n\n{key_fingerprint}\n\nCompare this fingerprint with the instructor through a separate trusted channel. Have you confirmed it?"):
            return
        name = simpledialog.askstring("Instructor name", "Enter the instructor's name:")
        if not name or not name.strip():
            return
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO trusted_instructors(fingerprint, instructor_name, public_key_pem, added_at) VALUES(?, ?, ?, ?)", (key_fingerprint, name.strip(), pem, datetime.now().isoformat(timespec="seconds")))
        self.refresh_trusted_keys()
        messagebox.showinfo("Instructor trusted", f"{name.strip()} can now send files signed with this key.")

    def remove_trusted_key(self) -> None:
        selection = self.trusted_table.selection()
        if not selection:
            messagebox.showinfo("Select an instructor", "Choose an instructor from the list first.")
            return
        fingerprint_value = selection[0]
        name = self.trusted_table.item(fingerprint_value, "values")[0]
        if not messagebox.askyesno("Remove instructor", f"Stop trusting {name}? Files they signed stay in the library, but they cannot be shared until you import this key again."):
            return
        self.delete_trusted_instructor(fingerprint_value)
        self.status.set(f"Stopped trusting {name}.")

    def delete_trusted_instructor(self, fingerprint_value: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM trusted_instructors WHERE fingerprint = ?", (fingerprint_value,))
        self.refresh_trusted_keys()

    def trusted_key_for_signature(self, signature_package: dict):
        key_fingerprint = str(signature_package.get("signer_fingerprint", "")).lower()
        row = self.db.execute("SELECT * FROM trusted_instructors WHERE fingerprint = ?", (key_fingerprint,)).fetchone()
        if not row:
            return None
        return row, load_ed25519_public_key(bytes(row["public_key_pem"]))
    def choose_peer(self, _event=None) -> None:
        selection = self.peer_table.selection()
        if selection:
            values = self.peer_table.item(selection[0], "values")
            self.peer_ip_var.set(values[1])

    def refresh_peer_rows(self) -> None:
        now = time.time()
        self.discovered_peers = {key: entry for key, entry in self.discovered_peers.items() if now - entry[1] < 7}
        for item in self.peer_table.get_children():
            self.peer_table.delete(item)
        for key, (name, _seen, ip, port) in sorted(self.discovered_peers.items()):
            address = ip if port == self.network.port else f"{ip}:{port}"
            self.peer_table.insert("", "end", iid=key, values=(name, address))
    def toggle_sharing(self) -> None:
        if self.network.running:
            self.network.stop()
            self.network_status.set("Sharing and discovery are stopped.")
            self.server_button.configure(text="Start sharing and discovery")
            self.status.set("Stopped accepting incoming files.")
            return
        try:
            self.network.start()
        except OSError as exc:
            messagebox.showerror("Could not start sharing", f"{exc}\n\nCheck whether another app is using port {self.network.port}, and allow EduBridge through Windows Firewall on private networks.")
            return
        self.network_status.set("Sharing and discovery are on.")
        self.ip_label.configure(text=f"This computer's Wi-Fi address: {self.network.local_ip()}   |   Port: {self.network.port}")
        self.server_button.configure(text="Stop sharing")
        self.status.set("Ready to receive a file from a computer on the same Wi-Fi.")

    def send_selected(self) -> None:
        row = self.selected_row()
        if not row:
            return
        if not row["signature_json"]:
            messagebox.showwarning("Unsigned resource", "Only resources signed by an instructor can be shared as official course materials.")
            return
        host_text = self.peer_ip_var.get().strip()
        if not host_text:
            messagebox.showwarning("Enter receiver address", "Choose a discovered computer or enter its Wi-Fi address, for example 127.0.0.1:45882.")
            return
        try:
            host, dest_port = parse_peer_address(host_text, self.network.port)
        except ValueError as exc:
            messagebox.showwarning("Invalid address", str(exc))
            return
        path = Path(row["file_path"])
        if not path.is_file():
            messagebox.showerror("File not found", "The selected resource file is not available at its saved location.")
            return
        try:
            package = json.loads(row["signature_json"])
            self._verify_official_file(path, package, row["title"], row["course_code"], row["academic_year"])
        except Exception as exc:
            messagebox.showerror("Cannot share resource", f"The resource signature could not be verified. It may have changed since it was signed.\n\n{exc}")
            return
        self.progress_value.set(0)
        self.cancel_button.state(["!disabled"])
        self.network.send_file(host, path, row["title"], row["course_code"], row["academic_year"], package, dest_port=dest_port)
        self.status.set(f"Connecting to {host}:{dest_port}...")

    def cancel_transfer(self) -> None:
        self.network.cancel_send()
        self.status.set("Cancelling the transfer...")

    def _set_transfer_idle(self) -> None:
        if hasattr(self, "cancel_button"):
            self.cancel_button.state(["disabled"])

    def _verify_official_file(self, path: Path, package: dict, title: str, course_code: str, academic_year: str) -> None:
        trusted = self.trusted_key_for_signature(package)
        if not trusted:
            raise ValueError("This computer does not trust the instructor key that signed this resource.")
        _trusted_row, public_key = trusted
        metadata = {"title": title, "course_code": course_code, "academic_year": academic_year, "sha256": str(package.get("file_sha256", ""))}
        verify_file(path, package, public_key, metadata)
    def process_network_events(self) -> None:
        try:
            while True:
                kind, data = self.network.events.get_nowait()
                if kind == "incoming":
                    request = data["request"]
                    package = request.get("signature")
                    trusted = self.trusted_key_for_signature(package) if isinstance(package, dict) else None
                    if not trusted:
                        data["decision"]["approved"] = False
                        data["approval"].set()
                        messagebox.showwarning("Untrusted instructor", "This file was rejected because its instructor signing key is not trusted. Import the instructor's public key and confirm its fingerprint first.")
                        continue
                    trusted_row, public_key = trusted
                    signed_metadata = {"title": request.get("title", ""), "course_code": request.get("course", ""), "academic_year": request.get("year", ""), "sha256": request.get("sha256", "")}
                    try:
                        verify_signature_claim(signed_metadata, package, public_key)
                    except Exception as exc:
                        data["decision"]["approved"] = False
                        data["approval"].set()
                        messagebox.showerror("Invalid instructor signature", f"This file was rejected. The course details or file hash do not match the trusted instructor signature.\n\n{exc}")
                        continue
                    size_mb = request["size"] / (1024 * 1024)
                    accepted = messagebox.askyesno("Verified course file", f"Signature verified for instructor {trusted_row['instructor_name']}.\n\n{request.get('title')}\nCourse: {request.get('course')}\nFile: {request.get('name')} ({size_mb:.1f} MB)\n\nAccept this file?")
                    data["decision"]["approved"] = accepted
                    data["approval"].set()
                elif kind == "progress":
                    self.progress_value.set(data["percent"])
                    self.status.set("{} {}: {}%".format(data["direction"], data["name"], data["percent"]))
                elif kind == "peer":
                    port = int(data.get("port") or self.network.port)
                    self.discovered_peers[f"{data['ip']}:{port}"] = (data["name"], time.time(), data["ip"], port)
                    self.refresh_peer_rows()
                elif kind == "received":
                    request = data["request"]
                    saved = Path(data["path"])
                    package = request.get("signature")
                    try:
                        if not isinstance(package, dict):
                            raise ValueError("The transfer did not include an instructor signature.")
                        self._verify_official_file(saved, package, str(request.get("title", "")), str(request.get("course", "")), str(request.get("year", "")))
                    except Exception as exc:
                        try:
                            saved.unlink(missing_ok=True)
                        except OSError:
                            pass
                        self._set_transfer_idle()
                        self.progress_value.set(0)
                        self.status.set("Received file failed verification and was discarded.")
                        messagebox.showerror("Verification failed", f"The received file was discarded.\n\n{exc}")
                        continue
                    with self.db:
                        self.db.execute("INSERT INTO resources(title, course_code, academic_year, file_path, added_at, signature_json) VALUES(?, ?, ?, ?, ?, ?)", (str(request.get("title", saved.stem)), str(request.get("course", "Uncategorized")), str(request.get("year", "")), data["path"], datetime.now().isoformat(timespec="seconds"), json.dumps(package)))
                    self.refresh_resources()
                    self._set_transfer_idle()
                    self.progress_value.set(100)
                    self.status.set(f"Received verified course file {saved.name} from {data['address']}.")
                    messagebox.showinfo("Verified transfer complete", f"The signed course file was verified and saved in your EduBridge Received folder:\n{data['path']}")
                elif kind == "sent":
                    self._set_transfer_idle()
                    self.progress_value.set(100)
                    self.status.set(data)
                    messagebox.showinfo("Transfer complete", data)
                elif kind == "error":
                    self._set_transfer_idle()
                    self.status.set("A file transfer did not complete.")
                    messagebox.showerror("Transfer problem", data)
        except queue.Empty:
            pass
        self.root.after(150, self.process_network_events)
    def close_app(self) -> None:
        self.network.cancel_send()
        self.network.stop()
        self.db.close()
        self.root.destroy()
    def load_profile(self) -> None:
        row = self.db.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if row:
            self.name_var.set(row["full_name"])
            self.id_var.set(row["academic_id"])
            self.department_var.set(row["department"])
            self.role_var.set(row["role"])

    def save_profile(self) -> None:
        values = [self.name_var.get().strip(), self.id_var.get().strip(), self.department_var.get().strip()]
        if not all(values):
            messagebox.showwarning("Missing information", "Enter your name, academic ID, and department.")
            return
        with self.db:
            self.db.execute("""INSERT INTO profile(id, full_name, academic_id, department, role)
                VALUES(1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
                full_name=excluded.full_name, academic_id=excluded.academic_id,
                department=excluded.department, role=excluded.role""",
                (*values, self.role_var.get()))
        self.status.set("Profile saved on this computer.")
        messagebox.showinfo("Saved", "Your profile has been saved.")

    def add_resource(self) -> None:
        path = filedialog.askopenfilename(title="Choose a course file", filetypes=[("Course materials", "*.pdf *.ppt *.pptx *.doc *.docx *.txt *.mp4 *.zip"), ("All files", "*.*")])
        if not path:
            return
        dialog = ResourceDialog(self.root, Path(path))
        self.root.wait_window(dialog.window)
        if not dialog.result:
            return
        title, course, year = dialog.result
        try:
            with self.db:
                self.db.execute("INSERT INTO resources(title, course_code, academic_year, file_path, added_at) VALUES(?, ?, ?, ?, ?)",
                                (title, course, year, path, datetime.now().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            messagebox.showinfo("Already in library", "That file is already listed in your library.")
            return
        self.refresh_resources()
        self.status.set(f"Added {Path(path).name} to your library.")

    def clear_search(self) -> None:
        self.search_var.set("")
        self.refresh_resources()

    def edit_selected(self) -> None:
        row = self.selected_row()
        if not row:
            return
        dialog = ResourceDialog(self.root, Path(row["file_path"]), initial=(row["title"], row["course_code"], row["academic_year"]), editing=True)
        self.root.wait_window(dialog.window)
        if not dialog.result:
            return
        self.apply_resource_details(row, *dialog.result)

    def apply_resource_details(self, row, title: str, course: str, year: str) -> None:
        if (title, course, year) == (row["title"], row["course_code"], row["academic_year"]):
            self.status.set("Course details are unchanged.")
            return
        signed = bool(row["signature_json"])
        with self.db:
            if signed:
                self.db.execute("UPDATE resources SET title = ?, course_code = ?, academic_year = ?, signature_json = NULL WHERE id = ?", (title, course, year, row["id"]))
            else:
                self.db.execute("UPDATE resources SET title = ?, course_code = ?, academic_year = ? WHERE id = ?", (title, course, year, row["id"]))
        self.refresh_resources()
        if signed:
            self.status.set("Course details saved. The instructor signature was cleared because those details are part of the signature.")
            messagebox.showinfo("Signature removed", "The course details changed, so the instructor signature was cleared. Sign the file again before sharing it.")
            return
        self.status.set("Course details saved.")

    def resource_signature_view(self, row) -> tuple[str, str]:
        raw = row["signature_json"]
        if not raw:
            return "Unsigned", ""
        try:
            package = json.loads(raw)
        except json.JSONDecodeError:
            return "Invalid", ""
        return "Signed", str(package.get("signer_name", ""))

    def refresh_resources(self) -> None:
        if not hasattr(self, "resource_table"):
            return
        for item in self.resource_table.get_children():
            self.resource_table.delete(item)
        query = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        rows = self.db.execute("""SELECT * FROM resources WHERE title LIKE ? OR course_code LIKE ? OR academic_year LIKE ? ORDER BY course_code, title""",
                               (f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
        for row in rows:
            status, signer = self.resource_signature_view(row)
            self.resource_table.insert("", "end", iid=str(row["id"]), values=(row["title"], row["course_code"], row["academic_year"], Path(row["file_path"]).name, status, signer))
        self.count_label.configure(text=f"{len(rows)} resource{'s' if len(rows) != 1 else ''}")

    def selected_row(self):
        selection = self.resource_table.selection()
        if not selection:
            messagebox.showinfo("Select a resource", "Choose a resource from the list first.")
            return None
        return self.db.execute("SELECT * FROM resources WHERE id = ?", (int(selection[0]),)).fetchone()

    def open_selected(self) -> None:
        row = self.selected_row()
        if not row:
            return
        path = Path(row["file_path"])
        if not path.exists():
            messagebox.showerror("File not found", f"This file has moved or been deleted:\n{path}")
            return
        if row["signature_json"]:
            try:
                package = json.loads(row["signature_json"])
                self._verify_official_file(path, package, row["title"], row["course_code"], row["academic_year"])
            except Exception as exc:
                self.resource_table.set(str(row["id"]), "signature", "Invalid")
                self.status.set(f"{path.name} failed signature verification.")
                if not messagebox.askyesno("Signature no longer valid", f"This file does not match its instructor signature.\n\n{exc}\n\nOpen it anyway?"):
                    return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Could not open file", str(exc))

    def remove_selected(self) -> None:
        row = self.selected_row()
        if not row:
            return
        if messagebox.askyesno("Remove resource", f"Remove '{row['title']}' from the library? The original file will stay on your computer."):
            with self.db:
                self.db.execute("DELETE FROM resources WHERE id = ?", (row["id"],))
            self.refresh_resources()
            self.status.set("Resource removed from the library.")


class ResourceDialog:
    def __init__(self, parent: tk.Tk, path: Path, initial: tuple[str, str, str] | None = None, editing: bool = False) -> None:
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.title("Edit course material" if editing else "Add course material")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Edit course details" if editing else "Add to course library", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(body, text=f"Selected file: {path.name}", wraplength=440).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.title_var = tk.StringVar(value=initial[0] if initial else path.stem)
        self.course_var = tk.StringVar(value=initial[1] if initial else "")
        self.year_var = tk.StringVar(value=initial[2] if initial else str(datetime.now().year))
        for row, (label, variable) in enumerate([("Title", self.title_var), ("Course code", self.course_var), ("Academic year", self.year_var)], start=2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            ttk.Entry(body, textvariable=variable, width=42).grid(row=row, column=1, sticky="ew", pady=6)
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Save changes" if editing else "Add resource", command=self.save).pack(side="right")
        self.window.bind("<Return>", lambda _event: self.save())

    def save(self) -> None:
        title, course, year = self.title_var.get().strip(), self.course_var.get().strip(), self.year_var.get().strip()
        if not all((title, course, year)):
            messagebox.showwarning("Missing information", "Enter a title, course code, and academic year.", parent=self.window)
            return
        self.result = (title, course, year)
        self.window.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EduBridge offline course library")
    parser.add_argument("--data-dir", type=Path, help="Folder for the local library, profile, and signing key")
    parser.add_argument("--port", type=int, default=PORT, help="TCP port used to receive files")
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT, help="UDP port used to find nearby computers")
    parser.add_argument("--title", default="EduBridge - Offline Course Library", help="Window title")
    parser.add_argument("--ui", choices=("web", "tk"), default="web", help="web works in any browser; tk is the desktop window")
    parser.add_argument("--web-port", type=int, default=8080, help="Port for the browser interface")
    parser.add_argument("--bind", default="0.0.0.0", help="Address for the browser interface")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    parser.add_argument("--local-only", action="store_true", help="Do not create a public HTTPS address")
    parser.add_argument("--public-url", default="", help="Fixed https address for a saved Cloudflare tunnel")
    parser.add_argument("--tunnel-token", default="", help="Cloudflare tunnel token for --public-url")
    return parser.parse_args(argv)


def run_web_ui(args: argparse.Namespace) -> None:
    import threading
    import webbrowser

    from werkzeug.serving import make_server

    from web_server import create_app

    core = EduBridgeCore(app_dir=args.data_dir, port=args.port, discovery_port=args.discovery_port)
    core.public_requested = not args.local_only
    app = create_app(core)
    app.config["WEB_PORT"] = args.web_port
    server = make_server(args.bind, args.web_port, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, name="edubridge-http", daemon=True)
    server_thread.start()
    local_url = f"http://127.0.0.1:{args.web_port}"
    print(f"EduBridge on this computer: {local_url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(local_url)).start()

    tunnel_box: dict[str, subprocess.Popen | None] = {"process": None}

    def start_tunnel() -> None:
        try:
            from public_tunnel import load_stable_tunnel, start_named_tunnel, start_public_tunnel, wait_for_url

            holder: dict[str, str] = {}
            stable = None
            try:
                stable = load_stable_tunnel(core.app_dir, args.public_url, args.tunnel_token)
            except ValueError as exc:
                print(f"{exc} Using a temporary address instead.", flush=True)

            if stable is not None:
                core.public_url = stable.url
                core.public_stable = True

                def ready() -> None:
                    holder["url"] = stable.url

                print(f"\nFixed address: {stable.url}\n", flush=True)
                process = start_named_tunnel(core.app_dir, stable.token, ready)
                tunnel_box["process"] = process
                wait_for_url(holder, process)
                print("Fixed address is connected.", flush=True)
                return

            def remember(url: str) -> None:
                holder["url"] = url
                core.public_url = url
                core.public_stable = False

            print("Creating a temporary public HTTPS address...", flush=True)
            process = start_public_tunnel(args.web_port, core.app_dir, remember)
            tunnel_box["process"] = process
            url = wait_for_url(holder, process)
            core.public_url = url
            core.public_stable = False
            print(f"\nTemporary address: {url}", flush=True)
            print("This address changes every time EduBridge starts.", flush=True)
            print("For a fixed address, save a Cloudflare tunnel token as tunnel.token", flush=True)
            print("and the https address as public_url.txt next to run.bat.\n", flush=True)
        except Exception as exc:
            core.public_url_error = str(exc)
            print(f"Public HTTPS address was not created ({exc}).", flush=True)
            print("Accounts still work on this computer. Other networks need that public address.", flush=True)

    if core.public_requested:
        threading.Thread(target=start_tunnel, name="edubridge-tunnel", daemon=True).start()

    try:
        while server_thread.is_alive():
            server_thread.join(0.5)
    except KeyboardInterrupt:
        print("\nStopping EduBridge.")
    finally:
        if server_thread.is_alive():
            server.shutdown()
        process = tunnel_box["process"]
        if process is not None and process.poll() is None:
            process.terminate()
        core.close()


def run_tk_ui(args: argparse.Namespace) -> None:
    root = tk.Tk()
    app = EduBridge(root, app_dir=args.data_dir, port=args.port, discovery_port=args.discovery_port, window_title=args.title)
    root.protocol("WM_DELETE_WINDOW", app.close_app)
    root.mainloop()


def main() -> None:
    args = parse_args()
    if args.ui == "tk":
        run_tk_ui(args)
        return
    run_web_ui(args)


if __name__ == "__main__":
    main()













