import json
import secrets

import pytest

from scripts import postgresql_offsite_backup as offsite


def test_key_file_generation_and_loading(tmp_path):
    path = tmp_path / "backup.key"

    result = offsite.generate_key_file(path)

    assert result["key_id"] == offsite.key_id(offsite.load_key_file(path))
    with pytest.raises(FileExistsError):
        offsite.generate_key_file(path)


def test_streaming_encryption_round_trip(tmp_path):
    plaintext = tmp_path / "plain.bin"
    encrypted = tmp_path / "backup.arpgbak"
    restored = tmp_path / "restored.bin"
    payload = secrets.token_bytes(offsite.CHUNK_BYTES * 2 + 137)
    plaintext.write_bytes(payload)
    key = secrets.token_bytes(offsite.KEY_BYTES)

    encrypted_report = offsite.encrypt_file(plaintext, encrypted, key, {"kind": "test"})
    decrypted_report = offsite.decrypt_file(encrypted, restored, key)

    assert restored.read_bytes() == payload
    assert encrypted_report["algorithm"] == "AES-256-GCM"
    assert decrypted_report["header"]["metadata"] == {"kind": "test"}
    assert decrypted_report["sha256"] == offsite.sha256_file(plaintext)


def test_tampered_ciphertext_is_rejected_without_publishing_plaintext(tmp_path):
    plaintext = tmp_path / "plain.bin"
    encrypted = tmp_path / "backup.arpgbak"
    restored = tmp_path / "restored.bin"
    plaintext.write_bytes(b"authenticated backup" * 100)
    key = secrets.token_bytes(offsite.KEY_BYTES)
    offsite.encrypt_file(plaintext, encrypted, key)
    tampered = bytearray(encrypted.read_bytes())
    tampered[-offsite.TAG_BYTES - 1] ^= 1
    encrypted.write_bytes(tampered)

    with pytest.raises(offsite.EncryptedBackupError, match="authentication failed"):
        offsite.decrypt_file(encrypted, restored, key)

    assert not restored.exists()
    assert not list(tmp_path.glob("*.unverified"))


def test_wrong_key_is_rejected(tmp_path):
    plaintext = tmp_path / "plain.bin"
    encrypted = tmp_path / "backup.arpgbak"
    restored = tmp_path / "restored.bin"
    plaintext.write_bytes(b"secret")
    offsite.encrypt_file(plaintext, encrypted, secrets.token_bytes(offsite.KEY_BYTES))

    with pytest.raises(offsite.EncryptedBackupError, match="authentication failed"):
        offsite.decrypt_file(encrypted, restored, secrets.token_bytes(offsite.KEY_BYTES))

    assert not restored.exists()


def test_backup_bundle_verifies_inner_dump_manifest(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    dump = backup / "database.dump"
    dump.write_bytes(b"postgresql custom dump")
    manifest = {
        "dump_file": dump.name,
        "bytes": dump.stat().st_size,
        "sha256": offsite.sha256_file(dump),
        "restore_list_entries": 10,
    }
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = tmp_path / "bundle.zip"

    bundled = offsite.bundle_backup(backup, bundle)
    verified = offsite.verify_bundle(bundle)

    assert bundled["files"] == 2
    assert all(verified["checks"].values())


def test_restore_bundle_publishes_verified_postgresql_backup(tmp_path, monkeypatch):
    backup_root = tmp_path / "postgresql"
    monkeypatch.setattr(offsite, "BACKUP_ROOT", backup_root)
    source = backup_root / "source"
    source.mkdir(parents=True)
    dump = source / "database.dump"
    dump.write_bytes(b"custom dump")
    manifest = {
        "dump_file": dump.name,
        "bytes": dump.stat().st_size,
        "sha256": offsite.sha256_file(dump),
        "restore_list_entries": 2,
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = tmp_path / "bundle.zip"
    offsite.bundle_backup(source, bundle)
    key_path = tmp_path / "key"
    offsite.generate_key_file(key_path)
    artifact = tmp_path / "backup.arpgbak"
    offsite.encrypt_file(bundle, artifact, offsite.load_key_file(key_path))
    restored = backup_root / "restored"

    result = offsite.restore_encrypted_backup(artifact, key_path, restored)

    assert result["status"] == "ok"
    assert (restored / "database.dump").read_bytes() == dump.read_bytes()
    assert json.loads((restored / "manifest.json").read_text(encoding="utf-8")) == manifest
