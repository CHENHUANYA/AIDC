from unittest.mock import MagicMock, patch

import pytest

import ingest
from signed_pickle import load_signed_pickle


class FakePage:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text


class FakeDocument:
    def __init__(self, *pages: str) -> None:
        self.pages = [FakePage(page) for page in pages]

    def __iter__(self):
        return iter(self.pages)


def test_alarm_parser_filters_headers_page_numbers_and_preserves_sections() -> None:
    document = FakeDocument(
        "\n".join(
            [
                "SINUMERIK 808D",
                "3000",
                "Drive failure",
                "Explanation:",
                "Motor overheated",
                "Check the power cable",
                "42",
                "Diagnostics Manual",
                "4000",
                "Encoder failure",
                "Reset the drive",
                "Inspect the encoder module",
            ]
        )
    )
    with patch.object(ingest.fitz, "open", return_value=document):
        sections = ingest.extract_alarm_sections("manual.pdf")

    assert [section["code"] for section in sections] == ["3000", "4000"]
    assert sections[0]["title"] == "Drive failure"
    assert "Explanation:" not in sections[0]["text"]
    assert "42" not in sections[0]["text"]


def test_general_chunk_parser_classifies_content_and_validates_window() -> None:
    document = FakeDocument(
        "\n".join(
            [
                "Legal notice and warranty terms",
                "Copyright applies to this manual",
                "Startup and commissioning procedure",
                "Power on the control module",
                "Backup and restore procedure",
                "Save data to the archive",
                "Hardware connector reference",
                "Inspect each terminal cable",
                "General maintenance guidance",
                "Follow the documented procedure",
            ]
        )
    )
    with patch.object(ingest.fitz, "open", return_value=document):
        chunks = ingest.extract_general_chunks("manual.pdf", chunk_size=2, overlap=0)

    assert [chunk["content_type"] for chunk in chunks] == [
        "license",
        "procedure",
        "procedure",
        "hardware",
        "procedure",
    ]
    assert [chunk["topic"] for chunk in chunks] == ["license", "startup", "backup", "hardware", "general"]
    assert all(chunk["type"] == "general" and chunk["code"] == "" for chunk in chunks)

    with pytest.raises(ValueError, match="greater than zero"):
        ingest.extract_general_chunks("manual.pdf", chunk_size=0)
    with pytest.raises(ValueError, match="chunk_size - 1"):
        ingest.extract_general_chunks("manual.pdf", chunk_size=4, overlap=4)


def test_general_chunk_parser_returns_empty_for_noise_only_document() -> None:
    document = FakeDocument("SINUMERIK\n1\n-\nExplanation:")
    with patch.object(ingest.fitz, "open", return_value=document):
        assert ingest.extract_general_chunks("manual.pdf") == []


def test_build_index_writes_vector_metadata_and_bm25_payload(tmp_path) -> None:
    sections = [
        {"code": "3000", "title": "Alarm", "text": "drive alarm text", "page": 1},
        {"title": "Procedure", "text": "general procedure text", "page": 2},
    ]
    store = MagicMock()
    store.delete_collection.side_effect = RuntimeError("missing")
    embeddings = MagicMock()
    embeddings.tolist.return_value = [[1.0, 0.0], [0.0, 1.0]]
    embedder = MagicMock()
    embedder.encode.return_value = embeddings
    bm25 = "bm25-index"

    with (
        patch.object(ingest, "DB_PATH", str(tmp_path)),
        patch.object(ingest, "ensure_db_dir"),
        patch.object(ingest, "get_store", return_value=store),
        patch.object(ingest, "SentenceTransformer", return_value=embedder),
        patch.object(ingest, "BM25Okapi", return_value=bm25) as bm25_class,
    ):
        ingest.build_index(sections, "808d")

    store.ensure_collection.assert_called_once_with("808d")
    add_call = store.add.call_args.kwargs
    assert add_call["texts"] == ["drive alarm text", "general procedure text"]
    assert add_call["ids"] == ["s0", "s1"]
    assert add_call["metadatas"][0]["type"] == "alarm"
    assert add_call["metadatas"][1]["type"] == "general"
    assert all("text" not in metadata for metadata in add_call["metadatas"])
    bm25_class.assert_called_once()

    payload = load_signed_pickle(tmp_path / "bm25_808d.pkl")
    assert payload["bm25"] == bm25
    assert payload["sections"] == sections
    assert payload["tokenizer_version"] == ingest.BM25_TOKENIZER_VERSION
