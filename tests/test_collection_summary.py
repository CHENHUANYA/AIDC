import unittest
from unittest.mock import patch

import app_context


class CollectionSummaryTests(unittest.TestCase):
    def setUp(self):
        self.previous_engines = dict(app_context.engines)
        app_context.engines.clear()
        self.addCleanup(self._restore_engines)

    def _restore_engines(self):
        app_context.engines.clear()
        app_context.engines.update(self.previous_engines)

    def test_summary_reports_vector_gap_when_engine_is_not_loaded(self):
        with patch.object(app_context, "get_collection_documents", return_value=[{"sections": 3}]):
            summary = app_context.get_collection_summary("808d")

        self.assertEqual(3, summary["sections"])
        self.assertEqual(0, summary["vector_points"])
        self.assertEqual(3, summary["bm25_sections"])
        self.assertEqual(0, summary["vector_coverage_percent"])
        self.assertFalse(summary["vector_ready"])
        self.assertEqual("none", summary["bm25_tokenizer_version"])

    def test_summary_includes_engine_vector_coverage(self):
        class FakeEngine:
            ready = True
            sections = [{"text": "a"}, {"text": "b"}]
            tokenizer_version = "unicode-domain-v1"

            def vector_coverage(self):
                return {
                    "vector_points": 1,
                    "bm25_sections": 2,
                    "vector_coverage_percent": 50,
                    "vector_ready": False,
                    "vector_error": "",
                }

        app_context.engines["808d"] = FakeEngine()

        with patch.object(app_context, "get_collection_documents", return_value=[]):
            summary = app_context.get_collection_summary("808d")

        self.assertTrue(summary["ready"])
        self.assertEqual(2, summary["sections"])
        self.assertEqual(1, summary["vector_points"])
        self.assertEqual(50, summary["vector_coverage_percent"])
        self.assertFalse(summary["vector_ready"])
        self.assertEqual("unicode-domain-v1", summary["bm25_tokenizer_version"])


if __name__ == "__main__":
    unittest.main()
