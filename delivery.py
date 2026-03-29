"""
delivery.py — PDF generation and Gmail email delivery (Level 1).

PDF rendering uses Playwright/Chromium for pixel-perfect fidelity with the
dark-themed HTML output.  Email delivery uses the Gmail API (same OAuth2
credentials as ingestion) so no separate SMTP configuration is required.

One-time setup (after installing requirements):
  playwright install chromium

Environment variables (set in .env):
  BRIEFING_EMAIL   — recipient address (defaults to your own Gmail address)
"""

from __future__ import annotations

import asyncio
import base64
import os
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import logging

import config

_logger = logging.getLogger("briefing")


# ── PDF Generation ────────────────────────────────────────────────────────────

async def generate_pdf(html_path: Path, pdf_path: Path) -> Path:
    """
    Render the saved HTML briefing to PDF using a headless Chromium browser.

    Chromium's print engine preserves all CSS (including dark backgrounds,
    custom properties, and grid layouts) when print_background=True.

    Args:
        html_path: Path to the saved .html file.
        pdf_path:  Destination .pdf path.

    Returns:
        The resolved pdf_path.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "Playwright is not installed.  Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page    = await browser.new_page()

        # Use file:/// URI so local assets resolve correctly.
        # "load" is used instead of "networkidle" — local file:// URIs have no
        # network activity so "networkidle" can stall indefinitely.
        await page.goto(html_path.resolve().as_uri(), wait_until="load")

        # Render using screen CSS — bypasses @media print reset entirely.
        # print_background=True ensures dark backgrounds and colors are included.
        await page.emulate_media(media="screen")

        await page.pdf(
            path=str(pdf_path.resolve()),  # must be absolute on Windows
            format="A4",
            print_background=True,
            margin={
                "top":    "18mm",
                "bottom": "18mm",
                "left":   "14mm",
                "right":  "14mm",
            },
        )
        await browser.close()

    return pdf_path


# ── Email Delivery ────────────────────────────────────────────────────────────

def _get_sender_address(service) -> str:
    """Return the authenticated user's Gmail address."""
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "me")


def _send_email_sync(
    pdf_path: Path,
    briefing_date: date,
    recipient: str,
) -> None:
    """
    Build and send the briefing email via Gmail API.
    The PDF is the sole deliverable; the body is a short plain-text notification.
    Runs synchronously — call via run_in_executor from async code.
    """
    from ingestion import _get_gmail_service

    service = _get_gmail_service()
    sender  = _get_sender_address(service)

    subject = f"Daily Brief — {briefing_date.strftime('%A, %d %B %Y')}"

    msg            = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient

    body_text = (
        f"Your Daily Brief for {briefing_date.strftime('%A, %d %B %Y')} "
        f"is attached.\n"
    )
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise FileNotFoundError(
            f"PDF not found or empty at {pdf_path}. "
            "Ensure Playwright/Chromium is installed and PDF generation succeeded."
        )

    pdf_bytes  = pdf_path.read_bytes()
    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"dailybrief{briefing_date.strftime('%m%d%y')}.pdf",
    )
    msg.attach(attachment)

    raw_bytes = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw_bytes}).execute()


# ── Public Entry Point ────────────────────────────────────────────────────────

async def deliver_briefing(
    html_path: Path,
    briefing_date: date,
    recipient_email: Optional[str] = None,
) -> Path:
    """
    Generate a PDF from the saved HTML briefing and email it via Gmail.

    Args:
        html_path:       Path to the saved .html file (source for PDF rendering).
        briefing_date:   The briefing date.
        recipient_email: Destination address.  Falls back to the BRIEFING_EMAIL
                         environment variable, then to the authenticated Gmail address.

    Returns:
        Path to the generated PDF file.
    """
    if not recipient_email:
        recipient_email = (
            config.BRIEFING_EMAIL
            or os.environ.get("BRIEFING_EMAIL", "")
        )

    pdf_path = html_path.with_name(
        f"dailybrief{briefing_date.strftime('%m%d%y')}.pdf"
    )

    _logger.info("[DELIVERY] Rendering PDF from HTML...")
    await generate_pdf(html_path, pdf_path)
    _logger.info("[DELIVERY] PDF saved → %s", pdf_path)

    if not recipient_email:
        _logger.warning(
            "[DELIVERY] No recipient configured — skipping email. "
            "Set BRIEFING_EMAIL in .env to enable delivery."
        )
        return pdf_path

    _logger.info("[DELIVERY] Sending briefing to %s...", recipient_email)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        _send_email_sync,
        pdf_path,
        briefing_date,
        recipient_email,
    )
    _logger.info("[DELIVERY] Briefing delivered to %s.", recipient_email)

    return pdf_path
