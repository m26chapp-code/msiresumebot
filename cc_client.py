"""
cc_client.py
Handles Constant Contact v3 OAuth token management and contact creation/update.
"""

import json
import os
import time
import requests

# ── Constant Contact credentials ──────────────────────────────────────────────
CC_CLIENT_ID = os.environ.get("CC_CLIENT_ID", "5b382762-e89c-41b3-9035-596b89990425")
CC_CLIENT_SECRET = os.environ.get("CC_CLIENT_SECRET", "LwGkKSmA9ww1b5GajiBq2w")
CC_LIST_ID = os.environ.get("CC_LIST_ID", "668975c4-1131-11f1-85c9-0242ca01a74b")
CC_TOKEN_URL = "https://authz.constantcontact.com/oauth2/default/v1/token"
CC_API_BASE = "https://api.cc.email/v3"

# Custom field name → Constant Contact custom_field_id mapping
# These will be resolved at runtime via the API if not set in env
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
    # Merge with existing to preserve refresh_token if not returned
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

    # Check expiry (with 60s buffer)
    obtained_at = token_data.get("obtained_at", 0)
    expires_in = token_data.get("expires_in", 86400)
    if int(time.time()) >= obtained_at + expires_in - 60:
        token_data = _refresh_access_token(token_data["refresh_token"])

    return token_data["access_token"]


# ── Custom field resolution ───────────────────────────────────────────────────

def _get_custom_field_id(label: str) -> str | None:
    """
    Look up the custom_field_id for a given label name.
    Results are cached for the lifetime of the process.
    """
    global _CUSTOM_FIELD_CACHE
    if label in _CUSTOM_FIELD_CACHE:
        return _CUSTOM_FIELD_CACHE[label]

    token = get_access_token()
    resp = requests.get(
        f"{CC_API_BASE}/contact_custom_fields",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        return None

    for field in resp.json().get("custom_fields", []):
        _CUSTOM_FIELD_CACHE[field["label"]] = field["custom_field_id"]

    return _CUSTOM_FIELD_CACHE.get(label)


# ── Contact creation / update ─────────────────────────────────────────────────

def upsert_contact(cc_data: dict) -> dict:
    """
    Create or update a Constant Contact contact.

    Args:
        cc_data: The ConstantContact dict from resume_extractor.extract_resume_data()
                 Structure: { "ContactFields": {...}, "CustomFields": {...} }

    Returns:
        API response dict.
    """
    token = get_access_token()
    contact_fields = cc_data.get("ContactFields", {})
    custom_fields_data = cc_data.get("CustomFields", {})

    # Build custom_fields list
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

    # Build phone numbers list
    phone_numbers = []
    if contact_fields.get("Phone"):
        phone_numbers.append({
            "phone_number": contact_fields["Phone"],
            "kind": "mobile"
        })

    # Build street addresses list
    street_addresses = []
    if contact_fields.get("City") or contact_fields.get("State"):
        street_addresses.append({
            "kind": "home",
            "city": contact_fields.get("City", ""),
            "state": contact_fields.get("State", ""),
            "country": "US"
        })

    payload = {
        "email_address": {
            "address": contact_fields.get("Email", ""),
            "permission_to_send": "implicit"
        },
        "first_name": contact_fields.get("FirstName", ""),
        "last_name": contact_fields.get("LastName", ""),
        "phone_numbers": phone_numbers,
        "street_addresses": street_addresses,
        "list_memberships": [CC_LIST_ID],
        "custom_fields": custom_fields,
    }

    # Use the "create or update" (UPSERT) endpoint
    resp = requests.post(
        f"{CC_API_BASE}/contacts/sign_up_form",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        # Fallback: try standard create endpoint
        resp2 = requests.post(
            f"{CC_API_BASE}/contacts",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        return {
            "status_code": resp2.status_code,
            "response": resp2.json() if resp2.content else {}
        }

    return {
        "status_code": resp.status_code,
        "response": resp.json() if resp.content else {}
    }
