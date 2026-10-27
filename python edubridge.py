"""EduBridge Windows desktop starter app.

Step 1-3: local profile and searchable resource library backed by SQLite.
Network transfer is intentionally a later milestone.
"""
from __future__ import annotations

import os
import queue
import sqlite3
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from lan_share import LanShare

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EduBridge"
DB_PATH = APP_DIR / "edubridge.sqlite3"


class EduBridge:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("EduBridge - Offline Course Library")
        self.root.geometry("1020x680")
        self.root.minsize(780, 520)
        self.root.configure(bg="#f4f7f6")
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH)
        self.db.row_factory = sqlite3.Row
        self.network = LanShare(APP_DIR / 'Received')
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
        self.tabs.add(self.library_tab, text="  Course library  ")
        self.tabs.add(self.profile_tab, text="  My profile  ")
        self.tabs.add(self.sharing_tab, text="  Share over Wi-Fi  ")
        self.build_library()
        self.build_profile()
        self.build_sharing()

        self.status = tk.StringVar(value="Ready - files and profile are saved on this computer.")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=(10, 5)).pack(fill="x", side="bottom")

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
        ttk.Button(controls, text="Clear", command=lambda: self.search_var.set("")).pack(side="left", padx=(8, 0))

        columns = ("title", "course", "year", "file")
        self.resource_table = ttk.Treeview(self.library_tab, columns=columns, show="headings", selectmode="browse")
        for col, label, width in [("title", "Resource title", 300), ("course", "Course", 130), ("year", "Academic year", 130), ("file", "File name", 280)]:
            self.resource_table.heading(col, text=label)
            self.resource_table.column(col, width=width, anchor="w")
        self.resource_table.pack(fill="both", expand=True)
        self.resource_table.bind("<Double-1>", lambda _event: self.open_selected())

        actions = ttk.Frame(self.library_tab)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Open selected file", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text="Remove from library", command=self.remove_selected).pack(side="left", padx=8)
        self.count_label = ttk.Label(actions, text="0 resources", style="Sub.TLabel")
        self.count_label.pack(side="right")

        self.notice = ttk.Label(self.library_tab, text="This first version stores a local file reference. Nearby sharing will be added in a later step.", style="Sub.TLabel", wraplength=850)
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
        ttk.Label(self.sharing_tab, text="Share with a nearby computer", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(self.sharing_tab, text="Both computers must run EduBridge and use the same Wi-Fi network.", style="Sub.TLabel").pack(anchor="w", pady=(0, 16))
        self.network_status = tk.StringVar(value="Sharing is stopped.")
        ttk.Label(self.sharing_tab, textvariable=self.network_status, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.ip_label = ttk.Label(self.sharing_tab, text=f"This computer's Wi-Fi address: {self.network.local_ip()}   |   Port: {self.network.port}", style="Sub.TLabel")
        self.ip_label.pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(self.sharing_tab)
        row.pack(anchor="w", fill="x")
        self.server_button = ttk.Button(row, text="Start sharing", command=self.toggle_sharing)
        self.server_button.pack(side="left")
        ttk.Separator(self.sharing_tab).pack(fill="x", pady=20)
        ttk.Label(self.sharing_tab, text="Send a library file", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 10))
        send_row = ttk.Frame(self.sharing_tab)
        send_row.pack(fill="x")
        ttk.Label(send_row, text="Receiver IP address").pack(side="left", padx=(0, 8))
        self.peer_ip_var = tk.StringVar()
        ttk.Entry(send_row, textvariable=self.peer_ip_var, width=25).pack(side="left")
        ttk.Button(send_row, text="Send selected library file", command=self.send_selected).pack(side="left", padx=10)
        ttk.Label(self.sharing_tab, text="Select a file in Course library first. The receiving computer will ask before accepting it.", style="Sub.TLabel", wraplength=820).pack(anchor="w", pady=(12, 0))
        ttk.Label(self.sharing_tab, text="Prototype limitation: transfer works on a trusted private Wi-Fi network. The SHA-256 check detects file corruption; instructor signatures and peer authentication are not implemented yet.", wraplength=850, foreground="#8a4b08").pack(anchor="w", pady=(22, 0))

    def toggle_sharing(self) -> None:
        if self.network.running:
            self.network.stop()
            self.network_status.set("Sharing is stopped.")
            self.server_button.configure(text="Start sharing")
            self.status.set("Stopped accepting incoming files.")
            return
        try:
            self.network.start()
        except OSError as exc:
            messagebox.showerror("Could not start sharing", f"{exc}\n\nCheck whether another app is using port {self.network.port}, and allow EduBridge through Windows Firewall on private networks.")
            return
        self.network_status.set("Sharing is on. Waiting for an incoming request.")
        self.ip_label.configure(text=f"This computer's Wi-Fi address: {self.network.local_ip()}   |   Port: {self.network.port}")
        self.server_button.configure(text="Stop sharing")
        self.status.set("Ready to receive a file from a computer on the same Wi-Fi.")

    def send_selected(self) -> None:
        row = self.selected_row()
        if not row:
            return
        host = self.peer_ip_var.get().strip()
        if not host:
            messagebox.showwarning("Enter receiver address", "Ask the receiving user for the Wi-Fi address shown on their Share over Wi-Fi tab.")
            return
        path = Path(row["file_path"])
        if not path.exists():
            messagebox.showerror("File not found", f"The selected file is no longer at this location:\n{path}")
            return
        self.network.send_file(host, path, row["title"], row["course_code"], row["academic_year"])
        self.status.set(f"Connecting to {host}...")

    def process_network_events(self) -> None:
        try:
            while True:
                kind, data = self.network.events.get_nowait()
                if kind == "incoming":
                    request = data["request"]
                    size_mb = request["size"] / (1024 * 1024)
                    accepted = messagebox.askyesno("Incoming file", f"{data['address']} wants to send:\n\n{request.get('title', request.get('name'))}\nCourse: {request.get('course', 'Unknown')}\nFile: {request.get('name')} ({size_mb:.1f} MB)\n\nAccept this file?")
                    data["decision"]["approved"] = accepted
                    data["approval"].set()
                elif kind == "received":
                    request = data["request"]
                    with self.db:
                        self.db.execute("INSERT INTO resources(title, course_code, academic_year, file_path, added_at) VALUES(?, ?, ?, ?, ?)", (str(request.get("title", Path(data["path"]).stem)), str(request.get("course", "Uncategorized")), str(request.get("year", "")), data["path"], datetime.now().isoformat(timespec="seconds")))
                    self.refresh_resources()
                    self.status.set(f"Received {Path(data['path']).name} from {data['address']}.")
                    messagebox.showinfo("Transfer complete", f"File saved to your EduBridge Received folder and added to the library:\n{data['path']}")
                elif kind == "sent":
                    self.status.set(data)
                    messagebox.showinfo("Transfer complete", data)
                elif kind == "error":
                    self.status.set("A file transfer did not complete.")
                    messagebox.showerror("Transfer problem", data)
        except queue.Empty:
            pass
        self.root.after(150, self.process_network_events)

    def close_app(self) -> None:
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

    def refresh_resources(self) -> None:
        if not hasattr(self, "resource_table"):
            return
        for item in self.resource_table.get_children():
            self.resource_table.delete(item)
        query = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        rows = self.db.execute("""SELECT * FROM resources WHERE title LIKE ? OR course_code LIKE ? OR academic_year LIKE ? ORDER BY course_code, title""",
                               (f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
        for row in rows:
            self.resource_table.insert("", "end", iid=str(row["id"]), values=(row["title"], row["course_code"], row["academic_year"], Path(row["file_path"]).name))
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
    def __init__(self, parent: tk.Tk, path: Path) -> None:
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.title("Add course material")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Add to course library", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(body, text=f"Selected file: {path.name}", wraplength=440).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.title_var = tk.StringVar(value=path.stem)
        self.course_var = tk.StringVar()
        self.year_var = tk.StringVar(value=str(datetime.now().year))
        for row, (label, variable) in enumerate([("Title", self.title_var), ("Course code", self.course_var), ("Academic year", self.year_var)], start=2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            ttk.Entry(body, textvariable=variable, width=42).grid(row=row, column=1, sticky="ew", pady=6)
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Add resource", command=self.save).pack(side="right")
        self.window.bind("<Return>", lambda _event: self.save())

    def save(self) -> None:
        title, course, year = self.title_var.get().strip(), self.course_var.get().strip(), self.year_var.get().strip()
        if not all((title, course, year)):
            messagebox.showwarning("Missing information", "Enter a title, course code, and academic year.", parent=self.window)
            return
        self.result = (title, course, year)
        self.window.destroy()


def main() -> None:
    root = tk.Tk()
    app = EduBridge(root)
    root.protocol("WM_DELETE_WINDOW", app.close_app)
    root.mainloop()


if __name__ == "__main__":
    main()





