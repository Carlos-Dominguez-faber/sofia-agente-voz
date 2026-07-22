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

# Import the resource modules eagerly, at module load, on one thread.
#
# The SDK exposes `client.call`, `client.llm` and `client.agent` as lazy
# properties that import their module on first access. The dashboard loads its
# sections concurrently, so two worker threads hit two different first accesses
# at the same moment and deadlock on Python's import lock — the page then
# reports Retell as unavailable, which is a lie: Retell was fine, we never
# asked it anything.
#
# Doing the imports here means every lazy path is already resolved before any
# thread starts. Sequential tests never reproduce this; the first real page load
# does.
import retell.resources.agent  # noqa: F401  (import for its side effect)
import retell.resources.call  # noqa: F401
import retell.resources.llm  # noqa: F401
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


def publish_initial_version(agent_id: str) -> dict[str, Any]:
    """Publish a freshly created agent so it HAS a published version.

    A `create` leaves the agent on an unpublished draft (v0). Everything that
    reads or edits the live agent — `_latest_published_version`, and therefore
    the whole control panel — refuses to work without a published baseline
    ("has no published version to base a change on"). Provisioning has to leave
    that baseline behind, or the panel starts broken on a brand-new install.

    Idempotent: if a published version already exists, nothing is published.
    """
    versions = _versions_list(agent_id)
    published = [int(v["version"]) for v in versions if v.get("is_published")]
    if published:
        return {"agent_id": agent_id, "published_version": max(published), "created": False}

    # Publish the newest existing version — v0 on a fresh agent.
    target = max((int(v["version"]) for v in versions), default=0)
    _client().agent.publish(agent_id, version=target)
    LOG.info("Published initial version v%s of agent %s", target, agent_id)
    return {"agent_id": agent_id, "published_version": target, "created": True}


def provision_outbound(*, persist: bool = True, publish: bool = True) -> dict[str, Any]:
    """Create the outbound LLM and agent, and record both ids in .env."""
    try:
        llm = create_outbound_llm()
        agent = create_outbound_agent(llm["llm_id"])
    except GHLConfigError as exc:
        raise RetellServiceError(str(exc)) from exc

    if persist:
        _upsert_env_var("RETELL_OUTBOUND_LLM_ID", llm["llm_id"])
        _upsert_env_var("RETELL_OUTBOUND_AGENT_ID", agent["agent_id"])
        # Seed os.environ too: a later step in the same `all` run (the Twilio
        # binding check) reads the agent id from os.environ, and the env loader
        # never overrides a var already there. Writing only the file leaves the
        # verify comparing the new binding against the id loaded at startup. Same
        # reason `deploy` sets MODAL_URL in-process.
        os.environ["RETELL_OUTBOUND_LLM_ID"] = llm["llm_id"]
        os.environ["RETELL_OUTBOUND_AGENT_ID"] = agent["agent_id"]

    published = publish_initial_version(agent["agent_id"]) if publish else None

    return {
        **agent,
        "model": llm["model"],
        "temperature": llm["temperature"],
        "tools": llm["tools"],
        "published_version": (published or {}).get("published_version"),
    }


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


def provision_inbound(*, persist: bool = True, publish: bool = True) -> dict[str, Any]:
    """Creates the LLM and the agent, and records both ids in .env."""
    try:
        llm = create_inbound_llm()
        agent = create_inbound_agent(llm["llm_id"])
    except GHLConfigError as exc:  # config file problems surface the same way
        raise RetellServiceError(str(exc)) from exc

    if persist:
        _upsert_env_var("RETELL_INBOUND_LLM_ID", llm["llm_id"])
        _upsert_env_var("RETELL_INBOUND_AGENT_ID", agent["agent_id"])
        # Seed os.environ too — see provision_outbound: the Twilio verify reads
        # the expected agent id from os.environ within this same run.
        os.environ["RETELL_INBOUND_LLM_ID"] = llm["llm_id"]
        os.environ["RETELL_INBOUND_AGENT_ID"] = agent["agent_id"]

    published = publish_initial_version(agent["agent_id"]) if publish else None

    return {
        **agent,
        "model": llm["model"],
        "temperature": llm["temperature"],
        "tools": llm["tools"],
        "published_version": (published or {}).get("published_version"),
    }


# --------------------------------------------------------------------------
# Read side — what the dashboard asks Retell
#
# Retell owns the call history: durations, transcripts, which tools fired, and
# the prompt that is actually live. None of it is mirrored anywhere, so these
# are the only way to see what Sofía did.
# --------------------------------------------------------------------------


def _inbound_agent_id() -> str:
    return _require_env("RETELL_INBOUND_AGENT_ID", "Run provision_inbound() first.")


def _inbound_llm_id() -> str:
    return _require_env("RETELL_INBOUND_LLM_ID", "Run provision_inbound() first.")


def _as_dict(obj: Any) -> dict[str, Any]:
    """Normalize an SDK model into a plain dict the rest of the code can read."""
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump()
    return dict(obj) if hasattr(obj, "keys") else {"value": obj}


def _filter_criteria(
    *,
    agent_id: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    tool_name: str | None = None,
    tool_success: bool | None = None,
) -> dict[str, Any]:
    """Build one Retell filter. Always scoped to a single agent.

    A Retell account can host agents for several businesses, so an unscoped
    query would show one clinic another clinic's patients.
    """
    criteria: dict[str, Any] = {"agent": [{"agent_id": agent_id or _inbound_agent_id()}]}

    if start_ms is not None and end_ms is not None:
        criteria["start_timestamp"] = {"type": "range", "op": "bt", "value": [start_ms, end_ms]}

    if tool_name:
        tool_filter: dict[str, Any] = {"name": tool_name}
        if tool_success is not None:
            tool_filter["success"] = {"op": "eq", "type": "boolean", "value": tool_success}
        criteria["tool_calls"] = [tool_filter]

    return criteria


def _items_of(response: Any) -> list[Any]:
    """Read the rows out of a CallListResponse.

    The SDK returns an object with `.items`, not a bare list. Reading it wrongly
    does not raise — it yields nothing, and "nothing" is indistinguishable from
    "this clinic had no calls". That is exactly the false zero this project
    refuses to display, so an unrecognised shape is an error, never an empty
    list.
    """
    rows = getattr(response, "items", None)
    if rows is not None:
        return list(rows)
    if isinstance(response, list):
        return response
    raise RetellServiceError(
        f"Unexpected shape from Retell call.list: {type(response).__name__}. "
        "Expected `.items`. Refusing to report zero calls without knowing it is true."
    )


def list_calls_page(
    *,
    limit: int = 50,
    start_ms: int | None = None,
    end_ms: int | None = None,
    agent_id: str | None = None,
    pagination_key: str | None = None,
    tool_name: str | None = None,
    tool_success: bool | None = None,
) -> dict[str, Any]:
    """One page of calls, newest first, with the cursor for the next one.

    NOTE: list rows are a light payload — no transcript, no
    `transcript_with_tool_calls`. Anything that depends on what happened inside
    the call has to fetch it with `get_call`.
    """
    kwargs: dict[str, Any] = {
        "filter_criteria": _filter_criteria(
            agent_id=agent_id,
            start_ms=start_ms,
            end_ms=end_ms,
            tool_name=tool_name,
            tool_success=tool_success,
        ),
        "limit": limit,
        "sort_order": "descending",
    }
    if pagination_key:
        kwargs["pagination_key"] = pagination_key

    response = _client().call.list(**kwargs)
    return {
        "calls": [_as_dict(row) for row in _items_of(response)],
        "has_more": bool(getattr(response, "has_more", False)),
        "pagination_key": getattr(response, "pagination_key", None),
    }


def list_calls(**kwargs: Any) -> list[dict[str, Any]]:
    """One page of calls as a plain list, for callers that do not paginate."""
    return list_calls_page(**kwargs)["calls"]


def count_calls(
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    agent_id: str | None = None,
    tool_name: str | None = None,
    tool_success: bool | None = None,
) -> int:
    """Exact number of matching calls, counted by Retell rather than by us.

    Counting the length of a page would silently cap at the page size: a clinic
    with 300 calls would be told it had 100. `include_total` makes Retell do the
    counting, so the headline number stays true as volume grows.
    """
    response = _client().call.list(
        limit=1,
        include_total=True,
        filter_criteria=_filter_criteria(
            agent_id=agent_id,
            start_ms=start_ms,
            end_ms=end_ms,
            tool_name=tool_name,
            tool_success=tool_success,
        ),
    )
    total = getattr(response, "total", None)
    if total is None:
        raise RetellServiceError(
            "Retell did not return a total for this query. Refusing to guess a count "
            "that the clinic will read as fact."
        )
    return int(total)


def get_call(call_id: str) -> dict[str, Any]:
    """One call in full: transcript, tool calls, timings, cost."""
    if not call_id:
        raise RetellServiceError("call_id is required")
    return _as_dict(_client().call.retrieve(call_id))


def get_live_prompt(agent_id: str | None = None) -> str:
    """The prompt Retell is speaking on live calls — the PUBLISHED version.

    Reads the published LLM, not the latest draft. Before this pinned the
    published version, a draft left by a console edit could be reported as the
    live prompt, and the undo baseline would capture it — an undo that reverts to
    something no caller ever heard.
    """
    llm = _live_llm(agent_id or _inbound_agent_id())
    prompt = llm.get("general_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RetellServiceError("The live Retell LLM has no general_prompt set")
    return prompt


# ==========================================================================
# The control panel — versioned update + PUBLISH
#
# THE RULE THAT MAKES THE PANEL REAL, not decorative: every change the client
# saves must reach the LIVE phone number, not sit in a draft. Retell splits an
# agent (and its LLM) into versions, and `update` alone writes to a DRAFT — the
# number keeps serving the last PUBLISHED version. That is the V07/V09 bug: the
# prompt "saved", nothing changed on a real call.
#
# The model, validated against throwaway agents before a line of this was
# written:
#   - Agent and LLM versions are coupled. Publishing the agent publishes the
#     LLM it references, together.
#   - A published LLM is frozen: `llm.update` on it returns
#     400 "Cannot update published LLM". This is exactly what bit V09.
#   - `agent.create_version(base_version=N)` spawns a fresh agent draft AND a
#     coupled, editable LLM draft. This is the escape hatch V09 never found —
#     no need to create a new agent, so no orphans.
#   - Write param is `model_temperature`; it reads back as `api_model_temperature`.
#
# The flow, idempotent and orphan-free:
#   base on the latest PUBLISHED version → create_version → update draft →
#   publish. Basing on PUBLISHED (never a dangling draft) means a half-finished
#   draft left in the console is never shipped live as a side effect.
# ==========================================================================


def _agent_llm_id(agent_id: str) -> str:
    """The stable LLM id an agent's response engine points at (same across versions)."""
    engine = _as_dict(_client().agent.retrieve(agent_id)).get("response_engine") or {}
    llm_id = engine.get("llm_id")
    if not llm_id:
        raise RetellServiceError(f"Agent {agent_id} has no retell-llm response engine")
    return str(llm_id)


def _versions_list(agent_id: str) -> list[dict[str, Any]]:
    """All versions of an agent. The SDK returns either a list or a `.versions` wrapper."""
    resp = _client().agent.get_versions(agent_id)
    raw = getattr(resp, "versions", None)
    if raw is None:
        raw = resp if isinstance(resp, list) else []
    return [_as_dict(v) for v in raw]


def _latest_published_version(agent_id: str) -> int:
    """Highest version number that is currently published — what the number serves.

    Deliberately ignores any newer unpublished draft. Building the next version
    on top of the published one is what keeps mystery draft content from ever
    reaching a live call.
    """
    published = [int(v["version"]) for v in _versions_list(agent_id) if v.get("is_published")]
    if not published:
        raise RetellServiceError(
            f"Agent {agent_id} has no published version to base a change on. "
            "It must be published once before the panel can edit it."
        )
    return max(published)


def _published_agent(agent_id: str) -> dict[str, Any]:
    """The agent as the phone number actually serves it — its latest PUBLISHED version.

    `agent.retrieve` without a version returns the latest version, which is a
    DRAFT whenever one exists — e.g. after the agency edits latency knobs in the
    Retell console. A read that claims to report what callers hear MUST pin the
    published version, or the panel shows a draft that is not live and the undo
    baseline captures a prompt nobody is speaking.
    """
    version = _latest_published_version(agent_id)
    return _as_dict(_client().agent.retrieve(agent_id, version=version))


def _live_llm(agent_id: str) -> dict[str, Any]:
    """The LLM version the PUBLISHED agent references — the prompt actually spoken."""
    agent = _published_agent(agent_id)
    engine = agent.get("response_engine") or {}
    llm_id = engine.get("llm_id")
    llm_version = engine.get("version")
    if not llm_id or llm_version is None:
        raise RetellServiceError(f"Published agent {agent_id} has no LLM reference")
    return _as_dict(_client().llm.retrieve(llm_id, version=int(float(llm_version))))


def publish_agent_change(
    agent_id: str,
    *,
    voice_id: str | None = None,
    voice_speed: float | None = None,
    voice_temperature: float | None = None,
    llm_temperature: float | None = None,
    llm_general_prompt: str | None = None,
) -> dict[str, Any]:
    """Apply a change to one agent (and/or its LLM) and PUBLISH it live.

    Any subset of the fields may be given. Voice fields land on the agent; the
    temperature and prompt land on the coupled LLM. Whatever is passed ends up
    on the live phone number, not in a draft.
    """
    published = _latest_published_version(agent_id)

    # A fresh draft (agent + coupled LLM) built from the published version.
    draft = _as_dict(_client().agent.create_version(agent_id, base_version=published))
    draft_version = int(draft["version"])

    llm_id = _agent_llm_id(agent_id)

    # LLM edits go on the coupled draft that create_version just spawned.
    llm_fields: dict[str, Any] = {}
    if llm_temperature is not None:
        llm_fields["model_temperature"] = llm_temperature
    if llm_general_prompt is not None:
        llm_fields["general_prompt"] = llm_general_prompt
    if llm_fields:
        _client().llm.update(llm_id, **llm_fields)

    # Voice edits go on the agent draft (targets the latest draft, i.e. this one).
    agent_fields: dict[str, Any] = {}
    if voice_id is not None:
        agent_fields["voice_id"] = voice_id
    if voice_speed is not None:
        agent_fields["voice_speed"] = voice_speed
    if voice_temperature is not None:
        agent_fields["voice_temperature"] = voice_temperature
    if agent_fields:
        _client().agent.update(agent_id, **agent_fields)

    # Publish makes the draft — agent and coupled LLM — the live version.
    _client().agent.publish(agent_id, version=draft_version)

    LOG.info(
        "Published agent %s v%s (voice=%s llm=%s)",
        agent_id,
        draft_version,
        list(agent_fields),
        list(llm_fields),
    )
    return {
        "agent_id": agent_id,
        "published_version": draft_version,
        "based_on": published,
        "voice_fields": list(agent_fields),
        "llm_fields": list(llm_fields),
    }


def set_live_prompt(prompt: str, *, agent_id: str | None = None) -> dict[str, Any]:
    """Publish an edited prompt to the LIVE inbound number.

    This routes through publish_agent_change on purpose. The old version only
    called `llm.update`, which wrote to a draft — so an edit from the panel
    never reached a real call. Only the inbound agent is touched: the outbound
    agent runs a different prompt of its own.
    """
    if not prompt or not prompt.strip():
        raise RetellServiceError("Refusing to publish an empty prompt")

    target_agent = agent_id or _inbound_agent_id()
    result = publish_agent_change(target_agent, llm_general_prompt=prompt)
    return {
        "agent_id": target_agent,
        "published_version": result["published_version"],
        "prompt_chars": len(prompt),
    }


# --------------------------------------------------------------------------
# The knobs the panel is allowed to turn — everything bounded
#
# A clinic owner cannot break Sofía from the panel. The voice is picked from a
# curated es-419 list, the speed is clamped, the behaviour is three named
# presets (never a raw temperature), and the latency / turn-taking knobs are not
# here at all — those stay with the agency in the Retell console.
# --------------------------------------------------------------------------

# Curated voices, verified present on the account. All Mexican-accent, female —
# Sofía is a female receptionist throughout the course; changing gender is niche
# customization (/customize), not a client-facing panel control.
CURATED_VOICES: list[dict[str, str]] = [
    {"voice_id": "retell-Andrea", "label": "Andrea", "note": "La voz actual"},
    {"voice_id": "retell-Gaby", "label": "Gaby", "note": "Cálida"},
    {"voice_id": "retell-Claudia", "label": "Claudia", "note": "Clara"},
    {"voice_id": "cartesia-Sofia", "label": "Sofía", "note": "Suave"},
    {"voice_id": "11labs-Andrea", "label": "Andrea (ElevenLabs)", "note": "Natural"},
]
_CURATED_VOICE_IDS = frozenset(v["voice_id"] for v in CURATED_VOICES)

VOICE_SPEED_MIN = 0.85
VOICE_SPEED_MAX = 1.15

# Behaviour, as the client sees it: three names, never the number underneath.
# Capped at 0.5 — above that Sofía starts improvising in ways a clinic will not want.
BEHAVIOUR_PRESETS: dict[str, float] = {"estricta": 0.2, "balanceada": 0.35, "flexible": 0.5}
BEHAVIOUR_TEMP_MAX = 0.5

# Expressiveness toggle -> the agent's voice_temperature (voice variation).
EXPRESSIVENESS_ON = 1.1
EXPRESSIVENESS_OFF = 0.5


def _outbound_agent_id() -> str | None:
    """The outbound agent id, or None on an inbound-only install."""
    return (os.environ.get("RETELL_OUTBOUND_AGENT_ID") or "").strip() or None


def _managed_agents() -> list[tuple[str, str]]:
    """(label, agent_id) for every agent the panel keeps in sync.

    Both agents get the same voice and behaviour so a call sounds the same
    whether Sofía dialled out or the patient dialled in. The outbound agent is
    included only if the install has one.
    """
    agents = [("inbound", _inbound_agent_id())]
    outbound = _outbound_agent_id()
    if outbound:
        agents.append(("outbound", outbound))
    return agents


def _nearest_preset(temperature: float | None) -> str | None:
    """Map a raw temperature back to the closest preset name for display."""
    if temperature is None:
        return None
    return min(BEHAVIOUR_PRESETS, key=lambda name: abs(BEHAVIOUR_PRESETS[name] - temperature))


def current_agent_config() -> dict[str, Any]:
    """What the panel shows as the current state — read from the inbound agent.

    The inbound agent is the reference; the two are kept in sync, so reading one
    is enough. Temperature is mapped back to a preset name and voice_temperature
    to the expressiveness toggle, so the panel never has to know the numbers.

    Reads the PUBLISHED version — what callers actually hear — not the latest
    draft a console edit may have left behind.
    """
    agent = _published_agent(_inbound_agent_id())
    llm = _live_llm(_inbound_agent_id())
    temperature = llm.get("api_model_temperature")
    voice_temp = agent.get("voice_temperature")

    return {
        "voice_id": agent.get("voice_id"),
        "voice_speed": agent.get("voice_speed"),
        "expressiveness": (voice_temp or 0) >= EXPRESSIVENESS_ON if voice_temp is not None else None,
        "behaviour": _nearest_preset(temperature),
        "curated_voices": CURATED_VOICES,
        "speed_min": VOICE_SPEED_MIN,
        "speed_max": VOICE_SPEED_MAX,
        "presets": list(BEHAVIOUR_PRESETS),
        "synced_agents": [label for label, _ in _managed_agents()],
    }


def apply_agent_config(
    *,
    voice_id: str | None = None,
    voice_speed: float | None = None,
    expressiveness: bool | None = None,
    behaviour: str | None = None,
) -> dict[str, Any]:
    """Validate, then push + PUBLISH a config change to every managed agent.

    Bounds are enforced HERE, in the backend — never trusting the front. An
    out-of-range value raises before anything touches Retell.
    """
    # --- Validate every field before mutating a single agent. ---
    if voice_id is not None and voice_id not in _CURATED_VOICE_IDS:
        raise ValueError(f"voice_id `{voice_id}` is not in the curated list {sorted(_CURATED_VOICE_IDS)}")
    if voice_speed is not None and not (VOICE_SPEED_MIN <= voice_speed <= VOICE_SPEED_MAX):
        raise ValueError(f"voice_speed must be within [{VOICE_SPEED_MIN}, {VOICE_SPEED_MAX}], got {voice_speed}")
    if behaviour is not None and behaviour not in BEHAVIOUR_PRESETS:
        raise ValueError(f"behaviour must be one of {list(BEHAVIOUR_PRESETS)}, got `{behaviour}`")

    llm_temperature = BEHAVIOUR_PRESETS[behaviour] if behaviour else None
    if llm_temperature is not None and llm_temperature > BEHAVIOUR_TEMP_MAX:
        raise ValueError(f"resolved temperature {llm_temperature} exceeds the cap {BEHAVIOUR_TEMP_MAX}")
    voice_temperature = None
    if expressiveness is not None:
        voice_temperature = EXPRESSIVENESS_ON if expressiveness else EXPRESSIVENESS_OFF

    if voice_id is None and voice_speed is None and voice_temperature is None and llm_temperature is None:
        raise ValueError("No changes to apply")

    # --- Apply to every managed agent. Report each; never claim a false save. ---
    results = []
    failures = []
    for label, agent_id in _managed_agents():
        try:
            outcome = publish_agent_change(
                agent_id,
                voice_id=voice_id,
                voice_speed=voice_speed,
                voice_temperature=voice_temperature,
                llm_temperature=llm_temperature,
            )
            results.append({"agent": label, **outcome})
        except Exception as exc:  # noqa: BLE001 - collected, not swallowed
            LOG.error("Config publish failed for %s agent %s: %s", label, agent_id, exc)
            failures.append({"agent": label, "error": str(exc)})

    if failures:
        # Partial failure is honest: say which agents are now on the new config
        # and which are not, rather than reporting a clean save that desynced them.
        raise RetellServiceError(
            f"Config published to {[r['agent'] for r in results]} but FAILED on "
            f"{[f['agent'] for f in failures]}: {failures}. The agents may be out of sync."
        )

    return {"applied": results, "behaviour": behaviour, "voice_id": voice_id}


def start_outbound_call(to_number: str, *, from_number: str | None = None) -> dict[str, Any]:
    """Place a call from the clinic's number to a patient. E.164 on both ends."""
    origin = from_number or _require_env("TWILIO_PHONE_NUMBER", "The clinic's number, in E.164.")
    agent_id = (os.environ.get("RETELL_OUTBOUND_AGENT_ID") or "").strip() or _inbound_agent_id()

    call = _client().call.create_phone_call(
        from_number=origin,
        to_number=to_number,
        override_agent_id=agent_id,
    )
    result = _as_dict(call)
    LOG.info("Outbound call started: %s -> %s (call_id=%s)", origin, to_number, result.get("call_id"))
    return {
        "call_id": result.get("call_id"),
        "agent_id": agent_id,
        "from_number": origin,
        "to_number": to_number,
        "call_status": result.get("call_status"),
    }


def inbound_agent_configured() -> bool:
    """True when .env already carries an inbound agent id (i.e. provision has run)."""
    _load_env_file()
    return bool((os.environ.get("RETELL_INBOUND_AGENT_ID") or "").strip())


def test_api_key() -> dict[str, Any]:
    """Prove the API key itself works, WITHOUT assuming any agent exists yet.

    On a fresh install the agents do not exist until `provision` creates them,
    and `provision` runs after validation. A validator that demands an agent id
    turns the install into a chicken-and-egg deadlock, so key-only is the check
    that can honestly run first.
    """
    client = _client()
    try:
        agents = client.agent.list()
    except TypeError:  # older SDKs want an explicit page size
        agents = client.agent.list(limit=1)
    return {"ok": True, "agent_count": len(_items_of(agents))}


def test_connection() -> dict[str, Any]:
    """Can we reach Retell with these credentials, and is the agent still there?"""
    agent_id = _inbound_agent_id()
    agent = _as_dict(_client().agent.retrieve(agent_id))
    return {
        "ok": True,
        "agent_id": agent_id,
        "agent_name": agent.get("agent_name"),
        "voice_id": agent.get("voice_id"),
        "language": agent.get("language"),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    result = provision_inbound()
    for key, value in result.items():
        print(f"{key}: {value}")
