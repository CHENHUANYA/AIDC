from unittest.mock import patch

import ingest


def test_ingest_main_rejects_invalid_collection_and_missing_pdf(tmp_path):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"pdf")
    assert ingest.main(["--pdf", str(pdf), "--name", "../unsafe"]) == 1
    assert ingest.main(["--pdf", str(tmp_path / "missing.pdf"), "--name", "808d"]) == 1


def test_ingest_main_skips_duplicate_without_force(tmp_path):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"pdf-content")
    existing = {"doc_id": "existing", "version": 2}
    with (
        patch.object(ingest, "ensure_db_dir"),
        patch.object(ingest, "find_document_by_hash", return_value=existing),
        patch.object(ingest, "extract_alarm_sections") as extract,
    ):
        assert ingest.main(["--pdf", str(pdf), "--name", "808d"]) == 0
    extract.assert_not_called()


def test_ingest_main_reports_manual_without_alarm_sections(tmp_path):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"pdf-content")
    with (
        patch.object(ingest, "ensure_db_dir"),
        patch.object(ingest, "find_document_by_hash", return_value=None),
        patch.object(ingest, "extract_alarm_sections", return_value=[]),
        patch.object(ingest, "build_index") as build_index,
    ):
        assert ingest.main(["--pdf", str(pdf), "--name", "808d"]) == 1
    build_index.assert_not_called()


def test_ingest_main_builds_combined_index_and_manifest(tmp_path):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"pdf-content")
    alarm = {"code": "3000", "title": "Alarm", "text": "alarm text", "page": 1}
    general = {"code": "", "title": "Procedure", "text": "procedure text", "page": 2}
    existing = {"doc_id": "old", "version": 2}
    with (
        patch.object(ingest, "ensure_db_dir"),
        patch.object(ingest, "find_document_by_hash", return_value=existing),
        patch.object(ingest, "generate_doc_id", return_value="doc-new"),
        patch.object(ingest, "now_iso", return_value="2026-08-21T00:00:00+00:00"),
        patch.object(ingest, "extract_alarm_sections", return_value=[alarm]),
        patch.object(ingest, "extract_general_chunks", return_value=[general]) as extract_general,
        patch.object(ingest, "build_index") as build_index,
        patch.object(ingest, "upsert_document_entry") as upsert,
    ):
        result = ingest.main([
            "--pdf", str(pdf),
            "--name", "808d",
            "--chunk-size", "20",
            "--chunk-overlap", "4",
            "--force",
        ])

    assert result == 0
    extract_general.assert_called_once_with(str(pdf), chunk_size=20, overlap=4)
    indexed_sections = build_index.call_args.args[0]
    assert len(indexed_sections) == 2
    assert all(section["doc_id"] == "doc-new" for section in indexed_sections)
    build_index.assert_called_once_with(indexed_sections, "808d")
    manifest = upsert.call_args.args[1]
    assert manifest["doc_id"] == "doc-new"
    assert manifest["version"] == 3
    assert manifest["sections"] == 2


def test_ingest_main_can_skip_general_content(tmp_path):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"pdf-content")
    alarm = {"code": "3000", "title": "Alarm", "text": "alarm text", "page": 1}
    with (
        patch.object(ingest, "ensure_db_dir"),
        patch.object(ingest, "find_document_by_hash", return_value=None),
        patch.object(ingest, "extract_alarm_sections", return_value=[alarm]),
        patch.object(ingest, "extract_general_chunks") as extract_general,
        patch.object(ingest, "build_index"),
        patch.object(ingest, "upsert_document_entry"),
    ):
        assert ingest.main(["--pdf", str(pdf), "--name", "808d", "--no-general"]) == 0
    extract_general.assert_not_called()


def test_alarm_line_helpers_cover_non_numeric_and_footer_boundaries():
    lines = [("3000", 1), ("Alarm title", 1), ("Remedy text", 1), ("42", 2), ("SINUMERIK", 2)]
    assert ingest._looks_like_page_number("not-number", lines, 0) is False
    assert ingest._looks_like_page_number("42", lines, 3) is True
    assert ingest.is_alarm_code_line("3000", lines, 0) is True
    assert ingest.is_alarm_code_line("42", lines, 3) is False
    assert {0, 1, 2, 3, 4} >= ingest._get_alarm_line_ranges(lines)
