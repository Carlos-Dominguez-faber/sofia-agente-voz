"""GoHighLevel integration layer — the hands of the voice agent.

Everything Sofía *does* goes through this module: creating the patient, reading
the calendar, booking the valoración and opening the opportunity in the pipeline.

GHL is the single source of truth. This layer keeps no local state — it reads
and writes the Location and nothing else.

Design rules enforced here (see planeacion.md §3, §4):
  * Phones are always E.164. Upsert is idempotent by phone — it never duplicates.
  * free-slots takes epoch milliseconds, an IANA timezone, and a range of at most
    31 days. Non-date keys in the response (``traceId``) are filtered out.
  * Appointments are ISO 8601 *with offset*, never bare UTC. The offset is
    DERIVED from the business timezone in sofia.config.yaml — never hardcoded.
  * On failure this layer raises. It never returns a fake success. A patient who
    hangs up believing they have an appointment that does not exist costs more
    than an honest error ever will.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import yaml

LOG = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DEFAULT_API_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

# GHL rejects free-slot queries wider than this.
FREE_SLOTS_MAX_DAYS = 31

# Keys the free-slots response carries that are NOT dates. Iterating over them
# as if they were dates is what breaks the parser.
FREE_SLOTS_NON_DATE_KEYS = frozenset({"traceId", "traceld", "message"})

# The calendar returns 18 slots between 08:00 and 17:00 — 30-minute blocks.
# Confirm against the clinic's real slot length before going live.
DEFAULT_APPOINTMENT_MINUTES = 30

# Values in sofia.config.yaml that are open questions, not defaults.
# Treated as "not configured" so they fail loudly instead of reaching the API.
_PENDING_PREFIX = "PENDIENTE"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "sofia.config.yaml"
_ENV_PATH = _REPO_ROOT / ".env"

# E.164: leading '+', country code, up to 15 digits total.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

# Dial codes for the markets this project serves. Used only to normalize a
# local number when the caller did not say the country code.
_COUNTRY_DIAL_CODES = {
    "MX": "52",
    "US": "1",
    "CA": "1",
    "ES": "34",
    "CO": "57",
    "AR": "54",
    "CL": "56",
    "PE": "51",
}

_HTTP_TIMEOUT = (5, 20)  # (connect, read) seconds
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_READ_ATTEMPTS = 3


# --------------------------------------------------------------------------
# Errors — callers distinguish these to decide what Sofía says out loud
# --------------------------------------------------------------------------


class GHLError(RuntimeError):
    """Base error for this layer."""


class GHLConfigError(GHLError):
    """A credential or config value is missing, empty or still PENDIENTE."""


class GHLAPIError(GHLError):
    """GHL answered with an error. Carries the status and the response body."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class GHLBookingError(GHLError):
    """Booking did not complete. Sofía must offer human follow-up, never confirm."""


# --------------------------------------------------------------------------
# Config and credentials
# --------------------------------------------------------------------------


def _load_env_file() -> None:
    """Load .env into the environment for local runs. On Modal the Secret already did it."""
    if not _ENV_PATH.exists():
        return
    for raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip inline comments and surrounding quotes.
        value = value.split(" #", 1)[0].split("\t#", 1)[0].strip().strip("'\"")
        # Never override what the runtime already injected.
        if key and value and key not in os.environ:
            os.environ[key] = value


@lru_cache(maxsize=1)
def _config() -> Mapping[str, Any]:
    """Read sofia.config.yaml once. Business data only — no secrets live here."""
    if not _CONFIG_PATH.exists():
        raise GHLConfigError(f"sofia.config.yaml not found at {_CONFIG_PATH}")
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GHLConfigError(f"sofia.config.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise GHLConfigError("sofia.config.yaml did not parse into a mapping")
    return data


def _cfg(dotted_path: str, default: Any = None) -> Any:
    """Look up a dotted path in the config. PENDIENTE_* values count as unset."""
    node: Any = _config()
    for part in dotted_path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    if isinstance(node, str) and node.startswith(_PENDING_PREFIX):
        return default
    return node


def _require_cfg(dotted_path: str, hint: str) -> Any:
    value = _cfg(dotted_path)
    if value in (None, ""):
        raise GHLConfigError(f"sofia.config.yaml: `{dotted_path}` is missing or still PENDIENTE. {hint}")
    return value


def _require_env(name: str, hint: str) -> str:
    _load_env_file()
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise GHLConfigError(f"Environment variable {name} is not set. {hint}")
    return value


def _api_base() -> str:
    return str(_cfg("crm.api_base", DEFAULT_API_BASE)).rstrip("/")


def _location_id() -> str:
    """The GHL subaccount every call is scoped to."""
    return _require_env("HIGHLEVEL_LOCATION_ID", "It is the GHL subaccount (Location) id.")


def _business_timezone() -> ZoneInfo:
    """The clinic's IANA timezone. Every appointment offset is derived from this."""
    name = str(_require_cfg("business.timezone", "Example: America/Cancun."))
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise GHLConfigError(f"Unknown IANA timezone `{name}` in sofia.config.yaml") from exc


def _default_calendar_id() -> str:
    return str(_require_cfg("crm.calendar_id", "Run /setup or set it from the GHL calendar."))


def _default_pipeline_id() -> str:
    return str(_require_cfg("crm.pipeline_id", "The `Nuevos Pacientes` pipeline id."))


def _default_stage_id() -> str:
    return str(_require_cfg("crm.stage_id", "The `Cita Agendada` stage id."))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    token = _require_env("HIGHLEVEL_PIT", "It is the GHL Private Integration Token.")
    return {
        "Authorization": f"Bearer {token}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request(
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    json: Mapping[str, Any] | None = None,
    retry: bool = False,
) -> Any:
    """Call GHL and return the decoded body, or raise GHLAPIError.

    `retry` is opt-in and only ever used for reads. Writes are never retried:
    POST /calendars/events/appointments is not idempotent, so a retry after an
    ambiguous timeout is how you double-book a patient.
    """
    url = f"{_api_base()}/{path.lstrip('/')}"
    attempts = _MAX_READ_ATTEMPTS if retry else 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=_headers(),
                params=params,
                json=json,
                timeout=_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(0.5 * attempt)
                continue
            raise GHLAPIError(f"{method} {path} failed to reach GHL: {exc}") from exc

        if response.status_code in _RETRY_STATUSES and attempt < attempts:
            LOG.warning("GHL %s %s -> %s, retrying (%s/%s)", method, path, response.status_code, attempt, attempts)
            time.sleep(0.5 * attempt)
            continue

        if response.status_code >= 400:
            raise GHLAPIError(
                f"{method} {path} -> HTTP {response.status_code}: {response.text[:400]}",
                status_code=response.status_code,
                payload=_safe_json(response),
            )

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise GHLAPIError(f"{method} {path} returned a non-JSON body: {response.text[:200]}") from exc

    raise GHLAPIError(f"{method} {path} exhausted retries: {last_error}")


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------


def to_e164(phone: str, *, default_country: str | None = None) -> str:
    """Normalize a spoken phone number to E.164, or raise. No silent mangling."""
    if not phone or not str(phone).strip():
        raise ValueError("phone is empty")

    raw = str(phone).strip()
    # Callers dictate numbers digit by digit; strip whatever punctuation survived STT.
    cleaned = re.sub(r"[\s\-().]", "", raw)

    if cleaned.startswith("00"):  # international prefix used in parts of LatAm/EU
        cleaned = "+" + cleaned[2:]

    if not cleaned.startswith("+"):
        country = (default_country or _cfg("business.country") or "").upper()
        dial = _COUNTRY_DIAL_CODES.get(country)
        if not dial:
            raise ValueError(
                f"Phone `{raw}` has no country code and business.country=`{country or 'unset'}` "
                "is unknown. Ask the caller for the full number including country code."
            )
        cleaned = f"+{dial}{cleaned.lstrip('0')}"

    if not _E164_RE.match(cleaned):
        raise ValueError(f"Phone `{raw}` normalized to `{cleaned}`, which is not valid E.164")
    return cleaned


def _to_epoch_ms(value: int | float | datetime) -> int:
    """Accept a datetime or an already-epoch value; return epoch milliseconds."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=_business_timezone())
        return int(moment.timestamp() * 1000)
    number = int(value)
    # A plain epoch in seconds is ~10 digits; milliseconds ~13. Catch the mixup
    # instead of querying a window somewhere in 1970.
    if number < 10_000_000_000:
        raise ValueError(f"{number} looks like epoch seconds; free-slots needs milliseconds")
    return number


def _to_offset_iso(value: str | datetime) -> str:
    """Return ISO 8601 carrying a real UTC offset, derived from the business timezone.

    A naive value is interpreted as clinic-local time. The offset comes from
    zoneinfo for that specific date, so a timezone with DST stays correct —
    Cancún has none, but the rule is the rule and it must not be hardcoded.
    """
    tz = _business_timezone()

    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            # Bare UTC is exactly the bug this function exists to prevent.
            raise ValueError(
                f"`{text}` is UTC. Appointments must carry the clinic's offset — "
                "pass local time or an offset-aware value."
            )
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"`{text}` is not an ISO 8601 datetime") from exc
    elif isinstance(value, datetime):
        moment = value
    else:
        raise TypeError(f"Expected str or datetime, got {type(value).__name__}")

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)

    stamped = moment.isoformat()
    if not re.search(r"[+-]\d{2}:\d{2}$", stamped):
        raise ValueError(f"Could not derive a UTC offset for `{stamped}`")
    return stamped


def _custom_fields_payload(custom_fields: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Translate {fieldKey: value} into GHL's [{id, value}] shape.

    GHL addresses custom fields by **id**. The `{key, field_value}` shape that
    the API docs show is accepted with a 200/201 and then silently discarded —
    no error, no field written. Verified against both /contacts/upsert and
    PUT /contacts/{id}. Resolving ids from the Location is the only shape that
    actually persists.
    """
    if not custom_fields:
        return []

    id_by_key = custom_field_ids()
    payload: list[dict[str, Any]] = []
    unknown: list[str] = []

    for key, value in custom_fields.items():
        if value is None:
            continue
        field_id = id_by_key.get(key)
        if field_id:
            payload.append({"id": field_id, "value": value})
        else:
            unknown.append(key)

    if unknown:
        raise GHLError(f"Custom field key(s) not found in the Location: {sorted(unknown)}")
    return payload


# ==========================================================================
# 1. upsert_contact
# ==========================================================================


# Creates or updates the patient in the CRM — matched by phone, so calling twice never duplicates them.
def upsert_contact(
    phone: str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    tags: Sequence[str] | None = None,
    custom_fields: Mapping[str, Any] | None = None,
    *,
    source: str = "Sofía · agente de voz",
) -> dict[str, Any]:
    """POST /contacts/upsert — idempotent by phone.

    Returns {"id", "is_new", "phone", "raw"}. Raises GHLAPIError on failure and
    GHLError if GHL answers without a contact id.
    """
    normalized_phone = to_e164(phone)

    payload: dict[str, Any] = {
        "locationId": _location_id(),
        "phone": normalized_phone,
        "source": source,
    }
    if first_name:
        payload["firstName"] = str(first_name).strip()
    if last_name:
        payload["lastName"] = str(last_name).strip()
    if email:
        payload["email"] = str(email).strip()
    if tags:
        payload["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]

    fields = _custom_fields_payload(custom_fields)
    if fields:
        payload["customFields"] = fields

    body = _request("POST", "/contacts/upsert", json=payload)
    contact = body.get("contact") if isinstance(body, Mapping) else None
    contact_id = (contact or {}).get("id")

    if not contact_id:
        # A 2xx without an id is not a success — treat it as the failure it is.
        raise GHLError(f"contacts/upsert returned no contact id for {normalized_phone}: {body}")

    is_new = bool(body.get("new")) if isinstance(body, Mapping) else False
    LOG.info("Contact upserted id=%s new=%s", contact_id, is_new)
    return {"id": contact_id, "is_new": is_new, "phone": normalized_phone, "raw": contact}


# ==========================================================================
# 2. get_free_slots
# ==========================================================================


# Reads the real calendar for the openings Sofía is allowed to offer out loud.
def get_free_slots(
    start: int | float | datetime,
    end: int | float | datetime,
    *,
    calendar_id: str | None = None,
    timezone: str | None = None,
) -> dict[str, list[str]]:
    """GET /calendars/{calendarId}/free-slots.

    Dates travel as epoch milliseconds with an IANA timezone; the range may not
    exceed 31 days. Returns {"YYYY-MM-DD": [iso slot, ...]} with the response's
    non-date keys (``traceId``) filtered out.
    """
    calendar = calendar_id or _default_calendar_id()
    tz_name = timezone or str(_require_cfg("business.timezone", "Example: America/Cancun."))

    start_ms = _to_epoch_ms(start)
    end_ms = _to_epoch_ms(end)

    if end_ms <= start_ms:
        raise ValueError("end must be after start")

    span_days = (end_ms - start_ms) / 86_400_000
    if span_days > FREE_SLOTS_MAX_DAYS:
        raise ValueError(
            f"Range spans {span_days:.1f} days; GHL allows at most {FREE_SLOTS_MAX_DAYS} per query. "
            "Split it into consecutive windows."
        )

    body = _request(
        "GET",
        f"/calendars/{calendar}/free-slots",
        params={"startDate": start_ms, "endDate": end_ms, "timezone": tz_name},
        retry=True,  # a read, safe to retry
    )
    if not isinstance(body, Mapping):
        raise GHLAPIError(f"free-slots returned an unexpected body: {body!r}")

    slots_by_date: dict[str, list[str]] = {}
    for key, value in body.items():
        if key in FREE_SLOTS_NON_DATE_KEYS:
            continue
        # Belt and braces: only keep keys that actually look like a date.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(key)):
            continue
        if isinstance(value, Mapping):
            slots = value.get("slots") or []
        elif isinstance(value, list):
            slots = value
        else:
            continue
        if slots:
            slots_by_date[str(key)] = [str(slot) for slot in slots]

    LOG.info("free-slots: %s day(s) with availability on calendar %s", len(slots_by_date), calendar)
    return slots_by_date


# ==========================================================================
# 3. book_appointment
# ==========================================================================


# Books the valoración on the clinic's calendar — the moment the call becomes worth something.
def book_appointment(
    contact_id: str,
    start: str | datetime,
    end: str | datetime | None = None,
    *,
    duration_minutes: int = DEFAULT_APPOINTMENT_MINUTES,
    calendar_id: str | None = None,
    title: str | None = None,
    appointment_status: str = "confirmed",
    notify: bool = True,
) -> dict[str, Any]:
    """POST /calendars/events/appointments.

    Times go out as ISO 8601 with the clinic's offset, derived from
    business.timezone. Never bare UTC — that books every patient an hour off.

    Raises GHLBookingError if the booking did not land. It never returns a fake
    success: Sofía offers human follow-up instead of confirming a ghost
    appointment.
    """
    if not contact_id:
        raise ValueError("contact_id is required to book")

    calendar = calendar_id or _default_calendar_id()
    start_iso = _to_offset_iso(start)

    if end is None:
        start_dt = datetime.fromisoformat(start_iso)
        end_iso = (start_dt + timedelta(minutes=duration_minutes)).isoformat()
    else:
        end_iso = _to_offset_iso(end)

    if end_iso <= start_iso:
        raise ValueError(f"Appointment end ({end_iso}) must be after start ({start_iso})")

    payload = {
        "calendarId": calendar,
        "locationId": _location_id(),
        "contactId": contact_id,
        "startTime": start_iso,
        "endTime": end_iso,
        "title": title or str(_cfg("knowledge_base.valoracion.name", "Cita de valoración")),
        "appointmentStatus": appointment_status,
        "toNotify": notify,
    }

    try:
        body = _request("POST", "/calendars/events/appointments", json=payload)
    except GHLError as exc:
        # Re-raised as a booking failure so the caller knows exactly which
        # promise it must NOT make to the patient.
        raise GHLBookingError(
            f"Could not book {start_iso} for contact {contact_id}: {exc}. "
            "Do not confirm the appointment — offer human follow-up."
        ) from exc

    appointment = body if isinstance(body, Mapping) else {}
    # GHL has shipped both shapes; accept either, trust neither blindly.
    details = appointment.get("appointment") if isinstance(appointment.get("appointment"), Mapping) else appointment
    appointment_id = details.get("id") or appointment.get("id")

    if not appointment_id:
        raise GHLBookingError(
            f"GHL accepted the request but returned no appointment id for contact {contact_id} "
            f"at {start_iso}: {body}. Treat this as NOT booked."
        )

    LOG.info("Appointment booked id=%s contact=%s start=%s", appointment_id, contact_id, start_iso)
    return {
        "id": appointment_id,
        "contact_id": contact_id,
        "calendar_id": calendar,
        "start_time": start_iso,
        "end_time": end_iso,
        "status": details.get("appointmentStatus", appointment_status),
        "raw": details,
    }


# ==========================================================================
# 4. create_opportunity
# ==========================================================================


# Opens the patient's card in the sales pipeline so the clinic can see the lead moving.
def create_opportunity(
    contact_id: str,
    name: str,
    *,
    pipeline_id: str | None = None,
    stage_id: str | None = None,
    status: str = "open",
    monetary_value: float | int | None = None,
) -> dict[str, Any]:
    """POST /opportunities/ — lands in the `Cita Agendada` stage by default."""
    if not contact_id:
        raise ValueError("contact_id is required to create an opportunity")
    if not name or not str(name).strip():
        raise ValueError("name is required to create an opportunity")

    payload: dict[str, Any] = {
        "pipelineId": pipeline_id or _default_pipeline_id(),
        "locationId": _location_id(),
        "pipelineStageId": stage_id or _default_stage_id(),
        "contactId": contact_id,
        "name": str(name).strip(),
        "status": status,
    }
    if monetary_value is not None:
        payload["monetaryValue"] = monetary_value

    body = _request("POST", "/opportunities/", json=payload)
    opportunity = body.get("opportunity") if isinstance(body, Mapping) else None
    opportunity_id = (opportunity or {}).get("id") or (body.get("id") if isinstance(body, Mapping) else None)

    if not opportunity_id:
        raise GHLError(f"opportunities/ returned no opportunity id for contact {contact_id}: {body}")

    LOG.info("Opportunity created id=%s contact=%s stage=%s", opportunity_id, contact_id, payload["pipelineStageId"])
    return {
        "id": opportunity_id,
        "contact_id": contact_id,
        "pipeline_id": payload["pipelineId"],
        "stage_id": payload["pipelineStageId"],
        "status": status,
        "raw": opportunity,
    }


# ==========================================================================
# Supporting operations (used by /update-lead-status)
# ==========================================================================


# Marks how warm the patient is, so the clinic sees at a glance who to call back first.
def add_tags(contact_id: str, tags: Sequence[str]) -> dict[str, Any]:
    """POST /contacts/{contactId}/tags — additive; GHL ignores tags already present."""
    if not contact_id:
        raise ValueError("contact_id is required to tag a contact")
    clean = [str(tag).strip() for tag in tags or [] if str(tag).strip()]
    if not clean:
        raise ValueError("at least one tag is required")

    body = _request("POST", f"/contacts/{contact_id}/tags", json={"tags": clean})
    applied = body.get("tags", clean) if isinstance(body, Mapping) else clean
    LOG.info("Tags applied to contact %s: %s", contact_id, clean)
    return {"contact_id": contact_id, "tags": list(applied)}


def remove_tags(contact_id: str, tags: Sequence[str]) -> dict[str, Any]:
    """DELETE /contacts/{contactId}/tags — the counterpart of add_tags."""
    if not contact_id:
        raise ValueError("contact_id is required to untag a contact")
    clean = [str(tag).strip() for tag in tags or [] if str(tag).strip()]
    if not clean:
        raise ValueError("at least one tag is required")

    _request("DELETE", f"/contacts/{contact_id}/tags", json={"tags": clean})
    LOG.info("Tags removed from contact %s: %s", contact_id, clean)
    return {"contact_id": contact_id, "removed": clean}


def get_contact(contact_id: str) -> dict[str, Any]:
    """GET /contacts/{contactId} — reads the contact back, tags included."""
    if not contact_id:
        raise ValueError("contact_id is required")
    body = _request("GET", f"/contacts/{contact_id}")
    contact = body.get("contact") if isinstance(body, Mapping) else None
    return dict(contact) if isinstance(contact, Mapping) else {}


def set_temperature_tag(contact_id: str, temperature: str) -> dict[str, Any]:
    """Makes `temperature` the ONLY temperature tag on the contact.

    GHL tags are additive: POSTing "warm" over an existing "hot" leaves both on
    the record. Three different writers set this tag — book_appointment,
    /update-lead-status and the post-call analysis — so an additive write ends
    with a contact tagged hot AND warm, and nobody in the clinic can tell which
    one is current.

    So this is the single entry point for temperature: it strips the sibling
    values first, then applies the new one. Any other temperature tag on the
    record is treated as stale by definition.
    """
    allowed = [str(t) for t in (_cfg("crm.tags.temperature", ["hot", "warm", "cold"]) or [])]
    value = str(temperature).strip()
    if value not in allowed:
        raise ValueError(f"temperature must be one of {allowed}, got `{value}`")

    # Only delete what is actually there — a blind DELETE per sibling would cost
    # two extra API calls on every single call that books.
    current = {str(t).strip() for t in (get_contact(contact_id).get("tags") or [])}
    stale = [t for t in allowed if t != value and t in current]
    if stale:
        remove_tags(contact_id, stale)

    applied = add_tags(contact_id, [value])["tags"]
    LOG.info("Temperature set to %s on contact %s (removed %s)", value, contact_id, stale)
    return {"contact_id": contact_id, "temperature": value, "removed": stale, "tags": applied}


# Moves the patient's card along the pipeline as the relationship advances.
def update_opportunity_stage(
    opportunity_id: str,
    stage_id: str,
    *,
    status: str | None = None,
    pipeline_id: str | None = None,
) -> dict[str, Any]:
    """PUT /opportunities/{opportunityId} — changes the stage of an existing card."""
    if not opportunity_id:
        raise ValueError("opportunity_id is required to move an opportunity")
    if not stage_id:
        raise ValueError("stage_id is required to move an opportunity")

    payload: dict[str, Any] = {
        "pipelineId": pipeline_id or _default_pipeline_id(),
        "pipelineStageId": stage_id,
    }
    if status:
        payload["status"] = status

    body = _request("PUT", f"/opportunities/{opportunity_id}", json=payload)
    opportunity = body.get("opportunity") if isinstance(body, Mapping) else None

    LOG.info("Opportunity %s moved to stage %s", opportunity_id, stage_id)
    return {
        "id": opportunity_id,
        "stage_id": stage_id,
        "status": status,
        "raw": opportunity,
    }


# Leaves a written record of the call on the patient's file, where the clinic actually looks.
def add_note(contact_id: str, body: str) -> dict[str, Any]:
    """POST /contacts/{contactId}/notes.

    This is where the post-call summary lands. It is NOT the clinical record:
    `contact.notas_clinicas` belongs to the doctor and this layer never writes
    to it — see planeacion.md §5.
    """
    if not contact_id:
        raise ValueError("contact_id is required to add a note")
    if not body or not str(body).strip():
        raise ValueError("note body is empty")

    response = _request("POST", f"/contacts/{contact_id}/notes", json={"body": str(body).strip()})
    note = response.get("note") if isinstance(response, Mapping) else None
    note_id = (note or {}).get("id")
    LOG.info("Note added to contact %s (id=%s)", contact_id, note_id)
    return {"id": note_id, "contact_id": contact_id}


@lru_cache(maxsize=1)
def custom_field_ids() -> dict[str, str]:
    """Map each contact custom field's `fieldKey` to its GHL id.

    PUT /contacts/{id} addresses custom fields by **id**, not by key — a payload
    keyed by `fieldKey` returns HTTP 200 and writes nothing at all. Resolving the
    ids from the Location keeps the config free of ids that would silently go
    stale if a field were ever recreated.
    """
    body = _request("GET", f"/locations/{_location_id()}/customFields", retry=True)
    fields = body.get("customFields", []) if isinstance(body, Mapping) else []
    return {f["fieldKey"]: f["id"] for f in fields if f.get("fieldKey") and f.get("id")}


# Writes the post-call scores onto the patient's file so the clinic can triage by them.
def update_contact_fields(contact_id: str, custom_fields: Mapping[str, Any]) -> dict[str, Any]:
    """PUT /contacts/{contactId} — sets custom fields addressed by id."""
    if not contact_id:
        raise ValueError("contact_id is required to update fields")

    # Raises loudly if a key does not exist in the Location — otherwise GHL
    # would drop it with a cheerful 200 and nobody would notice.
    payload = _custom_fields_payload(custom_fields)
    if not payload:
        raise ValueError("no custom fields to write")

    response = _request("PUT", f"/contacts/{contact_id}", json={"customFields": payload})
    LOG.info("Updated %s custom field(s) on contact %s", len(payload), contact_id)
    return {"contact_id": contact_id, "written": list(custom_fields), "raw": response}


# ==========================================================================
# Reads used by the outbound worker
# ==========================================================================


def search_opportunities(
    *,
    stage_id: str | None = None,
    pipeline_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """GET /opportunities/search — the cards sitting in one pipeline stage.

    The embedded `contact` carries name, phone and tags, which is the whole
    reason the worker can build its queue without an extra fetch per lead.
    """
    params: dict[str, Any] = {
        "location_id": _location_id(),
        "pipeline_id": pipeline_id or _default_pipeline_id(),
        "limit": max(1, min(int(limit), 100)),
    }
    if stage_id:
        params["pipeline_stage_id"] = stage_id

    body = _request("GET", "/opportunities/search", params=params, retry=True)
    opportunities = body.get("opportunities") if isinstance(body, Mapping) else None
    return list(opportunities or [])


def get_contact_appointments(contact_id: str) -> list[dict[str, Any]]:
    """GET /contacts/{contactId}/appointments — used to name the missed date out loud."""
    if not contact_id:
        raise ValueError("contact_id is required")
    body = _request("GET", f"/contacts/{contact_id}/appointments", retry=True)
    events = body.get("events") if isinstance(body, Mapping) else None
    return list(events or [])


def last_appointment_before(contact_id: str, moment: datetime | None = None) -> dict[str, Any] | None:
    """The most recent appointment already in the past. None if there is none.

    Errors are swallowed on purpose: a missing appointment date costs the
    outbound call a nicety, not the call itself.
    """
    reference = moment or datetime.now(tz=_business_timezone())
    try:
        events = get_contact_appointments(contact_id)
    except (GHLError, ValueError) as exc:
        LOG.warning("Could not read appointments for %s: %s", contact_id, exc)
        return None

    past: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        raw = event.get("startTime") or event.get("start_time")
        if not raw:
            continue
        try:
            start = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=_business_timezone())
        if start < reference:
            past.append((start, event))

    if not past:
        return None
    start, event = max(past, key=lambda pair: pair[0])
    return {"start": start, "raw": event}


# Reads a business value from sofia.config.yaml — handlers need the spoken copy and the stage map.
def config_value(dotted_path: str, default: Any = None) -> Any:
    """Public accessor for the config. PENDIENTE_* values come back as `default`."""
    return _cfg(dotted_path, default)


# ==========================================================================
# test_connection
# ==========================================================================


# Answers one question before a single call is taken: can we actually reach this clinic's GHL?
def test_connection() -> dict[str, Any]:
    """GET /locations/{locationId} — validates the PIT and the Location.

    Returns a summary for /test and /status. Raises GHLConfigError if something
    is missing locally, GHLAPIError if GHL rejects the credentials.
    """
    location_id = _location_id()
    body = _request("GET", f"/locations/{location_id}", retry=True)
    location = body.get("location", {}) if isinstance(body, Mapping) else {}

    configured_tz = _cfg("business.timezone")
    remote_tz = location.get("timezone")

    summary = {
        "ok": True,
        "location_id": location_id,
        "location_name": location.get("name"),
        "timezone": remote_tz,
        "calendar_id": _cfg("crm.calendar_id"),
        "pipeline_id": _cfg("crm.pipeline_id"),
        "stage_id": _cfg("crm.stage_id"),
    }

    # A drift here books every appointment at the wrong hour, silently.
    if configured_tz and remote_tz and configured_tz != remote_tz:
        summary["warning"] = (
            f"Timezone mismatch: sofia.config.yaml says `{configured_tz}`, "
            f"GHL says `{remote_tz}`. Appointment offsets are derived from the config value."
        )
        LOG.warning(summary["warning"])

    LOG.info("GHL connection OK: %s (%s)", summary["location_name"], location_id)
    return summary
