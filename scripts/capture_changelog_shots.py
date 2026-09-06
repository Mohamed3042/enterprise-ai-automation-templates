"""Capture the changelog's shots with the new thing boxed on the pixels.

    ATMPL_BASE_URL=http://127.0.0.1:8765 python scripts/capture_changelog_shots.py \
        changelog/2026-09-06-llmops

The box and its caption are drawn into the page before the screenshot, so the highlight is
part of the image rather than something a reader has to be told about.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE_URL = os.getenv("ATMPL_BASE_URL", "http://127.0.0.1:8765")
VIEWPORT = {"width": 1440, "height": 1000}

HIGHLIGHT_JS = """
([selector, caption]) => {
  const target = document.querySelector(selector);
  if (!target) throw new Error('no element for ' + selector);
  target.scrollIntoView({block: 'center'});
  const box = target.getBoundingClientRect();
  const frame = document.createElement('div');
  Object.assign(frame.style, {
    position: 'absolute',
    left: (box.left + window.scrollX - 8) + 'px',
    top: (box.top + window.scrollY - 8) + 'px',
    width: (box.width + 16) + 'px',
    height: (box.height + 16) + 'px',
    border: '3px solid #d94b2b',
    borderRadius: '10px',
    boxShadow: '0 0 0 4px rgba(217,75,43,0.15)',
    pointerEvents: 'none',
    zIndex: '9999',
  });
  const label = document.createElement('div');
  label.textContent = caption;
  Object.assign(label.style, {
    position: 'absolute',
    left: (box.left + window.scrollX - 8) + 'px',
    top: (box.top + window.scrollY - 40) + 'px',
    background: '#d94b2b',
    color: '#fff',
    font: '600 13px/1.5 system-ui, sans-serif',
    padding: '4px 10px',
    borderRadius: '6px',
    pointerEvents: 'none',
    zIndex: '10000',
    maxWidth: (box.width + 16) + 'px',
  });
  document.body.append(frame, label);
  return box.top + window.scrollY;
}
"""

#: (filename, path, wait-for text, CSS selector to box, caption)
SHOTS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "01-llmops-per-provider.png",
        "/llmops",
        "What each model actually did",
        "#providers-table .table-wrap",
        "NEW — every model call this process made: p50/p95, tokens, estimated cost, guardrail trips",
    ),
    (
        "02-llmops-span-tree.png",
        "/llmops",
        "Span tree",
        "#span-tree .verification",
        "NEW — the real span tree of one governed run: run > stage > guardrail, llm.call, guardrail",
    ),
    (
        "03-evals-report.png",
        "/evals",
        "What was measured",
        "#by-category .table-wrap",
        "NEW — 61 scored cases across six gated categories; CI fails the build below 1.000",
    ),
    (
        "04-evals-allowed-cases.png",
        "/evals",
        "Expected against observed",
        "#every-case .table-wrap",
        "NEW — cases that expect 'allowed' too: a gate refusing everything would pass every attack",
    ),
    (
        "05-metrics.png",
        "/metrics",
        "atmpl_llm_calls_total",
        "body",
        "NEW — Prometheus exposition: model calls, guardrail decisions, webhook deliveries",
    ),
    (
        "06-readonly-demo.png",
        "/",
        "READ-ONLY PUBLIC DEMO",
        ".demo-banner",
        "NEW — the hosted demo refuses every mutation, so no key can be spent from a public URL",
    ),
)


def capture(page: Page, output: Path) -> None:
    for filename, path, expected, selector, caption in SHOTS:
        response = page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        if not response or not response.ok:
            raise RuntimeError(f"{path} did not render: {response and response.status}")
        page.get_by_text(expected, exact=False).first.wait_for(timeout=20_000)
        page.evaluate(HIGHLIGHT_JS, [selector, caption])
        page.wait_for_timeout(250)
        page.screenshot(path=output / filename, full_page=True)
        print(f"CAPTURED {filename} from {path}")


def main() -> int:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "changelog/shots")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        capture(page, output)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
