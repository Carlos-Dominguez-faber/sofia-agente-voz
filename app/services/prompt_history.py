"""One previous prompt, kept so a bad edit can be undone in one click.

THIS IS THE ONE PLACE THE BACKEND KEEPS STATE OF ITS OWN, and it is a deliberate
exception to the rule that it keeps none. The reasoning, so nobody undoes it by
accident later:

  - The rule exists to stop the backend becoming a second, stale copy of the
    CRM. Contacts, appointments and pipeline all live in GoHighLevel, and
    mirroring them locally would create two answers to the same question.
  - This is not CRM data. It is one string belonging to Retell's own domain,
    and Retell does not version prompts.
  - What it buys: the prompt carries Sofía's medical safety guardrails. If a
    client publishes an edit that breaks the agent, "restore the previous
    version" has to be one click, not a support ticket while a live phone line
    keeps answering patients badly.

Exactly one version is kept. This is an undo button, not a history feature — a
growing archive of prompts is the kind of state that turns into a migration.

On Modal it persists in a `modal.Dict`. Locally, and if Modal is unavailable, it
degrades to process memory and says so, so nobody trusts an undo that a cold
start already threw away.
"""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger(__name__)

DICT_NAME = "sofia-prompt-history"
_PREVIOUS_KEY = "previous_prompt"
_SAVED_AT_KEY = "previous_saved_at"

# Fallback when Modal is not reachable. Lives and dies with the container.
_memory: dict[str, Any] = {}


def _store() -> tuple[Any, bool]:
    """The backing store and whether it actually survives a restart."""
    try:
        import modal

        return modal.Dict.from_name(DICT_NAME, create_if_missing=True), True
    except Exception as exc:  # noqa: BLE001 - any Modal problem falls back to memory
        LOG.debug("Modal Dict unavailable (%s); using in-process memory", exc)
        return _memory, False


def save_previous(prompt: str, *, saved_at: str) -> bool:
    """Record the prompt being replaced. Returns whether it will survive a restart."""
    store, durable = _store()
    try:
        store[_PREVIOUS_KEY] = prompt
        store[_SAVED_AT_KEY] = saved_at
    except Exception as exc:  # noqa: BLE001 - never let the undo buffer block a save
        LOG.error("Could not record the previous prompt: %s", exc)
        return False
    return durable


def load_previous() -> dict[str, Any]:
    """The stored prompt, or an explicit 'nothing to restore'."""
    store, durable = _store()
    try:
        prompt = store[_PREVIOUS_KEY] if _PREVIOUS_KEY in store else None
        saved_at = store[_SAVED_AT_KEY] if _SAVED_AT_KEY in store else None
    except Exception as exc:  # noqa: BLE001
        LOG.error("Could not read the previous prompt: %s", exc)
        prompt, saved_at = None, None

    return {
        "available": bool(prompt),
        "prompt": prompt,
        "saved_at": saved_at,
        "durable": durable,
    }
