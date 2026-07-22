"""The bounds that keep a clinic owner from breaking Sofía.

Every control on the panel is clamped in the backend — not in the front, which
a determined user or a bug can bypass. These tests pin the validation that runs
before anything touches Retell: an out-of-range value must raise, and it must
raise BEFORE a single agent is mutated.

The live update+publish cycle is validated separately against throwaway Retell
agents (it needs the real API and cannot be unit-tested); what is unit-tested
here is the pure logic that decides whether a change is even allowed.
"""

from __future__ import annotations

import pytest

from app.services import retell_service as rs
from app.services.retell_service import RetellServiceError

# --------------------------------------------------------------------------
# Preset mapping
# --------------------------------------------------------------------------


def test_presets_are_named_and_never_exceed_the_cap():
    assert set(rs.BEHAVIOUR_PRESETS) == {"estricta", "balanceada", "flexible"}
    for name, temp in rs.BEHAVIOUR_PRESETS.items():
        assert temp <= rs.BEHAVIOUR_TEMP_MAX, f"{name}={temp} exceeds cap"


def test_nearest_preset_maps_a_raw_temperature_back_to_a_name():
    assert rs._nearest_preset(0.2) == "estricta"
    assert rs._nearest_preset(0.34) == "balanceada"
    assert rs._nearest_preset(0.5) == "flexible"
    # An off-grid value snaps to the closest, so the panel can always show a name.
    assert rs._nearest_preset(0.28) == "balanceada"
    assert rs._nearest_preset(None) is None


def test_curated_voices_all_have_ids_and_the_current_one_is_present():
    ids = {v["voice_id"] for v in rs.CURATED_VOICES}
    assert rs.DEFAULT_VOICE_ID in ids, "the current voice must stay selectable"
    assert all(v.get("voice_id") and v.get("label") for v in rs.CURATED_VOICES)


# --------------------------------------------------------------------------
# Validation refuses out-of-bounds input BEFORE touching Retell
# --------------------------------------------------------------------------


def test_a_voice_outside_the_curated_list_is_refused():
    with pytest.raises(ValueError, match="curated"):
        rs.apply_agent_config(voice_id="11labs-SomeRandomEnglishVoice")


def test_speed_below_the_floor_is_refused():
    with pytest.raises(ValueError, match="voice_speed"):
        rs.apply_agent_config(voice_speed=0.5)


def test_speed_above_the_ceiling_is_refused():
    with pytest.raises(ValueError, match="voice_speed"):
        rs.apply_agent_config(voice_speed=1.5)


def test_speed_at_the_bounds_is_allowed_through_validation(monkeypatch):
    """0.85 and 1.15 are valid; prove validation passes by stubbing the publish."""
    calls = []
    monkeypatch.setattr(rs, "_managed_agents", lambda: [("inbound", "agent_x")])
    monkeypatch.setattr(
        rs, "publish_agent_change",
        lambda aid, **kw: calls.append(kw) or {"agent_id": aid, "published_version": 1},
    )
    rs.apply_agent_config(voice_speed=rs.VOICE_SPEED_MAX)
    rs.apply_agent_config(voice_speed=rs.VOICE_SPEED_MIN)
    assert len(calls) == 2


def test_an_unknown_behaviour_preset_is_refused():
    with pytest.raises(ValueError, match="behaviour"):
        rs.apply_agent_config(behaviour="caotica")


def test_an_empty_change_is_refused():
    with pytest.raises(ValueError, match="No changes"):
        rs.apply_agent_config()


# --------------------------------------------------------------------------
# What gets sent to Retell — resolved values, both agents
# --------------------------------------------------------------------------


def test_behaviour_resolves_to_temperature_and_hits_every_managed_agent(monkeypatch):
    sent = []
    monkeypatch.setattr(rs, "_managed_agents", lambda: [("inbound", "a_in"), ("outbound", "a_out")])
    monkeypatch.setattr(
        rs, "publish_agent_change",
        lambda aid, **kw: sent.append((aid, kw)) or {"agent_id": aid, "published_version": 1},
    )

    rs.apply_agent_config(behaviour="balanceada")

    assert len(sent) == 2, "both agents must be kept in sync"
    for _agent_id, kwargs in sent:
        assert kwargs["llm_temperature"] == 0.35
        assert kwargs["voice_id"] is None  # only behaviour changed


def test_expressiveness_toggle_maps_to_voice_temperature(monkeypatch):
    sent = []
    monkeypatch.setattr(rs, "_managed_agents", lambda: [("inbound", "a_in")])
    monkeypatch.setattr(
        rs, "publish_agent_change",
        lambda aid, **kw: sent.append(kw) or {"agent_id": aid, "published_version": 1},
    )

    rs.apply_agent_config(expressiveness=True)
    rs.apply_agent_config(expressiveness=False)

    assert sent[0]["voice_temperature"] == rs.EXPRESSIVENESS_ON
    assert sent[1]["voice_temperature"] == rs.EXPRESSIVENESS_OFF


def test_a_partial_failure_is_reported_not_swallowed(monkeypatch):
    """If the second agent fails, the save must not report success."""
    def flaky(agent_id, **kw):
        if agent_id == "a_out":
            raise RuntimeError("Retell rejected outbound")
        return {"agent_id": agent_id, "published_version": 1}

    monkeypatch.setattr(rs, "_managed_agents", lambda: [("inbound", "a_in"), ("outbound", "a_out")])
    monkeypatch.setattr(rs, "publish_agent_change", flaky)

    with pytest.raises(RetellServiceError, match="out of sync"):
        rs.apply_agent_config(behaviour="estricta")
