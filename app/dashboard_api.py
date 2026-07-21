"""The read endpoints the panel calls.

Kept in a router of their own, mounted onto the same FastAPI app in `main.py`.
Two reasons, and the second is the one that matters:

  1. The action endpoints in `main.py` are what Retell calls during a live phone
     call. Nothing added for the dashboard should be able to change them.
  2. These endpoints have a different security model. Every route here requires
     the shared token; the action endpoints authenticate differently, and mixing
     the two in one file is how a route ends up on the wrong side of the lock.

Handlers stay thin. Every join, every metric and every rule lives in
`app/services/` so the logic has exactly one home.

The response envelope is the same one the action endpoints use, so the frontend
has one shape to parse:

    {"ok": true,  "data": {...}, "message": "..."}
    {"ok": false, "error": {"code": "...", "detail": "..."}, "message": "..."}
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import require_token
from app.services import dashboard_service, prompt_guard, prompt_history, retell_service
from app.services.dashboard_service import SourceUnavailable
from app.services.ghl_service import to_e164

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_token)])


def _ok(data: Any, message: str = "") -> JSONResponse:
    return JSONResponse(status_code=200, content={"ok": True, "data": data, "message": message})


def _fail(*, status: int, code: str, detail: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "detail": detail}, "message": message},
    )


def _source_down(exc: SourceUnavailable) -> JSONResponse:
    """A dead source is a 503 with a name attached, never an empty success.

    The UI needs to distinguish "no calls in this range" from "Retell did not
    answer". Both would render as an empty table; only one of them means the
    clinic should worry.
    """
    LOG.error("Source unavailable: %s", exc)
    return _fail(
        status=503,
        code="source_unavailable",
        detail=exc.detail,
        message=f"No pude leer los datos de {exc.source}. El dato no está disponible ahora mismo.",
    )


# --------------------------------------------------------------------------
# Metrics, funnel, temperature
# --------------------------------------------------------------------------


@router.get("/metrics")
def get_metrics(
    start: str | None = Query(default=None, description="YYYY-MM-DD, hora de la clínica"),
    end: str | None = Query(default=None, description="YYYY-MM-DD, hora de la clínica"),
    days: int | None = Query(default=None, ge=1, le=365),
) -> JSONResponse:
    """Total calls, appointments Sofía booked, success rate and average duration."""
    try:
        return _ok(dashboard_service.metrics(start=start, end=end, days=days))
    except SourceUnavailable as exc:
        return _source_down(exc)
    except ValueError as exc:
        return _fail(status=400, code="invalid_range", detail=str(exc), message="El rango de fechas no es válido.")


@router.get("/funnel")
def get_funnel() -> JSONResponse:
    """How many patients sit in each stage of the pipeline."""
    try:
        return _ok(dashboard_service.funnel())
    except SourceUnavailable as exc:
        return _source_down(exc)


@router.get("/leads/temperature")
def get_lead_temperature() -> JSONResponse:
    """hot / warm / cold counts, from the tags the post-call analysis wrote."""
    try:
        return _ok(dashboard_service.lead_temperature())
    except SourceUnavailable as exc:
        return _source_down(exc)


# --------------------------------------------------------------------------
# Calls
# --------------------------------------------------------------------------


@router.get("/calls")
def get_calls(
    limit: int = Query(default=dashboard_service.DEFAULT_PAGE_SIZE, ge=1, le=dashboard_service.MAX_PAGE_SIZE),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1, le=365),
    pagination_key: str | None = Query(default=None),
) -> JSONResponse:
    """Recent calls with the patient's name and whether Sofía booked."""
    try:
        return _ok(
            dashboard_service.recent_calls(
                limit=limit, start=start, end=end, days=days, pagination_key=pagination_key
            )
        )
    except SourceUnavailable as exc:
        return _source_down(exc)
    except ValueError as exc:
        return _fail(status=400, code="invalid_range", detail=str(exc), message="El rango de fechas no es válido.")


@router.get("/calls/{call_id}")
def get_call(call_id: str) -> JSONResponse:
    """Transcript, tools fired, and what the post-call analysis concluded."""
    try:
        return _ok(dashboard_service.call_detail(call_id))
    except SourceUnavailable as exc:
        return _source_down(exc)


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


class PromptUpdate(BaseModel):
    editable: str = Field(description="El prompt con el marcador de reglas de seguridad en su lugar")


@router.get("/agent/prompt")
def get_prompt() -> JSONResponse:
    """The live prompt, split into the part the client may edit and the part it may not."""
    try:
        live = retell_service.get_live_prompt()
    except Exception as exc:  # noqa: BLE001
        return _source_down(SourceUnavailable("retell", str(exc)))

    try:
        split = prompt_guard.split_for_editing(live)
    except prompt_guard.GuardrailError as exc:
        return _fail(
            status=500,
            code="guardrails_unavailable",
            detail=str(exc),
            message="No pude leer las reglas de seguridad del repositorio.",
        )

    return _ok(
        {
            **split,
            "protection": prompt_guard.describe_protection(),
            "previous": prompt_history.load_previous(),
        }
    )


@router.put("/agent/prompt")
def put_prompt(payload: PromptUpdate) -> JSONResponse:
    """Publish an edited prompt to Retell, with the safety block reinstated.

    The previous version is recorded first. If this save is the one that breaks
    Sofía, the undo has to already exist by the time anyone notices.
    """
    try:
        composed = prompt_guard.compose_for_publish(payload.editable)
    except prompt_guard.GuardrailError as exc:
        # 422, not 500: the request was understood and deliberately refused.
        return _fail(
            status=422,
            code="guardrails_missing",
            detail=str(exc),
            message=str(exc),
        )

    try:
        previous = retell_service.get_live_prompt()
    except Exception as exc:  # noqa: BLE001
        return _source_down(SourceUnavailable("retell", str(exc)))

    durable = prompt_history.save_previous(
        previous, saved_at=datetime.now(UTC).isoformat()
    )

    try:
        result = retell_service.set_live_prompt(composed)
    except Exception as exc:  # noqa: BLE001
        LOG.error("Failed to publish prompt: %s", exc)
        return _fail(
            status=502,
            code="publish_failed",
            detail=str(exc),
            message="No pude guardar el prompt en Retell. El prompt anterior sigue activo.",
        )

    return _ok(
        {**result, "undo_available": True, "undo_durable": durable},
        message="Prompt actualizado. Sofía ya está usando la nueva versión.",
    )


@router.post("/agent/prompt/undo")
def undo_prompt() -> JSONResponse:
    """Restore the prompt that was live before the last save."""
    stored = prompt_history.load_previous()
    if not stored["available"]:
        return _fail(
            status=404,
            code="nothing_to_undo",
            detail="No previous prompt recorded",
            message="No hay una versión anterior guardada.",
        )

    try:
        current = retell_service.get_live_prompt()
        result = retell_service.set_live_prompt(stored["prompt"])
    except Exception as exc:  # noqa: BLE001
        return _fail(
            status=502,
            code="publish_failed",
            detail=str(exc),
            message="No pude restaurar el prompt anterior.",
        )

    # Undo is itself undoable: what was live a second ago becomes the new undo.
    prompt_history.save_previous(current, saved_at=datetime.now(UTC).isoformat())

    return _ok(result, message="Restauré la versión anterior del prompt.")


# --------------------------------------------------------------------------
# Manual outbound call
# --------------------------------------------------------------------------


class OutboundCallRequest(BaseModel):
    phone: str = Field(description="Teléfono del paciente, E.164 o 10 dígitos")


@router.post("/outbound/call")
def outbound_call(payload: OutboundCallRequest) -> JSONResponse:
    """Dial a patient now. This one places a real phone call to a real person."""
    try:
        normalized = to_e164(payload.phone)
    except ValueError as exc:
        return _fail(
            status=400,
            code="invalid_phone",
            detail=str(exc),
            message="Ese número no es válido. Escríbelo con lada, por ejemplo +52 998 123 4567.",
        )

    try:
        result = retell_service.start_outbound_call(normalized)
    except Exception as exc:  # noqa: BLE001
        LOG.error("Outbound call failed for %s: %s", normalized, exc)
        return _fail(
            status=502,
            code="call_failed",
            detail=str(exc),
            message="No pude iniciar la llamada. Revisa el estado de los servicios.",
        )

    return _ok(result, message=f"Llamando a {normalized}.")


# --------------------------------------------------------------------------
# Service status
# --------------------------------------------------------------------------


@router.get("/services/status")
def get_services_status() -> JSONResponse:
    """Live state of GoHighLevel, Retell, Twilio, Anthropic and the backend."""
    status_report = dashboard_service.services_status()
    message = (
        "Todos los servicios responden."
        if status_report["all_ok"]
        else f"Servicios con problema: {', '.join(status_report['degraded'])}."
    )
    return _ok(status_report, message=message)
