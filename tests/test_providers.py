import asyncio
import time

import httpx
import pytest
from sqlalchemy import delete

from backend.db import Provider, Record, Session, migrate
from backend.providers import DEFAULT_LIMITS, ProviderError, Router, Unavailable, cipher, classify, retry_delay

PROMPT = [{"role": "user", "content": "Identify a priority queue."}]


@pytest.fixture(autouse=True)
def setup_providers():
    migrate()
    with Session() as db:
        db.execute(delete(Provider))
        db.execute(delete(Record).where(Record.kind == "llm_cache"))
        for index in (1, 2):
            db.add(Provider(kind="Custom", name=f"Provider {index}", base_url="http://127.0.0.1",
                            model="fake", position=index, secret=cipher().encrypt(b"example"), last_four="mple",
                            limits=DEFAULT_LIMITS.copy()))
        db.commit()


def collect(router, prompt=PROMPT):
    async def run():
        return [event async for event in router.stream(prompt)]
    return asyncio.run(run())


def test_429_falls_back_and_marks_cooldown():
    async def provider(row, *_):
        if row.position == 1:
            raise ProviderError("HTTP 429", "cooldown", time.time() + 60)
        yield "fallback"
    result = collect(Router(adapter=provider, mock=False))
    assert result[-1]["provider"] == "Provider 2"
    with Session() as db:
        assert db.get(Provider, 1).cooldown > time.time()
    assert retry_delay({"retry-after": "15"}) == 15


def test_billing_quota_is_distinct_from_rate_limit_and_can_fall_back():
    response = httpx.Response(429, json={"error": {"code": "credit_balance_exhausted", "type": "insufficient_quota"}})

    async def provider(row, *_):
        if row.position == 1:
            classify(response)
        yield "Fallback answer"

    result = collect(Router(adapter=provider, mock=False))
    assert result[0]["message"] == "Provider 1: API credits or quota exhausted"
    assert result[-1]["provider"] == "Provider 2"
    with Session() as db:
        row = db.get(Provider, 1)
        assert row.enabled and row.cooldown == 0

    async def no_credits(*_):
        classify(response)
        yield ""

    with pytest.raises(Unavailable, match="Check the provider's API billing") as error:
        collect(Router(adapter=no_credits, mock=False), [{"role": "user", "content": "What is a binary heap?"}])
    assert error.value.next_reset is None


def test_partial_answer_resets_before_fallback():
    async def provider(row, *_):
        if row.position == 1:
            yield "partial"
            raise ProviderError("lost connection", "cooldown", time.time() + 10)
        yield "fresh"
    result = collect(Router(adapter=provider, mock=False))
    assert [event["type"] for event in result] == ["delta", "reset", "status", "delta", "done"]
    assert result[-2]["text"] == "fresh"


def test_invalid_key_is_disabled():
    async def provider(row, *_):
        if row.position == 1:
            raise ProviderError("invalid key", "disable")
        yield "fallback"
    assert collect(Router(adapter=provider, mock=False))[-1]["provider"] == "Provider 2"
    with Session() as db:
        assert not db.get(Provider, 1).enabled


def test_all_exhausted_reports_earliest_reset():
    now = time.time()
    async def provider(row, *_):
        if False:
            yield ""
        raise ProviderError("quota exhausted", "cooldown", now + row.position * 60)
    with pytest.raises(Unavailable) as error:
        collect(Router(adapter=provider, mock=False))
    assert error.value.next_reset == now + 60


def test_bad_request_does_not_fall_back():
    called = []
    async def provider(row, *_):
        called.append(row.position)
        if False:
            yield ""
        raise ProviderError("bad request", "stop")
    with pytest.raises(ProviderError):
        collect(Router(adapter=provider, mock=False))
    assert called == [1]
