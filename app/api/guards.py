"""HTTP-layer helpers shared by every API router.

``origin_allowed`` implements the single origin policy for this local app:
only same-origin pages (http://127.0.0.1:8765 / localhost:8765) and this
Chrome extension may mutate state. It is enforced centrally by the middleware
registered in ``app.main.create_app`` for every POST/PUT/PATCH/DELETE request,
so individual routes no longer need their own guard call.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from app.credentials import CredentialStore, CredentialStoreError

_CHROME_EXTENSION_ID = re.compile(r"[a-p]{32}")


def origin_allowed(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        return False
    if parsed.scheme == "chrome-extension":
        return bool(parsed.hostname and _CHROME_EXTENSION_ID.fullmatch(parsed.hostname))
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and port == 8765
    )


def require_credential_store(request: Request) -> CredentialStore:
    store = getattr(request.app.state, "credential_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="credential store unavailable",
        )
    return store


def restore_secret(
    store: CredentialStore,
    credential_id: str,
    old_secret: str | None,
) -> bool:
    """Best-effort keyring rollback after a failed database write."""
    try:
        if old_secret is None:
            store.delete(credential_id)
        else:
            store.set(credential_id, old_secret)
    except CredentialStoreError:
        return False
    return True
