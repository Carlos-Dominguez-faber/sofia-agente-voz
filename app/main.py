"""FastAPI on Modal — the HTTP tools Retell calls while Sofía is on the phone.

Every endpoint is a thin handler. The business logic lives in
`app/services/`, so the dashboard can reuse it later without duplicating a
single rule (CLAUDE.md §8).

Response envelope, identical on every endpoint so a frontend can rely on it:

    {"ok": true,  "data": {...},  "message": "..."}       # 2xx
    {"ok": false, "error": {"code": "...", "detail": "..."}, "message": "..."}   # 4xx/5xx

`message` is the line Sofía can say out loud. `data` is for the dashboard.

THE RULE THAT DOES NOT BEND: /book-appointment never answers 200 with a
booking that did not happen. If GHL fails, it returns an error status and the
human-follow-up line, and Sofía offers a callback instead of confirming.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from app.services import anthropic_service
from app.services import ghl_service as ghl
from app.services.ghl_service import (
    GHLAPIError,
    GHLBookingError,
    GHLConfigError,
    GHLError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
LOG = logging.getLogger("sofia.api")

MODAL_APP_NAME = "agente-voz-ghl"
MODAL_SECRET_NAME = "agente-voz-credentials"

# Voice constraint, not a technical one: a caller cannot hold twelve options in
# their head. Sofía offers a few and the prompt decides how to phrase them.
MAX_SPOKEN_OPTIONS = 6
DEFAULT_DAYS_AHEAD = 7

# Minimum lead time before a slot may be offered. Guards against offering an
# appointment the patient could not physically reach. Override in
# sofia.config.yaml with `business.min_notice_minutes`.
DEFAULT_MIN_NOTICE_MINUTES = 60

# Fallback if sofia.config.yaml has not been customized yet.
FALLBACK_HUMAN_FOLLOWUP = (
    "Déjame confirmártelo, en un momento te contacta una persona del equipo"
)


# --------------------------------------------------------------------------
# Response envelope
# --------------------------------------------------------------------------


def _human_followup_message() -> str:
    """The honest line Sofía says when GHL is not answering. Never a fake confirmation."""
    return str(ghl.config_value("crm.rules.failure_message", FALLBACK_HUMAN_FOLLOWUP))


def ok(data: Mapping[str, Any] | None = None, message: str = "") -> JSONResponse:
    return JSONResponse(status_code=200, content={"ok": True, "data": dict(data or {}), "message": message})


def fail(
    *,
    status: int,
    code: str,
    detail: str,
    message: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "detail": detail},
        "message": message or _human_followup_message(),
    }
    if data:
        body["data"] = dict(data)
    return JSONResponse(status_code=status, content=body)


def _business_tz() -> ZoneInfo:
    return ZoneInfo(str(ghl.config_value("business.timezone", "America/Cancun")))


# --------------------------------------------------------------------------
# Spoken date/time — computed here, not by the model
#
# The LLM has no clock and no calendar. Handed a bare ISO timestamp it cannot
# tell "hoy" from "mañana", and it will confidently say the wrong one out loud.
# The backend knows the clinic's timezone and the current time, so it does the
# conversion and the model only reads the label back.
# --------------------------------------------------------------------------

_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_HOURS_ES = {
    1: "una", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis",
    7: "siete", 8: "ocho", 9: "nueve", 10: "diez", 11: "once", 12: "doce",
}


def _spoken_time(moment: datetime) -> str:
    """`16:30` -> `las cuatro y media de la tarde`."""
    hour24 = moment.hour
    hour12 = hour24 % 12 or 12
    article = "la" if hour12 == 1 else "las"

    if moment.minute == 0:
        minutes = ""
    elif moment.minute == 15:
        minutes = " y cuarto"
    elif moment.minute == 30:
        minutes = " y media"
    else:
        minutes = f" y {moment.minute}"

    if hour24 < 12:
        period = "de la mañana"
    elif hour24 == 12:
        period = "del día"  # "las doce del día", not "de la tarde"
    elif hour24 < 19:
        period = "de la tarde"
    else:
        period = "de la noche"

    return f"{article} {_HOURS_ES[hour12]}{minutes} {period}"


def _spoken_day(moment: datetime, now: datetime) -> str:
    """`hoy`, `mañana`, or `el martes 21` — relative to the clinic's today."""
    delta_days = (moment.date() - now.date()).days
    if delta_days == 0:
        return "hoy"
    if delta_days == 1:
        return "mañana"
    return f"el {_WEEKDAYS_ES[moment.weekday()]} {moment.day}"


def _spoken_label(moment: datetime, now: datetime) -> str:
    """The whole phrase Sofía can say without doing any date math herself."""
    return f"{_spoken_day(moment, now)} a {_spoken_time(moment)}"


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class RetellToolRequest(BaseModel):
    """Base model that accepts either flat arguments or Retell's nested envelope.

    Retell has shipped both shapes for custom tools: sometimes the arguments
    arrive at the top level, sometimes wrapped in `args` alongside `call`
    metadata. Accepting both here means a Retell config change never silently
    breaks a live agent.
    """

    call_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        payload = {k: v for k, v in data.items() if k not in {"args", "call", "name"}}
        args = data.get("args")
        if isinstance(args, Mapping):
            payload.update(args)
        call = data.get("call")
        if isinstance(call, Mapping) and not payload.get("call_id"):
            payload["call_id"] = call.get("call_id") or call.get("callId")
        return payload


class CreateLeadRequest(RetellToolRequest):
    phone: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    reason: str | None = Field(default=None, description="Motivo de la llamada")
    tags: list[str] | None = None


class CheckAvailabilityRequest(RetellToolRequest):
    start_date: str | None = Field(default=None, description="YYYY-MM-DD; por defecto hoy")
    days_ahead: int = Field(default=DEFAULT_DAYS_AHEAD, ge=1, le=ghl.FREE_SLOTS_MAX_DAYS)
    max_options: int = Field(default=MAX_SPOKEN_OPTIONS, ge=1, le=20)
    calendar_id: str | None = None


class BookAppointmentRequest(RetellToolRequest):
    phone: str
    start_time: str = Field(description="ISO local de la clínica, ej. 2026-07-21T16:00:00")
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    reason: str | None = None
    treatment: str | None = None
    urgency: str | None = Field(default=None, description="urgente | normal | baja")
    temperature: str | None = Field(default=None, description="hot | warm | cold")
    duration_minutes: int | None = Field(default=None, ge=5, le=240)


class UpdateLeadStatusRequest(RetellToolRequest):
    contact_id: str | None = None
    phone: str | None = None
    temperature: str | None = Field(default=None, description="hot | warm | cold")
    stage: str | None = Field(default=None, description="clave de crm.pipeline_stages")
    stage_id: str | None = None
    opportunity_id: str | None = None
    opportunity_name: str | None = None


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

web_app = FastAPI(
    title="Sofía · agente de voz",
    description="Tools que Retell llama en vivo. GoHighLevel es la única fuente de la verdad.",
    version="0.1.0",
)


@web_app.exception_handler(GHLError)
async def _ghl_error_handler(_: Request, exc: GHLError) -> JSONResponse:
    """One place that turns a services-layer failure into an honest HTTP answer."""
    if isinstance(exc, GHLConfigError):
        # Our misconfiguration, not GHL's fault. Loud on purpose.
        LOG.error("Configuration error: %s", exc)
        return fail(status=500, code="config_error", detail=str(exc))
    if isinstance(exc, GHLBookingError):
        LOG.error("Booking failed: %s", exc)
        return fail(status=502, code="booking_failed", detail=str(exc))
    if isinstance(exc, GHLAPIError):
        LOG.error("GHL API error: %s", exc)
        return fail(status=502, code="ghl_unavailable", detail=str(exc))
    LOG.error("Unexpected GHL layer error: %s", exc)
    return fail(status=502, code="ghl_error", detail=str(exc))


@web_app.exception_handler(ValueError)
async def _value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    """Bad input — a malformed phone, a range too wide. The caller can fix it."""
    LOG.warning("Invalid input: %s", exc)
    return fail(
        status=400,
        code="invalid_input",
        detail=str(exc),
        message="No pude tomar ese dato, ¿me lo confirmas otra vez?",
    )


@web_app.exception_handler(RequestValidationError)
async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Keep FastAPI's 422 inside our envelope.

    The default body is `{"detail": [...]}`, a different shape from every other
    answer this API gives. Retell would hand Sofía something she cannot read,
    and the dashboard would need a second parser for one status code.
    """
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', []) if p != 'body')}: {err.get('msg')}"
        for err in exc.errors()
    )
    LOG.warning("Request validation failed: %s", problems)
    return fail(
        status=400,
        code="invalid_input",
        detail=problems or "request body failed validation",
        message="Me falta un dato para continuar, ¿me lo repites?",
    )


@web_app.exception_handler(Exception)
async def _unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Last resort: Sofía must always receive a parseable answer, never an HTML 500.

    The detail is deliberately generic — an internal traceback has no business
    reaching Retell — but the full error goes to the logs.
    """
    LOG.exception("Unhandled error: %s", exc)
    return fail(
        status=500,
        code="internal_error",
        detail="Unexpected backend error; see logs.",
    )


# ==========================================================================
# POST /create-lead
# ==========================================================================


@web_app.post("/create-lead")
async def create_lead(payload: CreateLeadRequest) -> JSONResponse:
    """Registers the caller in the CRM. Idempotent by phone — calling twice never duplicates."""
    custom_fields = {}
    reason_key = ghl.config_value("crm.custom_fields.reason_for_visit")
    if payload.reason and reason_key:
        custom_fields[reason_key] = payload.reason

    contact = ghl.upsert_contact(
        phone=payload.phone,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        tags=payload.tags,
        custom_fields=custom_fields or None,
    )

    LOG.info("create-lead call=%s contact=%s new=%s", payload.call_id, contact["id"], contact["is_new"])
    return ok(
        {
            "contact_id": contact["id"],
            "is_new": contact["is_new"],
            "phone": contact["phone"],
        },
        message="Listo, ya tengo tus datos.",
    )


# ==========================================================================
# POST /check-availability
# ==========================================================================


@web_app.post("/check-availability")
async def check_availability(payload: CheckAvailabilityRequest) -> JSONResponse:
    """Reads the real calendar and returns the openings Sofía is allowed to offer."""
    tz = _business_tz()
    now = datetime.now(tz)
    min_notice = int(ghl.config_value("business.min_notice_minutes", DEFAULT_MIN_NOTICE_MINUTES))
    cutoff = now + timedelta(minutes=min_notice)

    if payload.start_date:
        start = datetime.fromisoformat(payload.start_date).replace(tzinfo=tz)
    else:
        start = now
    # Never offer a slot that has already passed, nor one so close the patient
    # could not physically get there. Asking GHL from midnight returns the whole
    # day, including the hours that are already gone.
    start = max(start, cutoff)
    end = start + timedelta(days=payload.days_ahead)

    slots_by_date = ghl.get_free_slots(start, end, calendar_id=payload.calendar_id)

    # Belt and braces: GHL buckets by day, so a same-day query can still hand
    # back this morning's slots. Drop anything at or before the cutoff.
    future_by_date: dict[str, list[str]] = {}
    dropped = 0
    for date, slots in slots_by_date.items():
        kept = []
        for slot in slots:
            try:
                moment = datetime.fromisoformat(slot)
            except ValueError:
                continue
            if moment > cutoff:
                kept.append(slot)
            else:
                dropped += 1
        if kept:
            future_by_date[date] = kept

    # Flatten in chronological order and cap it — this is what Sofía reads out.
    options: list[dict[str, str]] = []
    for date in sorted(future_by_date):
        for slot in future_by_date[date]:
            options.append({"iso": slot, "label": _spoken_label(datetime.fromisoformat(slot), now)})
            if len(options) >= payload.max_options:
                break
        if len(options) >= payload.max_options:
            break

    if not options:
        LOG.info("check-availability call=%s no future slots (dropped %s past)", payload.call_id, dropped)
        return ok(
            {
                "slots_by_date": {},
                "options": [],
                "has_availability": False,
                "current_datetime": now.isoformat(),
            },
            message="No encontré espacios en ese rango. ¿Buscamos en otra fecha?",
        )

    LOG.info(
        "check-availability call=%s days=%s options=%s dropped_past=%s",
        payload.call_id,
        len(future_by_date),
        len(options),
        dropped,
    )
    return ok(
        {
            "slots_by_date": future_by_date,
            "options": options,
            "has_availability": True,
            "timezone": str(tz),
            # The model has no clock of its own. Without this it cannot tell
            # "hoy" from "mañana" and will narrate the wrong day out loud.
            "current_datetime": now.isoformat(),
        },
        message="Tengo estos horarios disponibles.",
    )


# ==========================================================================
# POST /book-appointment
# ==========================================================================


@web_app.post("/book-appointment")
async def book_appointment(payload: BookAppointmentRequest) -> JSONResponse:
    """Books the valoración: contact, appointment and pipeline card, in that order.

    The order is deliberate. The appointment is the promise made to the patient,
    so nothing is confirmed until GHL has actually created it.
    """
    # --- Step 1: the contact. Without it there is nothing to book against. ---
    custom_fields = {}
    reason_key = ghl.config_value("crm.custom_fields.reason_for_visit")
    if payload.reason and reason_key:
        custom_fields[reason_key] = payload.reason

    # The temperature tag is NOT passed to upsert_contact: that write is additive
    # and would stack on top of whatever tag is already there. It goes through
    # set_temperature_tag below, once the contact exists.
    contact = ghl.upsert_contact(
        phone=payload.phone,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        custom_fields=custom_fields or None,
    )

    if payload.temperature:
        try:
            ghl.set_temperature_tag(contact["id"], payload.temperature)
        except (ghl.GHLError, ValueError) as exc:
            # A wrong tag must never cost the patient the appointment.
            LOG.warning("Could not set temperature on contact %s: %s", contact["id"], exc)

    # --- Step 2: the appointment. This is the promise. It either lands or we say so. ---
    duration = payload.duration_minutes or ghl.DEFAULT_APPOINTMENT_MINUTES
    appointment = ghl.book_appointment(
        contact_id=contact["id"],
        start=payload.start_time,
        duration_minutes=duration,
        calendar_id=None,
    )
    # If we reach this line the slot is really taken in the clinic's calendar.
    # GHLBookingError would have been raised otherwise and handled above.

    # --- Step 3: the pipeline card. Bookkeeping, not a promise. ---
    #
    # A failure here must NOT be reported as a failed booking. The appointment
    # exists; telling the patient it did not would send them elsewhere while the
    # slot stays blocked — and risks a second booking on top of the first.
    # Sofía confirms truthfully; the gap is flagged for a human to close.
    warnings: list[str] = []
    opportunity_id = None
    treatment = payload.treatment or str(ghl.config_value("knowledge_base.valoracion.name", "Cita de valoración"))
    display_name = " ".join(filter(None, [payload.first_name, payload.last_name])) or payload.phone

    try:
        opportunity = ghl.create_opportunity(
            contact_id=contact["id"],
            name=f"{display_name} · {treatment}",
        )
        opportunity_id = opportunity["id"]
    except GHLError as exc:
        LOG.error("Appointment %s booked but opportunity failed: %s", appointment["id"], exc)
        warnings.append("opportunity_not_created")

    LOG.info(
        "book-appointment call=%s contact=%s appointment=%s start=%s warnings=%s",
        payload.call_id,
        contact["id"],
        appointment["id"],
        appointment["start_time"],
        warnings,
    )
    return ok(
        {
            "contact_id": contact["id"],
            "appointment_id": appointment["id"],
            "opportunity_id": opportunity_id,
            "start_time": appointment["start_time"],
            "end_time": appointment["end_time"],
            "status": appointment["status"],
            "urgency": payload.urgency,
            "warnings": warnings,
        },
        message="Tu cita quedó agendada.",
    )


# ==========================================================================
# POST /update-lead-status
# ==========================================================================


@web_app.post("/update-lead-status")
async def update_lead_status(payload: UpdateLeadStatusRequest) -> JSONResponse:
    """Sets the lead temperature and moves the patient's card along the pipeline."""
    if not payload.contact_id and not payload.phone:
        return fail(
            status=400,
            code="invalid_input",
            detail="Either contact_id or phone is required",
            message="Me falta identificar al paciente.",
        )

    # upsert is idempotent by phone, so it doubles as a lookup with no extra endpoint.
    contact_id = payload.contact_id or ghl.upsert_contact(phone=payload.phone)["id"]

    applied_tags: list[str] = []
    if payload.temperature:
        allowed = ghl.config_value("crm.tags.temperature", ["hot", "warm", "cold"])
        if payload.temperature not in allowed:
            return fail(
                status=400,
                code="invalid_input",
                detail=f"temperature must be one of {allowed}, got `{payload.temperature}`",
                message="No reconocí esa temperatura de lead.",
            )
        # Replaces the sibling temperature tags instead of stacking on them.
        applied_tags = ghl.set_temperature_tag(contact_id, payload.temperature)["tags"]

    # Resolve the stage from the config map so handlers never carry GHL ids.
    stage_id = payload.stage_id
    if not stage_id and payload.stage:
        stage_map = ghl.config_value("crm.pipeline_stages", {}) or {}
        stage_id = stage_map.get(payload.stage)
        if not stage_id:
            return fail(
                status=400,
                code="invalid_input",
                detail=f"stage `{payload.stage}` is not in crm.pipeline_stages ({sorted(stage_map)})",
                message="No reconocí esa etapa del pipeline.",
            )

    opportunity_id = payload.opportunity_id
    stage_changed = False
    if stage_id:
        if opportunity_id:
            ghl.update_opportunity_stage(opportunity_id, stage_id)
        else:
            created = ghl.create_opportunity(
                contact_id=contact_id,
                name=payload.opportunity_name or f"Lead {contact_id}",
                stage_id=stage_id,
            )
            opportunity_id = created["id"]
        stage_changed = True

    LOG.info(
        "update-lead-status call=%s contact=%s tags=%s stage=%s",
        payload.call_id,
        contact_id,
        applied_tags,
        stage_id,
    )
    return ok(
        {
            "contact_id": contact_id,
            "tags": applied_tags,
            "opportunity_id": opportunity_id,
            "stage_id": stage_id,
            "stage_changed": stage_changed,
        },
        message="Actualicé el estado del paciente.",
    )


# ==========================================================================
# Post-call analysis — runs after the webhook has already answered Retell
# ==========================================================================


def _transcript_from(call: Mapping[str, Any]) -> str:
    """Retell ships the transcript as a plain string; fall back to the object form."""
    transcript = call.get("transcript")
    if isinstance(transcript, str) and transcript.strip():
        return transcript

    turns = call.get("transcript_object") or []
    lines = [
        f"{turn.get('role', '?')}: {turn.get('content', '')}"
        for turn in turns
        if isinstance(turn, Mapping) and turn.get("content")
    ]
    return "\n".join(lines)


def _phone_from_tool_calls(call: Mapping[str, Any]) -> str | None:
    """Recover the patient's phone from the tool calls Sofía made during the call.

    The backend keeps no call_id -> contact_id map — GHL is the source of truth,
    not us. But the phone Sofía passed to create_lead / book_appointment is right
    there in the transcript payload, and upsert_contact is idempotent by phone,
    so it resolves back to the same contact without any local state.
    """
    for entry in call.get("transcript_with_tool_calls") or []:
        if not isinstance(entry, Mapping) or entry.get("role") != "tool_call_invocation":
            continue
        if entry.get("name") not in {"create_lead", "book_appointment"}:
            continue
        raw_args = entry.get("arguments")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            continue
        if isinstance(args, Mapping) and args.get("phone"):
            return str(args["phone"])
    return None


def _resolve_contact_id(call: Mapping[str, Any]) -> str | None:
    """Find the GHL contact this call belongs to, without any local state."""
    phone = _phone_from_tool_calls(call) or call.get("from_number")
    if not phone:
        return None
    try:
        return ghl.upsert_contact(phone=phone)["id"]  # idempotent: resolves, never duplicates
    except (ghl.GHLError, ValueError) as exc:
        LOG.error("Could not resolve contact for phone %s: %s", phone, exc)
        return None


def process_call_ended(call: Mapping[str, Any]) -> None:
    """Analyze the transcript with Claude and write the result onto the GHL contact.

    Runs in the background. Every failure is contained here: Retell already got
    its 200, and a broken analysis must never take down the webhook.
    """
    call_id = call.get("call_id")
    transcript = _transcript_from(call)

    if not transcript.strip():
        LOG.warning("call_ended call=%s had no transcript; nothing to analyze", call_id)
        return

    contact_id = _resolve_contact_id(call)
    if not contact_id:
        # No phone was ever captured — a hang-up before qualification. There is
        # no contact to attach anything to, and inventing one would pollute the CRM.
        LOG.warning("call_ended call=%s could not be matched to a contact; skipping write", call_id)
        return

    duration_s = round((call.get("duration_ms") or 0) / 1000) or None

    # --- The analysis. If it fails, the transcript still gets saved. ---
    try:
        analysis = anthropic_service.analyze_call(transcript, call_id=call_id)
    except anthropic_service.AnalysisError as exc:
        LOG.error("Analysis failed for call=%s: %s", call_id, exc)
        try:
            ghl.add_note(contact_id, anthropic_service.format_fallback_note(transcript, str(exc), call_id=call_id))
            LOG.info("Wrote raw-transcript fallback note for call=%s", call_id)
        except ghl.GHLError as note_exc:
            LOG.error("Could not even write the fallback note for call=%s: %s", call_id, note_exc)
        return

    # --- The note: summary + next action, on the contact's file. ---
    try:
        ghl.add_note(contact_id, anthropic_service.format_note(analysis, call_id=call_id, duration_s=duration_s))
    except ghl.GHLError as exc:
        LOG.error("Could not write note for call=%s: %s", call_id, exc)

    # --- The scores: each into its configured custom field. ---
    #
    # Written one mapping at a time so a single missing field key degrades that
    # field only. Note what is absent: contact.notas_clinicas. That field is the
    # doctor's clinical record; model output never goes there.
    field_values = {
        "interes_score": analysis.interes_score,
        "nivel_urgencia": analysis.nivel_urgencia,
        "probabilidad_asistir": analysis.probabilidad_asistir,
        "resumen_llamada": analysis.resumen,
    }
    custom_fields = {}
    for config_key, value in field_values.items():
        field_key = ghl.config_value(f"crm.custom_fields.{config_key}")
        if field_key and value is not None:
            custom_fields[field_key] = value

    if custom_fields:
        try:
            ghl.update_contact_fields(contact_id, custom_fields)
        except (ghl.GHLError, ValueError) as exc:
            LOG.error("Could not write custom fields for call=%s: %s", call_id, exc)

    # --- The temperature tag. ---
    # This runs last of the three writers and reads the whole transcript, so its
    # verdict replaces the preliminary one the agent set during the call.
    if analysis.temperatura:
        try:
            ghl.set_temperature_tag(contact_id, analysis.temperatura)
        except (ghl.GHLError, ValueError) as exc:
            LOG.error("Could not tag contact for call=%s: %s", call_id, exc)

    LOG.info(
        "Post-call analysis written call=%s contact=%s fields=%s tag=%s",
        call_id,
        contact_id,
        len(custom_fields),
        analysis.temperatura,
    )


# ==========================================================================
# POST /retell-webhook
# ==========================================================================


@web_app.post("/retell-webhook")
async def retell_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    """Receives Retell's call lifecycle events: call_started, call_ended, call_analyzed.

    The signature is verified because this URL is public: without it anyone who
    learns the address could post fabricated calls into the clinic's CRM.

    Today this endpoint records the event and returns fast — Retell retries on a
    slow or failing webhook, and a retry storm during a live call is the last
    thing the backend needs. The Claude post-call analysis hangs off `call_ended`
    and is step 3 of the build order.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-retell-signature", "")
    api_key = (os.environ.get("RETELL_API_KEY") or "").strip()

    if not api_key:
        LOG.error("RETELL_API_KEY missing; cannot verify webhook signature")
        return fail(status=500, code="config_error", detail="RETELL_API_KEY is not configured")

    try:
        from retell.lib import verify as verify_retell_signature

        valid = verify_retell_signature(raw_body.decode("utf-8"), api_key, signature)
    except Exception as exc:  # noqa: BLE001 - never let verification crash the webhook
        LOG.error("Signature verification raised: %s", exc)
        valid = False

    if not valid:
        LOG.warning("Rejected webhook with invalid signature from %s", request.client.host if request.client else "?")
        return fail(status=401, code="invalid_signature", detail="Signature verification failed")

    try:
        body = json.loads(raw_body or b"{}")
    except json.JSONDecodeError as exc:
        return fail(status=400, code="invalid_input", detail=f"Body is not JSON: {exc}")

    event = body.get("event")
    call = body.get("call") or {}
    call_id = call.get("call_id")

    if event == "call_started":
        LOG.info("call_started call=%s from=%s", call_id, call.get("from_number"))
    elif event == "call_ended":
        LOG.info(
            "call_ended call=%s duration=%sms disconnect=%s",
            call_id,
            call.get("duration_ms"),
            call.get("disconnection_reason"),
        )
        # Claude + GHL writes happen after this response is sent. Retell gets its
        # 200 immediately: an analysis that takes 15s must never look like a slow
        # webhook, or Retell starts retrying and we analyze the same call twice.
        background.add_task(process_call_ended, call)
    elif event == "call_analyzed":
        LOG.info("call_analyzed call=%s", call_id)
    else:
        LOG.info("Unhandled Retell event `%s` call=%s", event, call_id)

    return ok({"event": event, "call_id": call_id, "handled": True}, message="")


# ==========================================================================
# GET /health
# ==========================================================================


@web_app.get("/health")
async def health() -> JSONResponse:
    """Says whether the backend is up and configured. Deliberately does not call GHL.

    Modal probes this often, and a healthcheck that depends on a third party
    reports *their* outage as *our* death. The live GHL check is
    ghl_service.test_connection(), which /test and /status use.
    """
    config_ok = True
    missing: list[str] = []
    try:
        for path in ("business.timezone", "crm.calendar_id", "crm.pipeline_id", "crm.stage_id"):
            if not ghl.config_value(path):
                missing.append(path)
                config_ok = False
    except GHLError as exc:  # config file itself unreadable
        return fail(status=500, code="config_error", detail=str(exc), message="Backend mal configurado.")

    body = {
        "service": MODAL_APP_NAME,
        "status": "ok" if config_ok else "degraded",
        "config_ok": config_ok,
        "missing_config": missing,
        "business": ghl.config_value("business.name"),
        "timezone": ghl.config_value("business.timezone"),
    }
    return JSONResponse(status_code=200, content={"ok": True, "data": body, "message": "Backend arriba."})


# --------------------------------------------------------------------------
# Modal — public URL for Retell's custom tools
#
# Modal authenticates via CLI (`modal token new`), never through .env.
# Runtime secrets come from the Modal Secret `agente-voz-credentials`.
#
# Deploy:  modal deploy app/main.py::modal_app
#          (el sufijo ::modal_app es obligatorio — Modal busca por defecto
#           una variable llamada `app` y la nuestra se llama `modal_app`)
# Local:   uvicorn app.main:web_app --reload
# --------------------------------------------------------------------------

try:
    import modal
except ImportError:  # pragma: no cover - local dev without the Modal SDK
    modal = None  # type: ignore[assignment]
    LOG.warning("Modal SDK not installed; serve locally with `uvicorn app.main:web_app --reload`")

if modal is not None:
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install(
            "fastapi[standard]",
            "requests",
            "pyyaml",
            "pydantic",
            "anthropic",
            "retell-sdk",
            "twilio",
        )
        # sofia.config.yaml must travel with the code: ghl_service resolves it
        # relative to the package root, which is /root inside the image.
        .add_local_file("sofia.config.yaml", "/root/sofia.config.yaml")
        # prompts/ must travel too: the post-call analysis reads
        # prompts/<industry>.yaml on every call_ended. Without this the analysis
        # raises inside the webhook and fails silently (the webhook always
        # answers 200 by design, so Retell never retries and nothing surfaces).
        .add_local_dir("prompts", "/root/prompts")
        .add_local_python_source("app")
    )

    modal_app = modal.App(MODAL_APP_NAME)

    @modal_app.function(
        image=image,
        secrets=[modal.Secret.from_name(MODAL_SECRET_NAME)],
        # Scales to zero: we only pay while a call is actually being handled.
        # Before the number goes live, set `min_containers=1` — a cold start
        # mid-call is dead air on the phone, and dead air loses the patient.
    )
    @modal.asgi_app()
    def fastapi_app():
        return web_app
