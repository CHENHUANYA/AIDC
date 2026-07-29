"""
vector_store.py - abstraction layer for interchangeable vector backends (Chroma or Qdrant).
"""
import logging
import os
import warnings
from typing import List, Optional

from secret_values import secret_value
from config_values import env_int


logger = logging.getLogger("alarm_rag.vector_store")


def _warn(message: str):
    logger.warning(message)


def _looks_like_missing_collection(exc: Exception) -> bool:
    detail = str(exc).lower()
    return any(token in detail for token in ["does not exist", "not found", "not exists", "unknown collection"])


class BaseVectorStore:
    def ping(self) -> None:
        raise NotImplementedError

    def add(self, collection: str, texts: List[str], embeddings: List[list], metadatas: List[dict], ids: List[str]):
        raise NotImplementedError

    def query(self, collection: str, query_embeddings: List[list], n_results: int, where: Optional[dict] = None) -> dict:
        raise NotImplementedError

    def delete(self, collection: str, where: Optional[dict] = None):
        """Delete points/documents that match the filter."""
        raise NotImplementedError

    def delete_collection(self, collection: str):
        raise NotImplementedError

    def count(self, collection: str) -> int:
        raise NotImplementedError

    def ensure_collection(self, collection: str):
        raise NotImplementedError


def get_store():
    backend = os.getenv("VECTOR_STORE", "chroma").lower()
    if backend == "qdrant":
        try:
            import qdrant_client  # noqa: F401
        except Exception as e:
            raise RuntimeError("VECTOR_STORE=qdrant but qdrant-client not installed") from e
        return QdrantStore()
    return ChromaStore()


class ChromaStore(BaseVectorStore):
    def __init__(self):
        import chromadb
        self.client = chromadb.PersistentClient(path=os.getenv("DB_PATH", "./alarm_db"))

    def ping(self) -> None:
        self.client.heartbeat()

    def ensure_collection(self, collection: str):
        try:
            self.client.get_collection(collection)
        except Exception:
            self.client.create_collection(collection, metadata={"hnsw:space": "cosine"})

    def delete_collection(self, collection: str):
        try:
            self.client.delete_collection(collection)
        except Exception as exc:
            if not _looks_like_missing_collection(exc):
                _warn(f"Unable to delete Chroma collection {collection}: {exc}")

    def add(self, collection: str, texts: List[str], embeddings: List[list], metadatas: List[dict], ids: List[str]):
        col = self.client.get_collection(collection)
        col.add(documents=texts, embeddings=embeddings, metadatas=metadatas, ids=ids)

    def query(self, collection: str, query_embeddings: List[list], n_results: int, where: Optional[dict] = None) -> dict:
        col = self.client.get_collection(collection)
        return col.query(query_embeddings=query_embeddings, n_results=n_results, where=where)

    def delete(self, collection: str, where: Optional[dict] = None):
        col = self.client.get_collection(collection)
        if where:
            col.delete(where=where)
        else:
            col.delete(where={})

    def count(self, collection: str) -> int:
        col = self.client.get_collection(collection)
        return col.count()


class QdrantStore(BaseVectorStore):
    def __init__(self):
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm
        host = os.getenv("QDRANT_HOST", "localhost")
        port = env_int("QDRANT_PORT", 6333, minimum=1, maximum=65535)
        use_https = os.getenv("QDRANT_HTTPS", "false").strip().lower() in {"1", "true", "yes", "on"}
        api_key = secret_value("QDRANT_API_KEY")
        if not api_key:
            raise RuntimeError("QDRANT_API_KEY or QDRANT_API_KEY_FILE must be configured")
        if not use_https:
            trusted_hosts = {
                item.strip().lower()
                for item in os.getenv(
                    "QDRANT_INSECURE_TRUSTED_HOSTS",
                    "qdrant,localhost,127.0.0.1,::1",
                ).split(",")
                if item.strip()
            }
            if host.strip().lower() not in trusted_hosts:
                raise RuntimeError(
                    "Qdrant API keys require TLS for remote hosts. Set QDRANT_HTTPS=true, "
                    "or explicitly add a trusted private-network host to QDRANT_INSECURE_TRUSTED_HOSTS."
                )
            # qdrant-client warns for every authenticated HTTP connection even
            # when it is an explicitly trusted loopback/container-network hop.
            # Keep the dependency warning visible for all other configurations.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="Api key is used with an insecure connection.",
                    category=UserWarning,
                    module=r"qdrant_client\..*",
                )
                self.client = QdrantClient(host=host, port=port, api_key=api_key, https=False)
        else:
            self.client = QdrantClient(host=host, port=port, api_key=api_key, https=True)
        self.qm = qm

    def ping(self) -> None:
        self.client.get_collections()

    def ensure_collection(self, collection: str):
        size = env_int("QDRANT_VECTOR_SIZE", 1024, minimum=1)
        try:
            cols = [c.name for c in self.client.get_collections().collections]
            if collection in cols:
                return
        except Exception as exc:
            _warn(f"Unable to list Qdrant collections before ensuring {collection}: {exc}")
        self.client.create_collection(
            collection_name=collection,
            vectors_config=self.qm.VectorParams(size=size, distance=self.qm.Distance.COSINE),
        )

    def delete_collection(self, collection: str):
        try:
            self.client.delete_collection(collection)
        except Exception as exc:
            if not _looks_like_missing_collection(exc):
                _warn(f"Unable to delete Qdrant collection {collection}: {exc}")

    @staticmethod
    def _to_int_id(sid: str) -> int:
        """Convert 's123' string id to int 123 for Qdrant."""
        return int(sid.replace("s", "")) if isinstance(sid, str) else int(sid)

    @staticmethod
    def _to_str_id(iid: int) -> str:
        """Convert int id back to 's123' format."""
        return f"s{iid}"

    def add(self, collection: str, texts: List[str], embeddings: List[list], metadatas: List[dict], ids: List[str]):
        self.ensure_collection(collection)

        batch_size = env_int("QDRANT_UPSERT_BATCH_SIZE", 64, minimum=1)
        int_ids = [self._to_int_id(i) for i in ids]
        payloads = []
        for meta, text in zip(metadatas, texts):
            payload = dict(meta)
            payload["__text__"] = text
            payloads.append(payload)

        for start in range(0, len(int_ids), batch_size):
            end = start + batch_size
            self.client.upsert(
                collection_name=collection,
                points=self.qm.Batch(
                    ids=int_ids[start:end],
                    vectors=embeddings[start:end],
                    payloads=payloads[start:end],
                ),
                wait=True,
            )

    def query(self, collection: str, query_embeddings: List[list], n_results: int, where: Optional[dict] = None) -> dict:
        res = self.client.search(
            collection_name=collection,
            query_vector=query_embeddings[0],
            limit=n_results,
            query_filter=self._build_filter(where) if where else None,
        )
        return {
            "documents": [[hit.payload.get("__text__", "") for hit in res]],
            "ids": [[self._to_str_id(hit.id) for hit in res]],
            "metadatas": [[{k: v for k, v in hit.payload.items() if k != "__text__"} for hit in res]],
        }

    def delete(self, collection: str, where: Optional[dict] = None):
        self.client.delete(
            collection_name=collection,
            points_selector=self.qm.FilterSelector(filter=self._build_filter(where)) if where else self.qm.AllSelector(),
            wait=True,
        )

    def count(self, collection: str) -> int:
        try:
            info = self.client.get_collection(collection)
            return info.points_count or 0
        except Exception as exc:
            _warn(f"Unable to count Qdrant collection {collection}: {exc}")
            return 0

    def _build_filter(self, where: dict):
        must = []
        for key, cond in (where or {}).items():
            if isinstance(cond, dict) and "$eq" in cond:
                must.append(self.qm.FieldCondition(key=key, match=self.qm.MatchValue(value=cond["$eq"])) )
        return self.qm.Filter(must=must)
