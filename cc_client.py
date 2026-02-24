"""
cc_client.py
Handles Constant Contact v3 OAuth token management and contact creation/update.
"""

import json
import logging
import os
import time
import requests

log = logging.getLogger(__name__)

# ── Constant Contact credentials ──────────────────────────────────────────────
CC_CLIENT_ID = os.environ.get("CC_CLIENT_ID", "5b382762-e89c-41b3-9035-596b89990425")
CC_CLIENT_SECRET = os.environ.get("CC_CLIENT_SECRET", "LwGkKSmA9ww1b5GajiBq2w")
CC_LIST_ID = os.environ.get("CC_LIST_ID", "668975c4-1131-11f1-85c9-0242ca01a74b")
CC_TOKEN_URL = "https://authz.constantcontact.com/oauth2/default/v1/token"
CC_API_BASE = "https://api.cc.email/v3"

# Custom field name → Constant Contact custom_field_id mapping
_CUSTOM_FIELD_CACHE: dict = {}


# ── Token management ──────────────────────────────────────────────────────────

def _load_token() -> dict:
    """Load CC token from env var or token file."""
    token_json = os.environ.get("CC_TOKEN_JSON")
    if token_json:
        return json.loads(token_json)
    token_path = os.path.join(os.path.dirname(__file__), "cc_token.json")
    if os.path.exists(token_path):
        with open(token_path, "r") as f:
            return json.load(f)
    return {}


def _save_token(token_data: dict):
    """Persist CC token to file and env var."""
    token_path = os.path.join(os.path.dirname(__file__), "cc_token.json")
    with open(token_path, "w") as f:
        json.dump(token_data, f, indent=2)
    os.environ["CC_TOKEN_JSON"] = json.dumps(token_data)


def _refresh_access_token(refresh_token: str) -> dict:
    """Exchange refresh token for a new access token."""
    resp = requests.post(
        CC_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(CC_CLIENT_ID, CC_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    new_token = resp.json()
    existing = _load_token()
    if "refresh_token" not in new_token and existing.get("refresh_token"):
        new_token["refresh_token"] = existing["refresh_token"]
    new_token["obtained_at"] = int(time.time())
    _save_token(new_token)
    return new_token


def get_access_token() -> str:
    """Return a valid access token, refreshing if necessary."""
    token_data = _load_token()
    if not token_data:
        raise RuntimeError(
            "No Constant Contact token found. "
            "Run the OAuth flow first (visit /cc_auth on the bot server)."
        )
    obtained_at = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 86400)
    if int(time.time()) >= obtained_at + expires_in - 60:
        token_data = _refresh_access_token(token_data["refresh_token"])
    return token_data["access_token"]


# ── Custom field resolution ───────────────────────────────────────────────────

def _load_custom_field_cache():
    """Fetch all custom fields from CC and populate the cache."""
    global _CUSTOM_FIELD_CACHE
    token = get_access_token()
    resp = requests.get(
        f"{CC_API_BASE}/contact_custom_fields",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code == 200:
        for field in resp.json().get("custom_fields", []):
            # Cache by both label and name for flexibility
            _CUSTOM_FIELD_CACHE[field.get("label", "")] = field["custom_field_id"]
            _CUSTOM_FIELD_CACHE[field.get("name", "")] = field["custom_field_id"]
        log.info("CC custom fields loaded: %s", list(_CUSTOM_FIELD_CACHE.keys()))
    else:
        log.warning("Could not load CC custom fields: %s %s", resp.status_code, resp.text)


def _get_custom_field_id(label: str) -> str | None:
    """
    Look up the custom_field_id for a given label name.
    Refreshes the full cache on first miss.
    """
    global _CUSTOM_FIELD_CACHE
    if label in _CUSTOM_FIELD_CACHE:
        return _CUSTOM_FIELD_CACHE[label]
    # Refresh cache and try again
    _load_custom_field_cache()
    return _CUSTOM_FIELD_CACHE.get(label)


# ── Name splitting helper ─────────────────────────────────────────────────────

def _split_name(first: str, last: str) -> tuple[str, str]:
    """
    If last name is empty but first name contains a space,
    split it into first and last automatically.
    """
    first = (first or "").strip()
    last = (last or "").strip()

    if not last and " " in first:
        parts = first.split()
        first = parts[0]
        last = " ".join(parts[1:])

    return first, last


# ── Contact creation / update ─────────────────────────────────────────────────

def upsert_contact(cc_data: dict) -> dict:
    """
    Create or update a Constant Contact contact using the standard /contacts endpoint.

    Args:
        cc_data: The ConstantContact dict from resume_extractor.extract_resume_data()
                 Structure: { "ContactFields": {...}, "CustomFields": {...} }

    Returns:
        API response dict.
    """
    token = get_access_token()
    contact_fields = cc_data.get("ContactFields", {})
    custom_fields_data = cc_data.get("CustomFields", {})

    # ── Fix name splitting ────────────────────────────────────────────────────
    first_name, last_name = _split_name(
        contact_fields.get("FirstName", ""),
        contact_fields.get("LastName", "")
    )

    # ── Build custom_fields list ──────────────────────────────────────────────
    custom_fields = []
    for label, value in custom_fields_data.items():
        if not value:
            continue
        field_id = _get_custom_field_id(label)
        if field_id:
            custom_fields.append({
                "custom_field_id": field_id,
                "value": str(value)
            })
        else:
            log.warning("CC custom field not found for label: '%s'. Available: %s", label, list(_CUSTOM_FIELD_CACHE.keys()))

    # ── Build phone numbers list ──────────────────────────────────────────────
    phone_numbers = []
    if contact_fields.get("Phone"):
        phone_numbers.append({
            "phone_number": contact_fields["Phone"],
            "kind": "mobile"
        })

    # ── Build street addresses list ───────────────────────────────────────────
    street_addresses = []
    city = (contact_fields.get("City") or "").strip()
    state = (contact_fields.get("State") or "").strip()
    if city or state:
        street_addresses.append({
            "kind": "home",
            "city": city,
            "state": state,
            "country": "US"
        })

     # ── Build full payload ────────────────────────────────────────────
    payload = {
        "email_address": {
            "address": contact_fields.get("Email", ""),
            "permission_to_send": "implicit"
        },
        "create_source": "Contact",
        "first_name": first_name,
        "last_name": last_name,
        "phone_numbers": phone_numbers,
        "street_addresses": street_addresses,
        "list_memberships": [CC_LIST_ID],
        "custom_fields": custom_fields,
    }

    log.info("CC payload: %s", json.dumps(payload, indent=2))

    # ── Try standard POST /contacts (create or update by email) ──────────────
    resp = requests.post(
        f"{CC_API_BASE}/contacts",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    log.info("CC response %s: %s", resp.status_code, resp.text[:500])

    return {
        "status_code": resp.status_code,
        "response": resp.json() if resp.content else {}
    }
