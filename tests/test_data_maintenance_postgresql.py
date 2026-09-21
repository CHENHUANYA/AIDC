import argparse
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import session as database_session
from db.base import Base
from db.models import WorkOrder
from scripts import data_maintenance as maintenance


@pytest.fixture
def postgres_maintenance(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database_session, "_session_factory", factory)
    monkeypatch.setenv("DATA_STORE", "postgresql")
    monkeypatch.setattr(maintenance, "ensure_dirs", lambda: None)
    monkeypatch.setattr(maintenance, "WORK_ORDERS_FILE", tmp_path / "work_orders.json")
    monkeypatch.setattr(maintenance, "ISSUES_FILE", tmp_path / "issues.json")
    monkeypatch.setattr(maintenance, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(maintenance, "LOG_FILES", {
        name: tmp_path / f"{name}.jsonl" for name in ("alarms", "feedback", "queries", "errors", "ingest")
    })
    maintenance.WORK_ORDERS_FILE.write_text("invalid legacy JSON", encoding="utf-8")
    with factory.begin() as session:
        session.add(WorkOrder(work_order_no="WO-PG", alarm_code="3000"))
    yield tmp_path
    engine.dispose()


def test_work_order_export_reads_database_instead_of_legacy_file(postgres_maintenance):
    output = postgres_maintenance / "export.json"
    maintenance.export_work_orders(argparse.Namespace(format="json", output=str(output)))
    assert [row["id"] for row in json.loads(output.read_text())] == ["WO-PG"]


def test_data_audit_reads_database_and_only_operational_jsonl_logs(postgres_maintenance):
    maintenance.LOG_FILES["alarms"].write_text("invalid legacy alarm", encoding="utf-8")
    report = maintenance.runtime_data_report()
    assert report["data_store"] == "postgresql"
    assert report["database_tables"]["work_orders"] == 1
    assert report["json_files"] == {}
    assert set(report["jsonl_files"]) == {"queries", "errors", "ingest"}


@pytest.mark.parametrize("command", [maintenance.reset_stats, maintenance.reset_demo, maintenance.archive_work_orders])
def test_json_cleanup_cannot_claim_to_modify_postgresql(command, postgres_maintenance):
    before = maintenance.WORK_ORDERS_FILE.read_bytes()
    with pytest.raises(SystemExit, match="only supports legacy JSON"):
        command(argparse.Namespace(dry_run=False, no_backup=True))
    assert maintenance.WORK_ORDERS_FILE.read_bytes() == before
