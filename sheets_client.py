"""
sheets_client.py
Handles reading from and writing to Google Sheets using OAuth2.
Appends one row per candidate in the exact column order defined below.
"""

import json
import os

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Column order must match the Google Sheet exactly ──────────────────────────
SHEET_COLUMNS = [
    "First Name",
    "Last Name",
    "Email",
    "Phone",
    "City",
    "State",
    "College / University",
    "Expected Graduation Year",
    "Degree",
    "Major",
    "Company 1",
    "Title 1",
    "Dates 1",
    "Company 2",
    "Title 2",
    "Dates 2",
    "Company 3",
    "Title 3",
    "Dates 3",
    "Leadership & Organizations",
    "Skills",
    "Resume Image URL",
    "Drive File ID",
    "Date Added",
]

SPREADSHEET_ID = os.environ.get(
    "GOOGLE_SHEET_ID", "1rP1qMOASgMkkupGZP_BK1RXEMtpnmc9AWju2fvaaRzY"
)
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Sheet1")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_credentials() -> Credentials:
    """
    Load credentials from the token JSON stored in the GOOGLE_TOKEN_JSON env var,
    or from the token.json file on disk. Refreshes if expired.
    """
    token_json = os.environ.get("GOOGLE_TOKEN_JSON")
    if token_json:
        token_data = json.loads(token_json)
    else:
        token_path = os.path.join(os.path.dirname(__file__), "token.json")
        with open(token_path, "r") as f:
            token_data = json.load(f)

    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", SCOPES),
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist refreshed token
        _save_token(creds)

    return creds


def _save_token(creds):
    """Persist credentials to token.json and env var. Accepts Credentials object or plain dict."""
    if isinstance(creds, dict):
        token_data = creds
    else:
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        }
    token_path = os.path.join(os.path.dirname(__file__), "token.json")
    with open(token_path, "w") as f:
        json.dump(token_data, f, indent=2)
    # Also update env var so in-memory state is consistent
    os.environ["GOOGLE_TOKEN_JSON"] = json.dumps(token_data)


def append_candidate_row(sheet_data: dict) -> dict:
    """
    Append a single candidate row to the Google Sheet.

    Args:
        sheet_data: The GoogleSheet dict from resume_extractor.extract_resume_data()

    Returns:
        The API response dict.
    """
    creds = _get_credentials()
    service = build("sheets", "v4", credentials=creds)

    # Build row in exact column order
    row = [sheet_data.get(col, "") for col in SHEET_COLUMNS]

    body = {"values": [row]}
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_TAB}!A:Z",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )
    return result


def ensure_header_row():
    """
    Checks if row 1 has headers; if not, writes them.
    Safe to call on every startup.
    """
    creds = _get_credentials()
    service = build("sheets", "v4", credentials=creds)

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_TAB}!A1:Z1")
        .execute()
    )
    existing = result.get("values", [[]])[0] if result.get("values") else []

    if existing != SHEET_COLUMNS:
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_TAB}!A1",
            valueInputOption="RAW",
            body={"values": [SHEET_COLUMNS]},
        ).execute()
