import json
import shutil
import time
import unittest
import uuid
import zipfile
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from argparse import Namespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import data_maintenance


TEST_TMP = ROOT / "tests_tmp" / "data_maintenance"


class DataMaintenanceSafetyTests(unittest.TestCase):
    def make_case_dir(self) -> Path:
        base = TEST_TMP / uuid.uuid4().hex
        base.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        return base

    def test_unzip_rejects_unsafe_member_before_deleting_target(self):
        base = self.make_case_dir()
        archive_path = base / "unsafe.zip"
        target = base / "restore_target"
        target.mkdir()
        sentinel = target / "keep.txt"
        sentinel.write_text("still here", encoding="utf-8")

        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape.txt", "bad")

        with patch.object(data_maintenance, "ROOT", base):
            with self.assertRaises(ValueError):
                data_maintenance.unzip_path(archive_path, target)

        self.assertTrue(sentinel.exists())
        self.assertEqual("still here", sentinel.read_text(encoding="utf-8"))

    def test_retention_only_removes_product_backups(self):
        backup_root = self.make_case_dir()
        product = backup_root / "2026-01-01_000000"
        product.mkdir()
        (product / "data_manifest.json").write_text("{}", encoding="utf-8")
        safety = backup_root / "demo_reset_20260101_000000"
        safety.mkdir()

        old_time = time.time() - 60 * 60 * 24 * 30
        for path in [product, safety]:
            os.utime(path, (old_time, old_time))

        with patch.object(data_maintenance, "BACKUP_DIR", backup_root):
            removed = data_maintenance.cleanup_retention(retention_days=1)

        self.assertEqual(1, removed)
        self.assertFalse(product.exists())
        self.assertTrue(safety.exists())

    def test_reset_stats_dry_run_does_not_create_backup_or_delete_logs(self):
        base = self.make_case_dir()
        backup_root = base / "backups"
        db_dir = base / "alarm_db"
        db_dir.mkdir()
        alarm_log = db_dir / "alarm_log.jsonl"
        query_log = db_dir / "query_log.jsonl"
        error_log = db_dir / "error_log.jsonl"
        feedback_log = db_dir / "feedback.jsonl"
        for path in [alarm_log, query_log, error_log, feedback_log]:
            path.write_text('{"ok": true}\n', encoding="utf-8")

        with patch.object(data_maintenance, "BACKUP_DIR", backup_root):
            with patch.object(data_maintenance, "DB_DIR", db_dir):
                with patch.object(data_maintenance, "ARCHIVE_DIR", db_dir / "archive"):
                    with patch.object(data_maintenance, "LOG_FILES", {
                        "alarms": alarm_log,
                        "queries": query_log,
                        "errors": error_log,
                        "feedback": feedback_log,
                        "ingest": db_dir / "ingest_log.jsonl",
                    }):
                        data_maintenance.reset_stats(Namespace(dry_run=True, no_backup=False))

        self.assertFalse(backup_root.exists())
        self.assertEqual('{"ok": true}\n', alarm_log.read_text(encoding="utf-8"))

    def test_reset_demo_dry_run_does_not_create_backup_or_reset_work_orders(self):
        base = self.make_case_dir()
        backup_root = base / "backups"
        db_dir = base / "alarm_db"
        db_dir.mkdir()
        alarm_log = db_dir / "alarm_log.jsonl"
        work_orders = db_dir / "work_orders.json"
        alarm_log.write_text('{"alarm": true}\n', encoding="utf-8")
        work_orders.write_text('[{"id": "WO-1"}]', encoding="utf-8")

        with patch.object(data_maintenance, "BACKUP_DIR", backup_root):
            with patch.object(data_maintenance, "DB_DIR", db_dir):
                with patch.object(data_maintenance, "ARCHIVE_DIR", db_dir / "archive"):
                    with patch.object(data_maintenance, "WORK_ORDERS_FILE", work_orders):
                        with patch.object(data_maintenance, "LOG_FILES", {
                            "alarms": alarm_log,
                            "queries": db_dir / "query_log.jsonl",
                            "errors": db_dir / "error_log.jsonl",
                            "feedback": db_dir / "feedback.jsonl",
                            "ingest": db_dir / "ingest_log.jsonl",
                        }):
                            data_maintenance.reset_demo(Namespace(dry_run=True, no_backup=False))

        self.assertFalse(backup_root.exists())
        self.assertEqual('[{"id": "WO-1"}]', work_orders.read_text(encoding="utf-8"))

    def test_reset_demo_resets_work_orders_and_issues_with_backup(self):
        base = self.make_case_dir()
        backup_root = base / "backups"
        db_dir = base / "alarm_db"
        db_dir.mkdir()
        work_orders = db_dir / "work_orders.json"
        issues = db_dir / "issues.json"
        work_orders.write_text('[{"id": "WO-1"}]', encoding="utf-8")
        issues.write_text('[{"issue_id": "ISS-1"}]', encoding="utf-8")

        with patch.object(data_maintenance, "BACKUP_DIR", backup_root):
            with patch.object(data_maintenance, "DB_DIR", db_dir):
                with patch.object(data_maintenance, "ARCHIVE_DIR", db_dir / "archive"):
                    with patch.object(data_maintenance, "WORK_ORDERS_FILE", work_orders):
                        with patch.object(data_maintenance, "ISSUES_FILE", issues):
                            with patch.object(data_maintenance, "LOG_FILES", {
                                "alarms": db_dir / "alarm_log.jsonl",
                                "queries": db_dir / "query_log.jsonl",
                                "errors": db_dir / "error_log.jsonl",
                                "feedback": db_dir / "feedback.jsonl",
                                "ingest": db_dir / "ingest_log.jsonl",
                            }):
                                data_maintenance.reset_demo(Namespace(dry_run=False, no_backup=False))

        self.assertEqual([], json.loads(work_orders.read_text(encoding="utf-8")))
        self.assertEqual([], json.loads(issues.read_text(encoding="utf-8")))
        backups = list(backup_root.iterdir())
        self.assertEqual(1, len(backups))
        self.assertTrue((backups[0] / "work_orders.json").exists())
        self.assertTrue((backups[0] / "issues.json").exists())

    def test_runtime_data_audit_reports_large_jsonl_and_bad_json_files(self):
        base = self.make_case_dir()
        db_dir = base / "alarm_db"
        archive_dir = db_dir / "archive"
        archive_dir.mkdir(parents=True)
        work_orders = db_dir / "work_orders.json"
        issues = db_dir / "issues.json"
        alarm_log = db_dir / "alarm_log.jsonl"
        work_orders.write_text("{bad", encoding="utf-8")
        issues.write_text('{"not": "a list"}', encoding="utf-8")
        alarm_log.write_text(
            "\n".join(json.dumps({"i": i}) for i in range(1000)) + "\nnot-json\n[]\n",
            encoding="utf-8",
        )
        (archive_dir / "work_orders_archive_20260101.json").write_text("[{}", encoding="utf-8")

        with patch.object(data_maintenance, "WORK_ORDERS_FILE", work_orders):
            with patch.object(data_maintenance, "ISSUES_FILE", issues):
                with patch.object(data_maintenance, "ARCHIVE_DIR", archive_dir):
                    with patch.object(data_maintenance, "LOG_FILES", {
                        "alarms": alarm_log,
                        "queries": db_dir / "query_log.jsonl",
                        "errors": db_dir / "error_log.jsonl",
                        "feedback": db_dir / "feedback.jsonl",
                        "ingest": db_dir / "ingest_log.jsonl",
                    }):
                        report = data_maintenance.runtime_data_report()
                        checks = data_maintenance.runtime_data_checks(
                            report,
                            max_invalid_jsonl_lines=0,
                            max_archive_files=10,
                        )

        statuses = {check["name"]: check["status"] for check in checks}
        self.assertEqual(1000, report["jsonl_files"]["alarms"]["records"])
        self.assertEqual(2, report["jsonl_files"]["alarms"]["invalid_lines"])
        self.assertEqual("FAIL", statuses["json:work_orders"])
        self.assertEqual("FAIL", statuses["json:issues"])
        self.assertEqual("FAIL", statuses["jsonl:invalid-lines"])
        self.assertEqual("FAIL", statuses["archive:valid-json"])

    def test_runtime_data_audit_fails_for_archive_file_count_limit(self):
        base = self.make_case_dir()
        archive_dir = base / "alarm_db" / "archive"
        archive_dir.mkdir(parents=True)
        for index in range(3):
            (archive_dir / f"work_orders_archive_2026010{index}.json").write_text("[]", encoding="utf-8")

        with patch.object(data_maintenance, "WORK_ORDERS_FILE", base / "alarm_db" / "work_orders.json"):
            with patch.object(data_maintenance, "ISSUES_FILE", base / "alarm_db" / "issues.json"):
                with patch.object(data_maintenance, "ARCHIVE_DIR", archive_dir):
                    with patch.object(data_maintenance, "LOG_FILES", {
                        "alarms": base / "alarm_db" / "alarm_log.jsonl",
                        "queries": base / "alarm_db" / "query_log.jsonl",
                        "errors": base / "alarm_db" / "error_log.jsonl",
                        "feedback": base / "alarm_db" / "feedback.jsonl",
                        "ingest": base / "alarm_db" / "ingest_log.jsonl",
                    }):
                        report = data_maintenance.runtime_data_report()
                        checks = data_maintenance.runtime_data_checks(
                            report,
                            max_invalid_jsonl_lines=0,
                            max_archive_files=2,
                        )

        statuses = {check["name"]: check["status"] for check in checks}
        self.assertEqual(3, report["archives"]["files"])
        self.assertEqual("FAIL", statuses["archive:file-count"])

    def test_runtime_backup_verify_and_restore_roundtrip(self):
        base = self.make_case_dir()
        backup_root = base / "backups"
        alarm_db = base / "alarm_db"
        data_dir = base / "data"
        mock_data = base / "mock_data"
        n8n_data = base / "n8n_data"
        qdrant_data = base / "qdrant_data"
        hf_cache = base / "hf_cache"

        for path in [alarm_db, data_dir, mock_data, n8n_data, qdrant_data, hf_cache]:
            path.mkdir(parents=True)
        (alarm_db / "users.json").write_text('{"admin01": {"role": "admin"}}', encoding="utf-8")
        (data_dir / "manual.txt").write_text("manual data", encoding="utf-8")
        (mock_data / "workflow.json").write_text("workflow", encoding="utf-8")
        (n8n_data / "state.json").write_text("n8n", encoding="utf-8")
        (qdrant_data / "collection.bin").write_text("vectors", encoding="utf-8")

        patches = [
            patch.object(data_maintenance, "ROOT", base),
            patch.object(data_maintenance, "BACKUP_DIR", backup_root),
            patch.object(data_maintenance, "DB_DIR", alarm_db),
            patch.object(data_maintenance, "ARCHIVE_DIR", alarm_db / "archive"),
            patch.object(data_maintenance, "DATA_DIR", data_dir),
            patch.object(data_maintenance, "MOCK_DATA_DIR", mock_data),
            patch.object(data_maintenance, "N8N_DATA_DIR", n8n_data),
            patch.object(data_maintenance, "QDRANT_DATA_DIR", qdrant_data),
            patch.object(data_maintenance, "HF_CACHE_DIR", hf_cache),
        ]

        args = Namespace(
            include_hf_cache=False,
            include_mock_data=True,
            dry_run=False,
            retention_days=0,
        )
        restore_args = Namespace(backup="", dry_run=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            data_maintenance.backup_runtime(args)
            backup_path = data_maintenance.latest_product_backup()

            self.assertIsNotNone(backup_path)
            self.assertTrue(data_maintenance.verify_backup(backup_path, verbose=False))

            (alarm_db / "users.json").write_text("corrupted", encoding="utf-8")
            shutil.rmtree(n8n_data)
            data_maintenance.restore_runtime(restore_args)

        self.assertEqual('{"admin01": {"role": "admin"}}', (alarm_db / "users.json").read_text(encoding="utf-8"))
        self.assertEqual("n8n", (n8n_data / "state.json").read_text(encoding="utf-8"))

    def test_restore_smoke_extracts_to_staging_without_touching_runtime(self):
        base = self.make_case_dir()
        backup_root = base / "backups"
        staging = base / "tests_tmp" / "restore_smoke"
        alarm_db = base / "alarm_db"
        data_dir = base / "data"
        mock_data = base / "mock_data"
        n8n_data = base / "n8n_data"
        qdrant_data = base / "qdrant_data"
        hf_cache = base / "hf_cache"

        for path in [alarm_db, data_dir, mock_data, n8n_data, qdrant_data, hf_cache]:
            path.mkdir(parents=True)
        (alarm_db / "users.json").write_text('{"admin01": {"role": "admin"}}', encoding="utf-8")
        (n8n_data / "state.json").write_text("n8n", encoding="utf-8")
        (qdrant_data / "collection.bin").write_text("vectors", encoding="utf-8")

        patches = [
            patch.object(data_maintenance, "ROOT", base),
            patch.object(data_maintenance, "BACKUP_DIR", backup_root),
            patch.object(data_maintenance, "DB_DIR", alarm_db),
            patch.object(data_maintenance, "ARCHIVE_DIR", alarm_db / "archive"),
            patch.object(data_maintenance, "DATA_DIR", data_dir),
            patch.object(data_maintenance, "MOCK_DATA_DIR", mock_data),
            patch.object(data_maintenance, "N8N_DATA_DIR", n8n_data),
            patch.object(data_maintenance, "QDRANT_DATA_DIR", qdrant_data),
            patch.object(data_maintenance, "HF_CACHE_DIR", hf_cache),
        ]
        backup_args = Namespace(
            include_hf_cache=False,
            include_mock_data=False,
            dry_run=False,
            retention_days=0,
        )
        smoke_args = Namespace(backup="", output=str(staging), dry_run=False, cleanup=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            data_maintenance.backup_runtime(backup_args)
            backup_path = data_maintenance.latest_product_backup()
            self.assertIsNotNone(backup_path)

            (alarm_db / "users.json").write_text("corrupted", encoding="utf-8")
            data_maintenance.restore_smoke(smoke_args)

        self.assertEqual("corrupted", (alarm_db / "users.json").read_text(encoding="utf-8"))
        self.assertEqual(
            '{"admin01": {"role": "admin"}}',
            (staging / "alarm_db" / "users.json").read_text(encoding="utf-8"),
        )
        self.assertTrue((staging / "restore_smoke_manifest.json").exists())

    def test_restore_runtime_fails_when_backup_is_missing(self):
        base = self.make_case_dir()
        with patch.object(data_maintenance, "ROOT", base):
            with patch.object(data_maintenance, "BACKUP_DIR", base / "backups"):
                with self.assertRaises(SystemExit) as raised:
                    data_maintenance.restore_runtime(Namespace(backup="", dry_run=False))

        self.assertEqual(1, raised.exception.code)

    def test_restore_runtime_fails_when_manifest_is_missing(self):
        base = self.make_case_dir()
        backup = base / "backups" / "missing_manifest"
        backup.mkdir(parents=True)
        with patch.object(data_maintenance, "ROOT", base):
            with self.assertRaises(SystemExit) as raised:
                data_maintenance.restore_runtime(Namespace(backup=str(backup), dry_run=False))

        self.assertEqual(1, raised.exception.code)

    def test_product_backup_path_avoids_same_second_collision(self):
        backup_root = self.make_case_dir()
        base_name = "2026-06-03_120000"
        (backup_root / base_name).mkdir()

        with patch.object(data_maintenance, "BACKUP_DIR", backup_root):
            candidate = data_maintenance.next_available_backup_path(base_name)

        self.assertEqual(backup_root / f"{base_name}_001", candidate)

    def test_verify_backup_rejects_manifest_archive_path_escape(self):
        base = self.make_case_dir()
        backup = base / "2026-06-03_120000"
        backup.mkdir()
        manifest = {
            "components": [
                {
                    "name": "alarm_db",
                    "archive": "../outside.zip",
                    "source_files": 1,
                    "sha256": "",
                }
            ]
        }
        (backup / "data_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        self.assertFalse(data_maintenance.verify_backup(backup, verbose=False))

    def test_list_backups_summarizes_latest_first(self):
        backup_root = self.make_case_dir()
        older = backup_root / "2026-06-01_120000"
        newer = backup_root / "2026-06-02_120000"
        for path, created_at, component_name in [
            (older, "2026-06-01T12:00:00", "alarm_db"),
            (newer, "2026-06-02T12:00:00", "n8n_data"),
        ]:
            path.mkdir()
            manifest = {
                "created_at": created_at,
                "include_hf_cache": False,
                "include_mock_data": component_name == "mock_data",
                "components": [
                    {
                        "name": component_name,
                        "archive": f"{component_name}.zip",
                        "source_files": 1,
                        "source_bytes": 10,
                        "archive_bytes": 20,
                        "sha256": "",
                    }
                ],
            }
            (path / "data_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        old_time = time.time() - 10
        new_time = time.time()
        os.utime(older, (old_time, old_time))
        os.utime(newer, (new_time, new_time))

        with patch.object(data_maintenance, "BACKUP_DIR", backup_root):
            paths = data_maintenance.product_backup_paths()
            latest = data_maintenance.latest_product_backup()
            summary = data_maintenance.backup_summary(paths[0])

        self.assertEqual(newer, paths[0])
        self.assertEqual(newer, latest)
        self.assertEqual("2026-06-02_120000", summary["name"])
        self.assertEqual(["n8n_data"], summary["components"])
        self.assertEqual(20, summary["archive_bytes"])

    def test_backup_summary_can_verify_backup(self):
        base = self.make_case_dir()
        backup_root = base / "backups"
        alarm_db = base / "alarm_db"
        for path in [alarm_db, base / "data", base / "mock_data", base / "n8n_data", base / "qdrant_data", base / "hf_cache"]:
            path.mkdir(parents=True)
        (alarm_db / "users.json").write_text("{}", encoding="utf-8")

        patches = [
            patch.object(data_maintenance, "ROOT", base),
            patch.object(data_maintenance, "BACKUP_DIR", backup_root),
            patch.object(data_maintenance, "DB_DIR", alarm_db),
            patch.object(data_maintenance, "ARCHIVE_DIR", alarm_db / "archive"),
            patch.object(data_maintenance, "DATA_DIR", base / "data"),
            patch.object(data_maintenance, "MOCK_DATA_DIR", base / "mock_data"),
            patch.object(data_maintenance, "N8N_DATA_DIR", base / "n8n_data"),
            patch.object(data_maintenance, "QDRANT_DATA_DIR", base / "qdrant_data"),
            patch.object(data_maintenance, "HF_CACHE_DIR", base / "hf_cache"),
        ]
        args = Namespace(
            include_hf_cache=False,
            include_mock_data=False,
            dry_run=False,
            retention_days=0,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            data_maintenance.backup_runtime(args)
            backup_path = data_maintenance.latest_product_backup()
            summary = data_maintenance.backup_summary(backup_path, verify=True)

        self.assertTrue(summary["verified"])

    def test_backup_health_passes_for_fresh_required_components(self):
        backup_root = self.make_case_dir()
        backup = backup_root / "2026-06-04_120000"
        backup.mkdir()
        manifest = {
            "created_at": datetime.now().isoformat(),
            "components": [
                {"name": "alarm_db", "archive": "alarm_db.zip", "archive_bytes": 10, "source_bytes": 20},
                {"name": "data", "archive": "data.zip", "archive_bytes": 10, "source_bytes": 20},
            ],
        }
        (backup / "data_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        checks = data_maintenance.backup_health_checks(
            backup,
            max_age_hours=72,
            required_components=["alarm_db", "data"],
            verify=False,
        )

        self.assertTrue(all(check["status"] == "PASS" for check in checks))

    def test_backup_health_fails_for_missing_component_and_stale_backup(self):
        backup_root = self.make_case_dir()
        backup = backup_root / "2026-06-01_120000"
        backup.mkdir()
        manifest = {
            "created_at": (datetime.now() - timedelta(hours=100)).isoformat(),
            "components": [
                {"name": "alarm_db", "archive": "alarm_db.zip", "archive_bytes": 10, "source_bytes": 20},
            ],
        }
        (backup / "data_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        checks = data_maintenance.backup_health_checks(
            backup,
            max_age_hours=72,
            required_components=["alarm_db", "data"],
            verify=False,
        )
        statuses = {check["name"]: check["status"] for check in checks}

        self.assertEqual("FAIL", statuses["backup:age"])
        self.assertEqual("FAIL", statuses["backup:components"])

    def test_backup_health_fails_when_no_backup_exists(self):
        checks = data_maintenance.backup_health_checks(
            None,
            max_age_hours=72,
            required_components=["alarm_db"],
            verify=False,
        )

        self.assertEqual([{"name": "backup:exists", "status": "FAIL", "detail": "no product backup found"}], checks)

    def test_backup_health_empty_required_components_disables_component_check(self):
        backup_root = self.make_case_dir()
        backup = backup_root / "2026-06-04_120000"
        backup.mkdir()
        manifest = {
            "created_at": datetime.now().isoformat(),
            "components": [],
        }
        (backup / "data_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        checks = data_maintenance.backup_health_checks(
            backup,
            max_age_hours=72,
            required_components=[],
            verify=False,
        )
        names = [check["name"] for check in checks]

        self.assertNotIn("backup:components", names)

    def test_dry_run_can_be_placed_after_runtime_subcommands(self):
        parser = data_maintenance.build_parser()

        backup_args = parser.parse_args(["backup-runtime", "--dry-run", "--include-mock-data"])
        restore_args = parser.parse_args(["restore-runtime", "--dry-run", "--backup", "backups/sample"])
        reset_args = parser.parse_args(["reset-demo", "--dry-run", "--no-backup"])

        self.assertTrue(backup_args.dry_run)
        self.assertTrue(backup_args.include_mock_data)
        self.assertTrue(restore_args.dry_run)
        self.assertEqual("backups/sample", restore_args.backup)
        self.assertTrue(reset_args.dry_run)
        self.assertTrue(reset_args.no_backup)

    def test_global_dry_run_is_not_overwritten_by_subcommand_defaults(self):
        parser = data_maintenance.build_parser()

        backup_args = parser.parse_args(["--dry-run", "backup-runtime", "--include-mock-data"])
        reset_args = parser.parse_args(["--dry-run", "reset-demo"])

        self.assertTrue(backup_args.dry_run)
        self.assertTrue(backup_args.include_mock_data)
        self.assertTrue(reset_args.dry_run)


if __name__ == "__main__":
    unittest.main()
