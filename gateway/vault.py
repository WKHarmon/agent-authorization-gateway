"""Vault / OpenBao client — AppRole auth, KV v2 reads, SSH CA signing."""

import logging
import os
import time

import httpx

from gateway.config import CONFIG, VAULT_ADDR, VAULT_ENABLED, VAULT_ROLE_ID, VAULT_SECRET_ID

log = logging.getLogger("gateway.vault")


class VaultClient:
    """Unified client for Vault/OpenBao operations."""

    def __init__(self):
        self._enabled = VAULT_ENABLED
        self._addr = VAULT_ADDR
        if self._enabled:
            self._http = httpx.Client(timeout=10.0)
            self._token: str = ""
            self._token_expires: float = 0.0
        else:
            log.info("Vault not configured — reading secrets from environment variables")

    # ── Authentication ────────────────────────────────────────────────────

    def _login(self):
        resp = self._http.post(
            f"{self._addr}/v1/auth/approle/login",
            json={"role_id": VAULT_ROLE_ID, "secret_id": VAULT_SECRET_ID},
        )
        resp.raise_for_status()
        auth = resp.json()["auth"]
        self._token = auth["client_token"]
        lease = auth.get("lease_duration", 3600)
        self._token_expires = time.monotonic() + lease * 0.75
        log.info("Vault AppRole login successful (lease %ds)", lease)

    def _headers(self) -> dict:
        if not self._token or time.monotonic() >= self._token_expires:
            self._login()
        return {"X-Vault-Token": self._token}

    # ── KV v2 helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _kv2_api_path(kv_path: str) -> str:
        """Convert 'secret/foo' → 'secret/data/foo'."""
        parts = kv_path.split("/", 1)
        mount = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        return f"{mount}/data/{key}"

    def read_all(self) -> dict:
        """Read all fields from the primary vault secret (CONFIG['vault_path'])."""
        if not self._enabled:
            return {
                "client_id": os.environ.get("GMAIL_CLIENT_ID", ""),
                "client_secret": os.environ.get("GMAIL_CLIENT_SECRET", ""),
                "refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN", ""),
                "access_token": os.environ.get("GMAIL_ACCESS_TOKEN", ""),
                "CF-Access-Client-Id": os.environ.get("CF_ACCESS_CLIENT_ID", ""),
                "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_CLIENT_SECRET", ""),
            }
        api_path = self._kv2_api_path(CONFIG["vault_path"])
        resp = self._http.get(
            f"{self._addr}/v1/{api_path}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["data"]["data"]

    def read_path(self, kv_path: str) -> dict:
        """Read from an arbitrary KV v2 path."""
        if not self._enabled:
            return {"api_key": os.environ.get("API_KEY", "")}
        api_path = self._kv2_api_path(kv_path)
        resp = self._http.get(
            f"{self._addr}/v1/{api_path}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["data"]["data"]

    def patch(self, data: dict):
        """Update specific fields in the primary vault secret (KV v2 patch)."""
        if not self._enabled:
            return
        api_path = self._kv2_api_path(CONFIG["vault_path"])
        resp = self._http.patch(
            f"{self._addr}/v1/{api_path}",
            headers={**self._headers(), "Content-Type": "application/merge-patch+json"},
            json={"data": data},
        )
        if resp.status_code >= 400:
            current = self.read_all()
            current.update(data)
            resp = self._http.post(
                f"{self._addr}/v1/{api_path}",
                headers=self._headers(),
                json={"data": current},
            )
            resp.raise_for_status()

    # ── SSH CA signing ────────────────────────────────────────────────────

    async def sign_ssh_key(
        self,
        mount: str,
        role: str,
        public_key: str,
        valid_principals: str,
        ttl: str = "5m",
        extensions: dict | None = None,
        critical_options: dict | None = None,
    ) -> dict:
        """Sign an SSH public key via the SSH secrets engine.

        Calls POST /v1/{mount}/sign/{role} and returns the signed certificate data.
        """
        payload: dict = {
            "public_key": public_key,
            "valid_principals": valid_principals,
            "ttl": ttl,
            "cert_type": "user",
        }
        if extensions:
            payload["extensions"] = extensions
        if critical_options:
            payload["critical_options"] = critical_options

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._addr}/v1/{mount}/sign/{role}",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["data"]

    async def list_ssh_roles(self, mount: str, prefix: str = "") -> list[dict]:
        """List SSH roles from the secrets engine, optionally filtered by prefix."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                "LIST",
                f"{self._addr}/v1/{mount}/roles",
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            keys = resp.json().get("data", {}).get("keys", [])
            if prefix:
                keys = [k for k in keys if k.startswith(prefix)]
            return keys


# Module-level singleton
vault = VaultClient()
