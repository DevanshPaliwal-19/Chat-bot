"""Concrete providers: Ollama (default) and any OpenAI-compatible server."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import json
import logging
import time

import httpx

from ..config import Settings
from .base import (
    LLMBadResponse,
    LLMModelMissing,
    LLMProvider,
    LLMResult,
    LLMTimeout,
    LLMUnavailable,
    LLMError,
)

logger = logging.getLogger(__name__)


class _HttpProvider(LLMProvider):
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.model = settings.llm_model
        self._client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.llm_timeout_s, connect=5.0),
            transport=transport,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        return {}

    async def _post(self, path: str, payload: dict) -> dict:
        attempts = self.settings.llm_max_retries + 1
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.post(path, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = LLMUnavailable("cannot connect to the model server")
                last.__cause__ = exc
            except httpx.TimeoutException as exc:
                raise LLMTimeout("model request timed out") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"transport error: {type(exc).__name__}") from exc
            else:
                if resp.status_code == 404 and "not found" in resp.text.lower():
                    raise LLMModelMissing(f"model '{self.model}' is not available on the server")
                if resp.status_code >= 500:
                    last = LLMError(f"model server error {resp.status_code}")
                elif resp.status_code >= 400:
                    raise LLMError(f"model server rejected the request ({resp.status_code})")
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise LLMBadResponse("model server returned invalid JSON") from exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.4 * (attempt + 1))
        assert last is not None
        raise last

    async def aclose(self) -> None:
        await self._client.aclose()


class OllamaProvider(_HttpProvider):
    name = "ollama"

    async def chat(self, messages, *, json_mode=False, max_tokens=None, temperature=None) -> LLMResult:
        s = self.settings
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": s.llm_keep_alive,
            "options": {
                "temperature": s.llm_temperature if temperature is None else temperature,
                "num_ctx": s.llm_num_ctx,
                "num_predict": max_tokens or s.llm_max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"
        t0 = time.perf_counter()
        data = await self._post("/api/chat", payload)
        text = (data.get("message") or {}).get("content")
        if not isinstance(text, str):
            raise LLMBadResponse("model response had no message content")
        done_reason = data.get("done_reason")
        done = data.get("done", True)
        if not done_reason:
            done_reason = "stop" if done else "interrupted"
        is_truncated = done_reason == "length"
        return LLMResult(
            text=text.strip(),
            model=data.get("model", self.model),
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            finish_reason=done_reason,
            is_truncated=is_truncated,
        )

    async def chat_stream(
        self, messages, *, json_mode=False, max_tokens=None, temperature=None
    ) -> AsyncGenerator[str, None]:
        s = self.settings
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": s.llm_keep_alive,
            "options": {
                "temperature": s.llm_temperature if temperature is None else temperature,
                "num_ctx": s.llm_num_ctx,
                "num_predict": max_tokens or s.llm_max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"

        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code == 404:
                    body = await resp.aread()
                    if "not found" in body.decode("utf-8", "ignore").lower():
                        raise LLMModelMissing(f"model '{self.model}' is not available on the server")
                if resp.status_code >= 400:
                    raise LLMError(f"model server error {resp.status_code}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        content = (chunk.get("message") or {}).get("content", "")
                        if content:
                            yield content
                        if chunk.get("done"):
                            break
                    except (json.JSONDecodeError, KeyError):
                        continue
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailable("cannot connect to the model server") from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeout("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"transport error: {type(exc).__name__}") from exc

    async def health(self) -> dict:
        try:
            resp = await self._client.get("/api/tags", timeout=4.0)
            resp.raise_for_status()
            names = [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception as exc:
            return {"status": "unavailable", "provider": self.name, "model": self.model, "detail": type(exc).__name__}
        present = self.model in names or f"{self.model}:latest" in names
        return {
            "status": "ok" if present else "model_missing",
            "provider": self.name,
            "model": self.model,
            "installed_models": names,
        }

    async def warmup(self) -> None:
        try:
            await self.chat([{"role": "user", "content": "ok"}], max_tokens=1)
        except LLMError as exc:
            logger.warning("LLM warm-up failed: %s", exc)


class OpenAICompatibleProvider(_HttpProvider):
    name = "openai_compatible"

    def _headers(self) -> dict[str, str]:
        key = self.settings.llm_api_key
        return {"Authorization": f"Bearer {key.get_secret_value()}"} if key else {}

    async def chat(self, messages, *, json_mode=False, max_tokens=None, temperature=None) -> LLMResult:
        s = self.settings
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or s.llm_max_tokens,
            "temperature": s.llm_temperature if temperature is None else temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        data = await self._post("/chat/completions", payload)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = choice.get("finish_reason") or "stop"
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponse("unexpected response shape") from exc
        usage = data.get("usage") or {}
        is_truncated = finish_reason == "length"
        return LLMResult(
            text=(text or "").strip(),
            model=data.get("model", self.model),
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=finish_reason,
            is_truncated=is_truncated,
        )

    async def chat_stream(
        self, messages, *, json_mode=False, max_tokens=None, temperature=None
    ) -> AsyncGenerator[str, None]:
        s = self.settings
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens or s.llm_max_tokens,
            "temperature": s.llm_temperature if temperature is None else temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code >= 400:
                    raise LLMError(f"model server error {resp.status_code}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    except (json.JSONDecodeError, KeyError):
                        continue
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailable("cannot connect to the model server") from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeout("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"transport error: {type(exc).__name__}") from exc

    async def health(self) -> dict:
        try:
            resp = await self._client.get("/models", timeout=4.0)
            resp.raise_for_status()
            return {"status": "ok", "provider": self.name, "model": self.model}
        except Exception as exc:
            return {"status": "unavailable", "provider": self.name, "model": self.model, "detail": type(exc).__name__}


def build_llm(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> LLMProvider:
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings, transport)
    return OllamaProvider(settings, transport)
