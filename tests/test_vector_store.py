import io
import unittest
from contextlib import redirect_stdout

from vector_store import ChromaStore, QdrantStore


class FailingDeleteClient:
    def __init__(self, message):
        self.message = message

    def delete_collection(self, collection):
        raise RuntimeError(self.message)


class VectorStoreWarningTests(unittest.TestCase):
    def test_chroma_delete_collection_ignores_missing_collection_noise(self):
        store = ChromaStore.__new__(ChromaStore)
        store.client = FailingDeleteClient("Collection demo does not exist")

        output = io.StringIO()
        with redirect_stdout(output):
            store.delete_collection("demo")

        self.assertEqual("", output.getvalue())

    def test_qdrant_delete_collection_warns_for_unexpected_failure(self):
        store = QdrantStore.__new__(QdrantStore)
        store.client = FailingDeleteClient("connection refused")

        output = io.StringIO()
        with redirect_stdout(output):
            store.delete_collection("demo")

        self.assertIn("[WARN][vector_store]", output.getvalue())
        self.assertIn("connection refused", output.getvalue())


if __name__ == "__main__":
    unittest.main()
