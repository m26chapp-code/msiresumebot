"""
bot.py
Telegram webhook bot — receives resume PDFs/images, extracts data,
writes to Google Sheets, and pushes contacts to Constant Contact.

Webhook endpoint: POST /webhook
OAuth endpoints:  GET  /cc_auth, GET /cc_callback, GET /google_auth, GET /google_callback
Health check:     GET  /health
"""

import json
import logging
import os
import tempfile
import traceback
import urllib.parse
import secrets
import time

import requests
from flask import Flask, request, jsonify, redirect

from resume_extractor import extract_resume_data
from sheets_client import append_candidate_row, ensure_header_row
from cc_client import (
    upsert_contact,
    _refresh_access_token,
    _save_token as cc_save_token,
    CC_CLIENT_ID,
    CC_CLIENT_SECRET,
    CC_TOKEN_URL,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_TOKEN", "8637437120:AAEFwXB6CJBGzZWXjO-D-LasvLDZC4Iaqgo"
)
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "")  # e.g. https://yourapp.onrender.com

# Google OAuth
GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "28109824783-ffmvhnegamn58aor8e9psd8bttr7kc92.apps.googleusercontent.com",
)
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "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")
GOOGLE_SCOPES = "https://www.googleapis.com/auth/spreadsheets"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Constant Contact OAuth
CC_AUTH_URL = "https://authz.constantcontact.com/oauth2/default/v1/authorize"
CC_SCOPES = "contact_data offline_access"

app = Flask(__name__)

# ── Health check ──────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ── Debug endpoint — shows token status and current env var values ─────────────
@app.route("/debug", methods=["GET"])
def debug():
    import json as _json

    google_token_env = os.environ.get("GOOGLE_TOKEN_JSON", "")
    cc_token_env = os.environ.get("CC_TOKEN_JSON", "")

    # Also check disk
    base = os.path.dirname(os.path.abspath(__file__))
    google_disk = os.path.exists(os.path.join(base, "token.json"))
    cc_disk = os.path.exists(os.path.join(base, "cc_token.json"))

    google_token_data = {}
    cc_token_data = {}

    if google_token_env:
        try:
            google_token_data = _json.loads(google_token_env)
        except Exception:
            pass
    elif google_disk:
        try:
            with open(os.path.join(base, "token.json")) as f:
                google_token_data = _json.load(f)
        except Exception:
            pass

    if cc_token_env:
        try:
            cc_token_data = _json.loads(cc_token_env)
        except Exception:
            pass
    elif cc_disk:
        try:
            with open(os.path.join(base, "cc_token.json")) as f:
                cc_token_data = _json.load(f)
        except Exception:
            pass

    return jsonify({
        "google": {
            "env_var_set": bool(google_token_env),
            "file_on_disk": google_disk,
            "has_access_token": bool(google_token_data.get("token")),
            "has_refresh_token": bool(google_token_data.get("refresh_token")),
            "token_json_for_env": google_token_data,  # copy this into GOOGLE_TOKEN_JSON env var
        },
        "constant_contact": {
            "env_var_set": bool(cc_token_env),
            "file_on_disk": cc_disk,
            "has_access_token": bool(cc_token_data.get("access_token")),
            "has_refresh_token": bool(cc_token_data.get("refresh_token")),
            "token_json_for_env": cc_token_data,  # copy this into CC_TOKEN_JSON env var
        }
    }), 200


# ── CC test endpoint — sends a real test contact and shows raw API response ────
@app.route("/cc_test", methods=["GET"])
def cc_test():
    import json as _json
    from cc_client import get_access_token, _get_custom_field_id, _load_custom_field_cache, CC_API_BASE, CC_LIST_ID

    try:
        token = get_access_token()
    except Exception as e:
        return jsonify({"error": f"Token error: {str(e)}"}), 500

    # Load custom fields and show what's available
    _load_custom_field_cache()
    from cc_client import _CUSTOM_FIELD_CACHE

    # Try a minimal contact payload
    payload = {
        "email_address": {
            "address": "test.resume.bot@example.com",
            "permission_to_send": "implicit"
        },
        "first_name": "Test",
        "last_name": "Candidate",
        "list_memberships": [CC_LIST_ID],
    }

    resp = requests.post(
        f"{CC_API_BASE}/contacts",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    return jsonify({
        "status_code": resp.status_code,
        "response": resp.json() if resp.content else {},
        "available_custom_fields": list(_CUSTOM_FIELD_CACHE.keys()),
        "list_id_used": CC_LIST_ID,
    }), 200


# ── Telegram webhook ──────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    try:
        _handle_update(update)
    except Exception:
        log.error("Unhandled error in webhook:\n%s", traceback.format_exc())
    return jsonify({"ok": True}), 200


def _handle_update(update: dict):
    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    # ── /start command ────────────────────────────────────────────────────────
    if text.startswith("/start"):
        _send_message(
            chat_id,
            "👋 *Resume Bot Ready!*\n\n"
            "Send me a resume as a *PDF* or *image* (JPG/PNG) and I will:\n"
            "• Extract all candidate data\n"
            "• Add the row to Google Sheets\n"
            "• Create/update the contact in Constant Contact\n"
            "• Return the structured JSON\n\n"
            "Just drop the file here to get started!",
        )
        return

    # ── /status command ───────────────────────────────────────────────────────
    if text.startswith("/status"):
        _send_message(chat_id, "✅ Bot is running and ready to process resumes.")
        return

    # ── Document (PDF) ────────────────────────────────────────────────────────
    doc = message.get("document")
    if doc:
        mime = doc.get("mime_type", "")
        if mime == "application/pdf" or doc.get("file_name", "").lower().endswith(".pdf"):
            _process_file(chat_id, doc["file_id"], ".pdf")
            return
        # Could be an image sent as document
        if mime.startswith("image/"):
            ext = "." + mime.split("/")[-1]
            _process_file(chat_id, doc["file_id"], ext)
            return
        _send_message(chat_id, "⚠️ Please send a PDF or image file.")
        return

    # ── Photo ─────────────────────────────────────────────────────────────────
    photos = message.get("photo")
    if photos:
        # Use highest resolution photo
        best = max(photos, key=lambda p: p.get("file_size", 0))
        _process_file(chat_id, best["file_id"], ".jpg")
        return

    # ── Fallback ──────────────────────────────────────────────────────────────
    if text:
        _send_message(
            chat_id,
            "Please send a resume file (PDF or image). Use /start for instructions.",
        )


def _process_file(chat_id: int, file_id: str, ext: str):
    """Download the Telegram file, extract resume data, push to Sheet + CC."""
    _send_message(chat_id, "⏳ Processing your resume... please wait.")

    try:
        # 1. Download file from Telegram
        file_path = _download_telegram_file(file_id, ext)

        # 2. Extract resume data
        log.info("Extracting resume data from %s", file_path)
        result = extract_resume_data(file_path)
        sheet_data = result["GoogleSheet"]
        cc_data = result["ConstantContact"]

        # 3. Write to Google Sheets
        sheets_status = "✅ Added to Google Sheets"
        try:
            append_candidate_row(sheet_data)
            log.info("Row appended to Google Sheets")
        except Exception as e:
            log.error("Google Sheets error: %s", e)
            sheets_status = f"⚠️ Google Sheets error: {str(e)[:100]}"

        # 4. Push to Constant Contact
        cc_status = "✅ Contact pushed to Constant Contact"
        try:
            cc_result = upsert_contact(cc_data)
            log.info("CC upsert result: %s", cc_result)
            if cc_result.get("status_code") not in (200, 201):
                cc_status = f"⚠️ CC error {cc_result.get('status_code')}: {str(cc_result.get('response', ''))[:100]}"
        except Exception as e:
            log.error("Constant Contact error: %s", e)
            cc_status = f"⚠️ Constant Contact error: {str(e)[:100]}"

        # 5. Build summary message
        name = f"{sheet_data.get('First Name', '')} {sheet_data.get('Last Name', '')}".strip()
        email = sheet_data.get("Email", "N/A")
        school = sheet_data.get("College / University", "N/A")
        classification = cc_data["CustomFields"].get("Classification", "N/A")

        summary = (
            f"✅ *Resume Processed*\n\n"
            f"👤 *Name:* {name}\n"
            f"📧 *Email:* {email}\n"
            f"🎓 *School:* {school}\n"
            f"📋 *Classification:* {classification}\n\n"
            f"{sheets_status}\n"
            f"{cc_status}"
        )
        _send_message(chat_id, summary)

        # Send JSON separately to avoid Telegram 4096 char limit
        json_str = json.dumps(result, indent=2)
        # Split into chunks of 3800 chars if needed
        chunk_size = 3800
        chunks = [json_str[i:i+chunk_size] for i in range(0, len(json_str), chunk_size)]
        for i, chunk in enumerate(chunks):
            label = "*Extracted JSON:*" if i == 0 else f"*JSON (cont. {i+1}):*"
            _send_message(chat_id, f"{label}\n```\n{chunk}\n```")

    except Exception as e:
        log.error("Processing error: %s\n%s", e, traceback.format_exc())
        _send_message(
            chat_id,
            f"❌ Error processing resume: {str(e)[:200]}\n\nPlease try again or check the file format.",
        )
    finally:
        # Clean up temp file
        try:
            if "file_path" in locals() and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass


def _download_telegram_file(file_id: str, ext: str) -> str:
    """Download a file from Telegram and save to a temp file. Returns local path."""
    # Get file info
    resp = requests.get(
        f"{TELEGRAM_API}/getFile",
        params={"file_id": file_id},
        timeout=30,
    )
    resp.raise_for_status()
    file_info = resp.json()
    telegram_path = file_info["result"]["file_path"]

    # Download file bytes
    file_resp = requests.get(
        f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{telegram_path}",
        timeout=60,
    )
    file_resp.raise_for_status()

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.write(file_resp.content)
    tmp.close()
    return tmp.name


def _send_message(chat_id: int, text: str):
    """Send a Telegram message with Markdown parsing."""
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=30,
    )


# ── Webhook registration ──────────────────────────────────────────────────────
@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    """Register the webhook URL with Telegram."""
    host = request.args.get("host") or WEBHOOK_HOST
    if not host:
        return jsonify({"error": "Provide ?host=https://yourapp.onrender.com"}), 400

    webhook_url = f"{host.rstrip('/')}/webhook"
    resp = requests.post(
        f"{TELEGRAM_API}/setWebhook",
        json={"url": webhook_url, "allowed_updates": ["message", "channel_post"]},
        timeout=30,
    )
    return jsonify(resp.json()), resp.status_code


# ── Google OAuth flow ─────────────────────────────────────────────────────────
_google_state_store: dict = {}


@app.route("/google_auth", methods=["GET"])
def google_auth():
    """Redirect user to Google OAuth consent screen."""
    state = secrets.token_urlsafe(16)
    _google_state_store[state] = int(time.time())

    host = request.host_url.rstrip("/")
    redirect_uri = f"{host}/google_callback"

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return redirect(url)


@app.route("/google_callback", methods=["GET"])
def google_callback():
    """Handle Google OAuth callback and save token."""
    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return "Error: no code returned", 400

    host = request.host_url.rstrip("/")
    redirect_uri = f"{host}/google_callback"

    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    token_data = resp.json()

    if "access_token" not in token_data:
        return f"Error getting token: {token_data}", 400

    # Save in sheets_client format
    from sheets_client import _save_token as google_save_token
    google_save_token(
        {
            "token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
            "token_uri": GOOGLE_TOKEN_URL,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "scopes": [GOOGLE_SCOPES],
        }
    )

    # Ensure sheet header row
    try:
        ensure_header_row()
    except Exception as e:
        log.warning("Could not ensure header row: %s", e)

    return (
        "<h2>✅ Google Sheets connected!</h2>"
        "<p>You can close this tab and return to Telegram.</p>"
    )


# ── Constant Contact OAuth flow ───────────────────────────────────────────────
_cc_state_store: dict = {}


@app.route("/cc_auth", methods=["GET"])
def cc_auth():
    """Redirect user to Constant Contact OAuth consent screen."""
    state = secrets.token_urlsafe(16)
    _cc_state_store[state] = int(time.time())

    host = request.host_url.rstrip("/")
    redirect_uri = f"{host}/cc_callback"

    params = {
        "client_id": CC_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": CC_SCOPES,
        "state": state,
    }
    url = CC_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return redirect(url)


@app.route("/cc_callback", methods=["GET"])
def cc_callback():
    """Handle Constant Contact OAuth callback and save token."""
    code = request.args.get("code")
    if not code:
        return "Error: no code returned", 400

    host = request.host_url.rstrip("/")
    redirect_uri = f"{host}/cc_callback"

    resp = requests.post(
        CC_TOKEN_URL,
        data={
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        auth=(CC_CLIENT_ID, CC_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    token_data = resp.json()

    if "access_token" not in token_data:
        return f"Error getting CC token: {token_data}", 400

    token_data["obtained_at"] = int(time.time())
    cc_save_token(token_data)

    return (
        "<h2>✅ Constant Contact connected!</h2>"
        "<p>You can close this tab and return to Telegram.</p>"
    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
