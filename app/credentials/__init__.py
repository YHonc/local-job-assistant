from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class CredentialStoreError(RuntimeError):
    """Raised when the operating-system credential backend cannot be used."""


@runtime_checkable
class CredentialStore(Protocol):
    def get(self, credential_id: str) -> str | None: ...

    def set(self, credential_id: str, secret: str) -> None: ...

    def delete(self, credential_id: str) -> None: ...


class UnavailableCredentialStore:
    """A non-persistent failure object used when keyring cannot be imported."""

    @staticmethod
    def _raise() -> None:
        raise CredentialStoreError("credential store unavailable")

    def get(self, credential_id: str) -> str | None:
        self._raise()

    def set(self, credential_id: str, secret: str) -> None:
        self._raise()

    def delete(self, credential_id: str) -> None:
        self._raise()


class KeyringCredentialStore:
    service_name = "local-job-assistant"
    _missing_delete_errors = {"PasswordDeleteError", "ItemNotFoundException"}

    def __init__(self, keyring_module: Any | None = None) -> None:
        if keyring_module is None:
            try:
                import keyring as keyring_module
            except Exception as exc:
                raise CredentialStoreError("credential store unavailable") from exc
        self._keyring = keyring_module

    def get(self, credential_id: str) -> str | None:
        try:
            return self._keyring.get_password(self.service_name, credential_id)
        except Exception as exc:
            raise CredentialStoreError("credential store unavailable") from exc

    def set(self, credential_id: str, secret: str) -> None:
        try:
            self._keyring.set_password(self.service_name, credential_id, secret)
        except Exception as exc:
            raise CredentialStoreError("credential store unavailable") from exc

    def delete(self, credential_id: str) -> None:
        try:
            self._keyring.delete_password(self.service_name, credential_id)
        except Exception as exc:
            if exc.__class__.__name__ not in self._missing_delete_errors:
                raise CredentialStoreError("credential store unavailable") from exc


__all__ = [
    "CredentialStore",
    "CredentialStoreError",
    "KeyringCredentialStore",
    "UnavailableCredentialStore",
]
