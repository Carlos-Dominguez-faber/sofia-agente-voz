"""Retell integration layer — the voice and ears of the agent.

Creates and updates Sofía's Retell LLM (the brain) and her phone agent, from
code. Nothing here is configured by hand in the Retell dashboard: the repo is
the source of truth, so a rebuild is reproducible and reviewable.

Two objects, in this order:

  1. The Retell LLM — model, temperature, the 12-component prompt, and the
     custom tools that point at the Modal backend.
  2. The agent      — voice, language and webhook, mounted on that LLM.

The prompt is assembled from `prompts/<industry>.yaml`, with `{placeholders}`
resolved against `sofia.config.yaml`. An unresolved placeholder is a hard
error: shipping `PENDIENTE_CONFIRMAR` into a live prompt means Sofía says it
out loud to a patient.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from retell import Retell

from app.services.ghl_service import (
    GHLConfigError,
    _config,
    _load_env_file,
    config_value,
)

LOG = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_DIR = _REPO_ROOT / "prompts"
_ENV_PATH = _REPO_ROOT / ".env"

# Verified against the live API (POST /create-retell-llm rejects anything else).
# The catalog rotates — re-probe before changing this.
# Reasoning models are deliberately excluded: they add 1-2s of silence per turn,
# and on a phone call silence reads as a dropped line.
MODEL_HAIKU = "claude-4.5-haiku"
DEFAULT_TEMPERATURE = 0.3

# `es-MX` is NOT a valid Retell language. Spanish options are es-ES and es-419.
# Mexico falls under es-419 (Latin America).
LANGUAGE_LATAM_SPANISH = "es-419"

DEFAULT_VOICE_ID = "retell-Andrea"  # platform voice, Mexican, female

# Slightly under 1.0. Callers dictate phone numbers and emails, and the default
# pace runs over the digits faster than anyone can verify their own data.
DEFAULT_VOICE_SPEED = 0.9

# Single-brace tokens like {business.name}. Retell's own dynamic variables use
# double braces, so the two templating systems never collide.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_]+(?:\.[a-zA-Z_]+)*)\}")

_PENDING_PREFIX = "PENDIENTE"


class RetellServiceError(RuntimeError):
    """Something in the provisioning chain failed."""


# --------------------------------------------------------------------------
# Credentials and endpoints
# --------------------------------------------------------------------------


def _require_env(name: str, hint: str) -> str:
    _load_env_file()
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RetellServiceError(f"Environment variable {name} is not set. {hint}")
    return value


def _client() -> Retell:
    return Retell(api_key=_require_env("RETELL_API_KEY", "Get it from the Retell dashboard."))


def modal_url() -> str:
    """Public base URL of the backend. Retell's tools call this on every turn."""
    url = _require_env("MODAL_URL", "It is the URL printed by `modal deploy`.")
    return url.rstrip("/")


# --------------------------------------------------------------------------
# Prompt assembly
# --------------------------------------------------------------------------


def _format_treatments() -> str:
    """Render the price list the way it should be read aloud, not as a table."""
    treatments = config_value("knowledge_base.treatments", []) or []
    currency = config_value("knowledge_base.currency", "MXN")
    lines = []
    for item in treatments:
        price = item.get("price_approx")
        unit = item.get("unit")
        suffix = f" {unit}" if unit else ""
        lines.append(f"- {item.get('name')}: alrededor de {price:,} {currency}{suffix}".replace(",", ","))
    return "\n".join(lines)


def _format_consultation_price() -> str:
    """The valoración price plus the refund line — they are never said apart."""
    price = config_value("knowledge_base.valoracion.price_approx")
    if price is None:
        raise RetellServiceError(
            "knowledge_base.valoracion.price_approx is still PENDIENTE in sofia.config.yaml. "
            "Sofía says this number out loud on every call — set it before provisioning."
        )
    currency = config_value("knowledge_base.currency", "MXN")
    text = f"{price:,} {currency}"
    if config_value("knowledge_base.valoracion.refundable"):
        note = config_value("knowledge_base.valoracion.refund_note", "Se descuenta del tratamiento")
        text += f" · REEMBOLSABLE: {note.lower()}"
    return text


def _prompt_context() -> dict[str, str]:
    """Build every value the prompt template can reference.

    Some come straight from sofia.config.yaml; some are derived, because the
    prompt wants prose and the config stores structured data.
    """
    cfg = _config()
    business = cfg.get("business", {}) or {}
    agent = cfg.get("agent", {}) or {}

    context = {
        "business.name": business.get("name"),
        "business.hours": config_value("business.hours"),
        "business.website": business.get("website"),
        "business.timezone": business.get("timezone"),
        "business.industry": business.get("industry"),
        "agent.name": agent.get("name"),
        "agent.role": agent.get("role"),
        # The config calls it `tone`; the prompt template asks for `personality`.
        "agent.personality": agent.get("tone"),
        # Derived: the prompt needs these as spoken prose, not as YAML.
        "business.treatments": _format_treatments(),
        "business.consultation_price": _format_consultation_price(),
    }
    return {k: v for k, v in context.items() if v is not None}


def render_prompt(template: str, context: dict[str, str] | None = None) -> str:
    """Resolve {placeholders}. An unresolved or PENDIENTE value is a hard error.

    This is the guard that keeps `PENDIENTE_CONFIRMAR` from ever being spoken
    to a patient. Failing here is cheap; failing on a live call is not.
    """
    ctx = context if context is not None else _prompt_context()
    missing: list[str] = []
    pending: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in ctx:
            missing.append(key)
            return match.group(0)
        value = str(ctx[key])
        if value.startswith(_PENDING_PREFIX):
            pending.append(key)
        return value

    rendered = _PLACEHOLDER_RE.sub(substitute, template)

    if missing:
        raise RetellServiceError(
            f"Prompt placeholders with no value in sofia.config.yaml: {sorted(set(missing))}"
        )
    if pending:
        raise RetellServiceError(
            f"Prompt placeholders still PENDIENTE in sofia.config.yaml: {sorted(set(pending))}. "
            "Sofía would say that literally on a call."
        )
    return rendered


def load_prompt(variant: str = "inbound_prompt", industry: str | None = None) -> str:
    """Read prompts/<industry>.yaml and return the rendered prompt.

    Note: prompts/dental.yaml documents app/config.py as the home for this
    substitution. Until that module exists, it lives here.
    """
    name = industry or config_value("business.industry", "dental")
    path = _PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise RetellServiceError(f"Prompt file not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    template = data.get(variant)
    if not template or not isinstance(template, str):
        raise RetellServiceError(f"`{variant}` is missing or empty in {path}")
    if template.strip().startswith(_PENDING_PREFIX):
        raise RetellServiceError(f"`{variant}` in {path} is still a PENDIENTE placeholder")

    return render_prompt(template)


def begin_message() -> str:
    """The first line Sofía says. Inbound must speak first — silence reads as a dead line."""
    # Tuteo, matching the prompt's register. The greeting used to say "le
    # atiende" while the rest of the prompt tutea — the model resolved that
    # contradiction by drifting informal.
    return (
        f"{config_value('business.name')}, te atiende {config_value('agent.name')}, "
        "¿en qué te puedo ayudar?"
    )


# --------------------------------------------------------------------------
# Custom tools — these point at the Modal backend
# --------------------------------------------------------------------------


def build_custom_functions(base_url: str | None = None) -> list[dict[str, Any]]:
    """The three tools Sofía can call mid-call, wired to the Modal endpoints.

    `speak_during_execution` is on for all three: a silent agent while an HTTP
    call is in flight sounds like the line dropped.
    """
    url = (base_url or modal_url()).rstrip("/")

    return [
        {
            "type": "custom",
            "name": "create_lead",
            "description": (
                "Registra al paciente en el CRM. Úsala en cuanto tengas nombre y teléfono "
                "confirmados. Es idempotente: llamarla dos veces no duplica al paciente."
            ),
            "url": f"{url}/create-lead",
            "speak_during_execution": True,
            "speak_after_execution": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Teléfono en formato E.164 si lo tienes (+52...), o los 10 dígitos.",
                    },
                    "first_name": {"type": "string", "description": "Nombre del paciente."},
                    "last_name": {"type": "string", "description": "Apellido del paciente."},
                    "email": {"type": "string", "description": "Correo, ya deletreado y confirmado."},
                    "reason": {"type": "string", "description": "Motivo de la llamada, en las palabras del paciente."},
                },
                "required": ["phone"],
            },
        },
        {
            "type": "custom",
            "name": "check_availability",
            "description": (
                "Consulta los horarios libres reales del calendario de la clínica. "
                "Úsala SIEMPRE antes de ofrecer cualquier horario. Nunca inventes disponibilidad. "
                "Devuelve `options`, una lista de objetos con `label` e `iso`. "
                "DI EN VOZ ALTA el `label` tal cual viene (ya trae 'hoy', 'mañana' o el día "
                "correcto, y la hora en palabras): no calcules tú la fecha ni conviertas el ISO. "
                "Guarda el `iso` del horario que el paciente elija y pásalo a book_appointment. "
                "El campo `current_datetime` te dice la fecha y hora actuales de la clínica."
            ),
            "url": f"{url}/check-availability",
            "speak_during_execution": True,
            "speak_after_execution": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Cuántos días hacia adelante buscar. Por defecto 7, máximo 31.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Fecha inicial YYYY-MM-DD. Omítela para empezar hoy.",
                    },
                    "max_options": {
                        "type": "integer",
                        "description": "Cuántos horarios devolver. Usa 2 o 3: el paciente no retiene más.",
                    },
                },
                "required": [],
            },
        },
        {
            "type": "custom",
            "name": "book_appointment",
            "description": (
                "Agenda la cita de valoración con el horario que el paciente eligió. "
                "Si devuelve un error, NO confirmes la cita: ofrece seguimiento humano."
            ),
            "url": f"{url}/book-appointment",
            "speak_during_execution": True,
            "speak_after_execution": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Teléfono del paciente."},
                    "start_time": {
                        "type": "string",
                        "description": (
                            "El campo `iso` del horario que el paciente eligió, copiado tal cual "
                            "de la respuesta de check_availability (por ejemplo "
                            "2026-07-21T16:00:00-05:00). Nunca lo reconstruyas a mano."
                        ),
                    },
                    "first_name": {"type": "string", "description": "Nombre del paciente."},
                    "last_name": {"type": "string", "description": "Apellido del paciente."},
                    "email": {"type": "string", "description": "Correo del paciente."},
                    "reason": {"type": "string", "description": "Motivo de la consulta."},
                    "treatment": {"type": "string", "description": "Tratamiento de interés, si lo mencionó."},
                    "urgency": {
                        "type": "string",
                        "enum": ["urgente", "normal", "baja"],
                        "description": "urgente si hubo dolor, hinchazón o sangrado.",
                    },
                    "temperature": {
                        "type": "string",
                        "enum": ["hot", "warm", "cold"],
                        "description": "Qué tan cerca está de tomar el tratamiento.",
                    },
                },
                "required": ["phone", "start_time"],
            },
        },
    ]


# --------------------------------------------------------------------------
# Provisioning
# --------------------------------------------------------------------------


def create_inbound_llm(
    *,
    model: str = MODEL_HAIKU,
    temperature: float = DEFAULT_TEMPERATURE,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Creates the brain: model, temperature, prompt and the three tools."""
    prompt = load_prompt("inbound_prompt")
    tools = build_custom_functions(base_url)

    llm = _client().llm.create(
        model=model,
        model_temperature=temperature,
        general_prompt=prompt,
        general_tools=tools,
        begin_message=begin_message(),
        start_speaker="agent",  # inbound: the clinic answers, it does not wait
    )
    LOG.info("Retell LLM created: %s (model=%s, tools=%s)", llm.llm_id, model, len(tools))
    return {"llm_id": llm.llm_id, "model": model, "temperature": temperature, "tools": [t["name"] for t in tools]}


def create_inbound_agent(
    llm_id: str,
    *,
    agent_name: str | None = None,
    voice_id: str = DEFAULT_VOICE_ID,
    language: str = LANGUAGE_LATAM_SPANISH,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Mounts the voice and the phone number's behaviour on top of that brain."""
    url = (base_url or modal_url()).rstrip("/")
    name = agent_name or str(config_value("agent.name", "Sofía"))

    agent = _client().agent.create(
        response_engine={"type": "retell-llm", "llm_id": llm_id},
        agent_name=name,
        voice_id=voice_id,
        language=language,
        webhook_url=f"{url}/retell-webhook",
    )
    LOG.info("Retell agent created: %s (%s, voice=%s)", agent.agent_id, name, voice_id)
    return {
        "agent_id": agent.agent_id,
        "agent_name": name,
        "voice_id": voice_id,
        "language": language,
        "llm_id": llm_id,
        "webhook_url": f"{url}/retell-webhook",
    }


def update_inbound_agent(
    agent_id: str | None = None,
    *,
    voice_speed: float = DEFAULT_VOICE_SPEED,
) -> dict[str, Any]:
    """Adjusts the voice on the existing agent without recreating it."""
    target = agent_id or _require_env(
        "RETELL_INBOUND_AGENT_ID", "Run provision_inbound() first, or pass agent_id."
    )
    agent = _client().agent.update(target, voice_speed=voice_speed)
    LOG.info("Retell agent %s updated (voice_speed=%s)", target, voice_speed)
    return {"agent_id": agent.agent_id, "voice_speed": voice_speed}


def update_inbound_llm(llm_id: str | None = None, *, base_url: str | None = None) -> dict[str, Any]:
    """Pushes the current prompt and tool definitions onto the existing LLM.

    Re-provisioning would mint a new llm_id and orphan the agent, so changes to
    the prompt or the tools are applied in place.
    """
    target = llm_id or _require_env(
        "RETELL_INBOUND_LLM_ID", "Run provision_inbound() first, or pass llm_id."
    )
    prompt = load_prompt("inbound_prompt")
    tools = build_custom_functions(base_url)

    llm = _client().llm.update(
        target,
        general_prompt=prompt,
        general_tools=tools,
        begin_message=begin_message(),
    )
    LOG.info("Retell LLM %s updated (prompt=%s chars, tools=%s)", target, len(prompt), len(tools))
    return {"llm_id": llm.llm_id, "prompt_chars": len(prompt), "tools": [t["name"] for t in tools]}


def _upsert_env_var(key: str, value: str) -> None:
    """Write a key into .env without disturbing anything else in the file."""
    if not _ENV_PATH.exists():
        raise RetellServiceError(f".env not found at {_ENV_PATH}")

    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^{re.escape(key)}=")
    replaced = False

    for index, line in enumerate(lines):
        if pattern.match(line):
            comment = ""
            if "#" in line:
                comment = "        # " + line.split("#", 1)[1].strip()
            lines[index] = f"{key}={value}{comment}"
            replaced = True
            break

    if not replaced:
        lines.append(f"{key}={value}")

    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOG.info("Wrote %s to .env", key)


def provision_inbound(*, persist: bool = True) -> dict[str, Any]:
    """Creates the LLM and the agent, and records both ids in .env."""
    try:
        llm = create_inbound_llm()
        agent = create_inbound_agent(llm["llm_id"])
    except GHLConfigError as exc:  # config file problems surface the same way
        raise RetellServiceError(str(exc)) from exc

    if persist:
        _upsert_env_var("RETELL_INBOUND_LLM_ID", llm["llm_id"])
        _upsert_env_var("RETELL_INBOUND_AGENT_ID", agent["agent_id"])

    return {**agent, "model": llm["model"], "temperature": llm["temperature"], "tools": llm["tools"]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    result = provision_inbound()
    for key, value in result.items():
        print(f"{key}: {value}")
