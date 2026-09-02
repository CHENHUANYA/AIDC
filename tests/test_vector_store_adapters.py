import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

import vector_store


class FakeModels:
    class Distance:
        COSINE = "cosine"

    @staticmethod
    def VectorParams(**kwargs):
        return ("VectorParams", kwargs)

    @staticmethod
    def Batch(**kwargs):
        return ("Batch", kwargs)

    @staticmethod
    def FieldCondition(**kwargs):
        return ("FieldCondition", kwargs)

    @staticmethod
    def MatchValue(**kwargs):
        return ("MatchValue", kwargs)

    @staticmethod
    def Filter(**kwargs):
        return ("Filter", kwargs)

    @staticmethod
    def FilterSelector(**kwargs):
        return ("FilterSelector", kwargs)

    @staticmethod
    def AllSelector():
        return ("AllSelector", {})


class FakeCollection:
    def __init__(self):
        self.calls = []

    def add(self, **kwargs):
        self.calls.append(("add", kwargs))

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return {"ids": [["s1"]]}

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))

    def count(self):
        return 7


class FakeChromaClient:
    def __init__(self):
        self.collection = FakeCollection()
        self.calls = []
        self.exists = False

    def heartbeat(self):
        self.calls.append(("heartbeat", {}))

    def get_collection(self, name):
        self.calls.append(("get_collection", {"name": name}))
        if not self.exists:
            raise RuntimeError("not found")
        return self.collection

    def create_collection(self, name, metadata):
        self.calls.append(("create_collection", {"name": name, "metadata": metadata}))
        self.exists = True

    def delete_collection(self, name):
        self.calls.append(("delete_collection", {"name": name}))


class FakeQdrantClient:
    def __init__(self):
        self.calls = []
        self.collection_names = []
        self.list_error = None
        self.count_error = None
        self.hits = []

    def get_collections(self):
        self.calls.append(("get_collections", {}))
        if self.list_error:
            raise self.list_error
        return SimpleNamespace(collections=[SimpleNamespace(name=name) for name in self.collection_names])

    def create_collection(self, **kwargs):
        self.calls.append(("create_collection", kwargs))

    def delete_collection(self, name):
        self.calls.append(("delete_collection", {"name": name}))

    def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return self.hits

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))

    def get_collection(self, name):
        self.calls.append(("get_collection", {"name": name}))
        if self.count_error:
            raise self.count_error
        return SimpleNamespace(points_count=9)


def test_base_store_contract_methods_are_abstract_by_behavior():
    store = vector_store.BaseVectorStore()
    with pytest.raises(NotImplementedError):
        store.ping()
    with pytest.raises(NotImplementedError):
        store.add("c", [], [], [], [])
    with pytest.raises(NotImplementedError):
        store.query("c", [[]], 1)
    with pytest.raises(NotImplementedError):
        store.delete("c")
    with pytest.raises(NotImplementedError):
        store.delete_collection("c")
    with pytest.raises(NotImplementedError):
        store.count("c")
    with pytest.raises(NotImplementedError):
        store.ensure_collection("c")


@pytest.mark.parametrize("message", ["does not exist", "not found", "not exists", "unknown collection"])
def test_missing_collection_error_detection(message):
    assert vector_store._looks_like_missing_collection(RuntimeError(message)) is True
    assert vector_store._looks_like_missing_collection(RuntimeError("connection refused")) is False


def test_vector_batch_validation_rejects_length_mismatch_before_backend_call():
    chroma = vector_store.ChromaStore.__new__(vector_store.ChromaStore)
    chroma.client = FakeChromaClient()
    with pytest.raises(ValueError, match="texts=1, embeddings=0"):
        chroma.add("alarms", ["text"], [], [{}], ["s1"])
    assert chroma.client.calls == []

    qdrant = vector_store.QdrantStore.__new__(vector_store.QdrantStore)
    qdrant.client = FakeQdrantClient()
    qdrant.qm = FakeModels
    with pytest.raises(ValueError, match="metadatas=0"):
        qdrant.add("alarms", ["text"], [[1.0]], [], ["s1"])
    assert qdrant.client.calls == []


def test_get_store_selects_backend_and_reports_missing_qdrant_dependency(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE", "chroma")
    with pytest.raises(RuntimeError, match="Chroma dependency has known code-execution"):
        vector_store.get_store()

    monkeypatch.setenv("VECTOR_STORE", "none")
    assert isinstance(vector_store.get_store(), vector_store.DisabledVectorStore)

    monkeypatch.setenv("VECTOR_STORE", "qdrant")
    with (
        patch.dict(sys.modules, {"qdrant_client": ModuleType("qdrant_client")}),
        patch.object(vector_store, "QdrantStore", return_value="qdrant"),
    ):
        assert vector_store.get_store() == "qdrant"

    with patch.dict(sys.modules, {"qdrant_client": None}):
        with pytest.raises(RuntimeError, match="qdrant-client not installed"):
            vector_store.get_store()


def test_chroma_adapter_lifecycle_and_operations(monkeypatch):
    client = FakeChromaClient()
    chromadb = ModuleType("chromadb")
    chromadb.PersistentClient = lambda path: client
    monkeypatch.setitem(sys.modules, "chromadb", chromadb)
    monkeypatch.setenv("DB_PATH", "test-db")
    store = vector_store.ChromaStore()

    store.ping()
    store.ensure_collection("alarms")
    store.ensure_collection("alarms")
    store.add("alarms", ["text"], [[1.0]], [{"code": "3000"}], ["s1"])
    assert store.query("alarms", [[1.0]], 3, {"code": {"$eq": "3000"}}) == {"ids": [["s1"]]}
    store.delete("alarms", {"doc_id": {"$eq": "doc-1"}})
    store.delete("alarms")
    assert store.count("alarms") == 7
    store.delete_collection("alarms")

    assert ("create_collection", {"name": "alarms", "metadata": {"hnsw:space": "cosine"}}) in client.calls
    assert client.collection.calls[-2:] == [
        ("delete", {"where": {"doc_id": {"$eq": "doc-1"}}}),
        ("delete", {"where": {}}),
    ]


def test_chroma_delete_collection_warns_on_backend_failure():
    class FailingClient:
        def delete_collection(self, _name):
            raise RuntimeError("connection refused")

    store = vector_store.ChromaStore.__new__(vector_store.ChromaStore)
    store.client = FailingClient()
    with patch.object(vector_store, "_warn") as warn:
        store.delete_collection("alarms")
    warn.assert_called_once()


def qdrant_module(client_calls):
    root = ModuleType("qdrant_client")

    def constructor(**kwargs):
        client_calls.append(kwargs)
        return SimpleNamespace()

    root.QdrantClient = constructor
    http = ModuleType("qdrant_client.http")
    http.models = FakeModels
    return {"qdrant_client": root, "qdrant_client.http": http}


def test_qdrant_init_enforces_key_and_tls_boundary(monkeypatch):
    calls = []
    monkeypatch.setenv("QDRANT_HOST", "remote.example")
    monkeypatch.setenv("QDRANT_HTTPS", "false")
    with patch.dict(sys.modules, qdrant_module(calls)):
        with patch.object(vector_store, "secret_value", return_value=""):
            with pytest.raises(RuntimeError, match="must be configured"):
                vector_store.QdrantStore()
        with patch.object(vector_store, "secret_value", return_value="secret"):
            with pytest.raises(RuntimeError, match="require TLS"):
                vector_store.QdrantStore()

    monkeypatch.setenv("QDRANT_INSECURE_TRUSTED_HOSTS", "remote.example")
    with patch.dict(sys.modules, qdrant_module(calls)):
        with patch.object(vector_store, "secret_value", return_value="secret"):
            vector_store.QdrantStore()
    assert calls[-1]["https"] is False
    assert calls[-1]["timeout"] == 5

    monkeypatch.setenv("QDRANT_HTTPS", "true")
    monkeypatch.setenv("QDRANT_PORT", "7443")
    with patch.dict(sys.modules, qdrant_module(calls)):
        with patch.object(vector_store, "secret_value", return_value="secret"):
            vector_store.QdrantStore()
    assert calls[-1] == {
        "host": "remote.example",
        "port": 7443,
        "api_key": "secret",
        "https": True,
        "timeout": 5,
    }


def test_qdrant_collection_batch_query_delete_and_count(monkeypatch):
    client = FakeQdrantClient()
    store = vector_store.QdrantStore.__new__(vector_store.QdrantStore)
    store.client = client
    store.qm = FakeModels
    monkeypatch.setenv("QDRANT_VECTOR_SIZE", "3")
    monkeypatch.setenv("QDRANT_UPSERT_BATCH_SIZE", "2")

    store.ping()
    client.collection_names = ["alarms"]
    store.ensure_collection("alarms")
    assert not [call for call in client.calls if call[0] == "create_collection"]

    client.collection_names = []
    store.ensure_collection("alarms")
    create = [call for call in client.calls if call[0] == "create_collection"][-1][1]
    assert create["vectors_config"] == ("VectorParams", {"size": 3, "distance": "cosine"})

    client.list_error = RuntimeError("list unavailable")
    with patch.object(vector_store, "_warn") as warn:
        store.ensure_collection("fallback")
    warn.assert_called_once()
    client.list_error = None

    store.add(
        "alarms",
        ["a", "b", "c"],
        [[1.0], [2.0], [3.0]],
        [{"code": "1"}, {"code": "2"}, {"code": "3"}],
        ["s1", "s2", "s3"],
    )
    upserts = [call[1] for call in client.calls if call[0] == "upsert"]
    assert len(upserts) == 2
    assert upserts[0]["points"][1]["ids"] == [1, 2]
    assert upserts[0]["points"][1]["payloads"][0]["__text__"] == "a"

    client.hits = [SimpleNamespace(id=2, payload={"code": "3000", "__text__": "hit"})]
    result = store.query("alarms", [[0.5]], 5, {"code": {"$eq": "3000"}})
    assert result == {"documents": [["hit"]], "ids": [["s2"]], "metadatas": [[{"code": "3000"}]]}
    search = [call[1] for call in client.calls if call[0] == "search"][-1]
    assert search["query_filter"][0] == "Filter"

    store.delete("alarms", {"code": {"$eq": "3000"}})
    store.delete("alarms")
    deletes = [call[1] for call in client.calls if call[0] == "delete"]
    assert deletes[0]["points_selector"][0] == "FilterSelector"
    assert deletes[1]["points_selector"] == ("AllSelector", {})
    assert store.count("alarms") == 9

    client.count_error = RuntimeError("count failed")
    with patch.object(vector_store, "_warn") as warn:
        assert store.count("alarms") == 0
    warn.assert_called_once()


def test_qdrant_id_filter_and_delete_warning_branches():
    assert vector_store.QdrantStore._to_int_id("s42") == 42
    assert vector_store.QdrantStore._to_int_id(7) == 7
    assert vector_store.QdrantStore._to_str_id(8) == "s8"

    store = vector_store.QdrantStore.__new__(vector_store.QdrantStore)
    store.qm = FakeModels
    built = store._build_filter({"code": {"$eq": "3000"}, "ignored": {"$in": ["x"]}, "scalar": "x"})
    assert len(built[1]["must"]) == 1

    class MissingClient:
        def delete_collection(self, _name):
            raise RuntimeError("unknown collection")

    store.client = MissingClient()
    with patch.object(vector_store, "_warn") as warn:
        store.delete_collection("missing")
    warn.assert_not_called()
