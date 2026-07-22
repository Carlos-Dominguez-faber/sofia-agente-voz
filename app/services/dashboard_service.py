"""What the panel shows — the joins between Retell and GoHighLevel.

Neither source answers a clinic owner's questions on its own. Retell knows what
happened on the phone: how many calls, how long, what was said, which tools
fired. GoHighLevel knows who the patient is and what the post-call analysis
concluded about them. "Ana López called yesterday, booked, and sounded urgent"
is one sentence built from both.

The join key is the phone number in E.164, recovered from the arguments Sofía
passed to her own tools during the call (`call_parsing`). No local table maps
calls to contacts, because keeping one would mean keeping state — see
BLUEPRINT.md.

The honesty rule, applied to reading: when a source cannot answer, that fact
travels to the UI as a failed source. A metric is never defaulted to zero. The
owner of a clinic reads a zero as "Sofía did not work today", and a lie that
comfortable is worse than an error message.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services import anthropic_service, ghl_read_service, retell_service, twilio_service
from app.services import ghl_service as ghl
from app.services.call_parsing import (
    BOOKING_TOOL,
    booked_in_call,
    phone_from_tool_calls,
    tool_calls_from,
    transcript_from,
)

LOG = logging.getLogger(__name__)

DEFAULT_RANGE_DAYS = 30
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# GHL is rate limited and the panel is one user. A handful of parallel lookups
# turns a 20-call page from ~5s of sequential requests into well under one.
_LOOKUP_WORKERS = 5


class SourceUnavailable(RuntimeError):
    """A data source could not answer. Carries which one, for the UI."""

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


def _tz() -> ZoneInfo:
    return ZoneInfo(str(ghl.config_value("business.timezone", "America/Cancun")))


def resolve_range(start: str | None, end: str | None, days: int | None) -> dict[str, Any]:
    """Turn the query parameters into an explicit window in the clinic's timezone.

    Everything is computed in the clinic's own timezone, never the browser's. A
    dashboard opened from another country must still show "today" as the clinic
    lived it.
    """
    tz = _tz()
    now = datetime.now(tz)

    end_dt = datetime.fromisoformat(end).replace(tzinfo=tz) if end else now
    if start:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=tz)
    else:
        start_dt = end_dt - timedelta(days=days or DEFAULT_RANGE_DAYS)

    if start_dt > end_dt:
        raise ValueError("The start of the range is after its end")

    return {
        "start": start_dt,
        "end": end_dt,
        "start_ms": int(start_dt.timestamp() * 1000),
        "end_ms": int(end_dt.timestamp() * 1000),
        "timezone": str(tz),
    }


def _iso_local(epoch_ms: Any) -> str | None:
    if not isinstance(epoch_ms, (int, float)) or epoch_ms <= 0:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=_tz()).isoformat()


# --------------------------------------------------------------------------
# Metrics — Retell only
# --------------------------------------------------------------------------


def metrics(*, start: str | None = None, end: str | None = None, days: int | None = None) -> dict[str, Any]:
    """The four cards at the top of the panel.

    All four come from Retell alone, including the appointment count. It is
    derived from successful `book_appointment` tool calls rather than from the
    GHL calendar on purpose: the calendar also holds appointments the human
    receptionist booked by hand, and counting those would credit Sofía with work
    she did not do — and inflate the success rate that justifies her cost.
    """
    window = resolve_range(start, end, days)

    try:
        total_calls = retell_service.count_calls(
            start_ms=window["start_ms"], end_ms=window["end_ms"]
        )
        # Counted by Retell's own tool-call filter rather than by reading each
        # transcript. The list payload carries no tool calls, so counting them
        # here would have reported zero appointments forever — and a clinic
        # reads "0 citas" as "Sofía no sirve", not as "the query was wrong".
        booked = retell_service.count_calls(
            start_ms=window["start_ms"],
            end_ms=window["end_ms"],
            tool_name=BOOKING_TOOL,
            tool_success=True,
        )
        durations_sample = retell_service.list_calls(
            limit=MAX_PAGE_SIZE, start_ms=window["start_ms"], end_ms=window["end_ms"]
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a dead source
        raise SourceUnavailable("retell", str(exc)) from exc

    durations = [
        c.get("duration_ms") for c in durations_sample if isinstance(c.get("duration_ms"), (int, float))
    ]
    avg_duration_s = round(sum(durations) / len(durations) / 1000) if durations else None

    return {
        "range": {
            "start": window["start"].isoformat(),
            "end": window["end"].isoformat(),
            "timezone": window["timezone"],
        },
        "total_calls": total_calls,
        "appointments_booked": booked,
        "appointments_label": "Citas agendadas por Sofía",
        # None, not 0, when there were no calls: a success rate of 0% reads as
        # failure, and "no calls yet" is not failure.
        "success_rate": round(booked / total_calls * 100, 1) if total_calls else None,
        "avg_duration_seconds": avg_duration_s,
        # The average is drawn from the most recent page, not the whole window.
        # Said out loud so nobody presents a sample as a census.
        "avg_duration_sample_size": len(durations),
        "avg_duration_is_sample": total_calls > len(durations),
        "sources": {"retell": "ok"},
    }


# --------------------------------------------------------------------------
# Calls — Retell joined onto GHL
# --------------------------------------------------------------------------


def _patient_phone(call: dict[str, Any]) -> str | None:
    """The number to look up in the CRM.

    Prefers what Sofía captured over the caller ID, and has to: a `web_call` —
    what the Retell test console produces, and what every call is until the
    Twilio number is connected — carries no `from_number` at all. The number
    Sofía confirmed out loud is the one the contact was created with, so it is
    the reliable key for both call types.

    Still None for a caller who hung up before giving a number. That is a real
    outcome, not a failure: the row shows an unidentified call.
    """
    return phone_from_tool_calls(call) or call.get("from_number") or call.get("to_number")


def _origin(call: dict[str, Any]) -> str:
    """How this call reached Sofía, in a form the panel can label.

    `web_call` is a browser session from the Retell console; `phone_call` comes
    down the Twilio trunk. Both are real conversations with real bookings, and
    the panel must not treat the absence of a phone number as a broken row.
    """
    call_type = call.get("call_type")
    if call_type == "web_call":
        return "web"
    if call.get("direction") in {"inbound", "outbound"}:
        return str(call["direction"])
    return "phone" if call_type == "phone_call" else "desconocido"


def _hydrate_call(call: dict[str, Any]) -> dict[str, Any]:
    """Fetch a listed call in full, so its tool calls are available.

    On failure the light row is returned unchanged rather than dropped: the
    table still shows that the call happened, with the fields the list did
    carry. Losing the row entirely would under-report the call count.
    """
    call_id = call.get("call_id")
    if not call_id:
        return call
    try:
        return retell_service.get_call(str(call_id))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not hydrate call %s: %s", call_id, exc)
        return call


def _resolve_contact(phone: str | None) -> dict[str, Any] | None:
    if not phone:
        return None
    try:
        return ghl_read_service.find_contact_by_phone(phone)
    except Exception as exc:  # noqa: BLE001 - one failed lookup must not kill the page
        LOG.warning("Contact lookup failed for %s: %s", phone, exc)
        return None


def recent_calls(
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    start: str | None = None,
    end: str | None = None,
    days: int | None = None,
    pagination_key: str | None = None,
) -> dict[str, Any]:
    """The recent-calls table: who called, when, how long, did she book, summary."""
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    window = resolve_range(start, end, days)

    try:
        page = retell_service.list_calls_page(
            limit=limit,
            start_ms=window["start_ms"],
            end_ms=window["end_ms"],
            pagination_key=pagination_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable("retell", str(exc)) from exc

    # The list payload has no tool calls, and the tool calls are where the
    # patient's phone lives — which is the only key that reaches the CRM. So
    # each row on this page is fetched in full. It costs one request per row,
    # which is why the page is small: this table is read, not scrolled.
    try:
        with ThreadPoolExecutor(max_workers=_LOOKUP_WORKERS) as pool:
            calls = list(pool.map(_hydrate_call, page["calls"]))
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable("retell", str(exc)) from exc

    phones = [_patient_phone(call) for call in calls]

    # GHL may be down while Retell is fine. When that happens the table still
    # renders with phone numbers instead of names, and `sources.ghl` tells the
    # UI to say so — better a partial table than an empty one.
    ghl_status = "ok"
    try:
        with ThreadPoolExecutor(max_workers=_LOOKUP_WORKERS) as pool:
            contacts = list(pool.map(_resolve_contact, phones))
    except Exception as exc:  # noqa: BLE001
        LOG.error("Contact resolution failed wholesale: %s", exc)
        contacts = [None] * len(calls)
        ghl_status = f"unavailable: {exc}"

    rows = []
    for call, phone, contact in zip(calls, phones, contacts, strict=False):
        summary = ghl_read_service.post_call_summary(contact) if contact else {}
        rows.append(
            {
                "call_id": call.get("call_id"),
                "started_at": _iso_local(call.get("start_timestamp")),
                "duration_seconds": round((call.get("duration_ms") or 0) / 1000) or None,
                "origin": _origin(call),
                "call_type": call.get("call_type"),
                "phone": phone,
                "contact_name": ghl_read_service.contact_display_name(contact),
                "contact_id": (contact or {}).get("id"),
                "booked": booked_in_call(call),
                "resumen": summary.get("resumen"),
                "nivel_urgencia": summary.get("nivel_urgencia"),
                "interes_score": summary.get("interes_score"),
                "call_status": call.get("call_status"),
            }
        )

    return {
        "calls": rows,
        "count": len(rows),
        "has_more": page["has_more"],
        "pagination_key": page["pagination_key"],
        "range": {"start": window["start"].isoformat(), "end": window["end"].isoformat()},
        "sources": {"retell": "ok", "ghl": ghl_status},
    }


def call_detail(call_id: str) -> dict[str, Any]:
    """One call in full: transcript, the tools that fired, and the CRM verdict."""
    try:
        call = retell_service.get_call(call_id)
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable("retell", str(exc)) from exc

    phone = _patient_phone(call)
    ghl_status = "ok"
    contact = None
    try:
        contact = ghl_read_service.find_contact_by_phone(phone) if phone else None
    except Exception as exc:  # noqa: BLE001
        LOG.error("Contact lookup failed for call %s: %s", call_id, exc)
        ghl_status = f"unavailable: {exc}"

    summary = ghl_read_service.post_call_summary(contact) if contact else {}

    return {
        "call_id": call.get("call_id"),
        "started_at": _iso_local(call.get("start_timestamp")),
        "ended_at": _iso_local(call.get("end_timestamp")),
        "duration_seconds": round((call.get("duration_ms") or 0) / 1000) or None,
        "origin": _origin(call),
        "call_type": call.get("call_type"),
        "call_status": call.get("call_status"),
        "disconnection_reason": call.get("disconnection_reason"),
        "phone": phone,
        "contact_name": ghl_read_service.contact_display_name(contact),
        "contact_id": (contact or {}).get("id"),
        "booked": booked_in_call(call),
        "transcript": transcript_from(call),
        "tool_calls": tool_calls_from(call),
        "analysis": summary,
        # NOT the raw recording_url. Retell serves recordings from an
        # unauthenticated CloudFront URL — anyone holding it can replay a
        # patient's call with no session. The bytes are streamed instead through
        # /dashboard/calls/{id}/recording, behind the token; the browser only
        # learns whether a recording exists, never where it lives.
        "has_recording": bool(call.get("recording_url")),
        "sources": {"retell": "ok", "ghl": ghl_status},
    }


def call_recording_url(call_id: str) -> str | None:
    """The raw Retell recording URL for a call — server-side use only.

    Exists so the token-gated streaming endpoint can fetch the bytes. It must
    never be returned to the browser: that is the leak this whole indirection
    exists to close.
    """
    try:
        call = retell_service.get_call(call_id)
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable("retell", str(exc)) from exc
    url = call.get("recording_url")
    return str(url) if url else None


# --------------------------------------------------------------------------
# Funnel and temperature — GHL only
# --------------------------------------------------------------------------


def funnel() -> dict[str, Any]:
    try:
        data = ghl_read_service.funnel_counts()
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable("ghl", str(exc)) from exc
    return {**data, "sources": {"ghl": "ok"}}


def lead_temperature() -> dict[str, Any]:
    try:
        data = ghl_read_service.temperature_counts()
    except Exception as exc:  # noqa: BLE001
        raise SourceUnavailable("ghl", str(exc)) from exc
    return {**data, "sources": {"ghl": "ok"}}


# --------------------------------------------------------------------------
# Service status
# --------------------------------------------------------------------------


def _probe(name: str, check) -> dict[str, Any]:
    """Run one health check and report it without ever raising."""
    try:
        result = check()
    except Exception as exc:  # noqa: BLE001 - a dead service is a result, not a crash
        LOG.warning("Service check failed for %s: %s", name, exc)
        return {"service": name, "ok": False, "detail": str(exc)[:300]}
    detail = result if isinstance(result, dict) else {}
    return {"service": name, "ok": True, **{k: v for k, v in detail.items() if k != "ok"}}


def services_status() -> dict[str, Any]:
    """Live state of every service the system depends on.

    Each is probed independently: one dead service reports itself dead and the
    other four still report the truth about themselves.
    """
    checks = [
        ("gohighlevel", ghl.test_connection),
        ("retell", retell_service.test_connection),
        ("twilio", twilio_service.test_connection),
        ("anthropic", anthropic_service.test_connection),
    ]

    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        results = list(pool.map(lambda item: _probe(item[0], item[1]), checks))

    results.append(
        {
            "service": "backend",
            "ok": True,
            "business": ghl.config_value("business.name"),
            "timezone": ghl.config_value("business.timezone"),
        }
    )

    return {
        "services": results,
        "all_ok": all(item["ok"] for item in results),
        "degraded": [item["service"] for item in results if not item["ok"]],
    }
