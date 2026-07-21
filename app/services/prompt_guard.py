"""The guardrails the client is not allowed to delete.

The panel exists so the clinic can edit Sofía's prompt without opening Retell —
change a price, add a promotion, adjust how she greets people. That is the whole
commercial argument for it.

But the prompt also carries section 11, SAFETY & SCOPE GUARDRAILS, and those
rules are not preferences. They are the line between a receptionist and
practising medicine without a licence:

  - she never diagnoses,
  - she never recommends medication,
  - she never confirms an appointment the system did not actually create.

A clinic owner editing a price has no reason to touch them, and every reason not
to. But a textarea does not know that: select-all, paste, save, and the
guardrails are gone from a live phone line with no warning.

So the block is never editable in the first place. The API hands the panel the
prompt with section 11 replaced by a marker, and puts the canonical block back
on save. The client edits everything around it and cannot remove what was never
in their textarea.

The canonical text comes from `prompts/dental.yaml` — the repo, not Retell. If
the live prompt ever drifted (someone edited it in the Retell console), saving
through the panel restores the reviewed version rather than preserving the
drift.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.retell_service import RetellServiceError, load_prompt

LOG = logging.getLogger(__name__)

# The marker the panel sees in place of the protected block. Chosen to look
# deliberate in a textarea and to be impossible to type by accident.
GUARDRAILS_MARKER = "<<< REGLAS DE SEGURIDAD — NO EDITABLES >>>"

# Section 11 of the 12-component prompt. Numbers, not titles: a translated or
# reworded heading would slip past a title match, and the numbering is what the
# whole prompt structure is built on.
_PROTECTED_SECTION = 11
_SECTION_RE = re.compile(r"^[ \t]*#[ \t]*(\d+)\.[ \t]*(.*)$", re.MULTILINE)


class GuardrailError(RuntimeError):
    """The submitted prompt would have shipped without its safety block."""


def _find_section_bounds(prompt: str, number: int) -> tuple[int, int] | None:
    """Character span of section `number`, from its heading to the next heading."""
    matches = list(_SECTION_RE.finditer(prompt))
    for index, match in enumerate(matches):
        if int(match.group(1)) != number:
            continue
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        return start, end
    return None


def canonical_guardrails() -> str:
    """Section 11 as the repo defines it — the version that was reviewed.

    Returned as the exact span, trailing blank line included. Trimming it here
    would mean that saving an unchanged prompt still rewrites the live one by a
    newline, and an operator who clicks save to see what happens deserves to see
    nothing happen.
    """
    prompt = load_prompt("inbound_prompt")
    bounds = _find_section_bounds(prompt, _PROTECTED_SECTION)
    if not bounds:
        raise GuardrailError(
            f"prompts/dental.yaml has no section {_PROTECTED_SECTION} "
            "(SAFETY & SCOPE GUARDRAILS). The prompt cannot be published without it."
        )
    start, end = bounds
    return prompt[start:end]


def split_for_editing(prompt: str) -> dict[str, Any]:
    """Replace the safety block with the marker, and hand both parts back.

    When the live prompt has no section 11 at all — an older prompt, or one
    already damaged through the Retell console — the marker is appended at the
    end so the next save reinstates the guardrails instead of failing forever.
    """
    guardrails = canonical_guardrails()
    bounds = _find_section_bounds(prompt, _PROTECTED_SECTION)

    if not bounds:
        LOG.warning("Live prompt has no section %s; marker appended for repair", _PROTECTED_SECTION)
        editable = prompt.rstrip() + f"\n\n{GUARDRAILS_MARKER}\n"
        return {
            "editable": editable,
            "guardrails": guardrails,
            "guardrails_present_live": False,
            "guardrails_match_repo": False,
        }

    start, end = bounds
    live_block = prompt[start:end]
    # The marker stands in for the exact span, with no whitespace added around
    # it, so composing it back reproduces the prompt byte for byte.
    editable = f"{prompt[:start]}{GUARDRAILS_MARKER}{prompt[end:]}"

    return {
        "editable": editable,
        "guardrails": guardrails,
        "guardrails_present_live": True,
        # Drift is worth surfacing, not worth blocking: the next save fixes it.
        "guardrails_match_repo": _normalize(live_block) == _normalize(guardrails),
    }


def _normalize(text: str) -> str:
    """Compare guardrail blocks by content, ignoring whitespace reflow."""
    return re.sub(r"\s+", " ", text).strip()


def compose_for_publish(editable: str) -> str:
    """Put the canonical guardrails back where the marker is, ready for Retell.

    Raises if the marker is gone. That is the one thing the panel must not be
    able to do, and refusing the save is the only honest answer — silently
    re-appending the block would teach the client that deleting it worked.
    """
    if not editable or not editable.strip():
        raise GuardrailError("El prompt está vacío. No se puede publicar así.")

    occurrences = editable.count(GUARDRAILS_MARKER)
    if occurrences == 0:
        raise GuardrailError(
            "Falta el bloque de reglas de seguridad. Esas reglas impiden que Sofía "
            "dé diagnósticos o confirme citas que no existen, así que no se puede "
            "guardar un prompt sin ellas. Restaura el bloque y vuelve a guardar."
        )
    if occurrences > 1:
        raise GuardrailError(
            f"El bloque de reglas de seguridad aparece {occurrences} veces y debe "
            "aparecer exactamente una. Deja solo uno y vuelve a guardar."
        )

    composed = editable.replace(GUARDRAILS_MARKER, canonical_guardrails())

    # Second net: the marker could survive while the rest of the prompt is
    # gutted. A prompt that lost its role and its task is not a prompt Sofía can
    # answer a phone with, even if the safety block is technically intact.
    sections = {int(m.group(1)) for m in _SECTION_RE.finditer(composed)}
    missing_core = sorted({1, 4, _PROTECTED_SECTION} - sections)
    if missing_core:
        raise GuardrailError(
            f"El prompt perdió secciones que no son opcionales: {missing_core}. "
            "Se esperan las 12 secciones (1. ROLE … 12. OBJECTION HANDLING)."
        )

    return composed


def describe_protection() -> dict[str, Any]:
    """What the panel needs to explain the protection to the person editing."""
    try:
        guardrails = canonical_guardrails()
        available = True
        detail = None
    except (GuardrailError, RetellServiceError) as exc:
        guardrails = ""
        available = False
        detail = str(exc)

    return {
        "marker": GUARDRAILS_MARKER,
        "section": _PROTECTED_SECTION,
        "title": "Safety & Scope Guardrails",
        "guardrails": guardrails,
        "available": available,
        "detail": detail,
        "why": (
            "Estas reglas impiden que Sofía dé un diagnóstico, recomiende "
            "medicamentos o confirme una cita que el sistema no creó. Se pueden "
            "leer, no editar."
        ),
    }
