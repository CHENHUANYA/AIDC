import unittest

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

        with self.assertNoLogs("alarm_rag.vector_store", level="WARNING"):
            store.delete_collection("demo")

    def test_qdrant_delete_collection_warns_for_unexpected_failure(self):
        store = QdrantStore.__new__(QdrantStore)
        store.client = FailingDeleteClient("connection refused")

        with self.assertLogs("alarm_rag.vector_store", level="WARNING") as captured:
            store.delete_collection("demo")

        self.assertIn("connection refused", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
