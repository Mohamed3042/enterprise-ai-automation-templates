"""One browser journey through the thing the product actually claims.

Sign in -> open the approvals queue -> decide a MEDIUM stage -> the run page names the
authenticated human and shows the outbound delivery receipt -> the audit chain still verifies.

Run locally with:  pytest -m e2e     (needs: python -m playwright install chromium)
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn

from atmpl.audit import verify_audit
from atmpl.demos import seed_demo_data, seed_demo_subscription
from atmpl.security.credentials import hash_password
from atmpl.settings import Settings
from atmpl.web.app import create_app
from atmpl.webhooks.outbox import deliver_pending

pytestmark = pytest.mark.e2e

E2E_USER = "synthetic.approver"
E2E_PASSWORD = "synthetic-e2e-password"
RECEIVER_SECRET = "whsec_synthetic_e2e_receiver"
ARTIFACTS = Path("var") / "e2e"


class _Receiver(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        type(self).received.append(json.loads(body or b"{}"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        return


@pytest.fixture(scope="module")
def receiver() -> Iterator[str]:
    _Receiver.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/hook"
    finally:
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    """Never hardcode a port in a test: a leftover server from a previous run owns it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def live_app(tmp_path_factory, receiver) -> Iterator[dict]:
    """A real uvicorn server, because a browser cannot drive TestClient."""
    root = tmp_path_factory.mktemp("e2e")
    port = _free_port()
    engine = seed_demo_data(root / "demo.db", root / "audit.jsonl")
    seed_demo_subscription(engine, receiver, RECEIVER_SECRET)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{(root / 'demo.db').as_posix()}",
        admin_user=E2E_USER,
        admin_password_hash=hash_password(E2E_PASSWORD),
        rate_limit="0/minute",
    )
    app = create_app(engine, settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("uvicorn did not start within 30s")
    try:
        yield {"url": f"http://127.0.0.1:{port}", "engine": engine, "app": app}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def page(live_app):
    playwright = pytest.importorskip("playwright.sync_api")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        instance = context.new_page()
        try:
            yield instance
        finally:
            context.tracing.stop(path=ARTIFACTS / "approval-flow-trace.zip")
            browser.close()


def test_a_human_signs_in_decides_and_the_evidence_lands_everywhere(page, live_app):
    base = live_app["url"]
    engine = live_app["engine"]

    # 1. The queue is behind a login.
    page.goto(f"{base}/approvals")
    assert page.url.endswith("/login")
    page.get_by_label("User").fill(E2E_USER)
    page.get_by_label("Password").fill(E2E_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{base}/approvals")
    page.screenshot(path=ARTIFACTS / "01-approvals-queue.png", full_page=True)

    # 2. Decide the seeded MEDIUM stage: the EU regional manager review.
    card = page.locator("article.approval-card", has_text="Regional manager review — EU").first
    card.get_by_label("Human actor").fill("Synthetic E2E Reviewer")
    card.get_by_label("Reason").fill("Reviewed the synthetic draft against the fixture.")
    card.get_by_role("button", name="Approve").click()
    page.get_by_text("Signed human decision recorded", exact=False).first.wait_for()
    page.screenshot(path=ARTIFACTS / "02-decision-recorded.png", full_page=True)

    # 3. The run page names who was signed in, not only who was typed.
    page.goto(f"{base}/runs/run_retail_eu_1042")
    page.get_by_text("SIGNED HUMAN DECISION", exact=False).first.wait_for()
    assert f"human:{E2E_USER}" in page.content()

    # 4. The outbound delivery is receipted against the run.
    deliver_pending(live_app["app"].state.context, ignore_backoff=True)
    page.reload()
    page.get_by_text("Webhook deliveries for this run", exact=False).first.wait_for()
    assert "run.stage.decided" in page.content()
    assert "delivered" in page.content()
    page.screenshot(path=ARTIFACTS / "03-run-evidence-and-delivery.png", full_page=True)

    delivered = [item for item in _Receiver.received if item["type"] == "run.stage.decided"]
    assert delivered, "the receiver never saw the decision"
    assert delivered[0]["data"]["principal"] == f"human:{E2E_USER}"

    # 5. The chain still verifies after everything above.
    page.goto(f"{base}/audit")
    page.get_by_role("button", name="Verify chain").click()
    page.locator("#verification .valid").wait_for()
    page.screenshot(path=ARTIFACTS / "04-audit-chain-verified.png", full_page=True)
    assert verify_audit(engine.audit.path).valid
