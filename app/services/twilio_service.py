"""Twilio integration layer — the phone number and the SIP path into Retell.

Twilio owns the number; Retell owns the conversation. This module is the wire
between them, and it is written to be re-run by an installer for the next
client, not executed once by hand.

The call path, inbound:

    caller ──▶ Twilio number ──▶ Elastic SIP Trunk ──▶ sip.retellai.com ──▶ Sofía

Three Twilio objects have to exist, in this order:

  1. The trunk           — the SIP container, reachable at <prefix>.pstn.twilio.com
  2. The origination URI — where the trunk sends INBOUND calls (Retell's SBC)
  3. The IP access list  — who is allowed to send calls OUT through the trunk
                           (Retell's SBC, so outbound works later)

...and then the number is attached to the trunk, which is what actually takes it
off the demo TwiML app. Finally Retell imports the number and binds it to the
inbound agent.

Every function is idempotent: re-running against an already-connected account
finds the existing object instead of creating a duplicate. That matters because
a duplicate trunk is not a harmless leftover — a number can only live on one
trunk, and the second one silently owns nothing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.services.ghl_service import _load_env_file, config_value
from app.services.retell_service import RetellServiceError
from app.services.retell_service import _client as _retell_client

LOG = logging.getLogger(__name__)


# Retell's SIP signalling address. Inbound calls from Twilio are pointed here.
# Confirmed against docs.retellai.com/deploy/twilio — re-verify before changing:
# this single string is the difference between a ringing phone and a dead line.
RETELL_ORIGINATION_URI = "sip:sip.retellai.com"

# Retell's SBC CIDR. Whitelisting it is what lets Retell place calls OUT through
# this trunk. The alternative is credential auth; IP auth is preferred here
# because it adds no secret to store, rotate or leak.
RETELL_SIP_CIDR = "18.98.16.120/30"
RETELL_SIP_CIDR_NETWORK, RETELL_SIP_CIDR_PREFIX = RETELL_SIP_CIDR.split("/")

# Twilio requires the trunk domain to be globally unique across ALL accounts,
# not just yours. A generic prefix like "sofia" is already taken by someone.
DEFAULT_TRUNK_PREFIX = "sofia-voz"


class TwilioServiceError(RuntimeError):
    """Something in the Twilio <-> Retell wiring failed."""


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def _require_env(name: str, hint: str) -> str:
    _load_env_file()
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise TwilioServiceError(f"Environment variable {name} is not set. {hint}")
    return value


def _client() -> Client:
    return Client(
        _require_env("TWILIO_ACCOUNT_SID", "Find it on the Twilio console dashboard."),
        _require_env("TWILIO_AUTH_TOKEN", "Find it on the Twilio console dashboard."),
    )


def phone_number() -> str:
    """The number being connected, in E.164."""
    value = _require_env("TWILIO_PHONE_NUMBER", "Use E.164 format, e.g. +529983871006.")
    if not value.startswith("+"):
        raise TwilioServiceError(
            f"TWILIO_PHONE_NUMBER must be E.164 (leading + and country code), got `{value}`"
        )
    return value


def _trunk_domain(prefix: str) -> str:
    return f"{prefix}.pstn.twilio.com"


# --------------------------------------------------------------------------
# Step 1 — the trunk
# --------------------------------------------------------------------------


def find_trunk(domain_name: str) -> Any | None:
    """Return the trunk with this domain, or None. Keeps creation idempotent."""
    for trunk in _client().trunking.v1.trunks.list(limit=50):
        if trunk.domain_name == domain_name:
            return trunk
    return None


def create_trunk(
    *,
    friendly_name: str | None = None,
    prefix: str = DEFAULT_TRUNK_PREFIX,
) -> dict[str, Any]:
    """Create the Elastic SIP Trunk, or return the existing one.

    The domain must be unique across all of Twilio, so a collision here is a
    naming problem, not a permissions problem — the error says so explicitly.
    """
    domain = _trunk_domain(prefix)
    name = friendly_name or f"{config_value('business.name', 'Sofía')} — Retell"

    existing = find_trunk(domain)
    if existing:
        LOG.info("Trunk already exists: %s (%s)", existing.sid, domain)
        return {"sid": existing.sid, "domain_name": domain, "friendly_name": existing.friendly_name, "created": False}

    try:
        trunk = _client().trunking.v1.trunks.create(friendly_name=name, domain_name=domain)
    except TwilioRestException as exc:
        if exc.status == 400 and "already in use" in str(exc.msg).lower():
            raise TwilioServiceError(
                f"The SIP domain `{domain}` is already taken by another Twilio account. "
                f"Twilio requires it to be globally unique. Pass a different prefix, "
                f"e.g. create_trunk(prefix='{prefix}-mx')."
            ) from exc
        raise TwilioServiceError(f"Could not create the trunk: {exc.msg or exc}") from exc

    LOG.info("Trunk created: %s (%s)", trunk.sid, domain)
    return {"sid": trunk.sid, "domain_name": domain, "friendly_name": name, "created": True}


# --------------------------------------------------------------------------
# Step 2 — origination: inbound calls leave Twilio towards Retell
# --------------------------------------------------------------------------


def configure_origination(trunk_sid: str, *, uri: str = RETELL_ORIGINATION_URI) -> dict[str, Any]:
    """Point the trunk's inbound calls at Retell's SBC.

    Without this the number rings into the trunk and dies there: Twilio has
    nowhere to hand the call, and the caller hears silence and a hangup.
    """
    client = _client()
    for existing in client.trunking.v1.trunks(trunk_sid).origination_urls.list(limit=20):
        if existing.sip_url == uri:
            if not existing.enabled:
                client.trunking.v1.trunks(trunk_sid).origination_urls(existing.sid).update(enabled=True)
                LOG.info("Origination URL %s re-enabled", existing.sid)
            return {"sid": existing.sid, "sip_url": uri, "created": False}

    try:
        origination = client.trunking.v1.trunks(trunk_sid).origination_urls.create(
            friendly_name="Retell SBC",
            sip_url=uri,
            weight=10,
            priority=10,
            enabled=True,
        )
    except TwilioRestException as exc:
        raise TwilioServiceError(f"Could not add the origination URI `{uri}`: {exc.msg or exc}") from exc

    LOG.info("Origination configured: %s -> %s", trunk_sid, uri)
    return {"sid": origination.sid, "sip_url": uri, "created": True}


# --------------------------------------------------------------------------
# Step 3 — termination auth: Retell is allowed to call OUT through this trunk
# --------------------------------------------------------------------------


def authorize_retell_ips(trunk_sid: str, *, cidr: str = RETELL_SIP_CIDR) -> dict[str, Any]:
    """Whitelist Retell's SBC on the trunk, so outbound calls are accepted.

    Inbound does not need this. It is configured now anyway because the outbound
    worker is part of this system, and discovering the gap at 9am when the cron
    starts dialling no-shows is a worse way to find out.
    """
    client = _client()
    network, prefix = cidr.split("/")
    list_name = "Retell SBC"

    acl = next(
        (a for a in client.sip.ip_access_control_lists.list(limit=50) if a.friendly_name == list_name),
        None,
    )
    created = False
    if acl is None:
        acl = client.sip.ip_access_control_lists.create(friendly_name=list_name)
        created = True
        LOG.info("IP access control list created: %s", acl.sid)

    addresses = client.sip.ip_access_control_lists(acl.sid).ip_addresses.list(limit=50)
    if not any(a.ip_address == network and str(a.cidr_prefix_length) == prefix for a in addresses):
        client.sip.ip_access_control_lists(acl.sid).ip_addresses.create(
            friendly_name="Retell SBC range",
            ip_address=network,
            cidr_prefix_length=int(prefix),
        )
        LOG.info("Whitelisted %s on ACL %s", cidr, acl.sid)

    # Attaching an already-attached ACL is a 400, not a no-op.
    attached = client.trunking.v1.trunks(trunk_sid).ip_access_control_lists.list(limit=20)
    if not any(a.sid == acl.sid for a in attached):
        client.trunking.v1.trunks(trunk_sid).ip_access_control_lists.create(ip_access_control_list_sid=acl.sid)
        LOG.info("ACL %s attached to trunk %s", acl.sid, trunk_sid)

    return {"acl_sid": acl.sid, "cidr": cidr, "created": created}


# --------------------------------------------------------------------------
# Step 4 — the number joins the trunk
# --------------------------------------------------------------------------


def attach_number_to_trunk(trunk_sid: str, *, number: str | None = None) -> dict[str, Any]:
    """Move the number onto the trunk.

    This is the switch that takes the number off whatever TwiML app or webhook
    it was pointing at. A number lives on exactly one trunk.
    """
    client = _client()
    target = number or phone_number()

    matches = client.incoming_phone_numbers.list(phone_number=target, limit=5)
    if not matches:
        raise TwilioServiceError(
            f"The number {target} is not in this Twilio account. Check TWILIO_PHONE_NUMBER "
            f"and TWILIO_ACCOUNT_SID point at the same account."
        )
    incoming = matches[0]

    if incoming.trunk_sid == trunk_sid:
        LOG.info("Number %s already attached to trunk %s", target, trunk_sid)
        return {"sid": incoming.sid, "phone_number": target, "trunk_sid": trunk_sid, "moved": False}

    previous = incoming.trunk_sid
    updated = client.incoming_phone_numbers(incoming.sid).update(trunk_sid=trunk_sid)
    LOG.info("Number %s attached to trunk %s (was %s)", target, trunk_sid, previous or "no trunk")
    return {
        "sid": updated.sid,
        "phone_number": target,
        "trunk_sid": trunk_sid,
        "moved": True,
        "previous_trunk_sid": previous,
    }


# --------------------------------------------------------------------------
# Step 5 — Retell imports the number and binds the inbound agent
# --------------------------------------------------------------------------


def _retell_numbers() -> list[Any]:
    """Every phone number registered in Retell.

    The v4 SDK returns a paginated PhoneNumberListResponse, not a list. Iterating
    it directly yields (field_name, value) tuples off the pydantic model, which
    fails as a silent-looking AttributeError further down. Older SDK versions
    returned a plain list, so both shapes are handled.
    """
    response = _retell_client().phone_number.list()
    items = getattr(response, "items", None)
    if items is not None:
        return list(items)
    return list(response) if isinstance(response, list) else []


def _find_retell_number(target: str) -> Any | None:
    return next((p for p in _retell_numbers() if getattr(p, "phone_number", None) == target), None)


def import_number_into_retell(
    *,
    termination_uri: str,
    number: str | None = None,
    inbound_agent_id: str | None = None,
    nickname: str | None = None,
) -> dict[str, Any]:
    """Register the number in Retell and point inbound calls at Sofía.

    Retell keys phone numbers by the number itself, so a re-import of an already
    imported number is a 4xx. That case is handled as an update, which is what
    the caller actually meant.
    """
    target = number or phone_number()
    agent_id = inbound_agent_id or _require_env(
        "RETELL_INBOUND_AGENT_ID", "Run provision_inbound() in retell_service first."
    )
    label = nickname or f"{config_value('business.name', 'Sofía')} — inbound"
    client = _retell_client()

    existing = _find_retell_number(target)
    if existing:
        LOG.info("Number %s already in Retell; updating the inbound agent binding", target)
        updated = client.phone_number.update(target, inbound_agents=[{"agent_id": agent_id, "weight": 1}])
        return {
            "phone_number": target,
            "inbound_agent_id": agent_id,
            "termination_uri": getattr(updated, "termination_uri", termination_uri),
            "created": False,
        }

    try:
        client.phone_number.import_(
            phone_number=target,
            termination_uri=termination_uri,
            inbound_agents=[{"agent_id": agent_id, "weight": 1}],
            nickname=label,
        )
    except Exception as exc:  # the SDK raises provider-specific errors
        raise RetellServiceError(
            f"Retell rejected the import of {target} with termination_uri `{termination_uri}`: {exc}"
        ) from exc

    LOG.info("Number %s imported into Retell, inbound agent %s", target, agent_id)
    return {
        "phone_number": target,
        "inbound_agent_id": agent_id,
        "termination_uri": termination_uri,
        "nickname": label,
        "created": True,
    }


# --------------------------------------------------------------------------
# Verification — read back from BOTH APIs
# --------------------------------------------------------------------------


def bind_outbound_agent(
    *,
    number: str | None = None,
    outbound_agent_id: str | None = None,
) -> dict[str, Any]:
    """Let Sofía place calls FROM this number, not just answer on it.

    Inbound and outbound are two separate bindings on the same number. Importing
    the number binds inbound only; without this the outbound worker can dial but
    Retell has no agent to attach to the call it just placed.
    """
    target = number or phone_number()
    agent_id = outbound_agent_id or _require_env(
        "RETELL_OUTBOUND_AGENT_ID", "Run provision_outbound() in retell_service first."
    )

    if _find_retell_number(target) is None:
        raise TwilioServiceError(
            f"{target} is not imported in Retell yet. Run connect_number_to_retell() first."
        )

    _retell_client().phone_number.update(
        target, outbound_agents=[{"agent_id": agent_id, "weight": 1}]
    )
    LOG.info("Outbound agent %s bound to %s", agent_id, target)
    return {"phone_number": target, "outbound_agent_id": agent_id}


def verify_connection(*, number: str | None = None) -> dict[str, Any]:
    """Read the live state from Twilio and Retell and judge whether it is wired.

    Deliberately reads instead of trusting the POSTs: a 200 on create says the
    object was accepted, not that the call path resolves end to end.
    """
    target = number or phone_number()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    client = _client()
    matches = client.incoming_phone_numbers.list(phone_number=target, limit=5)
    if not matches:
        check("number_in_account", False, f"{target} not found in this Twilio account")
        return {"ok": False, "phone_number": target, "checks": checks}

    incoming = matches[0]
    check("number_in_account", True, f"{target} ({incoming.sid})")

    trunk_sid = incoming.trunk_sid
    check("number_on_trunk", bool(trunk_sid), trunk_sid or "the number is not attached to any trunk")

    if trunk_sid:
        trunk = client.trunking.v1.trunks(trunk_sid).fetch()
        check("trunk_exists", True, f"{trunk.friendly_name} ({trunk.domain_name})")

        origination = client.trunking.v1.trunks(trunk_sid).origination_urls.list(limit=20)
        retell_uris = [o for o in origination if "retellai.com" in (o.sip_url or "")]
        enabled = [o for o in retell_uris if o.enabled]
        check(
            "origination_to_retell",
            bool(enabled),
            ", ".join(f"{o.sip_url} (enabled={o.enabled})" for o in retell_uris) or "no Retell origination URI",
        )

        acls = client.trunking.v1.trunks(trunk_sid).ip_access_control_lists.list(limit=20)
        check("termination_auth", bool(acls), ", ".join(a.friendly_name for a in acls) or "no IP ACL attached")

    registered = _find_retell_number(target)
    if registered is None:
        check("number_in_retell", False, f"{target} is not imported in Retell")
        return {"ok": all(c["ok"] for c in checks), "phone_number": target, "checks": checks}

    # The termination URI is nested under sip_outbound_trunk_config, not flat.
    trunk_config = getattr(registered, "sip_outbound_trunk_config", None)
    termination = getattr(trunk_config, "termination_uri", None) or "not set"
    check("number_in_retell", True, f"termination_uri={termination}")

    bound = _agent_ids(registered, "inbound_agents", "inbound_agent_id")
    expected = (os.environ.get("RETELL_INBOUND_AGENT_ID") or "").strip()
    check(
        "inbound_agent_bound",
        bool(bound) and (not expected or expected in bound),
        f"bound={bound or 'none'} expected={expected or '(unset)'}",
    )

    # Outbound is optional: a client can run inbound-only. It is reported either
    # way so the installer never has to guess whether it was configured.
    outbound = _agent_ids(registered, "outbound_agents", "outbound_agent_id")
    expected_out = (os.environ.get("RETELL_OUTBOUND_AGENT_ID") or "").strip()
    if expected_out:
        check(
            "outbound_agent_bound",
            expected_out in outbound,
            f"bound={outbound or 'none'} expected={expected_out}",
        )
    else:
        check("outbound_agent_bound", True, "not configured (inbound-only install)")

    return {"ok": all(c["ok"] for c in checks), "phone_number": target, "checks": checks}


def _agent_ids(registered: Any, list_field: str, legacy_field: str) -> list[str]:
    """Pull agent ids out of whichever shape the Retell SDK returns."""
    ids: list[str] = []
    for agent in getattr(registered, list_field, None) or []:
        agent_id = getattr(agent, "agent_id", None) or (agent.get("agent_id") if isinstance(agent, dict) else None)
        if agent_id:
            ids.append(str(agent_id))
    # Older single-agent field, still returned by some API versions.
    legacy = getattr(registered, legacy_field, None)
    if legacy and str(legacy) not in ids:
        ids.append(str(legacy))
    return ids


def agent_publication_status(agent_id: str | None = None) -> dict[str, Any]:
    """Whether the bound agent's changes are live or still sitting in a draft.

    An unpublished agent is the quietest failure in this whole chain: the number
    connects, the phone rings, Sofía answers — with the last published prompt,
    not the one in the repo.
    """
    target = agent_id or _require_env("RETELL_INBOUND_AGENT_ID", "Provision the agent first.")
    agent = _retell_client().agent.retrieve(target)
    return {
        "agent_id": target,
        "agent_name": getattr(agent, "agent_name", None),
        "version": getattr(agent, "version", None),
        "is_published": getattr(agent, "is_published", None),
        "voice_id": getattr(agent, "voice_id", None),
        "end_call_after_silence_ms": getattr(agent, "end_call_after_silence_ms", None),
    }


# --------------------------------------------------------------------------
# The whole thing, in order
# --------------------------------------------------------------------------


def connect_number_to_retell(
    *,
    prefix: str = DEFAULT_TRUNK_PREFIX,
    number: str | None = None,
    inbound_agent_id: str | None = None,
) -> dict[str, Any]:
    """Wire Twilio to Retell end to end. Safe to re-run for the next client.

    Returns the verification result, not the create responses — what matters is
    the state that is actually live, not the calls that were accepted.
    """
    target = number or phone_number()
    LOG.info("Connecting %s to Retell", target)

    trunk = create_trunk(prefix=prefix)
    origination = configure_origination(trunk["sid"])
    acl = authorize_retell_ips(trunk["sid"])
    attached = attach_number_to_trunk(trunk["sid"], number=target)
    imported = import_number_into_retell(
        termination_uri=trunk["domain_name"],
        number=target,
        inbound_agent_id=inbound_agent_id,
    )

    verification = verify_connection(number=target)
    return {
        "trunk": trunk,
        "origination": origination,
        "acl": acl,
        "number": attached,
        "retell": imported,
        "verification": verification,
        "agent": agent_publication_status(inbound_agent_id),
    }


def test_connection() -> dict[str, Any]:
    """A cheap liveness check for the dashboard's /services/status.

    `verify_connection` above reads the whole SIP wiring — trunk, origination,
    ACLs, the Retell import — which is the right thing when installing a number
    but far too heavy to run on every status refresh in the panel. This answers
    only the two questions the status card needs: are the credentials valid, and
    is the clinic's number still on the account? A number that quietly left the
    account is a phone that rings nowhere, invisible until a patient complains.
    """
    account_sid = _require_env("TWILIO_ACCOUNT_SID", "Find it on the Twilio console dashboard.")
    configured_number = (os.environ.get("TWILIO_PHONE_NUMBER") or "").strip()

    client = _client()
    try:
        account = client.api.accounts(account_sid).fetch()
        numbers = client.incoming_phone_numbers.list(limit=20)
    except Exception as exc:  # noqa: BLE001 - the SDK raises a wide range of errors
        raise TwilioServiceError(f"Twilio rejected the request: {exc}") from exc

    owned = [n.phone_number for n in numbers]
    return {
        "ok": True,
        "account_status": account.status,
        "phone_number": configured_number or None,
        # False means the clinic's line is not on this account: it would ring
        # nowhere and no error would surface on its own.
        "number_on_account": configured_number in owned if configured_number else None,
        "numbers_on_account": len(owned),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    result = connect_number_to_retell()
    print()
    for check in result["verification"]["checks"]:
        print(f"  {'OK  ' if check['ok'] else 'FAIL'} {check['check']}: {check['detail']}")
    print()
    print("agent:", result["agent"])
