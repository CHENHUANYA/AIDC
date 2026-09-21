import asyncio
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import ingest
from app_context import IngestTextRequest
from repositories.postgres_content import ConcurrentContentUpdateError
from routes import ingest_routes
from signed_pickle import SignedPickleError


ADMIN = {"user_id": "admin01", "role": "admin"}
SUPERVISOR = {"user_id": "supervisor01", "role": "supervisor"}
OPERATOR = {"user_id": "operator01", "role": "operator"}


class FakeUpload:
    def __init__(self, filename="manual.pdf", content=b"%PDF test"):
        self.filename = filename
        self.content = content

    async def read(self):
        return self.content


class FakePdfDocument:
    def __init__(self, page_count):
        self.page_count = page_count

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeEngine:
    def __init__(self, sections=None, error=""):
        self.sections = list(sections or [])
        self.error = error

    def add_sections(self, sections):
        if self.error:
            raise RuntimeError(self.error)
        self.sections.extend(sections)
        return len(sections)

    def rebuild(self, sections):
        self.sections = list(sections)


@pytest.fixture(autouse=True)
def isolated_rebuild_jobs(monkeypatch):
    monkeypatch.setattr(ingest_routes, "REBUILD_JOBS", {})


@pytest.mark.parametrize(
    ("page_count", "expected"),
    [(0, "PDF contains no pages"), (1, ""), (1001, "PDF page count exceeds 1000 page limit")],
)
def test_validate_pdf_structure_page_boundaries(monkeypatch, page_count, expected):
    fake_fitz = SimpleNamespace(open=lambda _path: FakePdfDocument(page_count))
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setattr(ingest_routes, "PDF_MAX_PAGES", 1000)

    assert ingest_routes.validate_pdf_structure("manual.pdf") == expected


def test_validate_pdf_structure_handles_parser_failure(monkeypatch):
    def fail(_path):
        raise RuntimeError("broken PDF")

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=fail))
    assert ingest_routes.validate_pdf_structure("manual.pdf") == "Invalid or corrupt PDF file"


def test_rebuild_section_loader_rejects_unsigned_content_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_routes, "DB_PATH", str(tmp_path))
    (tmp_path / "bm25_json.pkl").write_text('{"sections": [{"code": "3000"}]}', encoding="utf-8")

    with pytest.raises(SignedPickleError, match="signature is missing"):
        ingest_routes._load_rebuild_sections("json")
    with pytest.raises(FileNotFoundError, match="Index file not found"):
        ingest_routes._load_rebuild_sections("missing")


def test_rebuild_job_internal_state_transitions(monkeypatch):
    ingest_routes._run_rebuild_job("missing")

    class ProgressEngine:
        def rebuild_with_progress(self, sections, progress_callback, stop_event):
            assert sections == [{"code": "3000"}]
            progress_callback(0, 0, "prepare")
            progress_callback(1, 2, "vector")

    completed_event = threading.Event()
    ingest_routes.REBUILD_JOBS["completed"] = {
        "collection": "808d",
        "state": "queued",
        "percent": 0,
        "stop_event": completed_event,
    }
    with (
        patch.object(ingest_routes, "_load_rebuild_sections", return_value=[{"code": "3000"}]),
        patch.object(ingest_routes, "get_engine", return_value=ProgressEngine()),
    ):
        ingest_routes._run_rebuild_job("completed")
    completed = ingest_routes.REBUILD_JOBS["completed"]
    assert completed["state"] == "completed"
    assert completed["percent"] == 100
    assert completed["processed_sections"] == 1

    cancelled_event = threading.Event()
    cancelled_event.set()
    ingest_routes.REBUILD_JOBS["cancelled"] = {
        "collection": "808d",
        "state": "queued",
        "percent": 25,
        "stop_event": cancelled_event,
    }
    with (
        patch.object(ingest_routes, "_load_rebuild_sections", return_value=[]),
        patch.object(ingest_routes, "get_engine", return_value=ProgressEngine()),
    ):
        ingest_routes._run_rebuild_job("cancelled")
    assert ingest_routes.REBUILD_JOBS["cancelled"]["state"] == "cancelled"
    assert ingest_routes.REBUILD_JOBS["cancelled"]["percent"] == 25

    ingest_routes.REBUILD_JOBS["failed"] = {
        "collection": "808d",
        "state": "queued",
        "percent": 0,
        "stop_event": threading.Event(),
    }
    with patch.object(ingest_routes, "_load_rebuild_sections", side_effect=RuntimeError("vector failed")):
        ingest_routes._run_rebuild_job("failed")
    assert ingest_routes.REBUILD_JOBS["failed"]["state"] == "failed"
    assert ingest_routes.REBUILD_JOBS["failed"]["error"] == "vector failed"


def test_start_rebuild_job_starts_once_and_hides_runtime_objects(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, self.daemon))

    monkeypatch.setattr(ingest_routes.threading, "Thread", FakeThread)
    first = ingest_routes._start_rebuild_job("808d")
    second = ingest_routes._start_rebuild_job("808d")

    assert first == second
    assert len(started) == 1
    assert "thread" not in first and "stop_event" not in first
    ingest_routes._update_rebuild_job("missing", state="failed")


def test_ingest_pdf_file_handles_empty_engine_failure_and_success(monkeypatch):
    monkeypatch.setattr(ingest_routes, "ingest_log", [])
    monkeypatch.setattr(ingest_routes, "generate_doc_id", lambda *_args: "doc-1")

    with (
        patch.object(ingest, "extract_alarm_sections", return_value=[]),
        patch.object(ingest, "extract_general_chunks", return_value=[]),
    ):
        assert ingest_routes.ingest_pdf_file("808d", "manual.pdf", "manual.pdf", "hash", None) == {
            "status": "error",
            "message": "No content extracted from PDF",
        }

    alarm = {"code": "3000", "title": "Alarm", "text": "Emergency stop", "page": 1}
    with (
        patch.object(ingest, "extract_alarm_sections", return_value=[alarm]),
        patch.object(ingest, "extract_general_chunks", return_value=[]),
        patch.object(ingest_routes, "get_engine", return_value=FakeEngine(error="vector unavailable")),
    ):
        result = ingest_routes.ingest_pdf_file("808d", "manual.pdf", "manual.pdf", "hash", None)
    assert result == {"status": "error", "message": "vector unavailable"}

    engine = FakeEngine()
    general = {"code": "", "title": "Procedure", "text": "Reset", "page": 2}
    with (
        patch.object(ingest, "extract_alarm_sections", return_value=[alarm]),
        patch.object(ingest, "extract_general_chunks", return_value=[general]),
        patch.object(ingest_routes, "get_engine", return_value=engine),
        patch.object(ingest_routes, "append_jsonl") as append,
        patch.object(ingest_routes, "upsert_document_entry") as upsert,
    ):
        result = ingest_routes.ingest_pdf_file(
            "808d", "manual.pdf", "manual.pdf", "hash", {"version": 2}
        )
    assert result["status"] == "ok"
    assert result["alarms_added"] == 1 and result["general_added"] == 1
    assert result["total_in_collection"] == 2
    append.assert_called_once()
    assert upsert.call_args.args[1]["version"] == 3
    assert upsert.call_args.args[1]["sections"] == 2


def test_pdf_ingest_route_permission_duplicate_force_and_cleanup(tmp_path):
    assert asyncio.run(ingest_routes.ingest_pdf("808d", FakeUpload(), actor={}))["message"] == "Not authenticated"
    assert asyncio.run(ingest_routes.ingest_pdf("808d", FakeUpload(), actor=OPERATOR))["message"] == "Permission denied"
    assert asyncio.run(ingest_routes.ingest_pdf("808d", FakeUpload("manual.txt"), actor=ADMIN))["message"] == "Only PDF files are supported"
    assert asyncio.run(ingest_routes.ingest_pdf("808d", FakeUpload(content=b"not-pdf"), actor=ADMIN))["message"] == "Invalid PDF file signature"

    duplicate_dir = tmp_path / "duplicate"
    duplicate_dir.mkdir()
    with (
        patch.object(ingest_routes.tempfile, "mkdtemp", return_value=str(duplicate_dir)),
        patch.object(ingest_routes, "validate_pdf_structure", return_value=""),
        patch.object(ingest_routes, "find_document_by_hash", return_value={"doc_id": "doc-existing"}),
    ):
        duplicate = asyncio.run(ingest_routes.ingest_pdf("808d", FakeUpload(), force=False, actor=ADMIN))
    assert duplicate["status"] == "duplicate", duplicate
    assert not duplicate_dir.exists()

    force_dir = tmp_path / "force"
    force_dir.mkdir()

    async def failing_threadpool(*_args):
        raise RuntimeError("worker failed")

    with (
        patch.object(ingest_routes.tempfile, "mkdtemp", return_value=str(force_dir)),
        patch.object(ingest_routes, "validate_pdf_structure", return_value=""),
        patch.object(ingest_routes, "find_document_by_hash", return_value={"doc_id": "doc-existing"}),
        patch.object(ingest_routes, "run_in_threadpool", failing_threadpool),
    ):
        failed = asyncio.run(ingest_routes.ingest_pdf("808d", FakeUpload(), force=True, actor=ADMIN))
    assert failed == {"status": "error", "message": "worker failed"}
    assert not force_dir.exists()


def test_ingest_text_entry_and_route_branches(monkeypatch):
    empty = asyncio.run(ingest_routes.ingest_text_entry("808d", IngestTextRequest(text="   ")))
    assert empty["message"] == "Text content is empty"

    request = IngestTextRequest(text="Emergency stop recovery", code="3000", source="field-note")
    monkeypatch.setattr(ingest_routes, "get_engine", lambda _name: FakeEngine(error="index unavailable"))
    assert asyncio.run(ingest_routes.ingest_text_entry("808d", request))["message"] == "index unavailable"

    engine = FakeEngine()
    monkeypatch.setattr(ingest_routes, "get_engine", lambda _name: engine)
    monkeypatch.setattr(ingest_routes, "ingest_log", [])
    with (
        patch.object(ingest_routes, "append_jsonl") as append,
        patch.object(ingest_routes, "upsert_document_entry") as upsert,
    ):
        result = asyncio.run(ingest_routes.ingest_text_entry("808d", request))
    assert result["status"] == "ok" and result["sections_added"] == 1
    assert engine.sections[0]["type"] == "workorder"
    append.assert_called_once()
    upsert.assert_called_once()

    assert asyncio.run(ingest_routes.ingest_text("808d", request, actor={}))["message"] == "Not authenticated"
    assert asyncio.run(ingest_routes.ingest_text("808d", request, actor=OPERATOR))["message"] == "Permission denied"
    assert asyncio.run(ingest_routes.ingest_text("../bad", request, actor=ADMIN))["message"] == "Invalid collection name"


def test_collection_log_and_document_read_routes(monkeypatch):
    entries = [{"collection": "808d", "index": index} for index in range(25)] + [{"collection": "840d"}]
    monkeypatch.setattr(ingest_routes, "ingest_log", entries)
    log = asyncio.run(ingest_routes.get_ingest_log("808d", actor=SUPERVISOR))
    assert len(log["entries"]) == 20 and log["entries"][0]["index"] == 5
    assert asyncio.run(ingest_routes.get_ingest_log("../bad", actor=SUPERVISOR))["status"] == "error"
    assert len(asyncio.run(ingest_routes.get_all_ingest_log(actor=SUPERVISOR))["entries"]) == 26
    assert asyncio.run(ingest_routes.get_all_ingest_log(actor=OPERATOR))["message"] == "Permission denied"

    monkeypatch.setattr(ingest_routes, "engines", {"840d": object()})
    monkeypatch.setattr(
        ingest_routes,
        "list_collections_summary",
        lambda: [{"name": "808d", "documents": 2, "sections": 20}],
    )
    monkeypatch.setattr(ingest_routes, "get_collection_summary", lambda name: {"name": name, "ready": True})
    collections = asyncio.run(ingest_routes.list_collections(actor=ADMIN))["collections"]
    assert [item["name"] for item in collections] == ["808d", "840d"]
    assert collections[0]["manifest_documents"] == 2
    assert collections[1]["manifest_documents"] == 0

    monkeypatch.setattr(ingest_routes, "get_collection_documents", lambda _name: [{"doc_id": "doc-1"}])
    documents = asyncio.run(ingest_routes.list_documents("808d", actor=SUPERVISOR))
    assert documents["documents"] == [{"doc_id": "doc-1"}]
    assert asyncio.run(ingest_routes.list_documents("../bad", actor=SUPERVISOR))["status"] == "error"


def test_delete_document_failure_branches(monkeypatch):
    assert asyncio.run(ingest_routes.delete_document("808d", "doc-1", "rev", actor={}))["message"] == "Not authenticated"
    assert asyncio.run(ingest_routes.delete_document("808d", "doc-1", "rev", actor=OPERATOR))["message"] == "Permission denied"
    assert asyncio.run(ingest_routes.delete_document("../bad", "doc-1", "rev", actor=ADMIN))["message"] == "Invalid collection name"

    monkeypatch.setattr(ingest_routes, "get_collection_documents", lambda _name: [])
    assert asyncio.run(ingest_routes.delete_document("808d", "missing", "rev", actor=ADMIN))["status"] == "not_found"

    monkeypatch.setattr(ingest_routes, "get_collection_documents", lambda _name: [{"doc_id": "legacy", "legacy": True}])
    assert "Legacy index" in asyncio.run(ingest_routes.delete_document("808d", "legacy", "rev", actor=ADMIN))["message"]

    document = {"doc_id": "doc-1", "revision": "rev-1"}
    monkeypatch.setattr(ingest_routes, "get_collection_documents", lambda _name: [document])
    assert "revision is required" in asyncio.run(ingest_routes.delete_document("808d", "doc-1", "", actor=ADMIN))["message"]

    monkeypatch.setattr(ingest_routes, "get_engine", lambda _name: FakeEngine())
    assert asyncio.run(ingest_routes.delete_document("808d", "doc-1", "rev-1", actor=ADMIN))["message"] == "Engine not ready"

    monkeypatch.setattr(ingest_routes, "get_engine", lambda _name: FakeEngine([{"doc_id": "other"}]))
    assert asyncio.run(ingest_routes.delete_document("808d", "doc-1", "rev-1", actor=ADMIN))["message"] == "No sections removed"

    engine = FakeEngine([{"doc_id": "doc-1"}, {"doc_id": "other"}])
    monkeypatch.setattr(ingest_routes, "get_engine", lambda _name: engine)
    monkeypatch.setattr(
        ingest_routes,
        "remove_document_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConcurrentContentUpdateError("stale document")),
    )
    result = asyncio.run(ingest_routes.delete_document("808d", "doc-1", "rev-1", actor=ADMIN))
    assert result == {"status": "error", "message": "stale document"}
    assert engine.sections == [{"doc_id": "doc-1"}, {"doc_id": "other"}]

    engine = FakeEngine([{"doc_id": "doc-1"}, {"doc_id": "other"}])
    monkeypatch.setattr(ingest_routes, "get_engine", lambda _name: engine)
    monkeypatch.setattr(ingest_routes, "remove_document_entry", lambda *_args, **_kwargs: False)
    result = asyncio.run(ingest_routes.delete_document("808d", "doc-1", "rev-1", actor=ADMIN))
    assert result == {"status": "not_found", "message": "Document metadata was already removed"}
    assert engine.sections == [{"doc_id": "doc-1"}, {"doc_id": "other"}]


def test_rebuild_route_and_job_failure_branches(monkeypatch):
    assert asyncio.run(ingest_routes.rebuild_collection("808d", actor={}))["message"] == "Not authenticated"
    assert asyncio.run(ingest_routes.rebuild_collection("808d", actor=OPERATOR))["message"] == "Permission denied"
    assert asyncio.run(ingest_routes.rebuild_collection("../bad", actor=ADMIN))["message"] == "Invalid collection name"
    assert asyncio.run(ingest_routes.rebuild_collection("missing", actor=ADMIN))["message"] == "Index file not found"

    monkeypatch.setattr(ingest_routes, "_load_rebuild_sections", lambda _name: (_ for _ in ()).throw(ValueError("bad index")))
    assert "Unable to load index file" in asyncio.run(ingest_routes.rebuild_collection("808d", actor=ADMIN))["message"]

    assert asyncio.run(ingest_routes.get_rebuild_job("808d", "missing", actor=OPERATOR))["message"] == "Permission denied"
    assert asyncio.run(ingest_routes.get_rebuild_job("../bad", "missing", actor=SUPERVISOR))["message"] == "Invalid collection name"
    assert asyncio.run(ingest_routes.get_rebuild_job("808d", "missing", actor=SUPERVISOR))["status"] == "not_found"
    assert asyncio.run(ingest_routes.cancel_rebuild_job("808d", "missing", actor=OPERATOR))["message"] == "Permission denied"
    assert asyncio.run(ingest_routes.cancel_rebuild_job("808d", "missing", actor=ADMIN))["status"] == "not_found"

    ingest_routes.REBUILD_JOBS["done"] = {
        "collection": "808d",
        "state": "completed",
        "stop_event": threading.Event(),
    }
    done = asyncio.run(ingest_routes.cancel_rebuild_job("808d", "done", actor=ADMIN))
    assert done["state"] == "completed"
