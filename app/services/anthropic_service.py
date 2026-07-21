"""Claude integration layer — the post-call analysis.

When a call ends, Retell hands us the transcript. This module turns it into a
structured read of the patient: how interested they were, how urgent their
problem is, how likely they are to actually show up, and a summary a human can
act on before calling them back.

The prompt lives in `prompts/<industry>.yaml` under `post_call_analysis`, so the
copy stays with the rest of the agent's copy and not buried in Python.

Two rules shape this module:

  * The output is validated against a schema. If Claude returns something that
    does not fit, this raises — the caller writes the raw transcript instead.
    Inventing a score for a call nobody analysed is worse than having no score.
  * Nothing here writes to GHL. The caller decides what lands where, and the
    clinical record (`contact.notas_clinicas`) is never a destination.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import anthropic
import yaml
from pydantic import BaseModel, Field, ValidationError

from app.services.ghl_service import _load_env_file, config_value
from app.services.retell_service import RetellServiceError, render_prompt

LOG = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_DIR = _REPO_ROOT / "prompts"

# Post-call analysis is asynchronous — nobody is waiting on the phone — so this
# runs on the most capable model rather than the fast one used in-call.
MODEL = "claude-opus-4-8"
MAX_TOKENS = 4096
EFFORT = "medium"

_PENDING_PREFIX = "PENDIENTE"


class AnalysisError(RuntimeError):
    """The analysis did not produce a usable result. Never fall back to invented scores."""


# --------------------------------------------------------------------------
# The schema Claude must fill
# --------------------------------------------------------------------------


class CallAnalysis(BaseModel):
    """Structured read of one call. Field names match prompts/dental.yaml."""

    interes_score: int | None = Field(
        default=None, ge=1, le=10, description="Qué tan cerca está de tomar el tratamiento (1-10)."
    )
    tratamiento_interes: str | None = Field(
        default=None, description="Tratamiento del catálogo que mencionó. null si ninguno."
    )
    nivel_urgencia: Literal["urgente", "normal", "baja"] | None = Field(
        default=None, description="urgente si mencionó dolor, hinchazón o sangrado."
    )
    probabilidad_asistir: int | None = Field(
        default=None, ge=1, le=10, description="Probabilidad de que asista a la cita (1-10)."
    )
    cita_agendada: bool = Field(
        description="True solo si la cita se creó de verdad. Si el sistema falló, False."
    )
    resumen: str = Field(description="2 o 3 oraciones en español: lo que un humano necesita saber.")
    siguiente_accion: str = Field(description="Qué conviene hacer, concreto.")
    temperatura: Literal["hot", "warm", "cold"] | None = Field(
        default=None, description="Temperatura del lead."
    )
    alertas: list[str] = Field(
        default_factory=list,
        description="Momentos en que el agente se salió de sus reglas. Vacía si no hubo.",
    )


# --------------------------------------------------------------------------
# Prompt and client
# --------------------------------------------------------------------------


def _client() -> anthropic.Anthropic:
    _load_env_file()
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise AnalysisError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=api_key)


def load_analysis_prompt(industry: str | None = None) -> str:
    """Read `post_call_analysis` from the industry prompt file and resolve placeholders."""
    name = industry or config_value("business.industry", "dental")
    path = _PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise AnalysisError(f"Prompt file not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    template = data.get("post_call_analysis")
    if not template or not isinstance(template, str):
        raise AnalysisError(f"`post_call_analysis` is missing or empty in {path}")
    if template.strip().startswith(_PENDING_PREFIX):
        raise AnalysisError(f"`post_call_analysis` in {path} is still a PENDIENTE placeholder")

    try:
        return render_prompt(template)
    except RetellServiceError as exc:
        # Same guard as the voice prompt: a PENDIENTE value must never reach the model.
        raise AnalysisError(str(exc)) from exc


# --------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------


# Reads the call transcript and returns the structured read of the patient behind it.
def analyze_call(transcript: str, *, call_id: str | None = None, industry: str | None = None) -> CallAnalysis:
    """Send the transcript to Claude and return a validated CallAnalysis.

    Raises AnalysisError if the transcript is empty, the API fails, or the
    response does not satisfy the schema. The caller must treat that as "no
    analysis" and fall back to storing the raw transcript — never to a guess.
    """
    if not transcript or not transcript.strip():
        raise AnalysisError("transcript is empty; nothing to analyze")

    system_prompt = load_analysis_prompt(industry)

    try:
        response = _client().messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            output_config={"effort": EFFORT},
            output_format=CallAnalysis,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analiza la siguiente transcripción de llamada y devuelve el JSON "
                        "estructurado. Si un dato no se dijo en la llamada, devuelve null: "
                        "no lo infieras ni lo inventes.\n\n"
                        f"<transcripcion>\n{transcript.strip()}\n</transcripcion>"
                    ),
                }
            ],
        )
    except anthropic.APIStatusError as exc:
        raise AnalysisError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalysisError(f"Could not reach the Anthropic API: {exc}") from exc
    except ValidationError as exc:
        raise AnalysisError(f"Claude returned a response that does not fit the schema: {exc}") from exc

    if response.stop_reason == "refusal":
        raise AnalysisError("Claude declined to analyze this transcript")

    analysis = response.parsed_output
    if analysis is None:
        raise AnalysisError("Claude returned no parsed output")

    LOG.info(
        "Call analyzed call=%s interes=%s urgencia=%s asistir=%s temp=%s alertas=%s",
        call_id,
        analysis.interes_score,
        analysis.nivel_urgencia,
        analysis.probabilidad_asistir,
        analysis.temperatura,
        len(analysis.alertas),
    )
    return analysis


# Turns the analysis into the note a receptionist would actually want to read.
def format_note(analysis: CallAnalysis, *, call_id: str | None = None, duration_s: int | None = None) -> str:
    """Render the summary and next action as the note body for the GHL contact."""
    lines = ["📞 Resumen de llamada — Sofía (agente de voz)", ""]
    lines.append(analysis.resumen)
    lines.append("")
    lines.append(f"➡️  Siguiente acción: {analysis.siguiente_accion}")
    lines.append("")

    details = [
        ("Cita agendada", "sí" if analysis.cita_agendada else "NO"),
        ("Interés", f"{analysis.interes_score}/10" if analysis.interes_score else "—"),
        ("Urgencia", analysis.nivel_urgencia or "—"),
        ("Probabilidad de asistir", f"{analysis.probabilidad_asistir}/10" if analysis.probabilidad_asistir else "—"),
        ("Tratamiento de interés", analysis.tratamiento_interes or "—"),
        ("Temperatura", analysis.temperatura or "—"),
    ]
    lines.extend(f"• {label}: {value}" for label, value in details)

    if analysis.alertas:
        lines.append("")
        lines.append("⚠️  Alertas de la llamada:")
        lines.extend(f"   - {alert}" for alert in analysis.alertas)

    footer = []
    if call_id:
        footer.append(f"call_id: {call_id}")
    if duration_s:
        footer.append(f"duración: {duration_s}s")
    if footer:
        lines.extend(["", " · ".join(footer)])

    return "\n".join(lines)


# Preserves what was actually said when the analysis fails — no invented scores, ever.
def format_fallback_note(transcript: str, reason: str, *, call_id: str | None = None) -> str:
    """The note written when Claude could not produce a valid analysis.

    The transcript is the evidence; the scores are the inference. When the
    inference fails we keep the evidence and say so plainly, so nobody reads a
    missing score as a low one.
    """
    lines = [
        "📞 Llamada registrada — el análisis automático NO se pudo completar",
        "",
        f"Motivo: {reason}",
        "",
        "Los campos de interés, urgencia y probabilidad quedaron sin llenar a propósito:",
        "no se inventan valores. Abajo está la transcripción completa para revisión manual.",
        "",
        "--- TRANSCRIPCIÓN ---",
        (transcript or "(sin transcripción)").strip(),
    ]
    if call_id:
        lines.extend(["", f"call_id: {call_id}"])
    return "\n".join(lines)
