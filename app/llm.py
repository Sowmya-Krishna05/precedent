"""Minimal OpenAI-compatible chat client (Groq by default) with defensive tool-calling.

Groq models occasionally emit malformed tool calls (HTTP 400 `tool_use_failed`), invalid JSON
arguments, or answer in prose instead of calling a tool. The hackathon brief calls this out,
so every one of those paths is handled here or in the agent loop:
  * 429 / 5xx            -> exponential backoff, honours Retry-After
  * 400 tool_use_failed  -> surfaced as ToolCallFailed (agent re-prompts, then switches model)
  * bad JSON arguments   -> parse_arguments() repairs common issues or raises
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class ToolCallFailed(LLMError):
    """Provider rejected the model's tool call (e.g. Groq `tool_use_failed`)."""

    def __init__(self, message: str, failed_generation: str | None = None):
        super().__init__(message)
        self.failed_generation = failed_generation


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[dict]
    raw_message: dict
    model: str


class ChatClient:
    def __init__(self, api_key: str, base_url: str, model: str, fallback_model: str | None = None,
                 timeout: float = 60.0, max_retries: int = 3, session: requests.Session | None = None):
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.model, self.fallback_model = model, fallback_model
        self.timeout, self.max_retries = timeout, max_retries
        self.http = session or requests.Session()

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             tool_choice: str | dict = "auto", model: str | None = None,
             temperature: float = 0.1) -> ChatResult:
        model = model or self.model
        body: dict = {"model": model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        delay = 1.5
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                                   headers={"Authorization": f"Bearer {self.api_key}"})
            except requests.RequestException as e:
                if attempt == self.max_retries:
                    raise LLMError(f"network error: {e}") from e
                time.sleep(delay); delay *= 2
                continue
            if r.status_code == 200:
                msg = r.json()["choices"][0]["message"]
                return ChatResult(msg.get("content"), msg.get("tool_calls") or [], msg, model)
            err = _error(r)
            if r.status_code == 400 and err.get("code") == "tool_use_failed":
                raise ToolCallFailed(err.get("message", "tool_use_failed"), err.get("failed_generation"))
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                wait = float(r.headers.get("retry-after", delay))
                log.warning("LLM %s, retrying in %.1fs", r.status_code, wait)
                time.sleep(min(wait, 20)); delay *= 2
                continue
            raise LLMError(f"{r.status_code}: {err.get('message') or r.text[:300]}")
        raise LLMError("exhausted retries")


def _error(r: requests.Response) -> dict:
    try:
        return r.json().get("error", {}) or {}
    except ValueError:
        return {}


_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def parse_arguments(raw: str | dict | None) -> dict:
    """Parse tool-call arguments, repairing the usual small-model mistakes."""
    if isinstance(raw, dict):
        return raw
    if not raw or not raw.strip():
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    for candidate in (text, _TRAILING_COMMA.sub(r"\1", text), text.replace("'", '"')):
        try:
            val = json.loads(candidate)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            continue
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(_TRAILING_COMMA.sub(r"\1", m.group(0)))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"could not parse tool arguments: {raw[:200]}")
