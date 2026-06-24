import shutil
import unittest
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import work_orders


class FakeExcelUploadFile:
    filename = "orders.xlsx"

    async def read(self) -> bytes:
        return b"x" * 12


class FakeBytesUploadFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


def make_xlsx_zip(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", b"<Types></Types>")
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class WorkOrderImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_excel_import_rejects_files_over_server_limit(self):
        tmp_root = Path("tests_tmp") / f"excel_{uuid.uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(tmp_root, ignore_errors=True))

        with patch.object(work_orders, "DB_DIR", str(tmp_root)):
            with patch.object(work_orders, "EXCEL_UPLOAD_MAX_BYTES", 8):
                result = await work_orders.import_excel(
                    file=FakeExcelUploadFile(),
                    actor={"user_id": "admin01", "role": "admin"},
                )

        self.assertEqual("error", result["status"])
        self.assertIn("Excel upload exceeds", result["message"])

    async def test_excel_import_rejects_corrupt_xlsx_archive_before_openpyxl(self):
        result = await work_orders.import_excel(
            file=FakeBytesUploadFile("orders.xlsx", b"PK\x03\x04not a valid zip"),
            actor={"user_id": "admin01", "role": "admin"},
        )

        self.assertEqual("error", result["status"])
        self.assertEqual("Invalid XLSX archive", result["message"])

    async def test_excel_import_rejects_large_shared_strings_before_openpyxl(self):
        content = make_xlsx_zip({"xl/sharedStrings.xml": b"a" * 128})

        with patch.object(work_orders, "XLSX_MAX_SHARED_STRINGS_BYTES", 64):
            result = await work_orders.import_excel(
                file=FakeBytesUploadFile("orders.xlsx", content),
                actor={"user_id": "admin01", "role": "admin"},
            )

        self.assertEqual("error", result["status"])
        self.assertIn("shared strings exceed", result["message"])

    async def test_excel_import_rejects_zip_bomb_compression_ratio(self):
        content = make_xlsx_zip({"xl/worksheets/sheet1.xml": b"0" * 4096})

        with patch.object(work_orders, "XLSX_MAX_COMPRESSION_RATIO", 5):
            result = await work_orders.import_excel(
                file=FakeBytesUploadFile("orders.xlsx", content),
                actor={"user_id": "admin01", "role": "admin"},
            )

        self.assertEqual("error", result["status"])
        self.assertEqual("XLSX archive compression ratio is too high", result["message"])


if __name__ == "__main__":
    unittest.main()
