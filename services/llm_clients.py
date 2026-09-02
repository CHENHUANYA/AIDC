from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx

from config_values import env_float, env_int


def _response_limits(timeout_seconds: float) -> tuple[int, int, float]:
    return (
        env_int("ALARM_RAG_LLM_RESPONSE_MAX_BYTES", 1_048_576, minimum=1024, maximum=16_777_216),
        env_int("ALARM_RAG_LLM_RESPONSE_MAX_EVENTS", 10_000, minimum=1, maximum=100_000),
        env_float(
            "ALARM_RAG_LLM_TOTAL_TIMEOUT_SECONDS",
            max(timeout_seconds * 4, 30.0),
            minimum=1.0,
            maximum=600.0,
        ),
    )


@dataclass(frozen=True)
class OllamaClientConfig:
    url: str
    model: str
    keep_alive: str
    timeout_seconds: float
    max_output_tokens: int
    context_tokens: int


@dataclass(frozen=True)
class SchoolClientConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    max_output_tokens: int


def _ollama_payload(
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    config: OllamaClientConfig,
    *,
    stream: bool,
) -> dict:
    return {
        "model": config.model,
        "messages": messages,
        "stream": stream,
        "keep_alive": config.keep_alive,
        "options": {
            "temperature": temperature,
            "num_predict": min(max_tokens, config.max_output_tokens),
            "num_ctx": config.context_tokens,
        },
    }


async def call_ollama(
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    config: OllamaClientConfig,
) -> str:
    # The caller needs the complete answer before applying retrieval guards, but
    # asking Ollama for one buffered response turns the HTTP read timeout into a
    # limit on the entire CPU-bound generation. Consume Ollama's stream
    # internally so the timeout only applies while no data is arriving, then
    # return the same complete string contract to the caller.
    parts: list[str] = []
    async for content in stream_ollama(messages, temperature, max_tokens, config):
        parts.append(content)
    return "".join(parts)


async def stream_ollama(
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    config: OllamaClientConfig,
    *,
    on_connected: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    payload = _ollama_payload(messages, temperature, max_tokens, config, stream=True)
    max_bytes, max_events, total_timeout = _response_limits(config.timeout_seconds)
    started = time.monotonic()
    received_bytes = 0
    received_events = 0
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        async with client.stream("POST", f"{config.url}/api/chat", json=payload) as response:
            response.raise_for_status()
            if on_connected is not None:
                on_connected()
            async for line in response.aiter_lines():
                if time.monotonic() - started > total_timeout:
                    raise RuntimeError("Ollama response exceeded total generation deadline")
                if not line.strip():
                    continue
                received_events += 1
                received_bytes += len(line.encode("utf-8"))
                if received_events > max_events or received_bytes > max_bytes:
                    raise RuntimeError("Ollama response exceeded configured size limits")
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Ollama returned invalid streaming JSON") from exc
                if event.get("error"):
                    raise RuntimeError("Ollama streaming request failed")
                content = str(event.get("message", {}).get("content") or "")
                if content:
                    yield content


async def call_school_api(
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    config: SchoolClientConfig,
) -> str:
    if not config.base_url:
        raise RuntimeError("SCHOOL_API_BASE_URL is required when LLM_PROVIDER=school")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": min(max_tokens, config.max_output_tokens),
    }
    max_bytes, max_events, total_timeout = _response_limits(config.timeout_seconds)
    started = time.monotonic()
    chunks: list[bytes] = []
    received_bytes = 0
    received_events = 0
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        async with client.stream(
            "POST",
            f"{config.base_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if time.monotonic() - started > total_timeout:
                    raise RuntimeError("School API response exceeded total generation deadline")
                if not chunk:
                    continue
                received_events += 1
                received_bytes += len(chunk)
                if received_events > max_events or received_bytes > max_bytes:
                    raise RuntimeError("School API response exceeded configured size limits")
                chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("School API returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("School API returned invalid JSON")
    return str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
