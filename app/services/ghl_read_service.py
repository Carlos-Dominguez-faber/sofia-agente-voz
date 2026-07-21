"""GoHighLevel, read side — everything the panel asks and nothing it writes.

Why this is a separate module from `ghl_service.py`: that one is the agent's
hands. It runs while a patient is on the phone, and every function in it either
creates or changes something in the CRM. This one only ever reads, and it is
called by a browser instead of by Sofía.

Keeping them apart means a change made for the dashboard can never alter what
happens mid-call. The plumbing is shared — base url, headers, retries, the
config accessor all come from `ghl_service` — so there is exactly one place that
knows how to talk to GHL.

Read requests are retried (`retry=True`); that is safe here precisely because
nothing in this file mutates anything.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.services.ghl_service import (
    GHLAPIError,
    _location_id,
    _request,
    config_value,
    custom_field_ids,
    to_e164,
)

LOG = logging.getLogger(__name__)

# GHL caps page size; asking for more is silently truncated.
_PAGE_LIMIT = 100

# A clinic pipeline holds hundreds of cards, not millions. This bound keeps a
# runaway pagination loop from hanging the dashboard on a bad `meta` payload.
_MAX_PAGES = 20


# --------------------------------------------------------------------------
# Funnel — opportunities grouped by pipeline stage
# --------------------------------------------------------------------------


def _stage_labels() -> list[tuple[str, str, str]]:
    """The pipeline stages as (config key, stage id, human label), in board order.

    Order comes from `crm.pipeline_stages` in the config, which lists them the
    way the pipeline is laid out in GHL. Rendering them in dict order keeps the
    dashboard funnel in the same sequence the clinic sees in the CRM.
    """
    stages = config_value("crm.pipeline_stages", {}) or {}
    return [(key, str(stage_id), key.replace("_", " ").capitalize()) for key, stage_id in stages.items()]


def fetch_pipeline_opportunities() -> list[Mapping[str, Any]]:
    """Every opportunity in the configured pipeline, following pagination."""
    pipeline_id = config_value("crm.pipeline_id")
    if not pipeline_id:
        raise GHLAPIError("crm.pipeline_id is not set in sofia.config.yaml")

    collected: list[Mapping[str, Any]] = []
    start_after: str | None = None
    start_after_id: str | None = None

    for _ in range(_MAX_PAGES):
        params: dict[str, Any] = {
            "location_id": _location_id(),
            "pipeline_id": pipeline_id,
            "limit": _PAGE_LIMIT,
        }
        if start_after and start_after_id:
            params["startAfter"] = start_after
            params["startAfterId"] = start_after_id

        body = _request("GET", "/opportunities/search", params=params, retry=True)
        if not isinstance(body, Mapping):
            break

        page = [item for item in (body.get("opportunities") or []) if isinstance(item, Mapping)]
        collected.extend(page)

        meta = body.get("meta") if isinstance(body.get("meta"), Mapping) else {}
        start_after = meta.get("startAfter")
        start_after_id = meta.get("startAfterId")
        if len(page) < _PAGE_LIMIT or not (start_after and start_after_id):
            break

    return collected


def funnel_counts() -> dict[str, Any]:
    """Count of opportunities sitting in each stage of the patients pipeline."""
    opportunities = fetch_pipeline_opportunities()

    by_stage: dict[str, int] = {}
    for opportunity in opportunities:
        stage_id = opportunity.get("pipelineStageId") or opportunity.get("pipeline_stage_id")
        if stage_id:
            by_stage[str(stage_id)] = by_stage.get(str(stage_id), 0) + 1

    stages = [
        {"key": key, "label": label, "stage_id": stage_id, "count": by_stage.get(stage_id, 0)}
        for key, stage_id, label in _stage_labels()
    ]

    # Cards in a stage the config does not know about still exist in GHL. Report
    # them rather than quietly dropping them: a total that does not add up is
    # how a clinic owner learns their pipeline changed underneath the panel.
    known = {stage_id for _, stage_id, _ in _stage_labels()}
    unmapped = sum(count for stage_id, count in by_stage.items() if stage_id not in known)

    return {
        "stages": stages,
        "total": len(opportunities),
        "unmapped": unmapped,
        "pipeline_id": config_value("crm.pipeline_id"),
    }


# --------------------------------------------------------------------------
# Lead temperature — contacts by tag
# --------------------------------------------------------------------------


def count_contacts_with_tag(tag: str) -> int:
    """How many contacts carry this tag. Uses the search total, not a full fetch."""
    body = _request(
        "POST",
        "/contacts/search",
        json={
            "locationId": _location_id(),
            "pageLimit": 1,  # we only want `total`; the rows are irrelevant
            "filters": [{"field": "tags", "operator": "contains", "value": tag}],
        },
        retry=True,
    )
    if not isinstance(body, Mapping):
        return 0
    total = body.get("total")
    return int(total) if isinstance(total, (int, float)) else len(body.get("contacts") or [])


def temperature_counts() -> dict[str, Any]:
    """hot / warm / cold, straight from the tags the post-call analysis wrote."""
    tags = config_value("crm.tags.temperature", ["hot", "warm", "cold"]) or []
    counts = {str(tag): count_contacts_with_tag(str(tag)) for tag in tags}
    return {"counts": counts, "total": sum(counts.values())}


# --------------------------------------------------------------------------
# Contact lookup — the join key between Retell and GHL
# --------------------------------------------------------------------------


def find_contact_by_phone(phone: str) -> dict[str, Any] | None:
    """Look up a contact by phone WITHOUT creating one.

    `ghl_service.upsert_contact` would also resolve a phone to a contact, and it
    is idempotent — but it is a write. Calling it from a read endpoint means
    opening the dashboard could mint contacts for every number that ever dialled
    the clinic, including wrong numbers and spam. The panel reads; it does not
    populate the CRM.

    Returns None when the number was never registered, which is the normal case
    for a caller who hung up before qualification.
    """
    try:
        normalized = to_e164(phone)
    except ValueError:
        return None

    try:
        body = _request(
            "GET",
            "/contacts/search/duplicate",
            params={"locationId": _location_id(), "number": normalized},
            retry=True,
        )
    except GHLAPIError as exc:
        # A miss is reported as 4xx by this endpoint on some Locations.
        if exc.status_code and 400 <= exc.status_code < 500:
            return None
        raise

    if not isinstance(body, Mapping):
        return None
    contact = body.get("contact")
    return dict(contact) if isinstance(contact, Mapping) else None


# Note: fetching a contact by id lives in ghl_service.get_contact, not here.
# It was briefly duplicated in this module during the dashboard build; the
# canonical one is in ghl_service, so this layer defers to it rather than keep a
# second copy that could drift.


# Fields that must never leave this layer, whatever asks for them.
#
# `contact.notas_clinicas` is the doctor's clinical record. The agent is barred
# from writing to it (planeacion.md §5) for a reason that applies just as much to
# reading: it is a medical record, and a call-centre panel is not where a
# medical record belongs. Filtering it here rather than at each call site means a
# future endpoint cannot expose it by omission.
_NEVER_SURFACE = frozenset({"contact.notas_clinicas"})


def readable_custom_fields(contact: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a contact's `customFields` id/value pairs into {fieldKey: value}.

    GHL returns custom fields addressed by id. The config — and every human
    reading this code — refers to them by key, so the ids are mapped back here
    instead of leaking into the API responses the dashboard consumes.

    Fields in `_NEVER_SURFACE` are dropped before anything else sees them.
    """
    try:
        key_by_id = {field_id: key for key, field_id in custom_field_ids().items()}
    except GHLAPIError:
        LOG.warning("Could not resolve custom field ids; returning raw values")
        key_by_id = {}

    resolved: dict[str, Any] = {}
    for entry in contact.get("customFields") or []:
        if not isinstance(entry, Mapping):
            continue
        field_id = entry.get("id")
        value = entry.get("value")
        if value in (None, ""):
            continue
        key = key_by_id.get(str(field_id))
        if key and key not in _NEVER_SURFACE:
            resolved[key] = value
    return resolved


def post_call_summary(contact: Mapping[str, Any]) -> dict[str, Any]:
    """The four values the post-call analysis wrote, addressed by config key.

    Deliberately absent: `contact.notas_clinicas`. That field is the doctor's
    clinical record and the panel does not surface it — the same line the agent
    respects when writing, applied to reading.
    """
    fields = readable_custom_fields(contact)

    def value_of(config_key: str) -> Any:
        field_key = config_value(f"crm.custom_fields.{config_key}")
        return fields.get(field_key) if field_key else None

    return {
        "resumen": value_of("resumen_llamada"),
        "interes_score": value_of("interes_score"),
        "nivel_urgencia": value_of("nivel_urgencia"),
        "probabilidad_asistir": value_of("probabilidad_asistir"),
        "motivo": value_of("reason_for_visit"),
    }


def contact_display_name(contact: Mapping[str, Any] | None) -> str | None:
    """Best available human name for a contact, or None to let the UI show the phone."""
    if not contact:
        return None
    name = " ".join(
        part for part in (contact.get("firstName"), contact.get("lastName")) if part
    ).strip()
    return name or (contact.get("name") or None)
