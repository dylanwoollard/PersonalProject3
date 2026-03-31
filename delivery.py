"""
delivery.py — GCS hosting and Gmail email delivery (Level 1).

The HTML briefing is uploaded to a Google Cloud Storage bucket and a signed
link (valid 7 days) is emailed via the Gmail API.  No PDF is generated.

One-time setup:
  Add to .env:
    GCS_BUCKET_NAME               = <your bucket name>
    GOOGLE_APPLICATION_CREDENTIALS = /absolute/path/to/service-account-key.json
  The service account needs the Storage Object Creator role on the bucket and
  the Service Account Token Creator role to generate signed URLs.

Environment variables (set in .env):
  BRIEFING_EMAIL                 — recipient address
  GCS_BUCKET_NAME                — target GCS bucket
  GOOGLE_APPLICATION_CREDENTIALS — path to GCP service account JSON key
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import os
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import logging

import config

_logger = logging.getLogger("briefing")


# ── GCS Upload ────────────────────────────────────────────────────────────────

def _upload_html_to_gcs_sync(html_path: Path, target_date: date) -> str:
    """
    Upload the HTML briefing file to GCS and return a signed URL valid for 7 days.

    The blob is stored as:
      briefings/dailybrief_YYYYMMDD.html

    content_type is set to text/html and content_disposition to inline so the
    link opens directly in a browser rather than triggering a download.

    Authentication uses GOOGLE_APPLICATION_CREDENTIALS from the environment
    (set in .env).  The service account must have Storage Object Creator and
    Service Account Token Creator roles.
    """
    try:
        from google.cloud import storage
    except ImportError:
        raise ImportError(
            "google-cloud-storage is not installed.  "
            "Run:  pip install google-cloud-storage"
        )

    blob_name = f"briefings/dailybrief_{target_date.strftime('%Y%m%d')}.html"

    client = storage.Client()
    bucket = client.bucket(config.GCS_BUCKET_NAME)
    blob   = bucket.blob(blob_name)

    blob.upload_from_filename(
        str(html_path.resolve()),
        content_type="text/html; charset=utf-8",
    )

    # Set inline disposition so the browser renders it rather than downloads it.
    blob.content_disposition = "inline"
    blob.patch()

    signed_url = blob.generate_signed_url(
        version    = "v4",
        expiration = datetime.timedelta(days=7),
        method     = "GET",
    )
    return signed_url


async def upload_html_to_s3(html_path: Path, target_date: date) -> str:
    """
    Async wrapper: upload the HTML briefing to GCS and return a signed URL.

    Named upload_html_to_s3 for backwards compatibility with main.py call sites;
    delegates to the GCS implementation via a thread executor.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _upload_html_to_gcs_sync, html_path, target_date
    )


# ── Email Delivery ────────────────────────────────────────────────────────────

def _get_sender_address(service) -> str:
    """Return the authenticated user's Gmail address."""
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "me")


def _send_email_sync(hosted_url: str, briefing_date: date, recipient: str) -> None:
    """
    Send a plain-text briefing link email via the Gmail API.
    Runs synchronously — called via run_in_executor from async code.
    """
    from ingestion import _get_gmail_service

    service = _get_gmail_service()
    sender  = _get_sender_address(service)

    subject = f"Daily Brief — {briefing_date.strftime('%A, %d %B %Y')}"

    body_text = (
        f"Your Daily Brief for {briefing_date.strftime('%A, %d %B %Y')} is ready.\n\n"
        f"View it here:\n{hosted_url}\n"
    )

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    body_html = (
        "<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;font-size:14px;"
        "color:#111;max-width:600px;margin:40px auto;padding:0 24px;'>"
        f"<p>Your Daily Brief for <strong>{briefing_date.strftime('%A, %d %B %Y')}</strong> is ready.</p>"
        f"<p><a href='{hosted_url}' style='display:inline-block;padding:12px 24px;"
        "background:#051c2c;color:#fff;text-decoration:none;border-radius:4px;"
        "font-weight:bold;'>View Briefing &rarr;</a></p>"
        f"<p style='font-size:12px;color:#888;margin-top:24px;'>"
        f"Or paste this URL into your browser:<br>{hosted_url}</p>"
        "</body></html>"
    )
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    raw_bytes = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw_bytes}).execute()


# ── Public Entry Point ────────────────────────────────────────────────────────

async def deliver_briefing(
    hosted_url: str,
    briefing_date: date,
    recipient_email: Optional[str] = None,
) -> None:
    """
    Email a link to the S3-hosted HTML briefing via Gmail.

    Args:
        hosted_url:      Public S3 URL of the uploaded HTML file.
        briefing_date:   The briefing date.
        recipient_email: Destination address.  Falls back to the BRIEFING_EMAIL
                         config value, then the BRIEFING_EMAIL env var.
    """
    if not recipient_email:
        recipient_email = (
            config.BRIEFING_EMAIL
            or os.environ.get("BRIEFING_EMAIL", "")
        )

    if not recipient_email:
        _logger.warning(
            "[DELIVERY] No recipient configured — skipping email. "
            "Set BRIEFING_EMAIL in config.py or .env to enable delivery."
        )
        return

    _logger.info("[DELIVERY] Sending briefing link to %s...", recipient_email)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        _send_email_sync,
        hosted_url,
        briefing_date,
        recipient_email,
    )
    _logger.info("[DELIVERY] Briefing link delivered to %s.", recipient_email)
