"""Signal messaging — send notifications and process approval replies."""

import asyncio
import hmac
import logging

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from gateway.config import CONFIG
from gateway.grants import activate_grant, deny_grant

log = logging.getLogger("gateway.signal")


async def send_signal_message(message: str):
    signal_cfg = CONFIG["signal"]
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{signal_cfg['api_url']}/v2/send",
                json={
                    "number": signal_cfg["sender"],
                    "recipients": [signal_cfg["approver"]],
                    "message": message,
                },
            )
            if resp.status_code not in (200, 201):
                log.error("Signal send failed: %s %s", resp.status_code, resp.text)
        except Exception as e:
            log.error("Signal send error (%s): %s", type(e).__name__, e)


async def process_signal_reply(text: str, *, fire_callback):
    """Match a Signal reply to a pending grant and approve/deny.

    fire_callback is an async callable(grant, status, expires_at=None) for
    dispatching callbacks after approval/denial.
    """
    from gateway.db import db_conn

    text_upper = text.upper().strip()

    conn = db_conn()
    try:
        has_code = "-" in text_upper
        if has_code:
            parts = text_upper.split("-", 1)
            keyword = parts[0]
            code = parts[1]
        else:
            keyword = text_upper
            code = None

        is_approve = keyword in ("YES", "Y", "APPROVE")
        is_deny = keyword in ("NO", "N", "DENY")

        if not is_approve and not is_deny:
            return

        if code:
            row = conn.execute(
                "SELECT * FROM grants WHERE status='pending' AND UPPER(signal_code)=?",
                (code,),
            ).fetchone()
            if not row:
                await send_signal_message(f"No pending request with code {code}.")
                return
            grant = dict(row)
        else:
            rows = conn.execute(
                "SELECT * FROM grants WHERE status='pending'"
            ).fetchall()
            if len(rows) == 0:
                await send_signal_message("No pending access requests.")
                return
            elif len(rows) > 1:
                codes = ", ".join(dict(r)["signal_code"] for r in rows)
                await send_signal_message(
                    f"Multiple pending requests. Reply with code:\n{codes}"
                )
                return
            grant = dict(rows[0])

        if is_approve:
            expires_at = activate_grant(grant, via="signal")
            await fire_callback(grant, "active", expires_at.isoformat())
            asyncio.create_task(send_signal_message(
                f"Approved ({grant.get('resource_type', 'gmail')} Level {grant['level']}). "
                f"Expires {expires_at.strftime('%H:%M UTC')}."
            ))
        else:
            deny_grant(grant, via="signal")
            await fire_callback(grant, "denied")
            asyncio.create_task(send_signal_message("Denied."))

    finally:
        conn.close()


async def signal_webhook(request: Request):
    """Receive incoming Signal messages from signal-api (json-rpc mode)."""
    expected_token = CONFIG.get("signal", {}).get("webhook_token", "")
    if expected_token:
        provided_token = request.query_params.get("token", "")
        if not hmac.compare_digest(provided_token, expected_token):
            return JSONResponse(status_code=401, content={"detail": "Invalid webhook token"})

    approver_number = CONFIG["signal"]["approver"]

    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored"}

    params = payload.get("params", payload)
    envelope = params.get("envelope", {})
    source = envelope.get("sourceNumber", "")
    data_msg = envelope.get("dataMessage", {})
    body = (data_msg.get("message") or "").strip()

    if source != approver_number or not body:
        return {"status": "ignored"}

    from gateway.app import make_fire_callback
    await process_signal_reply(body, fire_callback=make_fire_callback())
    return {"status": "processed"}
