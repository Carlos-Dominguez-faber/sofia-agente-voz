"""Reading a Retell call payload — the parsing both halves of the system need.

These helpers started inside `main.py`, serving the post-call analysis alone.
The dashboard needs exactly the same reading of the same payload: which phone
the call belongs to, what was said, which tools fired. Duplicating them would
mean two parsers drifting apart over the same Retell payload, so they live here
and both sides import them.

Nothing here calls an API. Give it the `call` object Retell hands over — in a
webhook or from `call.list` — and it answers questions about it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

LOG = logging.getLogger(__name__)

# The tools Sofía can call mid-conversation that carry the patient's phone.
# Both register the caller in GHL, so either one identifies the contact.
_PHONE_BEARING_TOOLS = frozenset({"create_lead", "book_appointment"})

# The one tool whose success means an appointment really exists in the calendar.
BOOKING_TOOL = "book_appointment"


def transcript_from(call: Mapping[str, Any]) -> str:
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


def _decode_arguments(raw: Any) -> Mapping[str, Any] | None:
    """Tool arguments arrive as a JSON string, or already decoded. Accept both."""
    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, Mapping) else None


def phone_from_tool_calls(call: Mapping[str, Any]) -> str | None:
    """Recover the patient's phone from the tool calls Sofía made during the call.

    The backend keeps no call_id -> contact_id map — GHL is the source of truth,
    not us. But the phone Sofía passed to create_lead / book_appointment is right
    there in the transcript payload, and upsert_contact is idempotent by phone,
    so it resolves back to the same contact without any local state.
    """
    for entry in call.get("transcript_with_tool_calls") or []:
        if not isinstance(entry, Mapping) or entry.get("role") != "tool_call_invocation":
            continue
        if entry.get("name") not in _PHONE_BEARING_TOOLS:
            continue
        args = _decode_arguments(entry.get("arguments"))
        if args and args.get("phone"):
            return str(args["phone"])
    return None


def tool_calls_from(call: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every tool Sofía fired, paired with whether the backend answered ok.

    Retell records an invocation and its result as two separate entries linked by
    `tool_call_id`. The dashboard shows them as one row per tool, which is how a
    clinic owner reads it: "she looked up availability, then she booked".
    """
    entries = call.get("transcript_with_tool_calls") or []

    results: dict[str, Any] = {}
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("role") == "tool_call_result":
            key = entry.get("tool_call_id")
            if key:
                results[str(key)] = entry.get("content")

    calls: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("role") != "tool_call_invocation":
            continue
        tool_call_id = str(entry.get("tool_call_id") or "")
        raw_result = results.get(tool_call_id)
        calls.append(
            {
                "name": entry.get("name"),
                "arguments": _decode_arguments(entry.get("arguments")) or {},
                "result": raw_result,
                "succeeded": _result_is_ok(raw_result),
            }
        )
    return calls


def _result_is_ok(raw_result: Any) -> bool | None:
    """Did the backend answer this tool call with ok:true?

    Returns None when there is no result at all — the call hung up mid-tool, and
    "unknown" is the honest answer. Never guess `False`: on the appointments
    metric a guess becomes a number the clinic reads as a lost patient.
    """
    if raw_result is None:
        return None
    decoded = _decode_arguments(raw_result)
    if decoded is None:
        return None
    value = decoded.get("ok")
    return bool(value) if isinstance(value, bool) else None


def booked_in_call(call: Mapping[str, Any]) -> bool:
    """True only when book_appointment ran AND the backend confirmed it.

    This is the numerator of "Citas agendadas por Sofía". It deliberately counts
    the tool result rather than the clinic's calendar: the calendar also holds
    appointments the human receptionist made by hand, and mixing them would
    inflate the success rate with work Sofía never did.
    """
    return any(
        entry["name"] == BOOKING_TOOL and entry["succeeded"] is True
        for entry in tool_calls_from(call)
    )
