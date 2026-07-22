#!/usr/bin/env python3
"""Live status of Sofía: services up, published agent, and the last call.

Reads only — it never writes to any system. Three sections:

  1. Backend    — GET /health on Modal.
  2. Servicios  — the same `test_connection()` each service exposes.
  3. Agente     — the PUBLISHED version of the inbound agent (never the draft):
                  what the phone number actually serves right now.
  4. Última llamada — newest call from Retell, crossed with GHL for the result,
                  because GHL is the source of truth for what happened to the patient.

    python scripts/status.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9, not our runtime
    ZoneInfo = None  # type: ignore[assignment]

import httpx  # noqa: E402

from app.services import (  # noqa: E402
    anthropic_service,
    ghl_read_service,
    ghl_service,
    retell_service,
    twilio_service,
)
from app.services.ghl_service import _load_env_file, config_value  # noqa: E402

_OK = "✓"
_BAD = "✗"
_DASH = "·"


def _mark(ok: bool) -> str:
    return _OK if ok else _BAD


def _section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# --------------------------------------------------------------------------
# 1 · Backend health
# --------------------------------------------------------------------------


def show_backend() -> bool:
    _section("Backend")
    try:
        base_url = retell_service.modal_url()
    except Exception as exc:  # noqa: BLE001
        print(f"  {_BAD}  sin URL de backend — revisa MODAL_URL en tu .env ({exc})")
        return False

    try:
        response = httpx.get(f"{base_url}/health", timeout=10.0)
        payload = response.json() if response.status_code == 200 else {}
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001
        print(f"  {_BAD}  no respondió en {base_url} ({exc})")
        return False

    status = data.get("status", "desconocido")
    ok = response.status_code == 200 and status == "ok"
    print(f"  {_mark(ok)}  {base_url}")
    print(f"     estado: {status} {_DASH} negocio: {data.get('business') or 'n/d'} "
          f"{_DASH} zona: {data.get('timezone') or 'n/d'}")
    if data.get("missing_config"):
        print(f"     falta configuración: {', '.join(data['missing_config'])}")
    return ok


# --------------------------------------------------------------------------
# 2 · Per-service liveness (reuses the same probes as /test)
# --------------------------------------------------------------------------


def show_services() -> bool:
    _section("Servicios")
    probes = [
        ("Retell", retell_service.test_connection),
        ("Twilio", twilio_service.test_connection),
        ("GHL", ghl_service.test_connection),
        ("Anthropic", anthropic_service.test_connection),
    ]
    all_ok = True
    for name, probe in probes:
        try:
            info = probe()
            print(f"  {_OK}  {name:<10} {_summarize(name, info)}")
        except Exception as exc:  # noqa: BLE001
            all_ok = False
            print(f"  {_BAD}  {name:<10} caído ({_short(exc)})")
    return all_ok


def _summarize(name: str, info: dict[str, Any]) -> str:
    if name == "Retell":
        return f"agente '{info.get('agent_name')}' (voz {info.get('voice_id')})"
    if name == "Twilio":
        return f"cuenta '{info.get('account_status')}', número {info.get('phone_number') or 'n/d'}"
    if name == "GHL":
        return f"Location '{info.get('location_name')}' ({info.get('timezone')})"
    if name == "Anthropic":
        return f"modelo {info.get('model')} responde"
    return "OK"


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:120]


# --------------------------------------------------------------------------
# 3 · The PUBLISHED agent — what the phone number is serving right now
#
# Always the published version, never the latest draft. A draft left by a
# console edit is not what callers hear, and reporting it here would lie about
# the live state.
# --------------------------------------------------------------------------


def show_agent() -> None:
    _section("Agente publicado")
    try:
        agent_id = retell_service._inbound_agent_id()
    except Exception as exc:  # noqa: BLE001
        print(f"  {_BAD}  no configurado — {_short(exc)}")
        return

    try:
        agent = retell_service._published_agent(agent_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  {_BAD}  sin versión publicada — {_short(exc)}")
        print("     Publica el agente una vez desde Retell (o /setup) para que el número lo sirva.")
        return

    version = agent.get("version")
    voice_id = agent.get("voice_id")
    print(f"  {_OK}  versión publicada V{version} {_DASH} voz {voice_id} {_DASH} "
          f"idioma {agent.get('language')}")

    # The prompt actually spoken lives in the published LLM.
    try:
        prompt = retell_service.get_live_prompt(agent_id)
        first_line = prompt.strip().splitlines()[0] if prompt.strip() else ""
        print(f"     prompt en vivo: {len(prompt)} caracteres {_DASH} \"{first_line[:70]}...\"")
    except Exception as exc:  # noqa: BLE001
        print(f"     no pude leer el prompt en vivo — {_short(exc)}")


# --------------------------------------------------------------------------
# 4 · Last call — Retell for the event, GHL for the result
# --------------------------------------------------------------------------


def _fmt_time(epoch_ms: Any) -> str:
    if not isinstance(epoch_ms, (int, float)) or epoch_ms <= 0:
        return "hora desconocida"
    tz = None
    tz_name = config_value("business.timezone")
    if tz_name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(str(tz_name))
        except Exception:  # noqa: BLE001
            tz = None
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=tz or UTC)
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_duration(call: dict[str, Any]) -> str:
    ms = call.get("duration_ms")
    if not isinstance(ms, (int, float)) or ms <= 0:
        start, end = call.get("start_timestamp"), call.get("end_timestamp")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            ms = end - start
        else:
            return "duración n/d"
    seconds = round(ms / 1000)
    return f"{seconds // 60}m {seconds % 60}s"


def show_last_call() -> None:
    _section("Última llamada")
    try:
        calls = retell_service.list_calls(limit=1)
    except Exception as exc:  # noqa: BLE001
        print(f"  {_BAD}  no pude leer las llamadas de Retell — {_short(exc)}")
        return

    if not calls:
        print("  · todavía no hay llamadas registradas.")
        return

    call_id = calls[0].get("call_id")
    # The list row is light (no tool calls); fetch the full call for the phone
    # Sofía captured, which is the key that reaches the CRM.
    try:
        call = retell_service.get_call(str(call_id)) if call_id else calls[0]
    except Exception:  # noqa: BLE001
        call = calls[0]

    when = _fmt_time(call.get("start_timestamp"))
    duration = _fmt_duration(call)
    disconnection = call.get("disconnection_reason") or call.get("call_status") or "n/d"
    phone = _patient_phone(call)

    print(f"  {when} {_DASH} {duration} {_DASH} cierre: {disconnection}")

    if not phone:
        print("     sin número capturado (colgó antes de identificarse).")
        return

    # GHL is the source of truth for what happened to the patient after the call.
    try:
        contact = ghl_read_service.find_contact_by_phone(phone)
    except Exception as exc:  # noqa: BLE001
        print(f"     {phone} {_DASH} no pude cruzar con GHL ({_short(exc)})")
        return

    if not contact:
        print(f"     {phone} {_DASH} sin contacto en GHL (no calificó).")
        return

    name = ghl_read_service.contact_display_name(contact) or phone
    summary = ghl_read_service.post_call_summary(contact)
    print(f"     paciente: {name}")
    resultado = summary.get("resumen") or "sin resumen guardado"
    score = summary.get("interes_score")
    urgencia = summary.get("nivel_urgencia")
    extras = f" {_DASH} ".join(
        part for part in (
            f"score {score}" if score is not None else "",
            f"urgencia {urgencia}" if urgencia else "",
        ) if part
    )
    print(f"     resultado: {resultado}")
    if extras:
        print(f"     {extras}")


def _patient_phone(call: dict[str, Any]) -> str | None:
    """Phone Sofía captured, falling back to caller ID. Mirrors the dashboard logic."""
    from app.services.call_parsing import phone_from_tool_calls

    return phone_from_tool_calls(call) or call.get("from_number") or call.get("to_number")


# --------------------------------------------------------------------------


def main() -> int:
    _load_env_file()
    print("\nEstado en vivo de Sofía")

    backend_ok = show_backend()
    services_ok = show_services()
    show_agent()
    show_last_call()

    print()
    if backend_ok and services_ok:
        print("Todo arriba.")
        return 0
    print("Hay servicios caídos. Corre `python scripts/test_services.py` para el diagnóstico con solución.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
