#!/usr/bin/env python3
"""Sweep of the 5 services Sofía depends on, each with an actionable fix.

Runs, in order: Retell, Twilio, GoHighLevel, Backend (Modal /health) and
Anthropic. Every check reuses the real `test_connection()` living in each
service module — this script never re-implements a probe, it only interprets
the result and, on failure, turns the raw error into a concrete next step in
plain Spanish. It never prints secrets and never dumps a bare stack trace.

Exit code is non-zero if any service failed, so it can gate a deploy.

    python scripts/test_services.py
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

# Make the repo importable when run as a plain script from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402

from app.services import (  # noqa: E402
    anthropic_service,
    ghl_read_service,
    ghl_service,
    retell_service,
    twilio_service,
)
from app.services.ghl_service import _load_env_file  # noqa: E402

# --------------------------------------------------------------------------
# Output helpers — a checklist a non-dev can read, no color codes required.
# --------------------------------------------------------------------------

_OK = "✓"   # ✓
_BAD = "✗"  # ✗


def _line(ok: bool, name: str, detail: str) -> None:
    mark = _OK if ok else _BAD
    print(f"  {mark}  {name:<12} {detail}")


def _fix(text: str) -> None:
    """Print the fix for a failed check, indented under it."""
    print(f"       → {text}")


# --------------------------------------------------------------------------
# Each check returns (ok, detail_line, fix_line_or_None).
#
# The `fix` string is the whole point of this script: a client running /test
# should never see a raw exception, only what to do about it.
# --------------------------------------------------------------------------


def check_retell() -> tuple[bool, str, str | None]:
    try:
        info = retell_service.test_connection()
    except Exception as exc:  # noqa: BLE001 - the SDK raises many error types
        return False, "no se pudo conectar", _retell_fix(exc)
    detail = f"agente '{info.get('agent_name')}' listo (voz {info.get('voice_id')})"
    return True, detail, None


def _retell_fix(exc: Exception) -> str:
    msg = str(exc).lower()
    if "retell_api_key" in msg:
        return "Falta RETELL_API_KEY en tu .env. Cópiala del dashboard de Retell."
    if "retell_inbound_agent_id" in msg:
        return "Falta RETELL_INBOUND_AGENT_ID. Corre /setup para crear el agente y guardarlo."
    if "401" in msg or "unauthor" in msg or "invalid" in msg or "api key" in msg:
        return "Retell rechazó la API key. Revisa RETELL_API_KEY en tu .env y que la cuenta esté activa."
    if "not found" in msg or "404" in msg:
        return "El agente ya no existe en Retell. Vuelve a correr /setup para recrearlo."
    return "Revisa RETELL_API_KEY y RETELL_INBOUND_AGENT_ID en tu .env, y que la cuenta de Retell esté activa."


def check_twilio() -> tuple[bool, str, str | None]:
    try:
        info = twilio_service.test_connection()
    except Exception as exc:  # noqa: BLE001
        return False, "no se pudo conectar", _twilio_fix(exc)

    number = info.get("phone_number") or "sin número configurado"
    on_account = info.get("number_on_account")
    if on_account is False:
        # Credentials work but the clinic's line is not on the account: it rings
        # nowhere and nothing else would surface it.
        return (
            False,
            f"cuenta '{info.get('account_status')}' pero {number} no está en la cuenta",
            "El número de TWILIO_PHONE_NUMBER no aparece en esta cuenta de Twilio. "
            "Verifica que compraste el número en esta misma cuenta y que el valor lleva + y lada (E.164).",
        )
    detail = f"cuenta '{info.get('account_status')}', número {number} OK"
    return True, detail, None


def _twilio_fix(exc: Exception) -> str:
    msg = str(exc).lower()
    if "twilio_account_sid" in msg:
        return "Falta TWILIO_ACCOUNT_SID en tu .env. Está en el dashboard de Twilio."
    if "twilio_auth_token" in msg:
        return "Falta TWILIO_AUTH_TOKEN en tu .env. Está junto al SID en el dashboard de Twilio."
    if "20003" in msg or "authenticate" in msg or "401" in msg:
        return "Twilio rechazó las credenciales. Revisa TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN en tu .env."
    return "Revisa TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN en tu .env, y que la cuenta de Twilio esté activa."


def check_ghl() -> tuple[bool, str, str | None]:
    try:
        info = ghl_service.test_connection()
    except Exception as exc:  # noqa: BLE001
        return False, "no se pudo conectar", _ghl_fix(exc)

    # Confirm the calendar + pipeline actually resolve, not just the Location.
    # test_connection validates the PIT and the Location; the pipeline read is
    # what proves bookings and the funnel will work.
    pipeline_note = ""
    try:
        opportunities = ghl_read_service.fetch_pipeline_opportunities()
        pipeline_note = f", pipeline OK ({len(opportunities)} oportunidades)"
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            f"Location '{info.get('location_name')}' OK pero el pipeline falló",
            "El PIT es válido pero no pude leer el pipeline. Revisa HIGHLEVEL_PIPELINE_ID "
            f"en tu .env y que el token tenga el scope 'opportunities'. Detalle: {exc}",
        )

    detail = f"Location '{info.get('location_name')}' ({info.get('timezone')}){pipeline_note}"
    # A timezone drift books every appointment at the wrong hour, silently.
    warning = info.get("warning")
    if warning:
        return True, detail, f"Advertencia: {warning}"
    return True, detail, None


def _ghl_fix(exc: Exception) -> str:
    msg = str(exc).lower()
    if "highlevel_pit" in msg or "pit" in msg:
        return "Falta HIGHLEVEL_PIT en tu .env. Genera un Private Integration Token en la subcuenta de GHL."
    if "location" in msg and ("not set" in msg or "missing" in msg):
        return "Falta HIGHLEVEL_LOCATION_ID en tu .env. Es el id de la subcuenta (Location) en GHL."
    if "401" in msg or "403" in msg or "unauthor" in msg or "forbidden" in msg:
        return (
            "GHL rechazó el token. Revisa HIGHLEVEL_PIT en tu .env y que tenga los scopes "
            "contacts, calendars y opportunities sobre esta Location."
        )
    if "404" in msg:
        return "GHL no encontró la Location. Revisa HIGHLEVEL_LOCATION_ID en tu .env."
    return (
        "Revisa HIGHLEVEL_PIT y HIGHLEVEL_LOCATION_ID en tu .env, y que el token tenga "
        "los scopes contacts, calendars y opportunities."
    )


def check_backend() -> tuple[bool, str, str | None]:
    # The backend URL is the same one Retell's tools hit on every call.
    try:
        base_url = retell_service.modal_url()
    except Exception as exc:  # noqa: BLE001
        return False, "sin URL de backend", _backend_url_fix(exc)

    try:
        response = httpx.get(f"{base_url}/health", timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - network/DNS/timeout
        return (
            False,
            "no respondió",
            f"El backend en {base_url} no respondió. Verifica que hiciste "
            "`modal deploy app/main.py::modal_app` y que MODAL_URL apunta a esa URL. "
            f"Detalle: {exc}",
        )

    if response.status_code != 200:
        return (
            False,
            f"respondió HTTP {response.status_code}",
            "El backend respondió con error. Revisa los logs de Modal "
            "(`modal app logs agente-voz-credentials`) y que el último deploy haya terminado bien.",
        )

    try:
        payload = response.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001
        data = {}

    status = data.get("status", "ok")
    if status == "degraded":
        missing = ", ".join(data.get("missing_config", [])) or "desconocido"
        return (
            False,
            "arriba pero degradado",
            f"El backend responde pero le falta configuración: {missing}. "
            "Revisa sofia.config.yaml (calendar_id, pipeline_id, stage_id, timezone) y vuelve a desplegar.",
        )
    return True, f"arriba ({data.get('business') or base_url})", None


def _backend_url_fix(exc: Exception) -> str:
    msg = str(exc).lower()
    if "modal_url" in msg:
        return (
            "Falta MODAL_URL en tu .env. Es la URL pública que imprime "
            "`modal deploy app/main.py::modal_app`. Cópiala ahí."
        )
    return "No pude resolver la URL del backend. Revisa MODAL_URL en tu .env."


def check_anthropic() -> tuple[bool, str, str | None]:
    try:
        info = anthropic_service.test_connection()
    except Exception as exc:  # noqa: BLE001
        return False, "no se pudo conectar", _anthropic_fix(exc)
    return True, f"modelo {info.get('model')} responde", None


def _anthropic_fix(exc: Exception) -> str:
    msg = str(exc).lower()
    if "anthropic_api_key" in msg or "api_key" in msg:
        return "Falta ANTHROPIC_API_KEY en tu .env. Genérala en console.anthropic.com."
    if "401" in msg or "authentication" in msg or "invalid x-api-key" in msg:
        return "Anthropic rechazó la API key. Revisa ANTHROPIC_API_KEY en tu .env."
    if "429" in msg or "rate" in msg or "credit" in msg or "billing" in msg:
        return "Anthropic respondió sin créditos o con límite de uso. Revisa el saldo en console.anthropic.com."
    return "Revisa ANTHROPIC_API_KEY en tu .env y que la cuenta tenga créditos."


# --------------------------------------------------------------------------
# Runner — fixed order, one checklist, honest exit code.
# --------------------------------------------------------------------------

_CHECKS: list[tuple[str, Callable[[], tuple[bool, str, str | None]]]] = [
    ("Retell", check_retell),
    ("Twilio", check_twilio),
    ("GHL", check_ghl),
    ("Backend", check_backend),
    ("Anthropic", check_anthropic),
]


def main() -> int:
    _load_env_file()  # so the services read the local .env, same as in production
    print("\nProbando los 5 servicios de Sofía\n")

    results: list[tuple[str, bool]] = []
    for name, check in _CHECKS:
        try:
            ok, detail, fix = check()
        except Exception as exc:  # noqa: BLE001 - a check must never crash the sweep
            ok, detail, fix = False, "error inesperado", f"Error no manejado: {exc}"
        _line(ok, name, detail)
        if fix:
            _fix(fix)
        results.append((name, ok))

    failed = [name for name, ok in results if not ok]
    print()
    if failed:
        print(f"Resultado: {len(results) - len(failed)}/{len(results)} OK. "
              f"Falta resolver: {', '.join(failed)}.")
        return 1
    print(f"Resultado: {len(results)}/{len(results)} OK. Sofía puede operar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
