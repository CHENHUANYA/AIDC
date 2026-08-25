from __future__ import annotations

import pickle

import pytest

from signed_pickle import (
    SignedPickleError,
    dump_signed_pickle,
    load_signed_pickle,
    sign_existing_pickle,
    signature_path,
)


TEST_KEY = "test-index-signing-key-that-is-at-least-thirty-two-bytes"


def test_signed_pickle_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARM_RAG_INDEX_SIGNING_KEY", TEST_KEY)
    path = tmp_path / "bm25_demo.pkl"

    dump_signed_pickle(path, {"sections": [{"text": "safe"}]})

    assert load_signed_pickle(path) == {"sections": [{"text": "safe"}]}
    assert signature_path(path).is_file()


def test_unsigned_and_tampered_pickles_are_rejected_before_deserialization(tmp_path, monkeypatch):
    monkeypatch.setenv("ALARM_RAG_INDEX_SIGNING_KEY", TEST_KEY)
    path = tmp_path / "bm25_demo.pkl"
    path.write_bytes(pickle.dumps({"sections": []}))

    with pytest.raises(SignedPickleError, match="signature is missing"):
        load_signed_pickle(path)

    sign_existing_pickle(path)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(SignedPickleError, match="verification failed"):
        load_signed_pickle(path)


def test_key_rotation_invalidates_old_signatures(tmp_path, monkeypatch):
    path = tmp_path / "bm25_demo.pkl"
    monkeypatch.setenv("ALARM_RAG_INDEX_SIGNING_KEY", TEST_KEY)
    dump_signed_pickle(path, {"sections": []})

    monkeypatch.setenv("ALARM_RAG_INDEX_SIGNING_KEY", "a-different-index-signing-key-with-thirty-two-bytes")
    with pytest.raises(SignedPickleError, match="verification failed"):
        load_signed_pickle(path)


def test_placeholder_or_short_signing_key_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "bm25_demo.pkl"
    monkeypatch.setenv("ALARM_RAG_INDEX_SIGNING_KEY", "short")

    with pytest.raises(SignedPickleError, match="at least 32"):
        dump_signed_pickle(path, {"sections": []})
