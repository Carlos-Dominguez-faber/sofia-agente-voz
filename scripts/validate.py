"""Shared live validators for every credential the agent depends on.

One question per service, answered against the real API — never a local guess:

  * GHL      -> the PIT and Location resolve, AND the configured pipeline/stage
                actually exist (a Location can be valid while the pipeline id is
                stale). The Location check is `ghl_service.test_connection`; the
                pipeline check is `ghl_read_service.fetch_pipeline_opportunities`,
                which only returns without error when the pipeline id resolves.
  * Retell   -> the key is valid and the inbound agent is still there.
  * Twilio   -> the credentials work and the clinic's number is on the account.
  * Anthropic-> the key can reach the API (one cheap token).

Every check comes back as a plain dict — {service, ok, detail, solution, warning}
— so /setup can stop on the first failure, and /test and /status can render the
same result without re-implementing any of it. On failure the `solution` field
carries what to do, never a raw stack trace: the person running this is often a
non-developer installing for a clinic.

Nothing here prints or returns a secret. It reports whether a credential works,
not what it is.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Make `app` importable when this file is run directly from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# --------------------------------------------------------------------------
# One result shape for every check
# --------------------------------------------------------------------------


def _ok(service: str, detail: str, warning: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"service": service, "ok": True, "detail": detail}
    if warning:
        result["warning"] = warning
    return result


def _fail(service: str, detail: str, solution: str) -> dict[str, Any]:
    return {"service": service, "ok": False, "detail": detail, "solution": solution}


# --------------------------------------------------------------------------
# GoHighLevel — Location + pipeline/stage
# --------------------------------------------------------------------------


def validate_ghl() -> dict[str, Any]:
    """Validate the PIT/Location, then confirm the configured pipeline resolves.

    GHL is only ever a REFERENCE here: /setup does not create the calendar or the
    pipeline. Both must already exist in the subaccount, and their ids must be in
    sofia.config.yaml (crm.calendar_id / crm.pipeline_id / crm.stage_id).
    """
    try:
        from app.services import ghl_read_service, ghl_service
    except ImportError as exc:  # dependency missing, not a credential problem
        return _fail(
            "GoHighLevel",
            f"No se pudo importar la capa de GHL: {exc}",
            "Instala las dependencias del proyecto (pip install -e . o el requirements del repo).",
        )

    try:
        summary = ghl_service.test_connection()
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return _fail(
            "GoHighLevel",
            str(exc),
            "Revisa HIGHLEVEL_PIT y HIGHLEVEL_LOCATION_ID en .env. El PIT necesita los "
            "scopes contacts, calendars y opportunities, y el Location id debe ser el de "
            "la subcuenta correcta.",
        )

    # The Location is valid — now prove the pipeline/stage ids resolve. A stale
    # pipeline id passes the Location check and only fails here.
    try:
        ghl_read_service.fetch_pipeline_opportunities()
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "GoHighLevel",
            f"La Location responde, pero el pipeline no resuelve: {exc}",
            "Revisa crm.pipeline_id y crm.stage_id en sofia.config.yaml contra la subcuenta. "
            "GHL es referencia: el pipeline `Nuevos Pacientes` y su etapa ya deben existir ahí.",
        )

    detail = f"{summary.get('location_name') or 'Location'} · tz {summary.get('timezone')}"
    return _ok("GoHighLevel", detail, warning=summary.get("warning"))


# --------------------------------------------------------------------------
# Retell
# --------------------------------------------------------------------------


def validate_retell() -> dict[str, Any]:
    """Key is valid and the inbound agent still exists."""
    try:
        from app.services import retell_service
    except ImportError as exc:
        return _fail(
            "Retell",
            f"No se pudo importar la capa de Retell: {exc}",
            "Instala las dependencias del proyecto.",
        )

    try:
        summary = retell_service.test_connection()
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "Retell",
            str(exc),
            "Revisa RETELL_API_KEY en .env. Si el error habla de un agente inexistente, "
            "corre `python scripts/setup.py provision` para crear los agentes y llenar "
            "RETELL_INBOUND_AGENT_ID.",
        )

    return _ok("Retell", f"agente {summary.get('agent_name')} · voz {summary.get('voice_id')}")


# --------------------------------------------------------------------------
# Twilio
# --------------------------------------------------------------------------


def validate_twilio() -> dict[str, Any]:
    """Credentials work and the configured number is on the account."""
    try:
        from app.services import twilio_service
    except ImportError as exc:
        return _fail(
            "Twilio",
            f"No se pudo importar la capa de Twilio: {exc}",
            "Instala las dependencias del proyecto.",
        )

    try:
        summary = twilio_service.test_connection()
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "Twilio",
            str(exc),
            "Revisa TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN en .env.",
        )

    number = summary.get("phone_number")
    on_account = summary.get("number_on_account")
    if number and on_account is False:
        return _fail(
            "Twilio",
            f"{number} no está en esta cuenta de Twilio",
            "TWILIO_PHONE_NUMBER debe ser un número que exista en la misma cuenta que "
            "TWILIO_ACCOUNT_SID, en formato E.164 (con + y lada).",
        )

    return _ok("Twilio", f"cuenta {summary.get('account_status')} · número {number or 'sin configurar'}")


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


def validate_anthropic() -> dict[str, Any]:
    """The key can reach the API — one cheap token."""
    try:
        from app.services import anthropic_service
    except ImportError as exc:
        return _fail(
            "Anthropic",
            f"No se pudo importar la capa de Anthropic: {exc}",
            "Instala las dependencias del proyecto.",
        )

    try:
        summary = anthropic_service.test_connection()
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "Anthropic",
            str(exc),
            "Revisa ANTHROPIC_API_KEY en .env. Debe empezar con `sk-ant-` y tener crédito.",
        )

    return _ok("Anthropic", f"modelo {summary.get('model')}")


# --------------------------------------------------------------------------
# The whole set
# --------------------------------------------------------------------------

# Order matters: GHL first because a broken CRM is the most common install
# problem and the one that blocks provisioning.
CHECKS: dict[str, Callable[[], dict[str, Any]]] = {
    "ghl": validate_ghl,
    "retell": validate_retell,
    "twilio": validate_twilio,
    "anthropic": validate_anthropic,
}


def validate_all() -> list[dict[str, Any]]:
    """Run every check and return one result per service, in order."""
    return [check() for check in CHECKS.values()]


# --------------------------------------------------------------------------
# CLI — also the shape /test and /status consume
# --------------------------------------------------------------------------


def _print_result(result: dict[str, Any]) -> None:
    mark = "OK  " if result["ok"] else "FALLA"
    print(f"  [{mark}] {result['service']}: {result['detail']}")
    if result.get("warning"):
        print(f"         aviso: {result['warning']}")
    if not result["ok"]:
        print(f"         solución: {result['solution']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida cada credencial contra su API real.")
    parser.add_argument(
        "service",
        nargs="?",
        default="all",
        choices=[*CHECKS.keys(), "all"],
        help="Qué servicio validar. Por defecto: all.",
    )
    args = parser.parse_args(argv)

    results = validate_all() if args.service == "all" else [CHECKS[args.service]()]

    print("\nValidación de servicios:\n")
    for result in results:
        _print_result(result)
    print()

    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"{len(failed)} servicio(s) con problemas. Corrige y vuelve a correr.\n")
        return 1

    print("Todos los servicios responden.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
