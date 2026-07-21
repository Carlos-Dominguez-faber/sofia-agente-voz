"""Reading a Retell call payload.

These run offline against payloads shaped like the real thing. That matters more
than it sounds: the appointment count on the panel, and the success rate the
clinic judges Sofía by, are both derived here. A parsing mistake does not crash
anything — it quietly reports the wrong number, and nobody can tell by looking.
"""

from __future__ import annotations

import json

from app.services.call_parsing import (
    booked_in_call,
    phone_from_tool_calls,
    tool_calls_from,
    transcript_from,
)

PHONE = "+529981234567"


def _invocation(tool_call_id: str, name: str, **arguments) -> dict:
    return {
        "role": "tool_call_invocation",
        "tool_call_id": tool_call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def _result(tool_call_id: str, *, ok: bool) -> dict:
    return {
        "role": "tool_call_result",
        "tool_call_id": tool_call_id,
        "content": json.dumps({"ok": ok, "data": {}, "message": ""}),
    }


def _call(*entries: dict, **extra) -> dict:
    return {"call_id": "call_test", "transcript_with_tool_calls": list(entries), **extra}


# --------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------


def test_transcript_prefers_the_plain_string():
    call = {"transcript": "Agent: Hola\nUser: Buenas"}
    assert transcript_from(call) == "Agent: Hola\nUser: Buenas"


def test_transcript_falls_back_to_the_object_form():
    call = {
        "transcript_object": [
            {"role": "agent", "content": "Hola"},
            {"role": "user", "content": "Quiero una cita"},
            {"role": "user", "content": ""},  # dropped: nothing was said
        ]
    }
    assert transcript_from(call) == "agent: Hola\nuser: Quiero una cita"


def test_transcript_of_a_silent_call_is_empty_not_an_error():
    assert transcript_from({}) == ""


# --------------------------------------------------------------------------
# The join key
# --------------------------------------------------------------------------


def test_phone_comes_from_the_tool_call():
    call = _call(_invocation("t1", "create_lead", phone=PHONE, first_name="Ana"))
    assert phone_from_tool_calls(call) == PHONE


def test_phone_ignores_tools_that_do_not_carry_one():
    call = _call(
        _invocation("t1", "check_availability", days_ahead=7),
        _invocation("t2", "book_appointment", phone=PHONE, start_time="2026-07-22T16:00:00-05:00"),
    )
    assert phone_from_tool_calls(call) == PHONE


def test_phone_survives_arguments_arriving_already_decoded():
    call = _call({"role": "tool_call_invocation", "name": "create_lead", "arguments": {"phone": PHONE}})
    assert phone_from_tool_calls(call) == PHONE


def test_phone_is_none_when_the_caller_hung_up_before_qualifying():
    call = _call(_invocation("t1", "check_availability", days_ahead=7))
    assert phone_from_tool_calls(call) is None


def test_malformed_arguments_do_not_raise():
    call = _call({"role": "tool_call_invocation", "name": "create_lead", "arguments": "{not json"})
    assert phone_from_tool_calls(call) is None


# --------------------------------------------------------------------------
# Tool calls
# --------------------------------------------------------------------------


def test_tool_calls_pair_invocations_with_their_results():
    call = _call(
        _invocation("t1", "create_lead", phone=PHONE),
        _result("t1", ok=True),
        _invocation("t2", "book_appointment", phone=PHONE),
        _result("t2", ok=False),
    )
    calls = tool_calls_from(call)
    assert [c["name"] for c in calls] == ["create_lead", "book_appointment"]
    assert [c["succeeded"] for c in calls] == [True, False]


def test_a_tool_call_with_no_result_is_unknown_not_failed():
    """The line dropped mid-tool. `False` would be a guess, and this feeds a metric."""
    call = _call(_invocation("t1", "book_appointment", phone=PHONE))
    assert tool_calls_from(call)[0]["succeeded"] is None


# --------------------------------------------------------------------------
# The appointment metric
# --------------------------------------------------------------------------


def test_booked_requires_the_backend_to_have_confirmed():
    booked = _call(
        _invocation("t1", "book_appointment", phone=PHONE),
        _result("t1", ok=True),
    )
    assert booked_in_call(booked) is True


def test_a_failed_booking_does_not_count_as_an_appointment():
    """GHL was down. Sofía offered a callback and no appointment exists."""
    failed = _call(
        _invocation("t1", "book_appointment", phone=PHONE),
        _result("t1", ok=False),
    )
    assert booked_in_call(failed) is False


def test_an_unanswered_booking_does_not_count_either():
    assert booked_in_call(_call(_invocation("t1", "book_appointment", phone=PHONE))) is False


def test_registering_a_lead_is_not_booking_an_appointment():
    lead_only = _call(
        _invocation("t1", "create_lead", phone=PHONE),
        _result("t1", ok=True),
    )
    assert booked_in_call(lead_only) is False


def test_a_call_with_no_tools_at_all_booked_nothing():
    assert booked_in_call({}) is False
