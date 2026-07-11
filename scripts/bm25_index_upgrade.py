from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bm25_text import BM25_TOKENIZER_VERSION, tokenize_bm25


DEFAULT_INDEX_DIR = ROOT / "alarm_db"
DEFAULT_BACKUP_ROOT = ROOT / "backups" / "bm25-index-upgrade"
DEFAULT_REPORT = ROOT / "tests_tmp" / "bm25-index-upgrade" / "report.json"
DEFAULT_MD_REPORT = ROOT / "tests_tmp" / "bm25-index-upgrade" / "report.md"


class IndexUpgradeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return completed.stdout.strip()


def load_trusted_index(path: Path) -> dict[str, Any]:
    """Load a locally generated trusted pickle. Never pass an untrusted file."""
    try:
        with path.open("rb") as file:
            payload = pickle.load(file)
    except Exception as exc:
        raise IndexUpgradeError(f"cannot load trusted index: {exc}") from exc
    if not isinstance(payload, dict):
        raise IndexUpgradeError("index payload must be an object")
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise IndexUpgradeError("index sections must be a non-empty list")
    if any(not isinstance(section, dict) or not isinstance(section.get("text"), str) for section in sections):
        raise IndexUpgradeError("every section must contain text")
    bm25 = payload.get("bm25")
    if bm25 is None or not hasattr(bm25, "get_scores"):
        raise IndexUpgradeError("index bm25 scorer is missing")
    try:
        score_count = len(bm25.get_scores(["upgrade-validation"]))
    except Exception as exc:
        raise IndexUpgradeError(f"index bm25 scorer is invalid: {exc}") from exc
    if score_count != len(sections):
        raise IndexUpgradeError(f"bm25/section count mismatch: {score_count} != {len(sections)}")
    return payload


def upgraded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sections = payload["sections"]
    upgraded = dict(payload)
    upgraded["bm25"] = BM25Okapi([tokenize_bm25(section["text"]) for section in sections])
    upgraded["tokenizer_version"] = BM25_TOKENIZER_VERSION
    return upgraded


def write_pickle_atomic(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            pickle.dump(payload, file)
            file.flush()
            os.fsync(file.fileno())
        verified = load_trusted_index(temp_path)
        if verified.get("tokenizer_version") != BM25_TOKENIZER_VERSION:
            raise IndexUpgradeError("staged index tokenizer version verification failed")
        new_hash = sha256_file(temp_path)
        os.replace(temp_path, path)
        return new_hash
    finally:
        temp_path.unlink(missing_ok=True)


def allocate_backup_dir(backup_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for suffix in ["", *[f"-{number:03d}" for number in range(1, 1000)]]:
        candidate = backup_root / f"{stamp}{suffix}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
    raise IndexUpgradeError("cannot allocate backup directory")


def discover_indexes(index_dir: Path, collections: list[str]) -> list[Path]:
    root = index_dir.resolve()
    if collections:
        invalid = [name for name in collections if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name)]
        if invalid:
            raise IndexUpgradeError(f"invalid collection name: {invalid[0]}")
        candidates = [root / f"bm25_{name}.pkl" for name in collections]
    else:
        candidates = sorted(root.glob("bm25_*.pkl")) if root.exists() else []
    for candidate in candidates:
        resolved = candidate.resolve()
        if root not in [resolved, *resolved.parents]:
            raise IndexUpgradeError(f"index path escapes index directory: {candidate}")
        if not resolved.is_file():
            raise IndexUpgradeError(f"index not found: {candidate}")
    if not candidates:
        raise IndexUpgradeError(f"no BM25 indexes found under {root}")
    return candidates


def upgrade_indexes(
    index_paths: list[Path],
    *,
    apply: bool,
    force: bool,
    backup_root: Path,
) -> dict[str, Any]:
    inspected: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    results: list[dict[str, Any]] = []
    for path in index_paths:
        payload = load_trusted_index(path)
        current_version = str(payload.get("tokenizer_version") or "legacy-whitespace-v0")
        base = {
            "collection": path.stem.removeprefix("bm25_"),
            "path": str(path.resolve()),
            "sections": len(payload["sections"]),
            "from_version": current_version,
            "to_version": BM25_TOKENIZER_VERSION,
            "before_sha256": sha256_file(path),
        }
        if current_version == BM25_TOKENIZER_VERSION and not force:
            results.append({**base, "status": "current", "after_sha256": base["before_sha256"]})
            continue
        if not apply:
            results.append({**base, "status": "would_upgrade", "after_sha256": ""})
            continue
        inspected.append((path, payload, base))

    backup_dir: Path | None = None
    if inspected:
        backup_dir = allocate_backup_dir(backup_root)
        for path, _, _ in inspected:
            shutil.copy2(path, backup_dir / path.name)
        try:
            for path, payload, base in inspected:
                new_hash = write_pickle_atomic(path, upgraded_payload(payload))
                results.append({**base, "status": "upgraded", "after_sha256": new_hash})
        except Exception as exc:
            for path, _, _ in inspected:
                shutil.copy2(backup_dir / path.name, path)
            raise IndexUpgradeError(f"upgrade failed; all indexes restored from backup: {exc}") from exc

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "target_tokenizer_version": BM25_TOKENIZER_VERSION,
            "indexes": [item for item in results if item["status"] == "upgraded"],
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    results.sort(key=lambda item: item["collection"])
    return {
        "status": "pass",
        "mode": "apply" if apply else "dry-run",
        "target_tokenizer_version": BM25_TOKENIZER_VERSION,
        "backup_dir": str(backup_dir.resolve()) if backup_dir else "",
        "indexes": results,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# BM25 Index Upgrade",
        "",
        f"- Status: **{str(report['status']).upper()}**",
        f"- Mode: `{report['mode']}`",
        f"- Target tokenizer: `{report['target_tokenizer_version']}`",
        f"- Git revision: `{report.get('git_revision', '')}`",
        f"- Backup directory: `{report.get('backup_dir') or 'N/A'}`",
        "",
        "| Collection | Sections | From | To | Result | Before SHA-256 | After SHA-256 |",
        "|---|---:|---|---|---|---|---|",
    ]
    for item in report.get("indexes", []):
        lines.append(
            f"| {item['collection']} | {item['sections']} | {item['from_version']} | "
            f"{item['to_version']} | {item['status']} | `{item['before_sha256']}` | "
            f"`{item.get('after_sha256') or 'N/A'}` |"
        )
    if report.get("error"):
        lines.extend(["", f"Error: `{report['error']}`"])
    lines.extend([
        "",
        "> This tool only accepts trusted, locally generated pickle indexes. Never use it with an untrusted pickle file.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely upgrade trusted local BM25 indexes")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--collection", action="append", default=[], help="limit to a collection; repeatable")
    parser.add_argument("--apply", action="store_true", help="backup and atomically replace indexes; default is dry-run")
    parser.add_argument("--force", action="store_true", help="rebuild indexes already at the target version")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args()

    try:
        paths = discover_indexes(args.index_dir, args.collection)
        report = upgrade_indexes(paths, apply=args.apply, force=args.force, backup_root=args.backup_root)
    except Exception as exc:
        report = {
            "status": "fail",
            "mode": "apply" if args.apply else "dry-run",
            "target_tokenizer_version": BM25_TOKENIZER_VERSION,
            "error": str(exc),
            "indexes": [],
        }
    report.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": git_revision(),
    })
    write_report(args.report, report)
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.write_text(markdown_report(report), encoding="utf-8")

    print("BM25 Index Upgrade")
    print(f"status={report['status']} mode={report['mode']} target={BM25_TOKENIZER_VERSION}")
    for item in report.get("indexes", []):
        print(
            f"collection={item['collection']} sections={item['sections']} "
            f"version={item['from_version']}->{item['to_version']} status={item['status']}"
        )
    if report.get("backup_dir"):
        print(f"backup_dir={report['backup_dir']}")
    if report.get("error"):
        print(f"error={report['error']}")
    print(f"report={args.report}")
    print(f"markdown_report={args.report_md}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
