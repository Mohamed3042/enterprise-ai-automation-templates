"""Record the five-minute demo path as a video, driven by Playwright.

    python -m atmpl demo up --exercise --port 8765     # in one terminal
    ATMPL_BASE_URL=http://127.0.0.1:8765 python scripts/record_demo.py

Writes `docs/proof/demo.mp4` and `docs/proof/demo.gif` when ffmpeg is on PATH, and leaves
Playwright's `demo.webm` when it is not. The GIF is capped so it stays under the 8 MB a README
can reasonably carry.

The path is the runbook's, in order, so what a reader watches is what a reviewer would be
shown — including the two pages that only exist because this deployment measured itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "proof"
BASE_URL = os.getenv("ATMPL_BASE_URL", "http://127.0.0.1:8765")
VIEWPORT = {"width": 1280, "height": 800}

#: (path, the text that proves the page rendered, how long to hold it)
BEATS: tuple[tuple[str, str, int], ...] = (
    ("/", "Ship governed automation", 3200),
    ("/runs/run_bank_override", "HUMAN AUTHORITY", 3600),
    ("/approvals", "Pending approvals", 3000),
    ("/audit", "CHAIN VERIFIED", 3000),
    ("/evals", "Expected against observed", 4200),
    ("/llmops", "What each model actually did", 5200),
    ("/webhooks", "Delivery attempts", 2600),
    ("/api/v1/docs", "Enterprise AI Automation Templates API", 2600),
)


def scroll_through(page: Page, hold_ms: int) -> None:
    """Show the whole page, not only its header."""
    steps = max(2, hold_ms // 900)
    for _ in range(steps):
        page.mouse.wheel(0, 520)
        page.wait_for_timeout(hold_ms // steps)


def record() -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    videos = OUTPUT / "_video"
    if videos.exists():
        shutil.rmtree(videos)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(videos),
            record_video_size=VIEWPORT,
        )
        page = context.new_page()
        for path, expected, hold in BEATS:
            response = page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
            if not response or not response.ok:
                raise RuntimeError(f"{path} did not render: {response and response.status}")
            page.get_by_text(expected, exact=False).first.wait_for(timeout=20_000)
            if path == "/audit":
                page.get_by_role("button", name="Verify chain").click()
                page.locator("#verification .valid").wait_for()
            page.wait_for_timeout(900)
            scroll_through(page, hold)
            print(f"RECORDED {path}")
        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()
    if not video_path:
        raise RuntimeError("Playwright produced no video")
    final = OUTPUT / "demo.webm"
    shutil.move(video_path, final)
    shutil.rmtree(videos, ignore_errors=True)
    print(f"WROTE {final} ({final.stat().st_size / 1e6:.1f} MB)")
    return final


def transcode(source: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg is not on PATH; the .webm is the deliverable")
        return
    mp4 = OUTPUT / "demo.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-c:v", "libx264", "-crf", "28",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)],
        check=True, capture_output=True,
    )
    print(f"WROTE {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")

    gif = OUTPUT / "demo.gif"
    palette = OUTPUT / "_palette.png"
    scale = "fps=8,scale=760:-1:flags=lanczos"
    subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-vf", f"{scale},palettegen=max_colors=128",
         str(palette)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-i", str(palette),
         "-lavfi", f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5", str(gif)],
        check=True, capture_output=True,
    )
    palette.unlink(missing_ok=True)
    size = gif.stat().st_size / 1e6
    print(f"WROTE {gif} ({size:.1f} MB)")
    if size > 8:
        print("WARNING: the GIF is over 8 MB; lower the fps or the width before committing it")
    # The intermediate .webm is three times the size of the .mp4 and carries nothing extra.
    source.unlink(missing_ok=True)
    print(f"REMOVED {source.name} (the mp4 and the gif are the deliverables)")


if __name__ == "__main__":
    transcode(record())
