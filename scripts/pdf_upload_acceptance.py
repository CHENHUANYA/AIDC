import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from env_utils import admin_initial_password, load_project_env


def request_json(url: str, method: str = "GET", payload: dict | None = None, token: str = "", timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def upload_pdf(base_url: str, collection: str, pdf_path: Path, token: str, timeout: int) -> dict:
    boundary = f"----alarm-rag-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{pdf_path.name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    body += pdf_path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib.request.Request(
        f"{base_url}/v1/{collection}/ingest",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_rebuild(base_url: str, collection: str, job_id: str, token: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        latest = request_json(f"{base_url}/v1/{collection}/rebuild/{job_id}", token=token, timeout=30)
        if latest.get("state") in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for rebuild job {job_id}: {latest}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PDF upload duplicate/delete/rebuild acceptance flow")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--collection", default="pdf_smoke")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    load_project_env()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    login = request_json(
        f"{args.base_url}/auth/login",
        method="POST",
        payload={"username": "admin01", "password": admin_initial_password()},
        timeout=30,
    )
    token = login.get("token", "")
    if login.get("status") != "ok" or not token:
        raise SystemExit(f"Login failed: {login}")

    first = upload_pdf(args.base_url, args.collection, pdf_path, token, args.timeout)
    if first.get("status") != "ok":
        raise SystemExit(f"Initial upload failed: {first}")

    duplicate = upload_pdf(args.base_url, args.collection, pdf_path, token, args.timeout)
    if duplicate.get("status") != "duplicate":
        raise SystemExit(f"Duplicate upload did not return duplicate: {duplicate}")

    documents = request_json(f"{args.base_url}/v1/{args.collection}/documents", token=token, timeout=30)
    doc_id = first.get("doc_id")
    if not doc_id or doc_id not in [doc.get("doc_id") for doc in documents.get("documents", [])]:
        raise SystemExit(f"Uploaded document not listed: {documents}")

    deleted = request_json(
        f"{args.base_url}/v1/{args.collection}/documents/{doc_id}",
        method="DELETE",
        token=token,
        timeout=args.timeout,
    )
    if deleted.get("status") != "ok":
        raise SystemExit(f"Delete failed: {deleted}")

    rebuild = request_json(
        f"{args.base_url}/v1/{args.collection}/rebuild",
        method="POST",
        token=token,
        timeout=30,
    )
    if rebuild.get("status") == "accepted":
        rebuild = wait_rebuild(args.base_url, args.collection, rebuild["job_id"], token, args.timeout)
        if rebuild.get("state") != "completed":
            raise SystemExit(f"Rebuild job failed: {rebuild}")
    elif rebuild.get("status") not in {"ok", "error"}:
        raise SystemExit(f"Unexpected rebuild response: {rebuild}")

    final_documents = request_json(f"{args.base_url}/v1/{args.collection}/documents", token=token, timeout=30)
    final_summary = final_documents.get("summary") or {}
    if final_summary.get("sections", 0) != 0 or final_documents.get("documents"):
        raise SystemExit(f"Collection not empty after delete/rebuild: {final_documents}")

    print(json.dumps({
        "status": "ok",
        "collection": args.collection,
        "uploaded_sections": first.get("total_added"),
        "duplicate_status": duplicate.get("status"),
        "deleted_sections": deleted.get("removed_sections"),
        "final_sections": final_summary.get("sections", 0),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
