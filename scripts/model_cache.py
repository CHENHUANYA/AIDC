import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_HF_HOME = ROOT / "hf_cache"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT))

from env_utils import load_project_env


load_project_env()


def configure_env(args: argparse.Namespace) -> None:
    os.environ["HF_HOME"] = str(Path(args.hf_home).resolve())
    if args.online:
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ["TRANSFORMERS_OFFLINE"] = "0"
        os.environ["RAG_HF_LOCAL_ONLY"] = "false"
    else:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("RAG_HF_LOCAL_ONLY", "true")


def cache_status() -> dict:
    from rag_engine import model_cache_status

    return model_cache_status()


def print_status(status: dict | None = None) -> bool:
    status = status or cache_status()
    print(f"HF_HOME={status['hf_home']}")
    print(f"local_only={status['local_only']}")
    print(f"offline={status['offline']}")
    for item in status["models"]:
        state = "OK" if item["available"] else "MISSING"
        print(f"[{state}] {item['role']}: {item['name']}")
        print(f"       cache={item['cache_dir']}")
        if item["snapshot_path"]:
            print(f"       snapshot={item['snapshot_path']}")
    return bool(status["ready"])


def preload_models(only: str = "all") -> None:
    from rag_engine import EMBEDDING_MODEL, HF_CACHE_DIR, RERANKER_MODEL
    from sentence_transformers import CrossEncoder, SentenceTransformer

    if only in {"all", "embedding"}:
        print(f"Downloading embedding model: {EMBEDDING_MODEL}")
        SentenceTransformer(EMBEDDING_MODEL, cache_folder=HF_CACHE_DIR)
    if only in {"all", "reranker"}:
        print(f"Downloading reranker model: {RERANKER_MODEL}")
        CrossEncoder(RERANKER_MODEL, cache_dir=HF_CACHE_DIR)
    print("Model cache preloaded.")


def manifest_path(args: argparse.Namespace) -> Path:
    return Path(args.manifest or Path(args.hf_home) / "model_cache_manifest.json").resolve()


def write_manifest(args: argparse.Namespace, status: dict) -> Path:
    target = manifest_path(args)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def print_doctor(status: dict) -> None:
    missing = [item for item in status["models"] if not item["available"]]
    if not missing:
        print("Model cache is ready for offline runtime.")
        return
    print("Model cache is not ready for offline runtime.")
    print("Missing models:")
    for item in missing:
        print(f"- {item['role']}: {item['name']}")
    print("")
    print("On a connected machine, run:")
    print("  python scripts/model_cache.py --online preload")
    print("")
    print("Then copy the hf_cache/ directory to the offline host and rerun:")
    print("  python scripts/model_cache.py check")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or preload Alarm RAG HuggingFace model cache")
    parser.add_argument("--hf-home", default=str(DEFAULT_HF_HOME), help="cache directory")
    parser.add_argument("--online", action="store_true", help="allow model downloads for preload")
    parser.add_argument("--manifest", default="", help="manifest output path")
    parser.add_argument("--only", choices=["all", "embedding", "reranker"], default="all")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="verify local model cache exists")
    subparsers.add_parser("doctor", help="print offline deployment remediation steps")
    subparsers.add_parser("manifest", help="write cache manifest JSON")
    subparsers.add_parser("preload", help="download embedding and reranker into cache")
    args = parser.parse_args()

    configure_env(args)
    if args.command == "preload":
        preload_models(args.only)
    status = cache_status()
    ready = print_status(status)
    if args.command in {"manifest", "preload"}:
        print(f"Manifest written: {write_manifest(args, status)}")
    if args.command == "doctor":
        print_doctor(status)
    if args.command == "manifest":
        return 0
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
