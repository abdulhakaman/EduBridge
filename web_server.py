"""HTTP UI so phones, tablets, and computers can use EduBridge from any network."""
from __future__ import annotations

import json
import secrets
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory, session

from core import EduBridgeCore

WEB_DIR = Path(__file__).resolve().parent / "web"


def _secret(app_dir: Path) -> str:
    path = app_dir / "secret.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(32)
    path.write_text(value, encoding="utf-8")
    return value


def create_app(core: EduBridgeCore) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.secret_key = _secret(core.app_dir)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    def fail(message: str, status: int = 400):
        return jsonify({"error": str(message)}), status

    def current_user_id():
        return session.get("user_id")

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user_id():
                return fail("Sign in first.", 401)
            return view(*args, **kwargs)
        return wrapped

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_from_directory(WEB_DIR, name)

    @app.post("/api/register")
    def register():
        body = request.get_json(force=True, silent=True) or {}
        try:
            user_id = core.register(
                body.get("username", ""),
                body.get("password", ""),
                body.get("full_name", ""),
                body.get("academic_id", ""),
                body.get("department", ""),
                body.get("role", "Student"),
            )
            session["user_id"] = user_id
            return jsonify(core.user_public(user_id))
        except ValueError as exc:
            return fail(exc)

    @app.post("/api/login")
    def login():
        body = request.get_json(force=True, silent=True) or {}
        try:
            user = core.login(body.get("username", ""), body.get("password", ""))
        except ValueError as exc:
            return fail(exc, 401)
        session["user_id"] = user["id"]
        return jsonify(user)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/state")
    @login_required
    def state():
        return jsonify(core.snapshot(request.args.get("q", ""), current_user_id()))

    @app.post("/api/profile")
    @login_required
    def save_profile():
        body = request.get_json(force=True, silent=True) or {}
        try:
            profile = core.save_profile(body.get("full_name", ""), body.get("academic_id", ""), body.get("department", ""), body.get("role", "Student"), current_user_id())
        except ValueError as exc:
            return fail(exc)
        return jsonify(profile)

    @app.post("/api/resources")
    @login_required
    def add_resource():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return fail("Choose a course file first.")
        try:
            result = core.add_uploaded_file(
                upload.filename,
                upload.read(),
                request.form.get("title", ""),
                request.form.get("course_code", ""),
                request.form.get("academic_year", ""),
                current_user_id(),
            )
        except ValueError as exc:
            return fail(exc)
        return jsonify(result)

    @app.post("/api/resources/<int:resource_id>")
    @login_required
    def update_resource(resource_id: int):
        body = request.get_json(force=True, silent=True) or {}
        try:
            result = core.update_resource(resource_id, body.get("title", ""), body.get("course_code", ""), body.get("academic_year", ""), current_user_id())
        except ValueError as exc:
            return fail(exc)
        return jsonify(result)

    @app.delete("/api/resources/<int:resource_id>")
    @login_required
    def delete_resource(resource_id: int):
        try:
            core.remove_resource(resource_id, current_user_id())
        except ValueError as exc:
            return fail(exc)
        return jsonify({"ok": True})

    @app.get("/api/resources/<int:resource_id>/file")
    @login_required
    def download_resource(resource_id: int):
        try:
            path = core.resource_path(resource_id, current_user_id())
            row = core.get_resource(resource_id, current_user_id())
            if row and row["signature_json"]:
                package = json.loads(row["signature_json"])
                try:
                    core.verify_official_file(path, package, row["title"], row["course_code"], row["academic_year"], current_user_id())
                except Exception as exc:
                    if request.args.get("anyway") != "1":
                        return fail(f"This file does not match its instructor signature. {exc}", 409)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            return fail(exc, 404)
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.post("/api/signing/key")
    @login_required
    def create_key():
        body = request.get_json(force=True, silent=True) or {}
        try:
            result = core.create_signing_key(body.get("password", ""), body.get("confirmation", ""), bool(body.get("replace")), current_user_id())
        except FileExistsError as exc:
            return fail(exc, 409)
        except ValueError as exc:
            return fail(exc)
        response = Response(result["public_key_pem"], mimetype="application/x-pem-file")
        response.headers["X-Key-Fingerprint"] = result["fingerprint"]
        response.headers["Content-Disposition"] = "attachment; filename=edubridge_instructor_public.pem"
        return response

    @app.post("/api/signing/sign/<int:resource_id>")
    @login_required
    def sign_resource(resource_id: int):
        body = request.get_json(force=True, silent=True) or {}
        try:
            core.sign_resource(resource_id, body.get("password", ""), current_user_id())
        except (ValueError, FileNotFoundError) as exc:
            return fail(exc)
        return jsonify({"ok": True})

    @app.post("/api/trust/preview")
    @login_required
    def preview_key():
        upload = request.files.get("file")
        if upload is None:
            return fail("Choose an instructor public key.")
        try:
            return jsonify(core.preview_trusted_key(upload.read()))
        except ValueError as exc:
            return fail(exc)

    @app.post("/api/trust")
    @login_required
    def import_key():
        upload = request.files.get("file")
        if upload is None:
            return fail("Choose an instructor public key.")
        try:
            result = core.import_trusted_key(upload.read(), request.form.get("instructor_name", ""), request.form.get("confirmed") == "1", current_user_id())
        except ValueError as exc:
            return fail(exc)
        return jsonify(result)

    @app.delete("/api/trust/<fingerprint_value>")
    @login_required
    def remove_trust(fingerprint_value: str):
        core.remove_trusted_key(fingerprint_value, current_user_id())
        return jsonify({"ok": True})

    @app.post("/api/share/toggle")
    @login_required
    def toggle_share():
        try:
            return jsonify(core.toggle_sharing())
        except OSError as exc:
            return fail(f"{exc} Check whether another app is using the transfer port, and allow EduBridge through the firewall on private networks.", 500)

    @app.post("/api/share/send")
    @login_required
    def send_file_api():
        body = request.get_json(force=True, silent=True) or {}
        try:
            if body.get("username"):
                return jsonify(core.send_to_username(int(body.get("resource_id")), body.get("username", ""), current_user_id()))
            core.send_resource(int(body.get("resource_id")), body.get("address", ""), current_user_id())
        except (ValueError, FileNotFoundError, TypeError) as exc:
            return fail(exc)
        return jsonify({"ok": True})

    @app.post("/api/share/cancel")
    @login_required
    def cancel_send():
        core.cancel_send()
        return jsonify({"ok": True})

    @app.post("/api/share/incoming")
    @login_required
    def decide_incoming():
        body = request.get_json(force=True, silent=True) or {}
        try:
            if body.get("share_id"):
                core.decide_share(int(body["share_id"]), bool(body.get("approved")), current_user_id())
            else:
                core.decide_incoming(bool(body.get("approved")))
        except ValueError as exc:
            return fail(exc)
        return jsonify({"ok": True})

    @app.get("/api/access")
    def access():
        share = core.share_status()
        web_port = int(app.config.get("WEB_PORT", 8080))
        public_url = share.get("public_url") or ""
        if public_url and share.get("public_stable"):
            hint = "This HTTPS address stays the same each time EduBridge starts. Create an account, then send files to a username."
        elif public_url:
            hint = "This HTTPS address changes when EduBridge restarts. Open it from any phone or network, then send files to a username."
        elif share.get("public_error"):
            hint = "The public HTTPS address was not created. This computer can still sign in. Other networks need that address."
        elif share.get("public_pending"):
            hint = "Creating a public HTTPS address. Keep EduBridge running, then share that address."
        else:
            hint = "Create an account on this computer, then send files to a username."
        return jsonify({
            "public_url": public_url,
            "public_error": share.get("public_error") or "",
            "public_pending": bool(share.get("public_pending")),
            "public_stable": bool(share.get("public_stable")),
            "local_url": f"http://127.0.0.1:{web_port}",
            "hint": hint,
        })

    return app


def run_web(core: EduBridgeCore, host: str = "0.0.0.0", web_port: int = 8080, public_url: str = "") -> None:
    app = create_app(core)
    app.config["WEB_PORT"] = web_port
    app.config["PUBLIC_URL"] = public_url
    print(f"EduBridge on this computer: http://127.0.0.1:{web_port}")
    if public_url:
        print(f"Any network: {public_url}")
    app.run(host=host, port=web_port, threaded=True, use_reloader=False)
