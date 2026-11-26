import asyncio
import hashlib
import json
import os
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select

from .db import DATA, Provider, Record, Session

ENDPOINTS = {
    "Gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "OpenAI": "https://api.openai.com/v1",
    "Groq": "https://api.groq.com/openai/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "Mistral": "https://api.mistral.ai/v1",
}
DEFAULT_LIMITS = {"context": 16000, "max_output": 2200, "vision": False, "structured": False, "long_context": False}
MOCK = os.getenv("BUDDY_MOCK", "1") == "1"


def cipher():
    path = DATA / "provider.key"
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        except FileExistsError:
            pass
    return Fernet(path.read_bytes())


def endpoint(kind, custom):
    if kind in ENDPOINTS:
        return ENDPOINTS[kind]
    url = urlsplit(custom)
    if (kind != "Custom" or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.scheme not in {"http", "https"}
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Use an HTTPS endpoint or a loopback HTTP endpoint")
    return custom.rstrip("/")


def public(provider):
    return {key: getattr(provider, key) for key in (
        "id", "kind", "name", "base_url", "model", "last_four", "position", "enabled",
        "cooldown", "status", "calls", "tokens", "limits",
    )}


def auth(provider):
    return {"Authorization": "Bearer " + cipher().decrypt(provider.secret).decode()}


async def models(provider):
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.get(provider.base_url + "/models", headers=auth(provider))
        classify(response)
        return [item["id"] for item in response.json().get("data", []) if item.get("id")]


class ProviderError(Exception):
    def __init__(self, message, action, reset=None):
        super().__init__(message)
        self.action = action
        self.reset = reset


class Unavailable(Exception):
    def __init__(self, next_reset=None):
        self.next_reset = next_reset
        label = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(next_reset)) if next_reset else ""
        super().__init__(f"Eligible providers are in cooldown. Next retry {label}." if label else
                         "No eligible provider is available. Check Settings or enable mock mode.")


def retry_delay(headers):
    now = time.time()
    retry = headers.get("retry-after")
    if retry:
        try:
            return max(1, float(retry))
        except ValueError:
            try:
                value = parsedate_to_datetime(retry)
                return max(1, value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp() - now)
            except (ValueError, TypeError):
                pass
    delays = []
    for key in ("x-ratelimit-reset", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        value = headers.get(key, "")
        if value:
            try:
                number = float(value)
                delays.append(number / 1000 - now if number > 1e12 else number - now if number > 1e9 else number)
            except ValueError:
                units = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}
                delays.append(sum(float(amount) * units[unit] for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|[smhd])", value)))
    return max([1, *delays]) if delays else 60


def classify(response):
    status = response.status_code
    if status in (401, 403):
        raise ProviderError(f"Credentials rejected ({status})", "disable")
    if status in (402, 429) or status >= 500:
        raise ProviderError(f"Upstream HTTP {status}", "cooldown", time.time() + retry_delay(response.headers))
    if status >= 300:
        raise ProviderError(f"Upstream rejected the request ({status}). Check the endpoint and model.", "stop")


async def compatible_stream(provider, messages, structured):
    limits = {**DEFAULT_LIMITS, **provider.limits}
    payload = {"model": provider.model, "messages": trim(messages, limits["context"] - limits["max_output"]),
               "stream": True}
    payload["max_completion_tokens" if provider.kind in {"OpenAI", "Groq"} else "max_tokens"] = limits["max_output"]
    if structured:
        payload["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=12), follow_redirects=False) as client:
            async with client.stream("POST", provider.base_url + "/chat/completions", headers=auth(provider), json=payload) as response:
                classify(response)
                finished = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    event = json.loads(data)
                    if event.get("error"):
                        code = str(event["error"].get("code", "")).lower()
                        if code in {"401", "403", "invalid_api_key"}:
                            raise ProviderError("Credentials rejected", "disable")
                        if code in {"400", "404", "422", "invalid_request_error"}:
                            raise ProviderError("Upstream rejected the request", "stop")
                        raise ProviderError("Upstream stream error", "cooldown", time.time() + retry_delay(response.headers))
                    for choice in event.get("choices", []):
                        finished = finished or bool(choice.get("finish_reason"))
                        if delta := choice.get("delta", {}).get("content"):
                            yield delta
                if not finished:
                    raise ProviderError("Stream disconnected", "cooldown", time.time() + 30)
    except (httpx.RequestError, json.JSONDecodeError) as exc:
        raise ProviderError("Upstream connection or stream failed", "cooldown", time.time() + 30) from exc


def estimate(messages):
    return sum(len(item["content"]) // 4 if isinstance(item["content"], str) else
               sum(len(part.get("text", "")) // 4 if part["type"] == "text" else 1000 for part in item["content"])
               for item in messages)


def trim(messages, maximum):
    if estimate(messages) <= maximum:
        return messages
    system = messages[:1] if messages[0]["role"] == "system" else []
    recent = messages[len(system):]
    while len(recent) > 1 and estimate(system + recent) > maximum:
        recent = recent[1:]
        while len(recent) > 1 and recent[0]["role"] == "assistant":
            recent = recent[1:]
    if estimate(system + recent) > maximum:
        recent = [dict(message) for message in recent]
        room = max(256, (maximum - estimate(system)) * 4 - 100)
        if isinstance(recent[-1]["content"], str):
            recent[-1]["content"] = recent[-1]["content"][:room]
    return system + recent


class Router:
    def __init__(self, adapter=compatible_stream, mock=MOCK):
        self.adapter = adapter
        self.mock = mock

    async def stream(self, messages, *, capability=None, schema=None, fixture=None):
        if self.mock:
            value = fixture() if callable(fixture) else fixture
            value = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            for start in range(0, len(value), 42):
                yield {"type": "delta", "text": value[start:start+42]}
                await asyncio.sleep(0)
            yield {"type": "done", "provider": "Mock · local demonstration", "estimated_tokens": estimate(messages) + len(value) // 4}
            return

        with Session() as db:
            providers = db.scalars(select(Provider).where(Provider.enabled).order_by(Provider.position, Provider.id)).all()
        cache_key = hashlib.sha256(json.dumps([messages, capability, getattr(schema, "__name__", None),
                                               [(p.id, p.model, p.limits) for p in providers]], sort_keys=True).encode()).hexdigest()
        with Session() as db:
            cached = db.scalar(select(Record).where(Record.kind == "llm_cache", Record.key == cache_key))
        if cached:
            yield {"type": "delta", "text": cached.data["text"]}
            yield {"type": "done", **cached.data["info"], "cached": True}
            return

        resets = []
        for provider in providers:
            if any(not provider.limits.get(requirement) for requirement in (capability, "structured" if schema else None) if requirement):
                continue
            if provider.cooldown > time.time():
                resets.append(provider.cooldown)
                continue
            answer = ""
            try:
                for attempt in range(2 if schema else 1):
                    answer = ""
                    prompt = messages if attempt == 0 else messages + [{"role": "user", "content": "Return only valid JSON matching the requested schema."}]
                    async for chunk in self.adapter(provider, prompt, bool(schema)):
                        answer += chunk
                        if not schema:
                            yield {"type": "delta", "text": chunk}
                    if not answer.strip():
                        raise ProviderError("Upstream returned an empty answer", "cooldown", time.time() + 30)
                    if not schema:
                        break
                    try:
                        answer = schema.model_validate_json(answer).model_dump_json()
                        yield {"type": "delta", "text": answer}
                        break
                    except ValueError as exc:
                        if attempt == 1:
                            raise ProviderError("Invalid structured response", "cooldown", time.time() + 30) from exc
            except ProviderError as exc:
                if exc.action == "stop":
                    raise
                with Session() as db:
                    row = db.get(Provider, provider.id)
                    if row:
                        row.status = str(exc)
                        if exc.action == "disable":
                            row.enabled = False
                        else:
                            row.cooldown = exc.reset
                        db.commit()
                if exc.reset:
                    resets.append(exc.reset)
                if answer and not schema:
                    yield {"type": "reset"}
                yield {"type": "status", "message": f"{provider.name}: {exc}"}
                continue

            count = estimate(messages) + len(answer) // 4
            info = {"provider": provider.name, "estimated_tokens": count}
            from sqlalchemy.dialects.sqlite import insert
            with Session() as db:
                row = db.get(Provider, provider.id)
                if row:
                    row.calls += 1
                    row.tokens += count
                    row.status = "Ready"
                    row.cooldown = 0
                db.execute(insert(Record).values(kind="llm_cache", key=cache_key, data={"text": answer, "info": info})
                           .on_conflict_do_nothing(index_elements=["kind", "key"]))
                db.commit()
            yield {"type": "done", **info}
            return
        raise Unavailable(min(resets) if resets else None)

    async def complete(self, messages, **options):
        answer = ""
        info = {}
        async for event in self.stream(messages, **options):
            if event["type"] == "delta":
                answer += event["text"]
            elif event["type"] == "reset":
                answer = ""
            elif event["type"] == "done":
                info = event
        return answer, info


router = Router()
