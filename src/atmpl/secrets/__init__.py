"""Secret material comes from a provider, never from a literal in the repository."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Protocol

from atmpl.settings import Settings

ENV_PREFIX = "ATMPL_SECRET_"

#: Secrets this project reads. Documented in .env.example and SECURITY.md.
KNOWN_SECRETS = ("jwt_signing_key", "session_signing_key")


class SecretNotConfigured(RuntimeError):
    """A required secret has no value in the configured backend."""


class SecretsProvider(Protocol):
    name: str

    def get(self, key: str) -> str | None: ...

    def keys(self) -> list[str]: ...


class EnvSecrets:
    """Default backend: ``ATMPL_SECRET_<KEY>`` environment variables."""

    name = "env"

    def get(self, key: str) -> str | None:
        return os.getenv(ENV_PREFIX + key.upper()) or None

    def keys(self) -> list[str]:
        return sorted(
            name[len(ENV_PREFIX) :].lower()
            for name in os.environ
            if name.startswith(ENV_PREFIX)
        )


class FileSecrets:
    """A JSON object on disk. The file must not be group/world readable on POSIX."""

    name = "file"

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        if os.name == "posix":
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if mode & 0o077:
                raise SecretNotConfigured(
                    f"Secrets file {self.path} is mode {mode:o}; it must be 0600."
                )
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SecretNotConfigured(f"Secrets file {self.path} must contain a JSON object.")
        return {str(key).lower(): str(value) for key, value in raw.items()}

    def get(self, key: str) -> str | None:
        return self._load().get(key.lower()) or None

    def keys(self) -> list[str]:
        return sorted(self._load())


def build_provider(settings: Settings) -> SecretsProvider:
    if settings.secrets_backend == "file":
        if settings.secrets_file is None:
            raise SecretNotConfigured(
                "ATMPL_SECRETS_BACKEND=file requires ATMPL_SECRETS_FILE=<path to a 0600 JSON file>."
            )
        return FileSecrets(settings.secrets_file)
    return EnvSecrets()


class SecretResolver:
    """Reads a secret, or mints an ephemeral one so the keyless demo still runs."""

    def __init__(self, provider: SecretsProvider) -> None:
        self.provider = provider
        self._ephemeral: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.provider.get(key)

    def require(self, key: str) -> str:
        value = self.provider.get(key)
        if not value:
            raise SecretNotConfigured(
                f"Secret '{key}' is not configured in the '{self.provider.name}' backend."
            )
        return value

    def get_or_ephemeral(self, key: str) -> str:
        """Configured value, else a process-lifetime random secret.

        An ephemeral key means tokens and sessions die when the process restarts. That is
        the correct default for a keyless demo and is wrong for a deployment, which is why
        ``atmpl doctor`` and the dashboard banner both say so.
        """
        value = self.provider.get(key)
        if value:
            return value
        if key not in self._ephemeral:
            self._ephemeral[key] = os.urandom(32).hex()
        return self._ephemeral[key]

    def is_ephemeral(self, key: str) -> bool:
        return not self.provider.get(key)

    def configured_values(self) -> list[str]:
        """Every non-empty secret value currently readable, for the leak gate."""
        found = [self.provider.get(key) for key in self.provider.keys()]  # noqa: SIM118
        return [value for value in found if value]
