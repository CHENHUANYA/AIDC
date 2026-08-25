import json
import hashlib
from urllib import parse
from unittest.mock import patch

from scripts import rag_runtime_check as runtime


class FakeLiveClient:
    def __init__(self, tokenizer_version="unicode-domain-v2"):
        self.tokenizer_version = tokenizer_version

    def request_json(self, path, method="GET", payload=None):
        del method, payload
        parsed = parse.urlparse(path)
        query = parse.parse_qs(parsed.query).get("query", [""])[0]
        if "coolant" in query:
            result = {
                "text": "coolant pressure pump nozzle",
                "code": "340100",
                "source": "manual-a",
                "id": "ragcite_a",
            }
        else:
            result = {
                "text": "hydraulic clamp pressure switch",
                "code": "5100",
                "source": "manual-b",
                "id": "ragcite_b",
            }
        return 200, {
            "ready": True,
            "tokenizer_version": self.tokenizer_version,
            "results": [result],
        }


def write_dataset(path):
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "dataset_version": "live-test-v1",
            "review_status": "engineering",
            "thresholds": {
                "recall_at_k": 1.0,
                "mrr": 1.0,
                "evidence_coverage_rate": 1.0,
                "source_hit_rate": 1.0,
            },
            "cases": [
                {
                    "id": "coolant",
                    "collection": "808d",
                    "query": "coolant pressure",
                    "expected_codes": ["340100"],
                    "expected_sources": ["manual-a"],
                    "required_term_groups": [["coolant"], ["pump"]],
                },
                {
                    "id": "hydraulic",
                    "collection": "808d",
                    "query": "hydraulic clamp",
                    "expected_codes": ["5100"],
                    "expected_sources": ["manual-b"],
                    "required_term_groups": [["hydraulic"], ["switch"]],
                },
            ],
        }),
        encoding="utf-8",
    )


def write_split(path, dataset):
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    path.write_text(json.dumps({
        "schema_version": 1,
        "split_version": "live-split-v1",
        "dataset_version": "live-test-v1",
        "dataset_sha256": digest,
        "claim_boundary": "Test fixture only.",
        "assignments": {"development": ["coolant"], "heldout": ["hydraulic"]},
    }), encoding="utf-8")


def test_live_gold_gate_uses_structured_retrieve_endpoint(tmp_path):
    dataset = tmp_path / "gold.json"
    write_dataset(dataset)

    check, report = runtime.check_gold_retrieval(FakeLiveClient(), dataset, top_k=5)

    assert check.status == "PASS"
    assert report["metrics"]["recall_at_k"] == 1.0
    assert report["metrics"]["evidence_coverage_rate"] == 1.0
    assert report["runtime_tokenizer_versions"] == {"808d": ["unicode-domain-v2"]}
    assert report["transport_errors"] == []


def test_live_gold_gate_defaults_can_scope_to_development_without_calling_heldout(tmp_path):
    dataset = tmp_path / "gold.json"
    split = tmp_path / "split.json"
    write_dataset(dataset)
    write_split(split, dataset)

    check, report = runtime.check_gold_retrieval(
        FakeLiveClient(),
        dataset,
        top_k=5,
        split_manifest_path=split,
        scope="development",
    )

    assert check.status == "PASS"
    assert report["scope"] == "development"
    assert report["metrics"]["case_count"] == 1
    assert [case["id"] for case in report["cases"]] == ["coolant"]


def test_live_gold_gate_rejects_stale_runtime_tokenizer(tmp_path):
    dataset = tmp_path / "gold.json"
    write_dataset(dataset)

    check, report = runtime.check_gold_retrieval(
        FakeLiveClient(tokenizer_version="legacy-whitespace-v0"),
        dataset,
        top_k=5,
    )

    assert check.status == "FAIL"
    assert "expected=unicode-domain-v2" in report["transport_errors"][0]


def test_chat_gate_requires_structured_expected_citation():
    class ChatClient:
        def request_json(self, _path, _method="GET", _payload=None):
            return 200, {
                "id": "chatcmpl_1",
                "choices": [{"message": {"content": "A sufficiently detailed maintenance response."}}],
                "rag": {
                    "answer_id": "chatcmpl_1",
                    "citations": [{
                        "code": "3000",
                        "id": "ragcite_1",
                        "source_id": "manual-v1",
                        "source_file": "manual.pdf",
                        "section_id": "manual-v1:s1",
                        "locator": "p.58#alarm-3000",
                        "source_hash": "a" * 64,
                        "official_source": True,
                    }],
                },
            }

    check = runtime.check_chat(ChatClient(), "808d", "Alarm 3000", "3000")

    assert check.status == "PASS"
    assert "citations=1" in check.detail
    assert "answer_id=True" in check.detail
    assert "traceable=True" in check.detail


def test_chat_gate_rejects_citation_without_traceability():
    class ChatClient:
        def request_json(self, _path, _method="GET", _payload=None):
            return 200, {
                "id": "chatcmpl_1",
                "choices": [{"message": {"content": "A sufficiently detailed maintenance response."}}],
                "rag": {"answer_id": "chatcmpl_1", "citations": [{"code": "3000"}]},
            }

    check = runtime.check_chat(ChatClient(), "808d", "Alarm 3000", "3000")

    assert check.status == "FAIL"
    assert "traceable=False" in check.detail


def test_traceability_health_gate_requires_full_coverage():
    health = {
        "collections": {
            "808d": {
                "alarms_indexed": 2,
                "traceability": {"traceable_sections": 2, "traceability_ready": True},
            }
        }
    }
    assert runtime.check_traceability_coverage(health).status == "PASS"
    health["collections"]["808d"]["traceability"]["traceable_sections"] = 1
    assert runtime.check_traceability_coverage(health).status == "FAIL"


def test_answer_snapshot_provider_accepts_retrieval_and_rejects_missing_provider():
    class SnapshotClient:
        def __init__(self, provider):
            self.provider = provider

        def request_json(self, _path):
            return 200, {
                "answer": {
                    "provider": self.provider,
                    "answer_state": "complete",
                }
            }

    check, provider = runtime.check_answer_snapshot_provider(SnapshotClient("retrieval"), "answer-1")
    missing, _ = runtime.check_answer_snapshot_provider(SnapshotClient(""), "answer-2")

    assert check.status == "PASS"
    assert provider == "retrieval"
    assert missing.status == "FAIL"


def test_reranker_gate_requires_successful_runtime_inference():
    health = {
        "collections": {
            "808d": {
                "retrieval_runtime": {
                    "reranker_loaded": True,
                    "reranker_active": True,
                    "reranker_calls": 3,
                    "last_retrieval_mode": "reranker",
                    "last_reranker_error": "",
                }
            }
        }
    }
    assert runtime.check_reranker_runtime(health, "808d", require=True).status == "PASS"
    health["collections"]["808d"]["retrieval_runtime"]["reranker_active"] = False
    assert runtime.check_reranker_runtime(health, "808d", require=True).status == "FAIL"


def test_runtime_markdown_discloses_live_gate_boundary():
    report = {
        "status": "pass",
        "git_revision": "abc",
        "base_url": "http://localhost:8100",
        "gold_dataset": "mock_data/rag_gold_v1.json",
        "gold_split_manifest": "mock_data/rag_evaluation_split_v1.json",
        "gold_scope": "development",
        "checks": [{"name": "rag:gold-dataset", "status": "PASS", "detail": "ok"}],
        "gold_retrieval": {"metrics": {"recall_at_k": 1.0}},
    }

    text = runtime.markdown_report(report)

    assert "structured citations" in text
    assert "does not replace technician review" in text
    assert runtime.report_path(runtime.ROOT / "mock_data" / "rag_gold_v1.json") == "mock_data/rag_gold_v1.json"


def test_qdrant_count_sends_api_key_without_exposing_it():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"result":{"points_count":42}}'

    captured = {}

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        return Response()

    with patch.object(runtime.request, "urlopen", side_effect=fake_urlopen):
        count = runtime.qdrant_count("http://qdrant:6333", "808d", 7, "secret-value")

    assert count == 42
    assert captured["request"].get_header("Api-key") == "secret-value"
    assert captured["timeout"] == 7


def test_stream_contract_requires_shared_answer_id_and_expected_citation():
    body = "\n\n".join([
        "data: " + json.dumps({
            "id": "chatcmpl_1",
            "choices": [{"delta": {"content": "answer"}, "finish_reason": None}],
            "rag": {
                "answer_id": "chatcmpl_1",
                "citations": [{
                    "id": "ragcite_1",
                    "code": "3000",
                    "source_id": "manual-v1",
                    "source_file": "manual.pdf",
                    "section_id": "manual-v1:s1",
                    "locator": "p.58#alarm-3000",
                    "source_hash": "b" * 64,
                    "official_source": True,
                }],
            },
        }),
        "data: " + json.dumps({
            "id": "chatcmpl_1",
            "choices": [{"delta": {}, "finish_reason": "stop"}],
        }),
        "data: [DONE]",
        "",
    ])

    ok, detail = runtime.validate_stream_contract(body, "3000")

    assert ok is True
    assert detail == {
        "events": 2,
        "ids": 1,
        "citations": 1,
        "expected_code": True,
        "traceable": True,
        "answer_id": True,
        "done": True,
    }


def test_stream_gate_requires_multiple_content_events():
    def sse_body(parts):
        events = []
        for index, part in enumerate(parts):
            event = {
                "id": "chatcmpl_1",
                "choices": [{"delta": {"content": part}, "finish_reason": None}],
            }
            if index == 0:
                event["rag"] = {
                    "answer_id": "chatcmpl_1",
                    "citations": [{
                        "id": "ragcite_1",
                        "code": "3000",
                        "source_id": "manual-v1",
                        "source_file": "manual.pdf",
                        "section_id": "manual-v1:s1",
                        "locator": "p.58#alarm-3000",
                        "source_hash": "c" * 64,
                        "official_source": True,
                    }],
                }
            events.append(f"data: {json.dumps(event)}\n\n")
        events.extend([
            'data: {"id":"chatcmpl_1","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            "data: [DONE]\n\n",
        ])
        return events

    class Response:
        def __init__(self, lines):
            self.lines = iter(lines)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def readline(self):
            return next(self.lines, "").encode("utf-8")

    client = runtime.RuntimeClient("http://localhost:8100", 10)
    client.token = "token"
    with patch.object(runtime.request, "urlopen", return_value=Response(sse_body(["first", "second"]))):
        incremental = runtime.check_stream_chat(client, "808d", "Alarm 3000", "3000")
    with patch.object(runtime.request, "urlopen", return_value=Response(sse_body(["single"]))):
        buffered = runtime.check_stream_chat(client, "808d", "Alarm 3000", "3000")

    assert incremental.status == "PASS"
    assert "incremental=True" in incremental.detail
    assert "traceable=True" in incremental.detail
    assert buffered.status == "FAIL"
    assert "incremental=False" in buffered.detail
