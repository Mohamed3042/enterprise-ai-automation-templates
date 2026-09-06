"""Capture portfolio proof from the live FastAPI application with Playwright Chromium."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Browser, Playwright, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "proof" / "screenshots"
BASE_URL = os.getenv("ATMPL_BASE_URL", "http://127.0.0.1:8765")


def _launch_chromium(playwright: Playwright) -> Browser:
    try:
        return playwright.chromium.launch(channel="chrome", headless=True)
    except Exception as channel_error:
        candidates = (
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        )
        for executable in candidates:
            if executable.exists():
                return playwright.chromium.launch(executable_path=executable, headless=True)
        raise RuntimeError(
            "Playwright could not launch the installed Chrome/Edge standard toolchain."
        ) from channel_error


def _webhook_run_path(page) -> str:
    """The run a signed inbound webhook started, found by its mapped title."""
    page.goto(f"{BASE_URL}/", wait_until="networkidle")
    link = page.get_by_role("link", name="Helpdesk ticket", exact=False).first
    if link.count() == 0:
        raise RuntimeError(
            "No webhook-started run on the dashboard. Send one first: see docs/webhooks.md."
        )
    return str(link.get_attribute("href"))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    shots = (
        ("dashboard-home.png", "/", "Ship governed automation"),
        (
            "bank-human-override.png",
            "/runs/run_bank_override",
            "HUMAN AUTHORITY · CAPTURED IN AUDIT",
        ),
        (
            "ministry-arabic.png",
            "/runs/run_ministry_ar_math",
            "خطة درس الرياضيات — الصف الخامس",
        ),
        ("pending-approvals.png", "/approvals", "Pending approvals"),
        ("audit-verify.png", "/audit", "CHAIN VERIFIED"),
        ("evals-report.png", "/evals", "Expected against observed"),
        ("llmops-page.png", "/llmops", "What each model actually did"),
        ("webhook-deliveries.png", "/webhooks", "Delivery attempts"),
        ("api-docs.png", "/api/v1/docs", "Enterprise AI Automation Templates API"),
    )
    with sync_playwright() as playwright:
        browser = _launch_chromium(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        for filename, path, expected in shots:
            response = page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
            if not response or not response.ok:
                raise RuntimeError(f"Live page failed: {path}")
            page.get_by_text(expected, exact=False).first.wait_for()
            if path == "/audit":
                page.get_by_role("button", name="Verify chain").click()
                page.locator("#verification .valid").wait_for()
            page.screenshot(path=OUTPUT / filename, full_page=True)
            print(f"CAPTURED {filename} from {path}")

        if os.getenv("ATMPL_CAPTURE_WEBHOOK_RUN", "1") == "1":
            run_path = _webhook_run_path(page)
            page.goto(f"{BASE_URL}{run_path}", wait_until="networkidle")
            page.get_by_text("VALIDATED INPUT", exact=False).first.wait_for()
            page.screenshot(path=OUTPUT / "webhook-started-run.png", full_page=True)
            print(f"CAPTURED webhook-started-run.png from {run_path}")
        browser.close()


if __name__ == "__main__":
    main()

