"""The token that guards the read endpoints.

The Modal URL is public — it has to be, because Retell calls it from the
internet on every turn of every phone call. The action endpoints are safe on a
public URL: they need a valid signature (the webhook) or they only write data
the caller already supplied.

The read endpoints are a different matter. They return call transcripts,
summaries and phone numbers of real patients. On an open URL, anyone who learns
the address reads the clinic's medical intake.

So: one shared bearer token, held in the Modal Secret, checked here.

WHERE THE TOKEN LIVES, AND WHERE IT MUST NOT. It is a server-to-server
credential. The browser never sees it. The Next.js panel keeps it in a
server-only environment variable and proxies every request through its own route
handlers — see dashboard/docs/BLUEPRINT.md. A token shipped to the browser (a
`NEXT_PUBLIC_*` variable, a fetch straight from a component) is a published
token, whatever the variable is called.

This is one of two layers. This one stops anyone reaching the backend directly.
It does nothing about who may open the panel itself — that is the panel's own
password gate, and it is a separate lock on a separate door.

`/health` stays unauthenticated: Modal probes it, and it deliberately exposes
nothing but whether the process is up and configured.
"""

from __future__ import annotations

import logging
import os
import secrets

from fastapi import Header, HTTPException, status

from app.services.ghl_service import _load_env_file

LOG = logging.getLogger(__name__)

TOKEN_ENV_VAR = "DASHBOARD_API_TOKEN"

# Below this, a token is guessable. Rejecting a short one at startup is far
# cheaper than discovering it was brute-forced.
_MIN_TOKEN_LENGTH = 32


class AuthConfigError(RuntimeError):
    """The backend has no usable token configured."""


def _expected_token() -> str:
    _load_env_file()
    token = (os.environ.get(TOKEN_ENV_VAR) or "").strip()
    if not token:
        raise AuthConfigError(
            f"{TOKEN_ENV_VAR} is not set. Generate one with `python -c "
            "\"import secrets; print(secrets.token_urlsafe(32))\"` and add it to the "
            "Modal Secret `agente-voz-credentials` and to the panel's environment."
        )
    if len(token) < _MIN_TOKEN_LENGTH:
        raise AuthConfigError(
            f"{TOKEN_ENV_VAR} is shorter than {_MIN_TOKEN_LENGTH} characters. "
            "Patient data is behind it; use a generated value, not a chosen one."
        )
    return token


def require_token(authorization: str = Header(default="")) -> None:
    """FastAPI dependency: reject anything without the shared bearer token."""
    try:
        expected = _expected_token()
    except AuthConfigError as exc:
        # Refuse to serve rather than fall open. A misconfigured backend that
        # answers anyway would publish patient data and look perfectly healthy.
        LOG.error("Auth misconfigured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="El backend no tiene token configurado.",
        ) from exc

    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el header Authorization: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Constant-time: a plain `==` leaks the token one character at a time to
    # anyone who can measure the response.
    if not secrets.compare_digest(presented.strip(), expected):
        LOG.warning("Rejected a dashboard request with an invalid token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def allowed_origins() -> list[str]:
    """Origins the browser may call from, for CORS.

    Comma-separated in `DASHBOARD_ORIGIN`. Empty by default: with the proxy
    architecture the browser never calls this API cross-origin, so no origin
    needs to be allowed for the panel to work. A wildcard here would undo the
    token by letting any page on the internet use a victim's session.
    """
    _load_env_file()
    raw = (os.environ.get("DASHBOARD_ORIGIN") or "").strip()
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
