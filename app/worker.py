"""Outbound follow-up worker — Sofía calls back, once an hour.

Wakes up on a Modal Cron, reads GoHighLevel, and dials patients one at a time:

    Cron (hourly) ─▶ build the queue from two pipeline stages
                  ─▶ filter: calling hours · cooldown · attempts · tags
                  ─▶ for each lead, SEQUENTIALLY:
                         Retell create_phone_call (Sofía outbound)
                         record the attempt back into GHL
                  ─▶ report what it did and what it skipped

Three decisions worth knowing before reading the code:

**The queue state lives in GHL, never here.** Two custom fields carry it:
`ultimo_intento_outbound` (when we last dialled) and `intentos_outbound` (how
many times). The worker is stateless — kill it, redeploy it, run it twice, and
it still knows who it already called, because the answer was never in memory.

**Nothing here marks a lead as rescued.** A call that fails to rebook leaves the
card exactly where it was, for the next run to pick up. The only thing that
moves a patient forward is `book_appointment` actually succeeding mid-call. A
worker that optimistically advanced the stage would quietly delete the follow-up
queue — the patients most in need of a callback would be the first to vanish.

**Calls are sequential on purpose.** Ten concurrent calls is ten times the spend
per minute and a clinic phone that cannot answer any transfer. The loop is slow
by design.

Manual run (no waiting for the hour):

    modal run app/worker.py::run_now
    modal run app/worker.py::run_now --dry-run     # queue only, dials nobody
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.services import ghl_service as ghl
from app.services.ghl_service import GHLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
LOG = logging.getLogger("sofia.worker")

MODAL_APP_NAME = "agente-voz-ghl-worker"
MODAL_SECRET_NAME = "agente-voz-credentials"

# Fallbacks, only used if sofia.config.yaml has not been customised. Every one
# of them errs on the side of calling FEWER people, not more.
DEFAULT_MAX_CALLS_PER_RUN = 10
DEFAULT_COOLDOWN_HOURS = 24
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_START_HOUR = 9
DEFAULT_END_HOUR = 19
DEFAULT_WEEKDAYS = (0, 1, 2, 3, 4, 5)  # Monday..Saturday


@dataclass
class Lead:
    """One patient to call, with everything Sofía needs to open the conversation."""

    contact_id: str
    name: str
    phone: str
    source: str  # "no_show" | "lead_fresco"
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    opportunity_id: str | None = None
    reason: str | None = None
    treatment: str | None = None
    missed_appointment: datetime | None = None
    attempts: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class SkippedLead:
    name: str
    phone: str
    why: str


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def _cfg(path: str, default: Any) -> Any:
    value = ghl.config_value(f"outbound.{path}", None)
    return default if value is None else value


def _timezone():
    return ghl._business_timezone()


def now_local() -> datetime:
    return datetime.now(tz=_timezone())


def within_calling_hours(moment: datetime | None = None) -> tuple[bool, str]:
    """Whether it is a civilised time to call. Returns (ok, human reason).

    The cron fires 24 times a day; this is the gate that turns 24 into ~10.
    Nobody wins a patient back by waking them up at 3am.
    """
    moment = moment or now_local()
    start = int(_cfg("call_window.start_hour", DEFAULT_START_HOUR))
    end = int(_cfg("call_window.end_hour", DEFAULT_END_HOUR))
    weekdays = [int(d) for d in _cfg("call_window.weekdays", list(DEFAULT_WEEKDAYS))]

    if moment.weekday() not in weekdays:
        return False, f"{moment:%A} is not an allowed weekday (allowed: {weekdays})"
    if not (start <= moment.hour < end):
        return False, f"{moment:%H:%M} is outside the {start}:00-{end}:00 window"
    return True, f"{moment:%A %H:%M} is inside the calling window"


def _stage_id(key: str) -> str | None:
    stages = ghl.config_value("crm.pipeline_stages", {}) or {}
    return stages.get(key)


# --------------------------------------------------------------------------
# Building the queue
# --------------------------------------------------------------------------


def _parse_attempts(contact: dict[str, Any], fields: dict[str, Any]) -> int:
    raw = fields.get(_cfg("tracking_fields.attempts", "contact.intentos_outbound"))
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _parse_last_attempt(fields: dict[str, Any]) -> datetime | None:
    raw = fields.get(_cfg("tracking_fields.last_attempt", "contact.ultimo_intento_outbound"))
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=_timezone()) if parsed.tzinfo is None else parsed


def _contact_details(contact_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The full contact plus its custom fields keyed by fieldKey.

    Returned together from a single GET: the queue needs the tracking fields for
    the cooldown AND the names for the call, and fetching the same contact twice
    per lead doubles the API cost of every run for nothing.
    """
    try:
        contact = ghl.get_contact(contact_id)
    except (GHLError, ValueError) as exc:
        LOG.warning("Could not read contact %s: %s", contact_id, exc)
        return {}, {}

    by_key: dict[str, Any] = {}
    ids_to_keys = {}
    try:
        ids_to_keys = {v: k for k, v in ghl.custom_field_ids().items()}
    except (GHLError, ValueError):
        pass

    for entry in contact.get("customFields") or []:
        value = entry.get("value")
        field_id = entry.get("id")
        key = ids_to_keys.get(field_id)
        if key:
            by_key[key] = value
    return contact, by_key


def _should_skip(lead_name: str, phone: str, tags: list[str], attempts: int,
                 last_attempt: datetime | None) -> str | None:
    """Return the reason to skip this lead, or None to call them."""
    excluded = {str(t).lower() for t in (_cfg("exclude_tags", []) or [])}
    hit = excluded.intersection({str(t).lower() for t in tags})
    if hit:
        return f"excluded by tag {sorted(hit)}"

    if not phone:
        return "no phone number on the contact"

    max_attempts = int(_cfg("max_attempts", DEFAULT_MAX_ATTEMPTS))
    if attempts >= max_attempts:
        return f"already tried {attempts} times (max {max_attempts})"

    cooldown = int(_cfg("cooldown_hours", DEFAULT_COOLDOWN_HOURS))
    if last_attempt and (now_local() - last_attempt) < timedelta(hours=cooldown):
        next_try = last_attempt + timedelta(hours=cooldown)
        return f"called {last_attempt:%d/%m %H:%M}, cooldown until {next_try:%d/%m %H:%M}"

    return None


def build_queue(limit: int | None = None) -> tuple[list[Lead], list[SkippedLead]]:
    """The two groups, filtered, newest-need-first, capped at the per-run limit.

    No-shows come first: someone who booked and did not arrive is a warmer lead
    than someone who never got a call.
    """
    cap = int(limit if limit is not None else _cfg("max_calls_per_run", DEFAULT_MAX_CALLS_PER_RUN))
    queue: list[Lead] = []
    skipped: list[SkippedLead] = []

    groups = [
        ("no_show", _stage_id("no_asistio")),
        ("lead_fresco", _stage_id("new_lead")),
    ]

    for source, stage_id in groups:
        if not stage_id:
            LOG.warning("No stage id configured for %s; skipping that group", source)
            continue

        try:
            opportunities = ghl.search_opportunities(stage_id=stage_id, limit=100)
        except (GHLError, ValueError) as exc:
            LOG.error("Could not read stage %s (%s): %s", source, stage_id, exc)
            continue

        LOG.info("Stage %s: %s opportunities", source, len(opportunities))

        for opportunity in opportunities:
            if len(queue) >= cap:
                break

            contact = opportunity.get("contact") or {}
            contact_id = contact.get("id")
            if not contact_id:
                continue

            name = (contact.get("name") or "").strip()
            phone = (contact.get("phone") or "").strip()
            tags = [str(t) for t in (contact.get("tags") or [])]

            full_contact, fields = _contact_details(contact_id)
            attempts = _parse_attempts(contact, fields)
            last_attempt = _parse_last_attempt(fields)

            reason_to_skip = _should_skip(name, phone, tags, attempts, last_attempt)
            if reason_to_skip:
                skipped.append(SkippedLead(name=name or contact_id, phone=phone, why=reason_to_skip))
                continue

            missed = None
            if source == "no_show":
                found = ghl.last_appointment_before(contact_id)
                missed = found["start"] if found else None

            queue.append(
                Lead(
                    contact_id=contact_id,
                    name=name,
                    phone=phone,
                    first_name=(full_contact.get("firstName") or name.split(" ")[0] or "").strip(),
                    last_name=(full_contact.get("lastName") or "").strip(),
                    email=(full_contact.get("email") or contact.get("email") or "").strip(),
                    source=source,
                    opportunity_id=opportunity.get("id"),
                    reason=fields.get("contact.reason_for_visit"),
                    treatment=_treatment_from_opportunity(opportunity.get("name"), name),
                    missed_appointment=missed,
                    attempts=attempts,
                    tags=tags,
                )
            )

    return queue, skipped


# --------------------------------------------------------------------------
# Placing the call
# --------------------------------------------------------------------------

_SPANISH_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _treatment_from_opportunity(opportunity_name: str | None, contact_name: str) -> str:
    """Strip the patient's name off the opportunity title.

    book_appointment names cards "Marc Cordero · Cita de valoración", so the raw
    title fed into {{lead_interest}} makes Sofía say "te marco por tu Marc
    Cordero · Cita de valoración". Only the part after the separator is the
    treatment.
    """
    title = (opportunity_name or "").strip()
    if not title:
        return ""
    for separator in ("·", " - ", "—"):
        if separator in title:
            title = title.split(separator, 1)[1].strip()
            break
    else:
        # No separator: drop a leading copy of the contact's name if present.
        if contact_name and title.lower().startswith(contact_name.lower()):
            title = title[len(contact_name):].strip(" -·—")
    return title


def _spoken_date(moment: datetime | None) -> str:
    """A date Sofía can say out loud. Empty string when unknown — never a guess."""
    if moment is None:
        return ""
    return f"{moment.day} de {_SPANISH_MONTHS[moment.month - 1]}"


def dynamic_variables(lead: Lead) -> dict[str, str]:
    """The {{variables}} the outbound prompt expects.

    The keys must match retell_service.OUTBOUND_DYNAMIC_VARIABLES exactly. Every
    value is a string, and an unknown value is "" rather than "None": the prompt
    tells Sofía to skip an empty field, and "None" is a word she would read aloud.
    """
    return {
        "lead_name": lead.first_name or (lead.name or "").split(" ")[0],
        "lead_interest": lead.treatment or "",
        "lead_last_contact": _spoken_date(lead.missed_appointment),
        "lead_notes": lead.reason or "",
        # Everything below exists so Sofía does NOT re-collect data the CRM
        # already has. Asking a patient for the phone number you just dialled
        # is the tell that there is no real system behind the call.
        "lead_last_name": lead.last_name,
        "lead_phone": lead.phone,
        "lead_email": lead.email,
        "contact_id": lead.contact_id,
    }


def record_attempt(lead: Lead) -> None:
    """Write the attempt back to GHL. This is the memory of the whole system.

    Called right after the dial, not after a successful conversation: a call
    that connected and went badly still counts. If this write fails the lead
    gets dialled again next hour, so it fails loudly.
    """
    fields = {
        _cfg("tracking_fields.last_attempt", "contact.ultimo_intento_outbound"): now_local().isoformat(),
        _cfg("tracking_fields.attempts", "contact.intentos_outbound"): lead.attempts + 1,
    }
    try:
        ghl.update_contact_fields(lead.contact_id, fields)
    except (GHLError, ValueError) as exc:
        LOG.error(
            "COULD NOT RECORD ATTEMPT for %s (%s): %s — this lead will be dialled again next run",
            lead.name,
            lead.contact_id,
            exc,
        )


def place_call(lead: Lead) -> dict[str, Any]:
    """Dial one patient with the outbound agent."""
    from app.services.retell_service import _client as retell_client
    from app.services.twilio_service import phone_number as from_number

    agent_id = (os.environ.get("RETELL_OUTBOUND_AGENT_ID") or "").strip()
    if not agent_id:
        raise RuntimeError(
            "RETELL_OUTBOUND_AGENT_ID is not set. Run retell_service.provision_outbound() first."
        )

    call = retell_client().call.create_phone_call(
        from_number=from_number(),
        to_number=lead.phone,
        override_agent_id=agent_id,
        retell_llm_dynamic_variables=dynamic_variables(lead),
        metadata={
            "contact_id": lead.contact_id,
            "opportunity_id": lead.opportunity_id or "",
            "source": lead.source,
            "attempt": lead.attempts + 1,
        },
    )
    call_id = getattr(call, "call_id", None)
    LOG.info("Called %s (%s) — call_id=%s attempt=%s", lead.name, lead.phone, call_id, lead.attempts + 1)
    return {"call_id": call_id, "lead": lead.name, "phone": lead.phone}


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def run_once(*, limit: int | None = None, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    """One pass of the worker. Safe to call by hand.

    `force` bypasses the calling-hours gate — for testing only, and it says so
    in the log so a 3am test call is never mistaken for normal behaviour.
    """
    started = now_local()

    if not _cfg("enabled", True):
        LOG.info("outbound.enabled is false in sofia.config.yaml; nothing to do")
        return {"ran": False, "why": "outbound disabled in config", "calls": []}

    allowed, why = within_calling_hours(started)
    if not allowed and not force:
        LOG.info("Outside the calling window (%s); nothing to do", why)
        return {"ran": False, "why": why, "calls": []}
    if not allowed and force:
        LOG.warning("FORCED run outside the calling window (%s)", why)

    queue, skipped = build_queue(limit)
    LOG.info("Queue: %s to call, %s skipped", len(queue), len(skipped))
    for entry in skipped:
        LOG.info("  skip %s (%s): %s", entry.name, entry.phone or "no phone", entry.why)

    if dry_run:
        LOG.info("DRY RUN — nobody was called")
        return {
            "ran": True,
            "dry_run": True,
            "why": why,
            "queued": [{"name": lead.name, "phone": lead.phone, "source": lead.source,
                        "attempts": lead.attempts, "variables": dynamic_variables(lead)}
                       for lead in queue],
            "skipped": [{"name": s.name, "why": s.why} for s in skipped],
            "calls": [],
        }

    # Sequential on purpose. See the module docstring.
    calls: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for lead in queue:
        try:
            result = place_call(lead)
        except Exception as exc:  # one bad number must not end the run
            LOG.error("Call to %s (%s) failed: %s", lead.name, lead.phone, exc)
            failures.append({"lead": lead.name, "phone": lead.phone, "error": str(exc)})
            continue

        # Recorded even on a call that goes nowhere: the attempt happened.
        record_attempt(lead)
        calls.append(result)

    LOG.info(
        "Run finished in %ss: %s called, %s failed, %s skipped",
        round((now_local() - started).total_seconds(), 1),
        len(calls),
        len(failures),
        len(skipped),
    )
    return {
        "ran": True,
        "dry_run": False,
        "why": why,
        "calls": calls,
        "failures": failures,
        "skipped": [{"name": s.name, "why": s.why} for s in skipped],
    }


# --------------------------------------------------------------------------
# Modal — hourly cron
#
# Deploy:  modal deploy app/worker.py::modal_app
# Manual:  modal run app/worker.py::run_now
#          modal run app/worker.py::run_now --dry-run
#
# The ::modal_app suffix is mandatory. Modal looks for a variable literally
# named `app`; ours is `modal_app`, and without the suffix it fails before
# building anything.
# --------------------------------------------------------------------------

try:
    import modal
except ImportError:  # pragma: no cover - local runs without the Modal SDK
    modal = None  # type: ignore[assignment]

if modal is not None:
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("requests", "pyyaml", "pydantic", "anthropic", "retell-sdk", "twilio")
        # Same data files as the API image. Miss one and the worker raises
        # inside the cron, where nobody is watching.
        .add_local_file("sofia.config.yaml", "/root/sofia.config.yaml")
        .add_local_dir("prompts", "/root/prompts")
        .add_local_python_source("app")
    )

    modal_app = modal.App(MODAL_APP_NAME)

    @modal_app.function(
        image=image,
        secrets=[modal.Secret.from_name(MODAL_SECRET_NAME)],
        schedule=modal.Cron("0 * * * *"),
        timeout=3600,
    )
    def hourly_followup() -> dict[str, Any]:
        """Entry point for the cron. The calling-hours gate lives in run_once."""
        return run_once()

    @modal_app.function(
        image=image,
        secrets=[modal.Secret.from_name(MODAL_SECRET_NAME)],
        timeout=3600,
    )
    def run_now(dry_run: bool = False, limit: int | None = None, force: bool = False) -> dict[str, Any]:
        """Manual trigger, so the worker can be tested without waiting an hour."""
        result = run_once(limit=limit, dry_run=dry_run, force=force)
        print()
        if not result.get("ran"):
            print(f"No corrió: {result.get('why')}")
            return result
        for entry in result.get("queued", []):
            print(f"  COLA  {entry['name']} ({entry['phone']}) · {entry['source']} · intentos={entry['attempts']}")
            print(f"        vars: {entry['variables']}")
        for call in result.get("calls", []):
            print(f"  LLAMÓ {call['lead']} ({call['phone']}) · call_id={call['call_id']}")
        for failure in result.get("failures", []):
            print(f"  FALLÓ {failure['lead']} ({failure['phone']}): {failure['error']}")
        for entry in result.get("skipped", []):
            print(f"  SALTÓ {entry['name']}: {entry['why']}")
        return result


if __name__ == "__main__":
    import json

    print(json.dumps(run_once(dry_run=True), indent=2, default=str, ensure_ascii=False))
