"""Заменяемый LLM-клиент. Всё, что зависит от конкретной модели, живёт в config.yaml.

Любой OpenAI-совместимый endpoint (gpt-oss, Qwen, модели Яндекса через совместимый API)
подключается правкой base_url/model, без изменения кода пайплайна.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...
    def complete_json(self, system: str, user: str) -> dict: ...


def extract_json(text: str) -> dict:
    """Достаёт первый JSON-объект: убирает ```-ограждения и рассуждения вокруг ответа."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += (text[i] == "{") - (text[i] == "}")
            if depth == 0:
                try:
                    return json.loads(text[start: i + 1])
                except json.JSONDecodeError:
                    break
        start = text.find("{", start + 1)
    raise ValueError("JSON не найден в ответе модели")


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = 0,
                 max_tokens: int = 2048, timeout_s: float = 60, retries: int = 3):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens
        self.timeout, self.retries = timeout_s, retries

    def complete(self, system: str, user: str) -> str:
        body = {"model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = httpx.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
                r.raise_for_status()
                # у reasoning-моделей рассуждения могут идти отдельным полем - берём только ответ
                return r.json()["choices"][0]["message"]["content"] or ""
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM недоступна после {self.retries} попыток: {last}")

    def complete_json(self, system: str, user: str, attempts: int = 2) -> dict:
        prompt = user
        for _ in range(attempts):
            raw = self.complete(system, prompt)
            try:
                return extract_json(raw)
            except ValueError as exc:
                prompt = f"{user}\n\nПредыдущий ответ не был валидным JSON ({exc}). Верни только JSON."
        raise ValueError("модель не вернула валидный JSON")


def make_client(cfg: dict) -> OpenAICompatibleClient | None:
    c = cfg.get("llm", {})
    if not c.get("base_url") or c["base_url"].startswith("${"):
        return None
    return OpenAICompatibleClient(c["base_url"], c.get("api_key", ""), c["model"],
                                  c.get("temperature", 0), c.get("max_tokens", 2048),
                                  c.get("timeout_s", 60), c.get("retries", 3))
