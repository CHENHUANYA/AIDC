import json
from urllib import parse
from unittest.mock import patch

from scripts import rag_runtime_check as runtime


class FakeLiveClient:
    def __init__(self, tokenizer_version="unicode-domain-v1"):
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


def test_live_gold_gate_uses_structured_retrieve_endpoint(tmp_path):
    dataset = tmp_path / "gold.json"
    write_dataset(dataset)

    check, report = runtime.check_gold_retrieval(FakeLiveClient(), dataset, top_k=5)

    assert check.status == "PASS"
    assert report["metrics"]["recall_at_k"] == 1.0
    assert report["metrics"]["evidence_coverage_rate"] == 1.0
    assert report["runtime_tokenizer_versions"] == {"808d": ["unicode-domain-v1"]}
    assert report["transport_errors"] == []


def test_live_gold_gate_rejects_stale_runtime_tokenizer(tmp_path):
    dataset = tmp_path / "gold.json"
    write_dataset(dataset)

    check, report = runtime.check_gold_retrieval(
        FakeLiveClient(tokenizer_version="legacy-whitespace-v0"),
        dataset,
        top_k=5,
    )

    assert check.status == "FAIL"
    assert "expected=unicode-domain-v1" in report["transport_errors"][0]


def test_chat_gate_requires_structured_expected_citation():
    class ChatClient:
        def request_json(self, _path, _method="GET", _payload=None):
            return 200, {
                "choices": [{"message": {"content": "A sufficiently detailed maintenance response."}}],
                "rag": {"citations": [{"code": "3000", "id": "ragcite_1"}]},
            }

    check = runtime.check_chat(ChatClient(), "808d", "Alarm 3000", "3000")

    assert check.status == "PASS"
    assert "citations=1" in check.detail


def test_runtime_markdown_discloses_live_gate_boundary():
    report = {
        "status": "pass",
        "git_revision": "abc",
        "base_url": "http://localhost:8100",
        "gold_dataset": "mock_data/rag_gold_v1.json",
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
