"""
render_pdf.py — Convert preview_output.html to PDF for formatting review.

Usage:
  python render_pdf.py

Requires Playwright/Chromium:
  pip install playwright
  playwright install chromium
"""

import asyncio
import subprocess
import sys
from pathlib import Path

from delivery import generate_pdf

HTML_PATH = Path(__file__).parent / "preview_output.html"
PDF_PATH  = Path(__file__).parent / "preview_output.pdf"


async def main():
    if not HTML_PATH.exists():
        print("[ERROR] preview_output.html not found — run preview.py first.")
        sys.exit(1)

    print(f"[RENDER] Converting {HTML_PATH.name} → {PDF_PATH.name} ...")
    await generate_pdf(HTML_PATH, PDF_PATH)
    print(f"[RENDER] Done → {PDF_PATH.resolve()}")

    # Open PDF in default viewer
    if sys.platform == "win32":
        subprocess.Popen(["start", "", str(PDF_PATH.resolve())], shell=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(PDF_PATH.resolve())])
    else:
        subprocess.Popen(["xdg-open", str(PDF_PATH.resolve())])


asyncio.run(main())
