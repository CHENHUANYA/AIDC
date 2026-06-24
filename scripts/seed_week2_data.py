import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from env_utils import EnvConfigError, admin_initial_password, load_project_env


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ORDERS = ROOT / "mock_data" / "week2_work_orders.json"
DEFAULT_KNOWLEDGE = ROOT / "mock_data" / "week2_knowledge_records.json"


load_project_env()


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
    except (TimeoutError, error.URLError) as exc:
        return 0, {"_error": str(exc)}


def login(base_url: str, timeout: int, username: str = "admin01") -> str:
    try:
        password = admin_initial_password()
    except EnvConfigError as exc:
        print(f"[FAIL] {exc}")
        return ""
    code, data = request_json(
        base_url,
        "/auth/login",
        "POST",
        {"username": username, "password": password},
        timeout,
    )
    return data.get("token") if code == 200 and isinstance(data, dict) else ""


def existing_work_order_keys(base_url: str, timeout: int, token: str) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    code, data = request_json(base_url, "/work-orders", timeout=timeout, token=token)
    if code != 200:
        return {}
    orders = data.get("orders") if isinstance(data, dict) else []
    if not isinstance(orders, list):
        return {}
    return {
        (
            str(order.get("alarm_code") or ""),
            str(order.get("machine_id") or ""),
            str(order.get("source") or ""),
            str(order.get("description") or ""),
        ): order
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


def seed_work_orders(
    base_url: str,
    records: list[dict[str, Any]],
    timeout: int,
    token: str,
    verifier_token: str,
) -> tuple[int, int, int]:
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
        target_status = str(record.get("status") or "pending")
        order_id = ""
        if key in existing:
            existing_order = existing[key]
            order_id = str(existing_order.get("id") or "")
            if existing_order.get("status") == target_status:
                skipped += 1
                print(f"[SKIP] work order {key[0]} {key[1]}")
                continue

        if not order_id:
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

        root_cause = str(record.get("root_cause") or record.get("description") or "Historical mock diagnosis")
        repair_action = str(record.get("repair_action") or record.get("resolution") or "Historical mock repair action")
        update_payload = {
            "status": str(record.get("status") or "pending"),
            "priority": str(record.get("priority") or "medium"),
            "assigned_to": str(record.get("assigned_to") or ""),
            "machine_id": str(record.get("machine_id") or ""),
            "description": str(record.get("description") or ""),
            "resolution": str(record.get("resolution") or ""),
            "notes": str(record.get("notes") or ""),
            "completed_by": str(record.get("completed_by") or record.get("assigned_to") or "maintenance-a"),
            "root_cause": root_cause,
            "repair_action": repair_action,
        }
        if target_status == "verified":
            update_payload["status"] = "completed"

        code, data = request_json(base_url, f"/work-orders/{order_id}", "PATCH", update_payload, timeout, token)
        if not (code == 200 and data.get("status") == "ok"):
            failed += 1
            print(f"[FAIL] update work order {order_id} HTTP {code}: {data}")
            continue

        if target_status == "verified":
            verify_payload = {
                **update_payload,
                "status": "verified",
                "verified_by": str(record.get("verified_by") or "supervisor01"),
            }
            code, data = request_json(
                base_url,
                f"/work-orders/{order_id}",
                "PATCH",
                verify_payload,
                timeout,
                verifier_token or token,
            )
            if not (code == 200 and data.get("status") == "ok"):
                failed += 1
                print(f"[FAIL] verify work order {order_id} HTTP {code}: {data}")
                continue

        created += 1
        existing[key] = {"id": order_id, "status": target_status}
        print(f"[ OK ] work order {order_id} {key[0]} {target_status}")

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
    verifier_token = login(args.base_url, args.timeout, "supervisor01")

    total_failed = 0
    if not args.skip_work_orders:
        created, skipped, failed = seed_work_orders(
            args.base_url,
            load_json_array(Path(args.work_orders)),
            args.timeout,
            token,
            verifier_token,
        )
        total_failed += failed
        print(f"Work orders: created={created} skipped={skipped} failed={failed}")

    if not args.skip_knowledge:
        created, skipped, failed = seed_knowledge(args.base_url, load_json_array(Path(args.knowledge)), args.timeout, token)
        total_failed += failed
        print(f"Knowledge: created={created} skipped={skipped} failed={failed}")

    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
