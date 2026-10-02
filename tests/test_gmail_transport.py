"""Regression tests: Gmail clients/transports must never cross operations."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from google.oauth2.credentials import Credentials


@pytest.fixture
def transport_env(gateway_env, monkeypatch):
    from gateway.providers import gmail
    creds = Credentials(token="test-token", refresh_token="test-refresh",
                        token_uri="https://example.invalid/token",
                        client_id="test-client", client_secret="test-secret")
    creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    monkeypatch.setattr(gmail, "_credentials", creds)
    # Never allow tests to reach the real Vault.
    monkeypatch.setattr(gmail.vault, "read_all", lambda: pytest.fail("Unexpected Vault read"))
    monkeypatch.setattr(gmail.vault, "patch", lambda data: None)
    return gmail


def test_each_service_has_independent_transport_and_credentials(transport_env):
    gmail = transport_env
    first = gmail.get_gmail_service()
    second = gmail.get_gmail_service()
    try:
        assert first is not second
        assert first._http is not second._http
        assert first._http.credentials is not second._http.credentials
        assert first._http.credentials is not gmail._credentials
        first._http.credentials.token = "changed-by-401-refresh"
        assert second._http.credentials.token == "test-token"
        assert gmail._credentials.token == "test-token"
    finally:
        if first is not None:
            first.close()
        if second is not None and second is not first:
            second.close()


def test_concurrent_expiry_refresh_happens_once(transport_env, monkeypatch):
    gmail = transport_env
    gmail._credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    refreshed = []
    def refresh(self, request):
        time.sleep(0.02)
        refreshed.append(1)
        self.token = "refreshed"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    monkeypatch.setattr(Credentials, "refresh", refresh)
    with ThreadPoolExecutor(max_workers=8) as pool:
        services = list(pool.map(lambda _: gmail.get_gmail_service(), range(8)))
    try:
        assert len(refreshed) == 1
        assert len({id(s._http) for s in services}) == 8
    finally:
        for service in services:
            service.close()


def test_cold_start_refreshes_once_even_with_expiryless_stored_token(transport_env, monkeypatch):
    gmail = transport_env
    monkeypatch.setattr(gmail, "_credentials", None)
    reads, refreshes, writes = [], [], []
    def read():
        reads.append(1)
        return {"access_token": "stale-with-no-expiry", "refresh_token": "test-refresh",
                "client_id": "test-client", "client_secret": "test-secret"}
    def refresh(self, request):
        time.sleep(0.02)
        refreshes.append(1)
        self.token = "fresh"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    monkeypatch.setattr(gmail.vault, "read_all", read)
    monkeypatch.setattr(gmail.vault, "patch", writes.append)
    monkeypatch.setattr(Credentials, "refresh", refresh)
    with ThreadPoolExecutor(max_workers=8) as pool:
        services = list(pool.map(lambda _: gmail.get_gmail_service(), range(8)))
    try:
        assert len(reads) == len(refreshes) == len(writes) == 1
        assert all(s._http.credentials.token == "fresh" for s in services)
        assert len({id(s._http) for s in services}) == 8
    finally:
        for service in services:
            service.close()


@pytest.mark.parametrize("fail", [False, True])
def test_execution_closes_service_in_same_worker(transport_env, monkeypatch, fail):
    gmail = transport_env
    events = []
    def make_service():
        events.append(("created", threading.get_ident()))
        return SimpleNamespace(close=lambda: events.append(("closed", threading.get_ident())))
    monkeypatch.setattr(gmail, "get_gmail_service", make_service)
    def operation(service):
        events.append(("executed", threading.get_ident()))
        if fail:
            raise ValueError("upstream failure")
        return "ok"
    async def run():
        return await asyncio.to_thread(gmail.execute_gmail, operation)
    if fail:
        with pytest.raises(ValueError, match="upstream failure"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == "ok"
    assert [e[0] for e in events] == ["created", "executed", "closed"]
    assert len({e[1] for e in events}) == 1


def test_cancelled_awaiter_leaves_worker_owning_cleanup(transport_env, monkeypatch):
    gmail = transport_env
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    events = []
    def make_service():
        events.append(threading.get_ident())
        def close():
            events.append(threading.get_ident())
            closed.set()
        return SimpleNamespace(close=close)
    monkeypatch.setattr(gmail, "get_gmail_service", make_service)
    def operation(service):
        started.set()
        assert release.wait(5)
    async def run():
        task = asyncio.create_task(asyncio.to_thread(gmail.execute_gmail, operation))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not closed.is_set()
        finally:
            release.set()
        assert await asyncio.to_thread(closed.wait, 5)
    asyncio.run(run())
    assert len(events) == 2 and events[0] == events[1]


def test_parallel_profile_search_batch_and_query_checks(transport_env, monkeypatch):
    gmail = transport_env
    services = []
    class Service:
        def __init__(self):
            self.owner = threading.get_ident()
            self.closed = False
            self.batch_count = 0
            services.append(self)
        def check(self):
            assert threading.get_ident() == self.owner
            assert not self.closed
        def users(self):
            self.check()
            return self
        def messages(self):
            return self
        def labels(self):
            return self
        def getProfile(self, **kwargs):
            return self.request({"emailAddress": "test@example.invalid"})
        def list(self, **kwargs):
            return self.request({"messages": [{"id": "m1"}], "labels": [{"id": "l1"}], "resultSizeEstimate": 1})
        def get(self, **kwargs):
            return self.request({"id": kwargs["id"], "name": "Test label", "payload": {"headers": []}})
        def request(self, result):
            def execute():
                self.check()
                time.sleep(0.005)
                return result
            return SimpleNamespace(execute=execute)
        def new_batch_http_request(self):
            self.check()
            calls = []
            def execute():
                self.check()
                self.batch_count += 1
                for req, cb in calls:
                    cb("req", req.execute(), None)
            return SimpleNamespace(add=lambda req, callback: calls.append((req, callback)), execute=execute)
        def close(self):
            self.check()
            self.closed = True
    monkeypatch.setattr(gmail, "get_gmail_service", Service)
    app = FastAPI()
    gmail._register_gmail_routes(app)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            paths = ["/api/profile", "/api/emails?q=test&maxResults=1", "/api/labels"] * 8
            responses = await asyncio.gather(*(client.get(path) for path in paths))
            checks = await asyncio.gather(*(asyncio.to_thread(gmail._message_matches_query, "m1", "test") for _ in range(8)))
            assert all(checks)
            assert all(r.status_code == 200 for r in responses)
            for path, response in zip(paths, responses):
                if path.startswith("/api/emails"):
                    assert [m["id"] for m in response.json()["messages"]] == ["m1"]
    asyncio.run(run())
    assert len(services) == 32
    assert all(s.closed for s in services)
    assert sum(s.batch_count for s in services) == 16
