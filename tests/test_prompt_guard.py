"""The guardrails the panel cannot delete.

Section 11 of Sofía's prompt is what stops her giving a diagnosis, recommending
medication, or confirming an appointment that was never created. The panel lets
a clinic edit the prompt; these tests are what stop that edit removing the
guardrails.

They run against a stand-in prompt rather than the repo's, so a legitimate
rewording of `prompts/dental.yaml` does not fail the suite that guards it.
"""

from __future__ import annotations

import pytest

from app.services import prompt_guard
from app.services.prompt_guard import GUARDRAILS_MARKER, GuardrailError

FAKE_PROMPT = """# 1. ROLE
Eres Sofía, recepcionista.

# 4. TASK
Agendar una cita de valoración.

# 7. KNOWLEDGE BASE
La valoración cuesta 500 MXN.

# 11. SAFETY & SCOPE GUARDRAILS
- NUNCA DIAGNOSTICAS.
- NUNCA recomiendas medicamentos.
- SI UNA HERRAMIENTA FALLA: no confirmas nada que no haya pasado.

# 12. OBJECTION HANDLING
- "Está caro" → el precio es aproximado.
"""


@pytest.fixture(autouse=True)
def _use_the_fake_prompt(monkeypatch):
    """Point the guard at the stand-in prompt instead of prompts/dental.yaml."""
    monkeypatch.setattr(prompt_guard, "load_prompt", lambda *a, **k: FAKE_PROMPT)


# --------------------------------------------------------------------------
# Splitting
# --------------------------------------------------------------------------


def test_the_editable_half_never_contains_the_guardrails():
    split = prompt_guard.split_for_editing(FAKE_PROMPT)
    assert GUARDRAILS_MARKER in split["editable"]
    assert "NUNCA DIAGNOSTICAS" not in split["editable"]
    # The client still edits the price and the objections around it.
    assert "500 MXN" in split["editable"]
    assert "Está caro" in split["editable"]


def test_the_guardrails_come_back_separately_for_display():
    split = prompt_guard.split_for_editing(FAKE_PROMPT)
    assert "NUNCA DIAGNOSTICAS" in split["guardrails"]
    assert split["guardrails_present_live"] is True
    assert split["guardrails_match_repo"] is True


def test_drift_from_the_repo_is_reported_not_hidden():
    drifted = FAKE_PROMPT.replace("- NUNCA recomiendas medicamentos.\n", "")
    split = prompt_guard.split_for_editing(drifted)
    assert split["guardrails_match_repo"] is False


def test_a_live_prompt_with_no_safety_section_can_still_be_repaired():
    """An older prompt gets the marker appended so the next save reinstates it."""
    broken = "# 1. ROLE\nEres Sofía.\n\n# 4. TASK\nAgendar.\n"
    split = prompt_guard.split_for_editing(broken)
    assert split["guardrails_present_live"] is False
    assert GUARDRAILS_MARKER in split["editable"]
    assert "NUNCA DIAGNOSTICAS" in prompt_guard.compose_for_publish(split["editable"])


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_saving_an_untouched_prompt_changes_nothing():
    """An operator who clicks save to see what happens should see nothing happen."""
    split = prompt_guard.split_for_editing(FAKE_PROMPT)
    assert prompt_guard.compose_for_publish(split["editable"]) == FAKE_PROMPT


def test_a_legitimate_edit_survives_and_the_guardrails_return():
    split = prompt_guard.split_for_editing(FAKE_PROMPT)
    edited = split["editable"].replace("500 MXN", "600 MXN")
    composed = prompt_guard.compose_for_publish(edited)
    assert "600 MXN" in composed
    assert "NUNCA DIAGNOSTICAS" in composed


# --------------------------------------------------------------------------
# What must be refused
# --------------------------------------------------------------------------


def test_deleting_the_marker_is_refused():
    split = prompt_guard.split_for_editing(FAKE_PROMPT)
    gutted = split["editable"].replace(GUARDRAILS_MARKER, "")
    with pytest.raises(GuardrailError, match="reglas de seguridad"):
        prompt_guard.compose_for_publish(gutted)


def test_select_all_and_overwrite_is_refused():
    with pytest.raises(GuardrailError):
        prompt_guard.compose_for_publish("Eres una recepcionista amable. Agenda citas.")


def test_an_empty_prompt_is_refused():
    with pytest.raises(GuardrailError):
        prompt_guard.compose_for_publish("   \n  ")


def test_duplicating_the_marker_is_refused():
    split = prompt_guard.split_for_editing(FAKE_PROMPT)
    with pytest.raises(GuardrailError, match="2 veces"):
        prompt_guard.compose_for_publish(split["editable"] + "\n" + GUARDRAILS_MARKER)


def test_keeping_the_marker_but_gutting_the_rest_is_refused():
    """The safety block alone is not a prompt Sofía can answer a phone with."""
    with pytest.raises(GuardrailError, match="secciones"):
        prompt_guard.compose_for_publish(GUARDRAILS_MARKER)
