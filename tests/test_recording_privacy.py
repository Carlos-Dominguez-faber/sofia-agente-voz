"""The raw recording URL must never reach the browser.

Retell serves call recordings from an unauthenticated CloudFront URL — a HEAD
with no credentials returns 200. Anyone holding that URL can replay a patient's
call with no session. The panel therefore streams the audio through a
token-gated endpoint and returns only a boolean to the client.

This pins the boundary: `call_detail` exposes `has_recording`, never the URL.
An adversarial review found the leak; this keeps it closed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.services import dashboard_service


def _fake_call(**extra):
    return {
        "call_id": "call_x",
        "start_timestamp": 1_780_000_000_000,
        "end_timestamp": 1_780_000_120_000,
        "duration_ms": 120_000,
        "transcript": "Agent: Hola",
        "transcript_with_tool_calls": [],
        "recording_url": "https://dxc03zgurdly9.cloudfront.net/secret-patient-audio.wav",
        **extra,
    }


def test_call_detail_never_returns_the_raw_recording_url():
    with (
        patch.object(dashboard_service.retell_service, "get_call", return_value=_fake_call()),
        patch.object(dashboard_service, "_patient_phone", return_value=None),
    ):
        detail = dashboard_service.call_detail("call_x")

    blob = json.dumps(detail)
    assert "cloudfront" not in blob, "the raw recording URL leaked into the response"
    assert "recording_url" not in detail
    assert detail["has_recording"] is True


def test_has_recording_is_false_when_there_is_none():
    call = _fake_call()
    del call["recording_url"]
    with (
        patch.object(dashboard_service.retell_service, "get_call", return_value=call),
        patch.object(dashboard_service, "_patient_phone", return_value=None),
    ):
        detail = dashboard_service.call_detail("call_x")
    assert detail["has_recording"] is False


def test_the_raw_url_is_available_server_side_for_streaming_only():
    """The streaming endpoint needs the URL; it just must not travel to the browser."""
    with patch.object(dashboard_service.retell_service, "get_call", return_value=_fake_call()):
        url = dashboard_service.call_recording_url("call_x")
    assert url and "cloudfront" in url


def test_recording_url_is_none_when_absent():
    call = _fake_call()
    del call["recording_url"]
    with patch.object(dashboard_service.retell_service, "get_call", return_value=call):
        assert dashboard_service.call_recording_url("call_x") is None
