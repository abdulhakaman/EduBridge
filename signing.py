"""Ed25519 signing helpers for official EduBridge course resources."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(public_key: Ed25519PublicKey) -> str:
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def public_key_pem(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def create_teacher_key(private_path: Path, password: str) -> tuple[bytes, str]:
    private_key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    ))
    public_key = private_key.public_key()
    return public_key_pem(public_key), fingerprint(public_key)


def load_teacher_key(private_path: Path, password: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(private_path.read_bytes(), password=password.encode("utf-8"))
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("The saved key is not an Ed25519 signing key.")
    return key


def load_ed25519_public_key(pem_data: bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem_data)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("The selected file is not an Ed25519 public key.")
    return key


def _signed_fields(metadata: dict) -> dict:
    return {
        "schema": 1,
        "title": str(metadata["title"]),
        "course_code": str(metadata["course_code"]),
        "academic_year": str(metadata["academic_year"]),
        "file_sha256": str(metadata["file_sha256"]).lower(),
        "signer_name": str(metadata["signer_name"]),
        "signer_fingerprint": str(metadata["signer_fingerprint"]).lower(),
    }


def _canonical_bytes(fields: dict) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_file(path: Path, title: str, course_code: str, academic_year: str, signer_name: str, private_key: Ed25519PrivateKey) -> dict:
    public = private_key.public_key()
    fields = {
        "title": title,
        "course_code": course_code,
        "academic_year": academic_year,
        "file_sha256": file_sha256(path),
        "signer_name": signer_name,
        "signer_fingerprint": fingerprint(public),
    }
    signature = private_key.sign(_canonical_bytes(_signed_fields(fields)))
    return {**fields, "schema": 1, "signature": base64.b64encode(signature).decode("ascii")}


def verify_signature_claim(metadata: dict, signature_package: dict, public_key: Ed25519PublicKey) -> None:
    """Verify signed course metadata; the transport separately checks the file hash."""
    if signature_package.get("schema") != 1:
        raise ValueError("Unsupported signature format.")
    fields = _signed_fields(signature_package)
    for field in ("title", "course_code", "academic_year"):
        if str(metadata.get(field, "")) != fields[field]:
            raise ValueError("The signed course details do not match the transfer request.")
    if str(metadata.get("sha256", "")).lower() != fields["file_sha256"]:
        raise ValueError("The signed file hash does not match the transfer request.")
    if fingerprint(public_key) != fields["signer_fingerprint"]:
        raise ValueError("The instructor key fingerprint does not match.")
    signature = base64.b64decode(signature_package["signature"], validate=True)
    try:
        public_key.verify(signature, _canonical_bytes(fields))
    except InvalidSignature as exc:
        raise ValueError("The instructor signature is invalid.") from exc


def verify_file(path: Path, signature_package: dict, public_key: Ed25519PublicKey, metadata: dict | None = None) -> None:
    if file_sha256(path) != str(signature_package.get("file_sha256", "")).lower():
        raise ValueError("The file contents do not match the signed hash.")
    claim = metadata if metadata is not None else {
        "title": signature_package.get("title", ""),
        "course_code": signature_package.get("course_code", ""),
        "academic_year": signature_package.get("academic_year", ""),
        "sha256": signature_package.get("file_sha256", ""),
    }
    verify_signature_claim(claim, signature_package, public_key)

