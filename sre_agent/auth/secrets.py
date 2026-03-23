"""Secret access providers for auth and runtime wiring."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import httpx


class SecretProvider:
    """Resolve secrets from env, mounted K8s secrets, or Vault."""

    def __init__(
        self,
        backend: Literal["env", "vault", "k8s"] = "env",
        *,
        vault_url: str | None = None,
        vault_token_env: str = "VAULT_TOKEN",
        k8s_mount_dir: str = "/secrets",
    ) -> None:
        self.backend = backend
        self.vault_url = vault_url
        self.vault_token_env = vault_token_env
        self.k8s_mount_dir = Path(k8s_mount_dir)

    async def get_secret(self, key: str) -> str:
        if self.backend == "env":
            value = os.getenv(key)
            if not value:
                raise ValueError(f"secret {key} not found in environment")
            return value
        if self.backend == "k8s":
            path = self.k8s_mount_dir / key
            if not path.exists():
                raise ValueError(f"secret {key} not found at {path}")
            return path.read_text(encoding="utf-8").strip()
        if self.backend == "vault":
            if not self.vault_url:
                raise ValueError("vault_url is required when backend=vault")
            token = os.getenv(self.vault_token_env)
            if not token:
                raise ValueError(f"Vault token env {self.vault_token_env} is not set")
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.vault_url.rstrip('/')}/v1/secret/data/sre-agent/{key}",
                    headers={"X-Vault-Token": token},
                )
                response.raise_for_status()
                return response.json()["data"]["data"]["value"]
        raise ValueError(f"unsupported secret backend: {self.backend}")
