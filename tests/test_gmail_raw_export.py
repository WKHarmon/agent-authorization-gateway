"""Tests for Gmail raw-message export and requestor-scoped grants."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


RAW_MESSAGE = (
    b"From: sender@example.com\r\n"
    b"To: recipient@example.com\r\n"
    b"Subject: Test message\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Hello from the original message.\r\n"
)


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeMessages:
    def __init__(self, raw=RAW_MESSAGE):
        self.raw = raw
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        fmt = kwargs.get("format")
        if fmt == "raw":
            raw = self.raw
            if isinstance(raw, bytes):
                raw = base64.urlsafe_b64encode(raw).decode().rstrip("=")
            return FakeRequest({"id": kwargs["id"], "raw": raw})
        headers = [
            {"name": "From", "value": "Sender <sender@example.com>"},
            {"name": "To", "value": "recipient@example.com"},
            {"name": "Subject", "value": "Test message"},
            {"name": "Date", "value": "Tue, 14 Jul 2026 20:00:00 +0000"},
        ]
        payload: dict = {"headers": headers}
        if fmt == "full":
            payload["body"] = {
                "data": base64.urlsafe_b64encode(b"Rendered body").decode()
            }
        return FakeRequest({
            "id": kwargs["id"],
            "threadId": "thread-1",
            "payload": payload,
        })

    def list(self, **kwargs):
        self.calls.append({"operation": "list", **kwargs})
        return FakeRequest({"messages": [{"id": "msg-1"}]})


class FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeGmailService:
    def __init__(self, raw=RAW_MESSAGE):
        self.messages_api = FakeMessages(raw)
        self._users = FakeUsers(self.messages_api)

    def users(self):
        return self._users


@pytest.fixture
def gmail_env(gateway_env, monkeypatch):
    from gateway.providers import gmail as gmail_module

    app = FastAPI()

    @app.middleware("http")
    async def set_requestor(request: Request, call_next):
        request.state.requestor_name = request.headers.get("X-Test-Requestor", "AgentA")
        return await call_next(request)

    gmail_module._register_gmail_routes(app)
    service = FakeGmailService()
    monkeypatch.setattr(gmail_module, "get_gmail_service", lambda: service)
    client = TestClient(app)

    def insert_grant(
        *,
        grant_id="g-gmail",
        level=1,
        status="active",
        message_id="msg-1",
        query=None,
        requestor="AgentA",
        remaining_minutes=10,
    ):
        now = datetime.now(timezone.utc)
        conn = gateway_env["db_conn"]()
        try:
            conn.execute(
                "INSERT INTO grants "
                "(id, level, status, message_id, query, description, approval_token, "
                "signal_code, created_at, approved_at, expires_at, duration_minutes, "
                "metadata, resource_type, requestor) "
                "VALUES (?, ?, ?, ?, ?, 'test', ?, 'ABC123', ?, ?, ?, 10, '{}', "
                "'gmail', ?)",
                (
                    grant_id,
                    level,
                    status,
                    message_id,
                    query,
                    f"token-{grant_id}",
                    now.isoformat(),
                    now.isoformat(),
                    (now + timedelta(minutes=remaining_minutes)).isoformat(),
                    requestor,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "client": client,
        "service": service,
        "insert_grant": insert_grant,
        "db_conn": gateway_env["db_conn"],
        "gmail_module": gmail_module,
    }


def test_raw_requires_covering_grant_before_gmail_call(gmail_env):
    response = gmail_env["client"].get("/api/emails/msg-1/raw")
    assert response.status_code == 403
    assert gmail_env["service"].messages_api.calls == []


def test_raw_returns_exact_eml_and_consumes_level1(gmail_env):
    gmail_env["insert_grant"]()
    response = gmail_env["client"].get("/api/emails/msg-1/raw")

    assert response.status_code == 200
    assert response.content == RAW_MESSAGE
    assert response.headers["content-type"].startswith("message/rfc822")
    assert response.headers["content-disposition"] == (
        'attachment; filename="gmail-msg-1.eml"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"

    conn = gmail_env["db_conn"]()
    try:
        status = conn.execute(
            "SELECT status FROM grants WHERE id='g-gmail'"
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "consumed"


def test_consumed_level1_can_export_until_expiry(gmail_env):
    gmail_env["insert_grant"](status="consumed")
    response = gmail_env["client"].get("/api/emails/msg-1/raw")
    assert response.status_code == 200
    assert response.content == RAW_MESSAGE


def test_raw_filename_sanitizes_message_id(gmail_env):
    gmail_env["insert_grant"](message_id='msg"name')
    response = gmail_env["client"].get("/api/emails/msg%22name/raw")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="gmail-msg_name.eml"'
    )


def test_raw_rejects_other_requestors_grant_without_gmail_call(gmail_env):
    gmail_env["insert_grant"](requestor="AgentA")
    response = gmail_env["client"].get(
        "/api/emails/msg-1/raw", headers={"X-Test-Requestor": "AgentB"}
    )
    assert response.status_code == 403
    assert gmail_env["service"].messages_api.calls == []


def test_regular_body_read_is_requestor_scoped(gmail_env):
    gmail_env["insert_grant"](requestor="AgentA")
    response = gmail_env["client"].get(
        "/api/emails/msg-1", headers={"X-Test-Requestor": "AgentB"}
    )
    assert response.status_code == 200
    assert response.json()["access"] == "metadata_only"
    assert response.json()["body"] is None


def test_attachment_and_history_grants_are_requestor_scoped(gmail_env):
    gmail_env["insert_grant"](level=3, message_id=None, requestor="AgentA")
    headers = {"X-Test-Requestor": "AgentB"}
    attachment = gmail_env["client"].get(
        "/api/emails/msg-1/attachments/att-1", headers=headers
    )
    history = gmail_env["client"].get(
        "/api/history?startHistoryId=123", headers=headers
    )
    assert attachment.status_code == 403
    assert history.status_code == 403
    assert gmail_env["service"].messages_api.calls == []


def test_level3_grant_can_export_without_consumption(gmail_env):
    gmail_env["insert_grant"](level=3, message_id=None)
    response = gmail_env["client"].get("/api/emails/msg-1/raw")
    assert response.status_code == 200
    conn = gmail_env["db_conn"]()
    try:
        status = conn.execute(
            "SELECT status FROM grants WHERE id='g-gmail'"
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "active"


def test_sensitive_raw_is_blocked_without_fetching_raw(gmail_env, monkeypatch):
    gmail_env["insert_grant"]()
    monkeypatch.setattr(gmail_env["gmail_module"], "is_sensitive", lambda *_: "code")
    response = gmail_env["client"].get("/api/emails/msg-1/raw")
    assert response.status_code == 403
    assert [call["format"] for call in gmail_env["service"].messages_api.calls] == [
        "metadata"
    ]


def test_sensitive_override_allows_export(gmail_env, monkeypatch):
    gmail_env["insert_grant"]()
    monkeypatch.setattr(gmail_env["gmail_module"], "is_sensitive", lambda *_: "code")
    response = gmail_env["client"].get(
        "/api/emails/msg-1/raw?override_sensitive=true"
    )
    assert response.status_code == 200
    assert response.content == RAW_MESSAGE


@pytest.mark.parametrize("raw_value", [None, "", "not valid base64 !!!"])
def test_missing_or_invalid_raw_data_returns_502(gmail_env, raw_value):
    gmail_env["insert_grant"]()
    gmail_env["service"].messages_api.raw = raw_value
    response = gmail_env["client"].get("/api/emails/msg-1/raw")
    assert response.status_code == 502

    conn = gmail_env["db_conn"]()
    try:
        status = conn.execute(
            "SELECT status FROM grants WHERE id='g-gmail'"
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "active"


def test_grant_management_routes_are_requestor_scoped(gateway_env):
    gateway_env["insert_active_ssh_grant"](
        grant_id="g-owned-by-a",
        level=1,
        host="server",
        principal="kyle",
        requestor="AgentA",
    )

    gateway_env["config"]["agent_name"] = "AgentB"
    response = gateway_env["client"].get("/api/grants/active")
    assert response.status_code == 200
    assert response.json()["grants"] == []
    assert gateway_env["client"].get(
        "/api/grants/g-owned-by-a"
    ).status_code == 404
    assert gateway_env["client"].delete(
        "/api/grants/g-owned-by-a"
    ).status_code == 404

    gateway_env["config"]["agent_name"] = "AgentA"
    response = gateway_env["client"].get("/api/grants/active")
    assert [grant["id"] for grant in response.json()["grants"]] == [
        "g-owned-by-a"
    ]
    assert gateway_env["client"].delete(
        "/api/grants/g-owned-by-a"
    ).status_code == 200
