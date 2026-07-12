import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app_context import ChatRequest, Message
from routes import chat_lookup_routes


class FakeEngine:
    ready = True

    def retrieve(self, _query, top_k=4):
        return [{
            "text": "3000 NC start condition missing Remedy: Check machine ready signal.",
            "meta": {"code": "3000", "page": 12, "title": "NC start condition missing"},
        }]


def http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://school.example/chat/completions")
    response = httpx.Response(status_code, request=request, text="error")
    return httpx.HTTPStatusError("school failed", request=request, response=response)


class LlmProviderMatrixTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_provider = chat_lookup_routes.LLM_PROVIDER
        self.previous_fallback = chat_lookup_routes.SCHOOL_API_FALLBACK_TO_OLLAMA
        self.previous_source = chat_lookup_routes.last_llm_source
        self.addCleanup(self._restore)

    def _restore(self):
        chat_lookup_routes.LLM_PROVIDER = self.previous_provider
        chat_lookup_routes.SCHOOL_API_FALLBACK_TO_OLLAMA = self.previous_fallback
        chat_lookup_routes.last_llm_source = self.previous_source

    async def test_school_success_does_not_call_ollama(self):
        chat_lookup_routes.LLM_PROVIDER = "school"
        chat_lookup_routes.SCHOOL_API_FALLBACK_TO_OLLAMA = True

        with patch.object(chat_lookup_routes, "call_school_api", new=AsyncMock(return_value="school answer")):
            with patch.object(chat_lookup_routes, "call_ollama", new=AsyncMock(return_value="ollama answer")) as ollama:
                content = await chat_lookup_routes.call_llm([{"role": "user", "content": "q"}], 0.1, 64)

        self.assertEqual("school answer", content)
        self.assertEqual("school", chat_lookup_routes.last_llm_source)
        ollama.assert_not_called()

    async def test_school_5xx_falls_back_to_ollama(self):
        chat_lookup_routes.LLM_PROVIDER = "school"
        chat_lookup_routes.SCHOOL_API_FALLBACK_TO_OLLAMA = True

        with patch.object(chat_lookup_routes, "call_school_api", new=AsyncMock(side_effect=http_status_error(503))):
            with patch.object(chat_lookup_routes, "call_ollama", new=AsyncMock(return_value="ollama fallback")):
                content = await chat_lookup_routes.call_llm([{"role": "user", "content": "q"}], 0.1, 64)

        self.assertEqual("ollama fallback", content)
        self.assertEqual("ollama", chat_lookup_routes.last_llm_source)

    async def test_school_timeout_falls_back_to_ollama(self):
        chat_lookup_routes.LLM_PROVIDER = "school"
        chat_lookup_routes.SCHOOL_API_FALLBACK_TO_OLLAMA = True

        with patch.object(chat_lookup_routes, "call_school_api", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))):
            with patch.object(chat_lookup_routes, "call_ollama", new=AsyncMock(return_value="ollama after timeout")):
                content = await chat_lookup_routes.call_llm([{"role": "user", "content": "q"}], 0.1, 64)

        self.assertEqual("ollama after timeout", content)
        self.assertEqual("ollama", chat_lookup_routes.last_llm_source)

    async def test_school_4xx_does_not_fallback(self):
        chat_lookup_routes.LLM_PROVIDER = "school"
        chat_lookup_routes.SCHOOL_API_FALLBACK_TO_OLLAMA = True

        with patch.object(chat_lookup_routes, "call_school_api", new=AsyncMock(side_effect=http_status_error(401))):
            with patch.object(chat_lookup_routes, "call_ollama", new=AsyncMock(return_value="ollama answer")) as ollama:
                with self.assertRaises(httpx.HTTPStatusError):
                    await chat_lookup_routes.call_llm([{"role": "user", "content": "q"}], 0.1, 64)

        ollama.assert_not_called()

    async def test_concurrent_requests_keep_request_local_provider_source(self):
        chat_lookup_routes.LLM_PROVIDER = "school"
        chat_lookup_routes.SCHOOL_API_FALLBACK_TO_OLLAMA = True

        async def school(messages, _temperature, _max_tokens):
            prompt = messages[-1]["content"]
            await asyncio.sleep(0.02)
            if prompt == "fallback":
                raise httpx.TimeoutException("timed out")
            return "school answer"

        async def ollama(_messages, _temperature, _max_tokens):
            await asyncio.sleep(0.02)
            return "ollama answer"

        async def invoke(prompt):
            content = await chat_lookup_routes.call_llm([{"role": "user", "content": prompt}], 0.1, 64)
            return content, chat_lookup_routes.request_llm_source.get()

        with (
            patch.object(chat_lookup_routes, "call_school_api", side_effect=school),
            patch.object(chat_lookup_routes, "call_ollama", side_effect=ollama),
        ):
            school_result, fallback_result = await asyncio.gather(invoke("school"), invoke("fallback"))

        self.assertEqual(("school answer", "school"), school_result)
        self.assertEqual(("ollama answer", "ollama"), fallback_result)

    async def test_streaming_error_response_contains_readable_fallback_and_done(self):
        request = ChatRequest(messages=[Message(role="user", content="Alarm 3000")], stream=True)

        async def failed_stream(*_args, **_kwargs):
            raise RuntimeError("LLM timeout")
            yield ""  # pragma: no cover - keeps this function an async generator

        with (
            patch.object(chat_lookup_routes, "get_existing_engine", return_value=FakeEngine()),
            patch.object(chat_lookup_routes, "stream_ollama", new=failed_stream),
            patch.object(chat_lookup_routes, "save_rag_answer"),
            patch.object(chat_lookup_routes, "record_chat_error"),
            patch.object(chat_lookup_routes, "log_query"),
        ):
            response = await chat_lookup_routes.handle_chat(request, "808d")

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        body = "".join(chunks)

        self.assertIn("目前無法連線至 LLM 服務", body)
        self.assertIn("來源：Alarm 3000 / P.12", body)
        self.assertIn("data: [DONE]", body)
        events = [
            json.loads(line[5:].strip())
            for line in body.splitlines()
            if line.startswith("data:") and line[5:].strip() != "[DONE]"
        ]
        self.assertEqual(1, len({event["id"] for event in events}))
        self.assertEqual(events[0]["id"], events[0]["rag"]["answer_id"])
        self.assertEqual("3000", events[0]["rag"]["citations"][0]["code"])
        self.assertEqual("no-cache", response.headers["cache-control"])
        self.assertEqual("no", response.headers["x-accel-buffering"])


if __name__ == "__main__":
    unittest.main()
