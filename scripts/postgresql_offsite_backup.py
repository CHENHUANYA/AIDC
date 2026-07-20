from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import struct
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scripts.postgresql_backup import (
    BACKUP_ROOT,
    load_manifest,
    manifest_integrity,
    resolve_backup,
)


ROOT = Path(__file__).resolve().parents[1]
LOCAL_OFFSITE_ROOT = ROOT / "backups" / "postgresql-offsite-local"
LOCAL_KEY_ROOT = ROOT / "backups" / "postgresql-offsite-local-keys"
MAGIC = b"ARPGBAK1"
FORMAT_VERSION = 1
HEADER_LENGTH_SIZE = 4
MAX_HEADER_BYTES = 64 * 1024
NONCE_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 32
CHUNK_BYTES = 1024 * 1024
DIRECTORY_PUBLISH_ATTEMPTS = 5
DIRECTORY_PUBLISH_RETRY_SECONDS = 0.05
IS_WINDOWS = os.name == "nt"


class EncryptedBackupError(RuntimeError):
    pass


def publish_verified_directory(source: Path, destination: Path) -> None:
    """Atomically publish a verified directory, tolerating transient Windows locks."""
    for attempt in range(DIRECTORY_PUBLISH_ATTEMPTS):
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite restored backup: {destination}")
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if not IS_WINDOWS or attempt + 1 >= DIRECTORY_PUBLISH_ATTEMPTS:
                raise
            time.sleep(DIRECTORY_PUBLISH_RETRY_SECONDS * (2**attempt))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def generate_key_file(path: Path) -> dict[str, str]:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite encryption key: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    encoded = base64.b64encode(key).decode("ascii") + "\n"
    path.write_text(encoded, encoding="ascii")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {"path": str(path), "key_id": key_id(key)}


def load_key_file(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"Encryption key not found: {path}")
    try:
        key = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Encryption key must be valid base64") from exc
    if len(key) != KEY_BYTES:
        raise ValueError(f"Encryption key must decode to {KEY_BYTES} bytes")
    return key


def encode_header(header: dict[str, Any]) -> tuple[bytes, bytes]:
    payload = json.dumps(header, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if not payload or len(payload) > MAX_HEADER_BYTES:
        raise ValueError("Encrypted backup header size is invalid")
    prefix = MAGIC + struct.pack(">I", len(payload))
    return prefix + payload, payload


def read_header(source: BinaryIO) -> tuple[dict[str, Any], bytes, int]:
    magic = source.read(len(MAGIC))
    if magic != MAGIC:
        raise EncryptedBackupError("Encrypted backup magic is invalid")
    raw_length = source.read(HEADER_LENGTH_SIZE)
    if len(raw_length) != HEADER_LENGTH_SIZE:
        raise EncryptedBackupError("Encrypted backup header length is truncated")
    length = struct.unpack(">I", raw_length)[0]
    if length < 2 or length > MAX_HEADER_BYTES:
        raise EncryptedBackupError("Encrypted backup header length is invalid")
    payload = source.read(length)
    if len(payload) != length:
        raise EncryptedBackupError("Encrypted backup header is truncated")
    try:
        header = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EncryptedBackupError("Encrypted backup header JSON is invalid") from exc
    if not isinstance(header, dict):
        raise EncryptedBackupError("Encrypted backup header must be an object")
    aad = MAGIC + raw_length + payload
    return header, aad, len(aad)


def validate_header(header: dict[str, Any]) -> bytes:
    if header.get("version") != FORMAT_VERSION:
        raise EncryptedBackupError(f"Unsupported encrypted backup version: {header.get('version')!r}")
    if header.get("algorithm") != "AES-256-GCM":
        raise EncryptedBackupError(f"Unsupported encryption algorithm: {header.get('algorithm')!r}")
    try:
        nonce = base64.b64decode(str(header["nonce_b64"]), validate=True)
    except (KeyError, ValueError) as exc:
        raise EncryptedBackupError("Encrypted backup nonce is invalid") from exc
    if len(nonce) != NONCE_BYTES:
        raise EncryptedBackupError("Encrypted backup nonce length is invalid")
    source_sha = header.get("plaintext_sha256")
    source_bytes = header.get("plaintext_bytes")
    if not isinstance(source_sha, str) or len(source_sha) != 64:
        raise EncryptedBackupError("Encrypted backup plaintext SHA-256 is invalid")
    if not isinstance(source_bytes, int) or isinstance(source_bytes, bool) or source_bytes < 0:
        raise EncryptedBackupError("Encrypted backup plaintext size is invalid")
    return nonce


def encrypt_file(source: Path, destination: Path, key: bytes, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if len(key) != KEY_BYTES:
        raise ValueError(f"AES-256 key must be {KEY_BYTES} bytes")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite encrypted backup: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = {
        "version": FORMAT_VERSION,
        "algorithm": "AES-256-GCM",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "key_id": key_id(key),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "plaintext_bytes": source.stat().st_size,
        "plaintext_sha256": sha256_file(source),
        "metadata": metadata or {},
    }
    aad, _ = encode_header(header)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    try:
        with source.open("rb") as plaintext, temporary.open("xb") as encrypted:
            encrypted.write(aad)
            for block in iter(lambda: plaintext.read(CHUNK_BYTES), b""):
                encrypted.write(encryptor.update(block))
            encrypted.write(encryptor.finalize())
            encrypted.write(encryptor.tag)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "path": str(destination),
        "algorithm": header["algorithm"],
        "key_id": header["key_id"],
        "plaintext_bytes": header["plaintext_bytes"],
        "plaintext_sha256": header["plaintext_sha256"],
        "artifact_bytes": destination.stat().st_size,
        "artifact_sha256": sha256_file(destination),
    }


def decrypt_file(source: Path, destination: Path, key: bytes) -> dict[str, Any]:
    if len(key) != KEY_BYTES:
        raise ValueError(f"AES-256 key must be {KEY_BYTES} bytes")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite decrypted backup: {destination}")
    total_size = source.stat().st_size
    with source.open("rb") as encrypted:
        header, aad, ciphertext_start = read_header(encrypted)
        nonce = validate_header(header)
        ciphertext_bytes = total_size - ciphertext_start - TAG_BYTES
        if ciphertext_bytes < 0:
            raise EncryptedBackupError("Encrypted backup ciphertext is truncated")
        encrypted.seek(total_size - TAG_BYTES)
        tag = encrypted.read(TAG_BYTES)
        if len(tag) != TAG_BYTES:
            raise EncryptedBackupError("Encrypted backup authentication tag is truncated")
        encrypted.seek(ciphertext_start)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(aad)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.unverified")
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("xb") as plaintext:
                remaining = ciphertext_bytes
                while remaining:
                    block = encrypted.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        raise EncryptedBackupError("Encrypted backup ciphertext is truncated")
                    remaining -= len(block)
                    clear = decryptor.update(block)
                    plaintext.write(clear)
                    digest.update(clear)
                    written += len(clear)
                final = decryptor.finalize()
                plaintext.write(final)
                digest.update(final)
                written += len(final)
            if written != header["plaintext_bytes"] or digest.hexdigest() != header["plaintext_sha256"]:
                raise EncryptedBackupError("Decrypted backup does not match authenticated plaintext metadata")
            os.replace(temporary, destination)
        except InvalidTag as exc:
            temporary.unlink(missing_ok=True)
            raise EncryptedBackupError("Encrypted backup authentication failed") from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    return {
        "path": str(destination),
        "bytes": written,
        "sha256": digest.hexdigest(),
        "header": header,
    }


def validate_source_backup(backup_dir: Path) -> dict[str, Any]:
    manifest = load_manifest(backup_dir)
    integrity = manifest_integrity(backup_dir, manifest)
    if not all(integrity[key] for key in ("dump_exists", "checksum", "size")):
        raise EncryptedBackupError(f"Source PostgreSQL backup integrity failed: {integrity}")
    return manifest


def bundle_backup(backup_dir: Path, bundle_path: Path) -> dict[str, Any]:
    manifest = validate_source_backup(backup_dir)
    files = sorted(path for path in backup_dir.rglob("*") if path.is_file())
    if not files:
        raise EncryptedBackupError("Source PostgreSQL backup is empty")
    with zipfile.ZipFile(bundle_path, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in files:
            archive.write(path, path.relative_to(backup_dir).as_posix())
    return {
        "files": len(files),
        "bytes": bundle_path.stat().st_size,
        "sha256": sha256_file(bundle_path),
        "source_manifest": manifest,
    }


def verify_bundle(bundle_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(bundle_path, "r") as archive:
        bad_member = archive.testzip()
        names = set(archive.namelist())
        if bad_member:
            raise EncryptedBackupError(f"Backup bundle CRC failed: {bad_member}")
        if not {"manifest.json", "database.dump"} <= names:
            raise EncryptedBackupError("Backup bundle is missing manifest.json or database.dump")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        digest = hashlib.sha256()
        total = 0
        with archive.open("database.dump", "r") as dump:
            for block in iter(lambda: dump.read(CHUNK_BYTES), b""):
                digest.update(block)
                total += len(block)
        checks = {
            "zip_crc": True,
            "dump_sha256": digest.hexdigest() == manifest.get("sha256"),
            "dump_bytes": total == manifest.get("bytes"),
            "restore_list_recorded": int(manifest.get("restore_list_entries") or 0) > 0,
        }
        if not all(checks.values()):
            raise EncryptedBackupError(f"Decrypted backup bundle verification failed: {checks}")
        return {"checks": checks, "members": len(names), "manifest": manifest}


def safe_extract_bundle(bundle_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(bundle_path, "r") as archive:
        for member in archive.infolist():
            mode = (member.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise EncryptedBackupError(f"Backup bundle contains a symbolic link: {member.filename}")
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise EncryptedBackupError(f"Backup bundle path escapes destination: {member.filename}") from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=CHUNK_BYTES)


def restore_encrypted_backup(artifact: Path, key_path: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite restored backup: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.resolve().relative_to(BACKUP_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Restored PostgreSQL backup must be under {BACKUP_ROOT.resolve()}") from exc
    key = load_key_file(key_path)
    temporary_output = output.parent / f".{output.name}.{uuid.uuid4().hex}.part"
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="alarm-rag-decryption-", dir=BACKUP_ROOT) as staging:
            bundle = Path(staging) / "bundle.zip"
            decrypted = decrypt_file(artifact, bundle, key)
            verification = verify_bundle(bundle)
            temporary_output.mkdir(parents=False, exist_ok=False)
            safe_extract_bundle(bundle, temporary_output)
        publish_verified_directory(temporary_output, output)
    except Exception:
        if temporary_output.exists():
            shutil.rmtree(temporary_output)
        raise
    return {
        "status": "ok",
        "artifact": str(artifact),
        "artifact_sha256": sha256_file(artifact),
        "output": str(output),
        "decrypted_sha256": decrypted["sha256"],
        "checks": verification["checks"],
    }


def next_artifact_path(destination: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = destination / f"postgresql_{stamp}.arpgbak"
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = destination / f"postgresql_{stamp}_{suffix:03d}.arpgbak"
    return candidate


def rehearse(backup: str, key_path: Path, destination: Path) -> dict[str, Any]:
    backup_dir = resolve_backup(backup)
    key = load_key_file(key_path)
    destination.mkdir(parents=True, exist_ok=True)
    artifact = next_artifact_path(destination)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="alarm-rag-encryption-", dir=BACKUP_ROOT) as staging:
        staging_dir = Path(staging)
        plaintext_bundle = staging_dir / "bundle.zip"
        verified_bundle = staging_dir / "verified.zip"
        bundle = bundle_backup(backup_dir, plaintext_bundle)
        encrypted = encrypt_file(
            plaintext_bundle,
            artifact,
            key,
            {"source_backup": backup_dir.name, "bundle_sha256": bundle["sha256"]},
        )
        decrypted = decrypt_file(artifact, verified_bundle, key)
        verification = verify_bundle(verified_bundle)
        checks = {
            "encrypted_artifact": artifact.is_file(),
            "authenticated_decryption": decrypted["sha256"] == bundle["sha256"],
            "bundle_verified": all(verification["checks"].values()),
            "plaintext_staging_removed": True,
        }
        report = {
            "status": "ok" if all(checks.values()) else "fail",
            "environment": "local",
            "scope": "local_encryption_rehearsal",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "encrypted": True,
            "remote": False,
            "immutable": False,
            "restore_verified": False,
            "bundle_restore_verified": all(verification["checks"].values()),
            "database_restore_verified": False,
            "key_managed_externally": False,
            "retention_lock_verified": False,
            "separate_failure_domain": False,
            "artifact": str(artifact),
            "artifact_sha256": encrypted["artifact_sha256"],
            "artifact_bytes": encrypted["artifact_bytes"],
            "algorithm": encrypted["algorithm"],
            "key_id": encrypted["key_id"],
            "source_backup": str(backup_dir),
            "source_bundle_sha256": bundle["sha256"],
            "source_bundle_bytes": bundle["bytes"],
            "checks": checks,
            "bundle_verification": verification["checks"],
        }
        manifest_path = artifact.with_suffix(artifact.suffix + ".manifest.json")
        manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Authenticated local PostgreSQL backup encryption rehearsal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    key_parser = subparsers.add_parser("keygen")
    key_parser.add_argument("--output", default=str(LOCAL_KEY_ROOT / "rehearsal.key"))

    rehearsal_parser = subparsers.add_parser("rehearse")
    rehearsal_parser.add_argument("--backup", default="")
    rehearsal_parser.add_argument("--key-file", default=str(LOCAL_KEY_ROOT / "rehearsal.key"))
    rehearsal_parser.add_argument("--destination", default=str(LOCAL_OFFSITE_ROOT))
    rehearsal_parser.add_argument(
        "--report",
        default=str(ROOT / "exports" / "postgresql_offsite_backup_local_rehearsal.json"),
    )
    restore_parser = subparsers.add_parser("restore-bundle")
    restore_parser.add_argument("--artifact", required=True)
    restore_parser.add_argument("--key-file", default=str(LOCAL_KEY_ROOT / "rehearsal.key"))
    restore_parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.command == "keygen":
        result = generate_key_file(Path(args.output))
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "restore-bundle":
        try:
            report = restore_encrypted_backup(
                Path(args.artifact),
                Path(args.key_file),
                Path(args.output),
            )
        except Exception as exc:
            report = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ok" else 1

    try:
        report = rehearse(args.backup, Path(args.key_file), Path(args.destination))
    except Exception as exc:
        report = {
            "status": "fail",
            "environment": "local",
            "scope": "local_encryption_rehearsal",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "encrypted": False,
            "remote": False,
            "immutable": False,
            "restore_verified": False,
            "bundle_restore_verified": False,
            "database_restore_verified": False,
            "key_managed_externally": False,
            "retention_lock_verified": False,
            "separate_failure_domain": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    write_report(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
