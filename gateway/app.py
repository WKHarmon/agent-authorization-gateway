"""FastAPI app creation, lifespan, and provider wiring."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

from gateway.audit import audit
from gateway.callbacks import fire_grant_callback
from gateway.config import CONFIG, DATA_DIR, VAULT_ENABLED
from gateway.db import db_conn, init_db
from gateway.middleware import check_api_key
from gateway.providers import all_providers, register_provider
from gateway.providers.gmail import GmailProvider
from gateway.vault import vault

log = logging.getLogger("gateway")

# ── Module-level state loaded at startup ──────────────────────────────────

_api_key: str = ""
_callback_cf_client_id: str = ""
_callback_cf_client_secret: str = ""
_callback_hooks_token: str = ""


def get_api_key() -> str:
    return _api_key


def make_fire_callback():
    """Return an async callable for firing grant callbacks with loaded credentials."""
    async def _fire(grant, status, expires_at=None):
        await fire_grant_callback(
            grant, status, expires_at,
            cf_client_id=_callback_cf_client_id,
            cf_client_secret=_callback_cf_client_secret,
            hooks_token=_callback_hooks_token,
        )
    return _fire


# ── Background tasks ──────────────────────────────────────────────────────

async def _expire_grants_loop():
    """Periodically expire stale grants."""
    while True:
        try:
            now = datetime.now(timezone.utc).isoformat()
            approval_cutoff = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()

            conn = db_conn()
            try:
                expired = conn.execute(
                    "SELECT id FROM grants WHERE status='active' "
                    "AND expires_at IS NOT NULL AND expires_at<=?",
                    (now,),
                ).fetchall()
                for row in expired:
                    conn.execute(
                        "UPDATE grants SET status='expired' WHERE id=?", (row["id"],)
                    )
                    audit({"action": "grant_expired", "grantId": row["id"]})

                stale = conn.execute(
                    "SELECT id FROM grants WHERE status='pending' AND created_at<?",
                    (approval_cutoff,),
                ).fetchall()
                for row in stale:
                    conn.execute(
                        "UPDATE grants SET status='expired' WHERE id=?", (row["id"],)
                    )
                    audit({
                        "action": "grant_expired",
                        "grantId": row["id"],
                        "reason": "approval_timeout",
                    })

                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log.error("Grant expiry loop error: %s", e)
        await asyncio.sleep(15)


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _api_key, _callback_cf_client_id, _callback_cf_client_secret, _callback_hooks_token

    # Load API key
    if VAULT_ENABLED:
        api_key_path = CONFIG.get("vault_api_key_path", "")
        if api_key_path:
            try:
                shared = vault.read_path(api_key_path)
                _api_key = shared["api_key"].strip()
                log.info("API key loaded from Vault")
            except Exception as e:
                log.warning("Could not load API key from Vault: %s — /api/* routes are unauthenticated", e)
        else:
            log.warning("vault_api_key_path not set in config — /api/* routes are unauthenticated")
    else:
        _api_key = os.environ.get("API_KEY", "").strip()
        if _api_key:
            log.info("API key loaded from environment")
        else:
            log.warning("API_KEY not set — /api/* routes are unauthenticated")

    # Load callback credentials
    try:
        gmail_secrets = vault.read_all()
        _callback_cf_client_id = gmail_secrets.get("CF-Access-Client-Id", "")
        _callback_cf_client_secret = gmail_secrets.get("CF-Access-Client-Secret", "")
        if _callback_cf_client_id:
            log.info("Callback CF Access credentials loaded from Vault")
    except Exception as e:
        log.warning("Could not load callback CF credentials: %s", e)

    # Load hooks token
    callback_cfg = CONFIG.get("callback", {})
    hooks_vault_path = callback_cfg.get("hooks_token_vault_path", "")
    if hooks_vault_path and VAULT_ENABLED:
        try:
            gw = vault.read_path(hooks_vault_path)
            _callback_hooks_token = gw.get("hooks_token", "")
            if _callback_hooks_token:
                log.info("Callback hooks token loaded from Vault")
        except Exception as e:
            log.warning("Could not load hooks token from Vault: %s", e)
    elif not VAULT_ENABLED:
        _callback_hooks_token = os.environ.get("CALLBACK_HOOKS_TOKEN", "")
        if _callback_hooks_token:
            log.info("Callback hooks token loaded from environment")

    if not CONFIG.get("signal", {}).get("webhook_token"):
        log.warning("signal.webhook_token not set — webhook endpoint is unauthenticated")

    # Init database
    init_db()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Start providers (already registered during create_app)
    for p in all_providers().values():
        await p.startup()

    # Start background tasks
    tasks = [asyncio.create_task(_expire_grants_loop())]
    log.info("Authorization gateway started on port %s", CONFIG.get("port", 18795))

    yield

    for t in tasks:
        t.cancel()


# ── App factory ───────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    application = FastAPI(title="OpenClaw Authorization Gateway", lifespan=lifespan)
    application.middleware("http")(check_api_key)

    # Register shared routes
    from gateway.routes import health, audit as audit_routes, grants, approval
    from gateway.signal import signal_webhook

    _fire_callback = make_fire_callback()

    health.register(application)
    audit_routes.register(application)
    grants.register(application, fire_callback=_fire_callback)
    approval.register(application, fire_callback=_fire_callback)

    # Signal webhook
    application.post("/internal/signal-webhook")(signal_webhook)

    # Register providers and their routes (routes registered at creation time,
    # startup() called during lifespan)
    gmail = GmailProvider()
    register_provider(gmail)
    gmail.register_routes(application)

    ssh_cfg = CONFIG.get("providers", {}).get("ssh", {})
    if ssh_cfg.get("enabled"):
        from gateway.providers.ssh import SSHProvider
        ssh = SSHProvider()
        register_provider(ssh)
        ssh.register_routes(application)

    return application


app = create_app()
