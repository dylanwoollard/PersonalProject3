"""
ingestion.py — Gmail ingestion via the Gmail API (Level 6).

On first run a browser window opens for OAuth2 authorization and the token is
cached in token.json.  Subsequent runs are fully automated.

Prerequisites (one-time setup):
  1. Go to console.cloud.google.com and select (or create) a project.
  2. Enable the Gmail API for that project.
  3. Go to APIs & Services → Credentials → Create Credentials → OAuth client ID.
     Application type: Desktop App.
  4. Download the JSON file and save it as  credentials.json  in this directory.

Fallback:
  If credentials.json is absent the function falls back to reading a local
  text file (intelligence.txt or intelligence_YYYY_MM_DD.txt), preserving
  offline/testing capability.  File-based ingestion never attempts label removal.
"""

from __future__ import annotations

import asyncio
import base64
import html as _html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read + label modification
    "https://www.googleapis.com/auth/gmail.send",
]
CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE       = Path("token.json")
GMAIL_LABEL      = config.GMAIL_LABEL


# ── Payload dataclass ─────────────────────────────────────────────────────────

@dataclass
class IntelligencePayload:
    """
    Result of ingestion.  Carries both the formatted text content for prompt
    injection and the raw Gmail message IDs needed for post-processing
    (e.g., label removal).  File-based ingestion returns an empty message_ids
    list so downstream code can handle both paths uniformly.
    """
    content:     str
    message_ids: list[str] = field(default_factory=list)

    @property
    def from_gmail(self) -> bool:
        return len(self.message_ids) > 0


# ── OAuth2 / Service ──────────────────────────────────────────────────────────

def _get_gmail_service():
    """Return an authorized Gmail API service object, refreshing or creating the token as needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds: Credentials | None = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    "credentials.json not found in the project directory.\n\n"
                    "Gmail API setup (one-time):\n"
                    "  1. Open https://console.cloud.google.com\n"
                    "  2. Enable the Gmail API for your project.\n"
                    "  3. Create an OAuth 2.0 Client ID (Desktop App).\n"
                    "  4. Download the JSON and save it as credentials.json here.\n"
                    "  5. Run the script again — a browser window will open for authorization."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        with TOKEN_FILE.open("w") as fh:
            fh.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ── Email Parsing ─────────────────────────────────────────────────────────────

def _strip_html(raw: str) -> str:
    """Remove HTML markup and decode entities, returning clean prose text."""
    raw = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        "",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    raw = re.sub(r"<(?:br|p|div|tr|li)[^>]*>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = _html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _extract_body(payload: dict) -> str:
    """
    Recursively walk a Gmail message payload and extract the best available
    text representation.  Prefers text/plain; falls back to text/html.
    """
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            raw = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return _strip_html(raw)

    if "multipart" in mime:
        parts = payload.get("parts", [])
        for preferred in ("text/plain", "text/html"):
            for part in parts:
                if part.get("mimeType") == preferred:
                    result = _extract_body(part)
                    if result.strip():
                        return result
        for part in parts:
            if "multipart" in part.get("mimeType", ""):
                result = _extract_body(part)
                if result.strip():
                    return result

    return ""


# ── Gmail Fetch ───────────────────────────────────────────────────────────────

def _fetch_labeled_emails(
    service,
    label_name: str,
    since_unix: int,
) -> tuple[list[dict], list[str]]:
    """
    Fetch emails carrying `label_name` received after the given Unix timestamp.

    Returns:
        emails:      List of dicts with keys: subject, sender, date, body.
        message_ids: Corresponding Gmail message IDs (parallel list).
    """
    query = f"label:{label_name} after:{since_unix}"

    result = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=config.MAX_EMAILS)
        .execute()
    )

    message_refs = result.get("messages", [])
    if not message_refs:
        return [], []

    emails:      list[dict] = []
    message_ids: list[str]  = []

    for ref in message_refs:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )

        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        subject  = headers.get("Subject", "(No Subject)")
        sender   = headers.get("From",    "(Unknown Sender)")
        msg_date = headers.get("Date",    "")

        body = _extract_body(msg.get("payload", {})).strip()
        if body:
            emails.append(
                {"subject": subject, "sender": sender, "date": msg_date, "body": body}
            )
            message_ids.append(ref["id"])

    return emails, message_ids


def _format_emails(emails: list[dict]) -> str:
    """Render a list of email dicts into a structured intelligence text block."""
    header = (
        f"=== INTELLIGENCE INPUT: {len(emails)} email(s) "
        f"from Gmail label '{GMAIL_LABEL}' ===\n"
    )
    sections = [header]
    for i, em in enumerate(emails, 1):
        sections.append(
            f"{'─' * 60}\n"
            f"EMAIL {i} OF {len(emails)}\n"
            f"FROM:    {em['sender']}\n"
            f"DATE:    {em['date']}\n"
            f"SUBJECT: {em['subject']}\n"
            f"{'─' * 60}\n\n"
            f"{em['body']}\n"
        )
    return "\n".join(sections)


# ── Label Removal ─────────────────────────────────────────────────────────────

def _resolve_label_id(service, label_name: str) -> str | None:
    """Return the Gmail label ID for a given label name, or None if not found."""
    response = service.users().labels().list(userId="me").execute()
    for label in response.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    return None


def unlabel_emails_sync(message_ids: list[str], label_name: str = GMAIL_LABEL) -> None:
    """
    Remove `label_name` from every message in `message_ids`.
    Runs synchronously — call via run_in_executor from async code.
    """
    if not message_ids:
        return

    service  = _get_gmail_service()
    label_id = _resolve_label_id(service, label_name)

    if not label_id:
        print(
            f"[LEVEL 6] Warning: label '{label_name}' not found in Gmail. "
            "No emails were unlabeled.",
            flush=True,
        )
        return

    for msg_id in message_ids:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": [label_id]},
        ).execute()

    print(
        f"[LEVEL 6] Removed '{label_name}' label from {len(message_ids)} email(s).",
        flush=True,
    )


async def unlabel_emails(
    message_ids: list[str],
    label_name: str = GMAIL_LABEL,
) -> None:
    """Async wrapper around unlabel_emails_sync."""
    if not message_ids:
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, unlabel_emails_sync, message_ids, label_name)


# ── Synchronous Gmail worker (runs in executor) ───────────────────────────────

def _gmail_ingestion_sync(since_hours: int) -> IntelligencePayload:
    """Authenticate, fetch, and format emails.  Runs in a thread-pool executor."""
    print("[LEVEL 6] Authenticating with Gmail API...", flush=True)
    service = _get_gmail_service()

    since_dt   = datetime.utcnow() - timedelta(hours=since_hours)
    since_unix = int(since_dt.timestamp())
    since_str  = since_dt.strftime("%Y-%m-%d %H:%M UTC")
    print(
        f"[LEVEL 6] Fetching '{GMAIL_LABEL}' emails from the last {since_hours}h "
        f"(since {since_str})...",
        flush=True,
    )

    emails, message_ids = _fetch_labeled_emails(
        service, GMAIL_LABEL, since_unix=since_unix
    )

    if not emails:
        raise ValueError(
            f"No emails found under Gmail label '{GMAIL_LABEL}' "
            f"in the last {since_hours} hours (since {since_str}).  "
            "Verify that messages are correctly labeled in Gmail and try again."
        )

    print(f"[LEVEL 6] Retrieved {len(emails)} email(s).", flush=True)
    return IntelligencePayload(
        content=_format_emails(emails),
        message_ids=message_ids,
    )


# ── File-based fallback ───────────────────────────────────────────────────────

async def _load_from_file(filepath: Optional[str]) -> IntelligencePayload:
    """Read intelligence from a local text file (offline / testing fallback)."""
    import aiofiles

    if filepath:
        target = Path(filepath)
    else:
        dated    = Path(f"intelligence_{date.today().strftime('%Y_%m_%d')}.txt")
        fallback = Path("intelligence.txt")
        target   = dated if dated.exists() else fallback

    if not target.exists():
        raise FileNotFoundError(
            f"Neither credentials.json (Gmail API) nor a local intelligence file "
            f"('{target.name}') was found.  "
            "Set up Gmail API credentials or place intelligence text in intelligence.txt."
        )

    async with aiofiles.open(target, "r", encoding="utf-8") as fh:
        content = await fh.read()

    content = content.strip()
    if not content:
        raise ValueError(f"Intelligence file '{target}' is empty.")

    # No message_ids — file-based ingestion has nothing to unlabel
    return IntelligencePayload(content=content, message_ids=[])


# ── Public entry point ────────────────────────────────────────────────────────

async def load_email_intelligence(
    filepath: Optional[str] = None,
    since_hours: int = 24,
) -> IntelligencePayload:
    """
    Load raw daily email intelligence.

    Resolution order:
      1. If `filepath` is given → read that local file directly.
      2. If credentials.json exists → fetch from Gmail API (DailyBriefing label).
      3. Otherwise → fall back to intelligence.txt / intelligence_YYYY_MM_DD.txt.

    Args:
        filepath:    Optional explicit path to a local intelligence file.
        since_hours: Fetch emails from the last N hours (default 24).

    Returns:
        IntelligencePayload with .content (str) and .message_ids (list[str]).
        message_ids is empty for file-based ingestion.
    """
    if filepath:
        return await _load_from_file(filepath)

    if CREDENTIALS_FILE.exists():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _gmail_ingestion_sync, since_hours)

    return await _load_from_file(None)
