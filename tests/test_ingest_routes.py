import unittest
import pickle
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import ingest
from routes import ingest_routes


class FakeUploadFile:
    filename = "manual.pdf"

    async def read(self) -> bytes:
        return b"%PDF smoke"


class FakeLargeUploadFile:
    filename = "manual.pdf"

    async def read(self) -> bytes:
        return b"x" * 12


class IngestRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_ingest_runs_heavy_work_in_threadpool(self):
        async def fake_threadpool(func, *args):
            self.assertIs(func, ingest_routes.ingest_pdf_file)
            return {
                "status": "ok",
                "collection": args[0],
                "filename": args[2],
                "source_hash": args[3],
            }

        with patch.object(ingest_routes, "find_document_by_hash", return_value=None):
            with patch.object(ingest_routes, "run_in_threadpool", fake_threadpool):
                with patch.object(ingest_routes, "validate_pdf_structure", return_value=""):
                    tmp_root = Path("tests_tmp") / f"ingest_{uuid.uuid4().hex}"
                    tmp_root.mkdir(parents=True, exist_ok=False)
                    self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
                    with patch.object(ingest_routes.tempfile, "mkdtemp", return_value=str(tmp_root)):
                        result = await ingest_routes.ingest_pdf(
                            "808d",
                            file=FakeUploadFile(),
                            actor={"user_id": "admin01", "role": "admin"},
                        )

        self.assertEqual("ok", result["status"])
        self.assertEqual("808d", result["collection"])
        self.assertEqual("manual.pdf", result["filename"])

    async def test_pdf_ingest_rejects_unsafe_collection_before_threadpool(self):
        with patch.object(ingest_routes, "run_in_threadpool") as threadpool:
            result = await ingest_routes.ingest_pdf(
                "../bad",
                file=FakeUploadFile(),
                actor={"user_id": "admin01", "role": "admin"},
            )

        self.assertEqual("error", result["status"])
        self.assertEqual("Invalid collection name", result["message"])
        threadpool.assert_not_called()

    def test_cli_collection_name_validation_rejects_path_segments(self):
        with self.assertRaises(ValueError):
            ingest.validate_collection_name("../bad")

        self.assertEqual("808d", ingest.validate_collection_name("808d"))

    async def test_pdf_ingest_rejects_files_over_server_limit(self):
        tmp_root = Path("tests_tmp") / f"ingest_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))

        with patch.object(ingest_routes, "PDF_UPLOAD_MAX_BYTES", 8):
            with patch.object(ingest_routes.tempfile, "mkdtemp", return_value=str(tmp_root)):
                result = await ingest_routes.ingest_pdf(
                    "808d",
                    file=FakeLargeUploadFile(),
                    actor={"user_id": "admin01", "role": "admin"},
                )

        self.assertEqual("error", result["status"])
        self.assertIn("PDF upload exceeds", result["message"])

    async def test_pdf_ingest_rejects_corrupt_pdf_before_threadpool(self):
        tmp_root = Path("tests_tmp") / f"ingest_corrupt_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))

        with patch.object(ingest_routes, "run_in_threadpool") as threadpool:
            with patch.object(ingest_routes.tempfile, "mkdtemp", return_value=str(tmp_root)):
                result = await ingest_routes.ingest_pdf(
                    "808d",
                    file=FakeUploadFile(),
                    actor={"user_id": "admin01", "role": "admin"},
                )

        self.assertEqual("error", result["status"])
        self.assertEqual("Invalid or corrupt PDF file", result["message"])
        threadpool.assert_not_called()

    async def test_pdf_ingest_rejects_excessive_page_count_before_threadpool(self):
        tmp_root = Path("tests_tmp") / f"ingest_pages_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))

        with patch.object(ingest_routes, "validate_pdf_structure", return_value="PDF page count exceeds 1000 page limit"):
            with patch.object(ingest_routes, "run_in_threadpool") as threadpool:
                with patch.object(ingest_routes.tempfile, "mkdtemp", return_value=str(tmp_root)):
                    result = await ingest_routes.ingest_pdf(
                        "808d",
                        file=FakeUploadFile(),
                        actor={"user_id": "admin01", "role": "admin"},
                    )

        self.assertEqual("error", result["status"])
        self.assertEqual("PDF page count exceeds 1000 page limit", result["message"])
        threadpool.assert_not_called()

    async def test_rebuild_collection_runs_engine_rebuild_in_threadpool(self):
        tmp_root = Path("tests_tmp") / f"rebuild_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        sections = [{"text": "alarm section", "code": "3000"}]
        with (tmp_root / "bm25_808d.pkl").open("wb") as file:
            pickle.dump({"sections": sections}, file)

        class FakeEngine:
            def rebuild_with_progress(self, rebuild_sections):
                self.sections = rebuild_sections

        fake_engine = FakeEngine()

        async def fake_threadpool(func, *args):
            self.assertIs(func.__self__, fake_engine)
            self.assertEqual("rebuild_with_progress", func.__name__)
            self.assertEqual((sections,), args)
            func(*args)

        with patch.object(ingest_routes, "DB_PATH", str(tmp_root)):
            with patch.object(ingest_routes, "get_engine", return_value=fake_engine):
                with patch.object(ingest_routes, "run_in_threadpool", fake_threadpool):
                    result = await ingest_routes.rebuild_collection(
                        "808d",
                        sync=True,
                        actor={"user_id": "admin01", "role": "admin"},
                    )

        self.assertEqual({"status": "ok", "sections": 1}, result)
        self.assertEqual(sections, fake_engine.sections)

    async def test_rebuild_collection_starts_background_job_by_default(self):
        tmp_root = Path("tests_tmp") / f"rebuild_async_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))
        sections = [{"text": "alarm section", "code": "3000"}]
        with (tmp_root / "bm25_808d.pkl").open("wb") as file:
            pickle.dump({"sections": sections}, file)

        fake_job = {
            "job_id": "job123",
            "collection": "808d",
            "state": "queued",
            "phase": "queued",
            "sections": 1,
        }

        with patch.object(ingest_routes, "DB_PATH", str(tmp_root)):
            with patch.object(ingest_routes, "_start_rebuild_job", return_value=fake_job) as start_job:
                result = await ingest_routes.rebuild_collection(
                    "808d",
                    actor={"user_id": "admin01", "role": "admin"},
                )

        self.assertEqual("accepted", result["status"])
        self.assertEqual("job123", result["job_id"])
        start_job.assert_called_once_with("808d")

    async def test_rebuild_job_status_and_cancel(self):
        job_id = f"job_{uuid.uuid4().hex}"
        stop_event = ingest_routes.threading.Event()
        job = {
            "job_id": job_id,
            "collection": "808d",
            "state": "running",
            "phase": "vector_rebuild",
            "processed_sections": 10,
            "total_sections": 20,
            "sections": 20,
            "percent": 50,
            "error": "",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:01",
            "finished_at": "",
            "stop_event": stop_event,
        }
        ingest_routes.REBUILD_JOBS[job_id] = job
        self.addCleanup(lambda: ingest_routes.REBUILD_JOBS.pop(job_id, None))

        status = await ingest_routes.get_rebuild_job(
            "808d",
            job_id,
            actor={"user_id": "supervisor01", "role": "supervisor"},
        )
        self.assertEqual("ok", status["status"])
        self.assertEqual(50, status["percent"])

        cancelled = await ingest_routes.cancel_rebuild_job(
            "808d",
            job_id,
            actor={"user_id": "admin01", "role": "admin"},
        )
        self.assertEqual("ok", cancelled["status"])
        self.assertEqual("cancelling", cancelled["state"])
        self.assertTrue(stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
