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

# Retell never hangs up on its own. Without the `end_call` tool AND this timeout,
# a call where the patient just says "gracias, adiós" and stops talking stays
# open, billing minutes, until max_call_duration_ms fires.
DEFAULT_END_CALL_AFTER_SILENCE_MS = 10_000

# A reception call that books one appointment runs 3-5 minutes. Ten is the
# ceiling for a genuinely messy call, not a target.
DEFAULT_MAX_CALL_DURATION_MS = 600_000

# Single-brace tokens like {business.name}, resolved from sofia.config.yaml at
# provisioning time.
#
# The lookarounds matter: Retell's own dynamic variables are {{lead_name}}, and
# without them this pattern happily matches the inner {lead_name} and blows up
# with "placeholder with no value" — the per-call variables do not exist yet
# when the agent is created. The two templating systems share a delimiter, so
# they have to be told apart explicitly.
_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_]+(?:\.[a-zA-Z_]+)*)\}(?!\})")

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
    """The tools Sofía can call mid-call: four wired to Modal, plus `end_call`.

    `speak_during_execution` is on for the four custom ones: a silent agent
    while an HTTP call is in flight sounds like the line dropped.

    `end_call` is Retell's built-in and takes no URL — it is what lets Sofía
    hang up. It pairs with `end_call_after_silence_ms` on the agent: the tool
    covers a clean goodbye, the timeout covers a caller who just walks away.
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
        {
            "type": "custom",
            "name": "update_lead_status",
            "description": (
                "Marca la temperatura del paciente y mueve su tarjeta en el pipeline. "
                "Úsala cuando el paciente NO agenda pero sí muestra interés, o cuando "
                "quieras registrar que quedó pendiente de decidir. Si ya agendaste con "
                "book_appointment no la necesitas: esa ya deja la etapa correcta."
            ),
            "url": f"{url}/update-lead-status",
            "speak_during_execution": True,
            "speak_after_execution": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Teléfono del paciente."},
                    "temperature": {
                        "type": "string",
                        "enum": ["hot", "warm", "cold"],
                        "description": (
                            "hot si hay dolor o quiere agendar ya; warm si le interesa pero "
                            "lo está pensando; cold si solo preguntaba."
                        ),
                    },
                    "stage": {
                        "type": "string",
                        "enum": ["new_lead", "engagement"],
                        "description": (
                            "new_lead si apenas se registró; engagement si hubo interés real "
                            "pero no cerró cita."
                        ),
                    },
                },
                "required": ["phone"],
            },
        },
        {
            # Built-in, no URL. Without this Sofía physically cannot hang up.
            "type": "end_call",
            "name": "end_call",
            "description": (
                "Cuelga la llamada. Úsala SOLO después de despedirte, cuando la "
                "conversación ya terminó: la cita quedó confirmada, el paciente dijo que "
                "no quiere nada más, o se despidió. Nunca cuelgues a media frase ni antes "
                "de confirmar lo que quedó agendado."
            ),
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
    """Creates the brain: model, temperature, prompt and the tools."""
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
    voice_speed: float = DEFAULT_VOICE_SPEED,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Mounts the voice and the phone number's behaviour on top of that brain."""
    url = (base_url or modal_url()).rstrip("/")
    name = agent_name or str(config_value("agent.name", "Sofía"))

    agent = _client().agent.create(
        response_engine={"type": "retell-llm", "llm_id": llm_id},
        agent_name=name,
        voice_id=voice_id,
        voice_speed=voice_speed,
        language=language,
        webhook_url=f"{url}/retell-webhook",
        end_call_after_silence_ms=DEFAULT_END_CALL_AFTER_SILENCE_MS,
        max_call_duration_ms=DEFAULT_MAX_CALL_DURATION_MS,
    )
    LOG.info("Retell agent created: %s (%s, voice=%s)", agent.agent_id, name, voice_id)
    return {
        "agent_id": agent.agent_id,
        "agent_name": name,
        "voice_id": voice_id,
        "voice_speed": voice_speed,
        "language": language,
        "llm_id": llm_id,
        "webhook_url": f"{url}/retell-webhook",
        "end_call_after_silence_ms": DEFAULT_END_CALL_AFTER_SILENCE_MS,
        "max_call_duration_ms": DEFAULT_MAX_CALL_DURATION_MS,
    }


def update_inbound_agent(
    agent_id: str | None = None,
    *,
    voice_speed: float = DEFAULT_VOICE_SPEED,
    end_call_after_silence_ms: int = DEFAULT_END_CALL_AFTER_SILENCE_MS,
    max_call_duration_ms: int = DEFAULT_MAX_CALL_DURATION_MS,
) -> dict[str, Any]:
    """Adjusts voice and call-termination settings without recreating the agent."""
    target = agent_id or _require_env(
        "RETELL_INBOUND_AGENT_ID", "Run provision_inbound() first, or pass agent_id."
    )
    agent = _client().agent.update(
        target,
        voice_speed=voice_speed,
        end_call_after_silence_ms=end_call_after_silence_ms,
        max_call_duration_ms=max_call_duration_ms,
    )
    LOG.info(
        "Retell agent %s updated (voice_speed=%s, silence=%sms, max=%sms)",
        target,
        voice_speed,
        end_call_after_silence_ms,
        max_call_duration_ms,
    )
    return {
        "agent_id": agent.agent_id,
        "voice_speed": voice_speed,
        "end_call_after_silence_ms": end_call_after_silence_ms,
        "max_call_duration_ms": max_call_duration_ms,
    }


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


# --------------------------------------------------------------------------
# Outbound — Sofía calls the patient
# --------------------------------------------------------------------------

# The dynamic variables the outbound prompt expects. The worker fills these on
# every call; they are declared here so provisioning can check the prompt and
# the caller agree, instead of finding out when Sofía says "Hola {{lead_name}}"
# out loud to a real patient.
OUTBOUND_DYNAMIC_VARIABLES = (
    "lead_name",
    "lead_last_name",
    "lead_interest",
    "lead_last_contact",
    "lead_notes",
    # The CRM already knows these. They are injected so Sofía can confirm them
    # instead of asking — a callback that asks for the number it just dialled
    # tells the patient there is nothing joined up behind the call.
    "lead_phone",
    "lead_email",
    "contact_id",
)

# Outbound is a courtesy callback, not a consultation. The prompt says two
# minutes; five is the ceiling before something has clearly gone wrong.
OUTBOUND_MAX_CALL_DURATION_MS = 300_000


def validate_outbound_variables(prompt: str) -> list[str]:
    """Return the {{dynamic_variables}} in the prompt that the worker does not fill."""
    found = set(re.findall(r"\{\{([a-zA-Z_]+)\}\}", prompt))
    return sorted(found - set(OUTBOUND_DYNAMIC_VARIABLES))


def create_outbound_llm(
    *,
    model: str = MODEL_HAIKU,
    temperature: float = DEFAULT_TEMPERATURE,
    base_url: str | None = None,
) -> dict[str, Any]:
    """The outbound brain. Same tools as inbound, different prompt and opening.

    No `begin_message`: the first line has to name the patient, and that name
    only exists at call time. Letting the model generate it from the prompt is
    what makes {{lead_name}} resolve; a hardcoded greeting would not.
    """
    prompt = load_prompt("outbound_prompt")

    unknown = validate_outbound_variables(prompt)
    if unknown:
        raise RetellServiceError(
            f"The outbound prompt uses dynamic variables nobody fills: {unknown}. "
            f"Add them to OUTBOUND_DYNAMIC_VARIABLES and to the worker, or remove them "
            f"from prompts/*.yaml — Retell would say the raw {{{{placeholder}}}} out loud."
        )

    tools = build_custom_functions(base_url)
    llm = _client().llm.create(
        model=model,
        model_temperature=temperature,
        general_prompt=prompt,
        general_tools=tools,
        start_speaker="agent",  # we placed the call; we speak first
    )
    LOG.info("Outbound LLM created: %s (model=%s, tools=%s)", llm.llm_id, model, len(tools))
    return {"llm_id": llm.llm_id, "model": model, "temperature": temperature, "tools": [t["name"] for t in tools]}


def create_outbound_agent(
    llm_id: str,
    *,
    agent_name: str | None = None,
    voice_id: str = DEFAULT_VOICE_ID,
    language: str = LANGUAGE_LATAM_SPANISH,
    voice_speed: float = DEFAULT_VOICE_SPEED,
    base_url: str | None = None,
) -> dict[str, Any]:
    """The outbound agent. Same voice as inbound — it is the same Sofía."""
    url = (base_url or modal_url()).rstrip("/")
    name = agent_name or f"{config_value('agent.name', 'Sofía')} outbound"

    agent = _client().agent.create(
        response_engine={"type": "retell-llm", "llm_id": llm_id},
        agent_name=name,
        voice_id=voice_id,
        voice_speed=voice_speed,
        language=language,
        webhook_url=f"{url}/retell-webhook",
        end_call_after_silence_ms=DEFAULT_END_CALL_AFTER_SILENCE_MS,
        max_call_duration_ms=OUTBOUND_MAX_CALL_DURATION_MS,
    )
    LOG.info("Outbound agent created: %s (%s)", agent.agent_id, name)
    return {
        "agent_id": agent.agent_id,
        "agent_name": name,
        "voice_id": voice_id,
        "language": language,
        "llm_id": llm_id,
        "webhook_url": f"{url}/retell-webhook",
        "end_call_after_silence_ms": DEFAULT_END_CALL_AFTER_SILENCE_MS,
        "max_call_duration_ms": OUTBOUND_MAX_CALL_DURATION_MS,
    }


def update_outbound_llm(
    llm_id: str | None = None,
    *,
    base_url: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Push the current outbound prompt and tools onto the outbound LLM.

    Retell refuses to modify a PUBLISHED LLM ("Cannot update published LLM"),
    and offers no endpoint to branch a new version from one. So once the agent
    has been published, the only way forward is to mint a fresh LLM and repoint
    the agent at it. That path is taken automatically here, because the
    alternative is an installer that works before publishing and 400s after.

    Repointing leaves the agent on a NEW DRAFT version — it has to be published
    again for callers to hear the change.
    """
    target = llm_id or _require_env(
        "RETELL_OUTBOUND_LLM_ID", "Run provision_outbound() first, or pass llm_id."
    )
    prompt = load_prompt("outbound_prompt")

    unknown = validate_outbound_variables(prompt)
    if unknown:
        raise RetellServiceError(f"The outbound prompt uses unfilled dynamic variables: {unknown}")

    tools = build_custom_functions(base_url)
    client = _client()

    try:
        llm = client.llm.update(target, general_prompt=prompt, general_tools=tools)
        LOG.info("Outbound LLM %s updated in place (prompt=%s chars)", target, len(prompt))
        return {
            "llm_id": llm.llm_id,
            "prompt_chars": len(prompt),
            "tools": [t["name"] for t in tools],
            "replaced": False,
        }
    except Exception as exc:
        if "published" not in str(exc).lower():
            raise

    LOG.warning("Outbound LLM %s is published and immutable; creating a replacement", target)
    replacement = create_outbound_llm(base_url=base_url)
    new_llm_id = replacement["llm_id"]

    agent = agent_id or _require_env("RETELL_OUTBOUND_AGENT_ID", "Provision the outbound agent first.")
    client.agent.update(agent, response_engine={"type": "retell-llm", "llm_id": new_llm_id})
    _upsert_env_var("RETELL_OUTBOUND_LLM_ID", new_llm_id)

    LOG.info("Outbound agent %s repointed to new LLM %s", agent, new_llm_id)
    return {
        "llm_id": new_llm_id,
        "previous_llm_id": target,
        "prompt_chars": len(prompt),
        "tools": [t["name"] for t in tools],
        "replaced": True,
        "needs_publish": True,
    }


def provision_outbound(*, persist: bool = True) -> dict[str, Any]:
    """Create the outbound LLM and agent, and record both ids in .env."""
    try:
        llm = create_outbound_llm()
        agent = create_outbound_agent(llm["llm_id"])
    except GHLConfigError as exc:
        raise RetellServiceError(str(exc)) from exc

    if persist:
        _upsert_env_var("RETELL_OUTBOUND_LLM_ID", llm["llm_id"])
        _upsert_env_var("RETELL_OUTBOUND_AGENT_ID", agent["agent_id"])

    return {**agent, "model": llm["model"], "temperature": llm["temperature"], "tools": llm["tools"]}


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
