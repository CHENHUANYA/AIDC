import argparse
import json
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ORDERS = ROOT / "mock_data" / "week2_work_orders.json"
DEFAULT_KNOWLEDGE = ROOT / "mock_data" / "week2_knowledge_records.json"


def load_json_array(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [item for item in data if isinstance(item, dict)]


def request_json(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
    token: str = "",
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), json.loads(body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"_raw": body}


def login(base_url: str, timeout: int) -> str:
    code, data = request_json(
        base_url,
        "/auth/login",
        "POST",
        {"username": "admin01", "password": "demo1234"},
        timeout,
    )
    return data.get("token") if code == 200 and isinstance(data, dict) else ""


def existing_work_order_keys(base_url: str, timeout: int, token: str) -> set[tuple[str, str, str, str]]:
    code, data = request_json(base_url, "/work-orders", timeout=timeout, token=token)
    if code != 200:
        return set()
    orders = data.get("orders") if isinstance(data, dict) else []
    if not isinstance(orders, list):
        return set()
    return {
        (
            str(order.get("alarm_code") or ""),
            str(order.get("machine_id") or ""),
            str(order.get("source") or ""),
            str(order.get("description") or ""),
        )
        for order in orders
        if isinstance(order, dict)
    }


def existing_ingest_titles(base_url: str, collection: str, timeout: int, token: str) -> set[tuple[str, str]]:
    code, data = request_json(base_url, f"/v1/{collection}/ingest-log", timeout=timeout, token=token)
    if code != 200:
        return set()
    entries = data.get("entries") if isinstance(data, dict) else []
    if not isinstance(entries, list):
        return set()
    return {
        (str(entry.get("title") or ""), str(entry.get("source") or ""))
        for entry in entries
        if isinstance(entry, dict)
    }


def seed_work_orders(base_url: str, records: list[dict[str, Any]], timeout: int, token: str) -> tuple[int, int, int]:
    existing = existing_work_order_keys(base_url, timeout, token)
    created = 0
    skipped = 0
    failed = 0

    for record in records:
        key = (
            str(record.get("alarm_code") or ""),
            str(record.get("machine_id") or ""),
            str(record.get("source") or "week2-history"),
            str(record.get("description") or ""),
        )
        if key in existing:
            skipped += 1
            print(f"[SKIP] work order {key[0]} {key[1]}")
            continue

        create_payload = {
            "alarm_code": str(record.get("alarm_code") or ""),
            "manual": str(record.get("manual") or "808d"),
            "machine_id": str(record.get("machine_id") or ""),
            "priority": str(record.get("priority") or "medium"),
            "assigned_to": str(record.get("assigned_to") or ""),
            "description": str(record.get("description") or ""),
            "rag_suggestion": str(record.get("rag_suggestion") or ""),
            "source": str(record.get("source") or "week2-history"),
        }
        code, data = request_json(base_url, "/work-orders", "POST", create_payload, timeout, token)
        order = data.get("order") if isinstance(data, dict) else {}
        order_id = order.get("id") if isinstance(order, dict) else None
        if code != 200 or data.get("status") != "ok" or not order_id:
            failed += 1
            print(f"[FAIL] create work order {key[0]} HTTP {code}: {data}")
            continue

        update_payload = {
            "status": str(record.get("status") or "pending"),
            "priority": str(record.get("priority") or "medium"),
            "assigned_to": str(record.get("assigned_to") or ""),
            "machine_id": str(record.get("machine_id") or ""),
            "description": str(record.get("description") or ""),
            "resolution": str(record.get("resolution") or ""),
            "notes": str(record.get("notes") or ""),
        }
        code, data = request_json(base_url, f"/work-orders/{order_id}", "PATCH", update_payload, timeout, token)
        if code == 200 and data.get("status") == "ok":
            created += 1
            existing.add(key)
            print(f"[ OK ] work order {order_id} {key[0]} {update_payload['status']}")
            continue

        failed += 1
        print(f"[FAIL] update work order {order_id} HTTP {code}: {data}")

    return created, skipped, failed


def seed_knowledge(base_url: str, records: list[dict[str, Any]], timeout: int, token: str) -> tuple[int, int, int]:
    created = 0
    skipped = 0
    failed = 0
    title_cache: dict[str, set[tuple[str, str]]] = {}

    for record in records:
        collection = str(record.get("collection") or "808d")
        title = str(record.get("title") or "")
        source = str(record.get("source") or "week2-sop")
        title_cache.setdefault(collection, existing_ingest_titles(base_url, collection, timeout, token))
        if (title, source) in title_cache[collection]:
            skipped += 1
            print(f"[SKIP] knowledge {collection} {title}")
            continue

        payload = {
            "text": str(record.get("text") or ""),
            "code": str(record.get("code") or ""),
            "title": title,
            "source": source,
        }
        code, data = request_json(base_url, f"/v1/{collection}/ingest-text", "POST", payload, timeout, token)
        if code == 200 and data.get("status") == "ok":
            created += 1
            title_cache[collection].add((title, source))
            print(f"[ OK ] knowledge {collection} {title}")
            continue

        failed += 1
        print(f"[FAIL] knowledge {collection} {title} HTTP {code}: {data}")

    return created, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed week-2 mock work orders and SOP records")
    parser.add_argument("--base-url", default="http://localhost:8100", help="Alarm RAG base URL")
    parser.add_argument("--work-orders", default=str(DEFAULT_WORK_ORDERS), help="mock work-order JSON array")
    parser.add_argument("--knowledge", default=str(DEFAULT_KNOWLEDGE), help="mock knowledge-record JSON array")
    parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds")
    parser.add_argument("--skip-work-orders", action="store_true", help="do not seed work orders")
    parser.add_argument("--skip-knowledge", action="store_true", help="do not seed knowledge records")
    args = parser.parse_args()

    token = login(args.base_url, args.timeout)
    if not token:
        print("[FAIL] login admin01 failed")
        return 1

    total_failed = 0
    if not args.skip_work_orders:
        created, skipped, failed = seed_work_orders(args.base_url, load_json_array(Path(args.work_orders)), args.timeout, token)
        total_failed += failed
        print(f"Work orders: created={created} skipped={skipped} failed={failed}")

    if not args.skip_knowledge:
        created, skipped, failed = seed_knowledge(args.base_url, load_json_array(Path(args.knowledge)), args.timeout, token)
        total_failed += failed
        print(f"Knowledge: created={created} skipped={skipped} failed={failed}")

    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
