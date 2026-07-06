from __future__ import annotations

import argparse
import os
from pathlib import Path


PLACEHOLDERS = {"", "replace-with-a-long-random-password", "change-me-now"}


def env_file_value(path: Path, name: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"Environment file does not exist: {path}")
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def stage_secret(source: Path, output: Path) -> int:
    value = env_file_value(source, "POSTGRES_PASSWORD")
    if value in PLACEHOLDERS or "\n" in value or "\r" in value or "\x00" in value:
        raise RuntimeError("POSTGRES_PASSWORD is missing, unsafe, or still uses a placeholder")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="")
    os.chmod(temporary, 0o600)
    temporary.replace(output)
    os.chmod(output, 0o600)
    return len(value.encode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage PostgreSQL password as a local Docker secret")
    parser.add_argument("--env-file", type=Path, default=Path(".env.postgresql"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("backups/postgresql-local-secrets/postgres_password"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    byte_count = stage_secret(args.env_file, args.output)
    print(f"Staged PostgreSQL Docker secret: path={args.output} bytes={byte_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
