"""Add HMAC sidecars to existing, locally trusted BM25 pickle indexes."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

from signed_pickle import SignedPickleError, sign_existing_pickle


def load_dotenv_defaults(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sign existing local BM25 indexes without deserializing them",
    )
    parser.add_argument("--index-dir", type=Path, default=ROOT / "alarm_db")
    parser.add_argument(
        "--trust-existing-files",
        action="store_true",
        help="confirm the current bm25_*.pkl files came from a trusted local ingest",
    )
    args = parser.parse_args()
    if not args.trust_existing_files:
        parser.error("--trust-existing-files is required")

    load_dotenv_defaults(ROOT / ".env")
    index_dir = args.index_dir.resolve()
    if not index_dir.is_dir():
        print(f"Index directory not found: {index_dir}", file=sys.stderr)
        return 1
    paths = sorted(index_dir.glob("bm25_*.pkl"))
    if not paths:
        print(f"No BM25 indexes found in {index_dir}")
        return 0
    try:
        for path in paths:
            sign_existing_pickle(path)
            print(f"Signed {path.name}")
    except (OSError, SignedPickleError) as exc:
        print(f"Signing failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
