from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx


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
    payload = _ollama_payload(messages, temperature, max_tokens, config, stream=False)
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response = await client.post(f"{config.url}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
    return data.get("message", {}).get("content", "")


async def stream_ollama(
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    config: OllamaClientConfig,
    *,
    on_connected: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    payload = _ollama_payload(messages, temperature, max_tokens, config, stream=True)
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        async with client.stream("POST", f"{config.url}/api/chat", json=payload) as response:
            response.raise_for_status()
            if on_connected is not None:
                on_connected()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
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
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response = await client.post(f"{config.base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")
